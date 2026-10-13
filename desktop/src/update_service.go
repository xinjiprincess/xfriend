package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"

	"octop.desktop/internal/updates"
)

type UpdateStatus struct {
	Available      bool   `json:"available"`
	Current        string `json:"current"`
	Version        string `json:"version"`
	InstallerBytes int64  `json:"installerBytes"`
}

type UpdateResult struct {
	State   string `json:"state"`
	Message string `json:"message"`
	Time    string `json:"time"`
}

func (a *App) GetLastUpdateResult() (UpdateResult, error) {
	var result UpdateResult
	data, err := os.ReadFile(filepath.Join(octopHome(), "updates", "last-result.json"))
	if os.IsNotExist(err) {
		return result, nil
	}
	if err != nil {
		return result, err
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return UpdateResult{}, err
	}
	return result, nil
}

func installedUpdateContext() (string, string, string, error) {
	executable, err := os.Executable()
	if err != nil {
		return "", "", "", err
	}
	directory := filepath.Dir(executable)
	root, installed, err := installedRuntime(directory)
	if err != nil {
		return "", "", "", err
	}
	if !installed {
		return "", "", "", errors.New("updates require an installed desktop release")
	}
	return directory, root, portableVersion(root), nil
}

func checkUpdate(ctx context.Context) (UpdateStatus, []byte, updates.Release, error) {
	var status UpdateStatus
	var empty updates.Release
	_, _, current, err := installedUpdateContext()
	if err != nil {
		return status, nil, empty, err
	}
	minimum, err := updates.ReadSequence(octopHome())
	if err != nil {
		return status, nil, empty, err
	}
	release, signed, err := updates.FetchSigned(ctx, &http.Client{}, updates.StableManifestURL, updates.StablePolicy(minimum, time.Now()))
	if err != nil {
		return status, nil, empty, err
	}
	status = UpdateStatus{Current: current, Version: release.Version, InstallerBytes: release.Installer.Size, Available: updates.NewerVersion(current, release.Version)}
	return status, signed, release, nil
}

// CheckForUpdates is called from settings, after the normal desktop is ready.
func (a *App) CheckForUpdates() (UpdateStatus, error) {
	status, _, _, err := checkUpdate(context.Background())
	return status, err
}

// DownloadAndInstallUpdate stages verified bytes, then lets an independent
// helper wait for this process to exit before starting the signed installer.
func (a *App) DownloadAndInstallUpdate() error {
	directory, root, current, err := installedUpdateContext()
	if err != nil {
		return err
	}
	status, signed, release, err := checkUpdate(context.Background())
	if err != nil {
		return err
	}
	if !status.Available || status.Current != current {
		return errors.New("no newer release available")
	}
	cache := filepath.Join(octopHome(), "updates", "cache")
	installer, err := (updates.Downloader{Client: &http.Client{Timeout: 30 * time.Minute}, Cache: cache}).Fetch(context.Background(), release.Installer)
	if err != nil {
		return err
	}
	manifest := filepath.Join(cache, fmt.Sprintf("release-%d.json", release.Sequence))
	if existing, err := os.ReadFile(manifest); err == nil {
		if !bytes.Equal(existing, signed) {
			return errors.New("cached release sequence has different signed content")
		}
	} else if os.IsNotExist(err) {
		manifestTemp := manifest + ".tmp"
		if err := os.WriteFile(manifestTemp, signed, 0600); err != nil {
			return err
		}
		if err := os.Rename(manifestTemp, manifest); err != nil {
			_ = os.Remove(manifestTemp)
			return err
		}
	} else {
		return err
	}
	helper := filepath.Join(root, "Xfriend-updater.exe")
	if info, err := os.Stat(helper); err != nil || !info.Mode().IsRegular() {
		return errors.New("installed update helper is missing")
	}
	command := exec.Command(helper,
		"--manifest", manifest,
		"--installer", installer,
		"--install-dir", directory,
		"--state-dir", octopHome(),
		"--parent-pid", strconv.Itoa(os.Getpid()),
	)
	if err := command.Start(); err != nil {
		return err
	}
	if err := command.Process.Release(); err != nil {
		return err
	}
	go func() { time.Sleep(500 * time.Millisecond); a.requestQuit() }()
	return nil
}
