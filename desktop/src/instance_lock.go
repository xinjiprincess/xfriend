package main

import (
	"fmt"
	"os"
	"path/filepath"
)

// Wails supplies the window-activation IPC. This OS-backed file lock additionally
// covers the same Windows user running in two login/RDP sessions, where Wails'
// named mutex is session-local. The file is outside the home being migrated.
func lockDesktopData(instanceID string) (*os.File, error) {
	cache, err := os.UserCacheDir()
	if err != nil {
		return nil, err
	}
	directory := filepath.Join(cache, "Xfriend", "instance-locks")
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, err
	}
	return lockInstanceFile(filepath.Join(directory, instanceID+".lock"))
}

func lockInstanceFile(path string) (*os.File, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err := tryLockInstance(file); err != nil {
		file.Close()
		return nil, fmt.Errorf("当前数据目录已由另一个 Singu Xfriend 实例使用，请切换到已打开的窗口：%w", err)
	}
	// Closing the handle releases the lock, including process crash/termination.
	return file, nil
}
