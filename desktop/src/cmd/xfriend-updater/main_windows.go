package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"

	"octop.desktop/internal/updates"
)

func waitForParent(pid uint32) error {
	process, err := windows.OpenProcess(windows.SYNCHRONIZE, false, pid)
	if errors.Is(err, windows.ERROR_INVALID_PARAMETER) {
		return nil
	}
	if err != nil {
		return err
	}
	defer windows.CloseHandle(process)
	result, err := windows.WaitForSingleObject(process, 60*1000)
	if err != nil {
		return err
	}
	if result != windows.WAIT_OBJECT_0 {
		return errors.New("application did not exit")
	}
	return nil
}

// ShellExecuteEx asks Windows to elevate the signed installer while retaining
// the helper's original user token for relaunching the desktop afterwards.
func runInstaller(installer, directory string) error {
	verb, err := windows.UTF16PtrFromString("runas")
	if err != nil {
		return err
	}
	file, err := windows.UTF16PtrFromString(installer)
	if err != nil {
		return err
	}
	// NSIS requires /D to be last and consumes the remaining unquoted path.
	parameters, err := windows.UTF16PtrFromString("/S /D=" + directory)
	if err != nil {
		return err
	}
	info := struct {
		Size, Mask                        uint32
		Window                            windows.Handle
		Verb, File, Parameters, Directory *uint16
		Show                              int32
		Instance                          windows.Handle
		IDList                            unsafe.Pointer
		Class                             *uint16
		ClassKey                          windows.Handle
		HotKey                            uint32
		Icon                              windows.Handle
		Process                           windows.Handle
	}{Mask: 0x40 | 0x100, Verb: verb, File: file, Parameters: parameters, Show: 1}
	info.Size = uint32(unsafe.Sizeof(info))
	result, _, callErr := windows.NewLazySystemDLL("shell32.dll").NewProc("ShellExecuteExW").Call(uintptr(unsafe.Pointer(&info)))
	if result == 0 {
		return fmt.Errorf("installer elevation failed: %w", callErr)
	}
	if info.Process == 0 {
		return errors.New("installer did not provide a process handle")
	}
	defer windows.CloseHandle(info.Process)
	if result, err := windows.WaitForSingleObject(info.Process, windows.INFINITE); err != nil || result != windows.WAIT_OBJECT_0 {
		return fmt.Errorf("waiting for installer failed: %v", err)
	}
	var code uint32
	if err := windows.GetExitCodeProcess(info.Process, &code); err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("installer exited with code %d", code)
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		_, _ = fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() (runErr error) {
	manifestPath := flag.String("manifest", "", "signed manifest path")
	installerPath := flag.String("installer", "", "verified installer path")
	installDir := flag.String("install-dir", "", "current installation directory")
	stateDir := flag.String("state-dir", "", "current user data directory")
	parentPID := flag.Uint("parent-pid", 0, "desktop process to wait for")
	flag.Parse()
	if *manifestPath == "" || *installerPath == "" || *installDir == "" || *stateDir == "" || *parentPID == 0 || flag.NArg() != 0 {
		return errors.New("missing update arguments")
	}
	desktopExited := false
	defer func() {
		result := map[string]any{"state": "success", "time": time.Now().UTC().Format(time.RFC3339)}
		if runErr != nil {
			result["state"] = "failed"
			result["message"] = runErr.Error()
			if desktopExited {
				_ = exec.Command(filepath.Join(*installDir, "Xfriend.exe")).Start()
			}
		}
		data, _ := json.Marshal(result)
		path := filepath.Join(*stateDir, "updates", "last-result.json")
		if os.MkdirAll(filepath.Dir(path), 0700) == nil {
			_ = os.WriteFile(path+".tmp", data, 0600)
			_ = os.Rename(path+".tmp", path)
		}
	}()
	manifest, err := os.ReadFile(*manifestPath)
	if err != nil {
		return err
	}
	minimum, err := updates.ReadSequence(*stateDir)
	if err != nil {
		return err
	}
	flavor, err := updates.InstalledFlavor(*installDir)
	if err != nil {
		return err
	}
	policy := updates.StablePolicy(minimum, time.Now())
	policy.Flavor = flavor
	release, err := updates.Verify(manifest, policy)
	if err != nil {
		return err
	}
	if !updates.VerifyArtifact(*installerPath, release.Installer) {
		return errors.New("installer digest mismatch")
	}
	var current struct {
		Version string `json:"version"`
	}
	before, err := os.ReadFile(filepath.Join(*installDir, "runtime-release.json"))
	if err != nil {
		return err
	}
	if err := json.Unmarshal(before, &current); err != nil || current.Version == "" {
		return errors.New("invalid installed version")
	}
	if !updates.NewerVersion(current.Version, release.Version) {
		return errors.New("release is not newer than installed version")
	}
	if err := waitForParent(uint32(*parentPID)); err != nil {
		return err
	}
	desktopExited = true
	// NSIS elevates only the installation step for a machine-wide install. The
	// helper remains at the original user privilege and starts the app likewise.
	if err := runInstaller(*installerPath, *installDir); err != nil {
		return fmt.Errorf("installer failed: %w", err)
	}
	after, err := os.ReadFile(filepath.Join(*installDir, "runtime-release.json"))
	if err != nil {
		return err
	}
	var installed struct {
		Version string `json:"version"`
	}
	if err := json.Unmarshal(after, &installed); err != nil || installed.Version != release.Version {
		return errors.New("installed version did not match signed release")
	}
	if _, err := os.Stat(filepath.Join(*installDir, "runtimes", release.Version, "launch.py")); err != nil {
		return errors.New("new runtime is incomplete")
	}
	if err := updates.StoreSequence(*stateDir, release.Sequence); err != nil {
		return err
	}
	return exec.Command(filepath.Join(*installDir, "Xfriend.exe")).Start()
}
