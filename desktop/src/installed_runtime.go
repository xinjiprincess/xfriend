package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// Installed releases keep immutable code beside the executable and mutable
// accounts, conversations and trial state under octopHome(). No extraction is
// performed at launch, including the first launch after an upgrade.
func installedRuntime(directory string) (root string, installed bool, err error) {
	// macOS reserves Contents/MacOS for signed executables, not runtime data.
	if filepath.Base(directory) == "MacOS" && filepath.Base(filepath.Dir(directory)) == "Contents" {
		directory = filepath.Join(filepath.Dir(directory), "Resources")
	}
	data, err := os.ReadFile(filepath.Join(directory, "runtime-release.json"))
	if os.IsNotExist(err) {
		return "", false, nil
	}
	if err != nil {
		return "", true, err
	}
	var manifest struct {
		Version string `json:"version"`
	}
	if err := json.Unmarshal(data, &manifest); err != nil {
		return "", true, err
	}
	if !regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*$`).MatchString(manifest.Version) {
		return "", true, fmt.Errorf("invalid installed runtime version")
	}
	root = filepath.Join(directory, "runtimes", manifest.Version)
	if !launchReady(root) || portableVersion(root) != manifest.Version {
		return "", true, fmt.Errorf("installed runtime is incomplete or mismatched: %s", root)
	}
	return root, true, nil
}

func installedRuntimeVersionPath() string {
	return filepath.Join(octopHome(), "desktop-runtime-version.txt")
}

func backupInstalledRuntimeUpgrade(root string) error {
	previous, err := os.ReadFile(installedRuntimeVersionPath())
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	current := strings.TrimSpace(string(previous))
	if current == "" {
		current = portableVersion(filepath.Join(octopHome(), "portable"))
	}
	next := portableVersion(root)
	if current != next {
		_, err = backupSQLiteBeforeUpgradeWith(root, current, next, runInstalledSQLiteBackup)
		return err
	}
	return nil
}
