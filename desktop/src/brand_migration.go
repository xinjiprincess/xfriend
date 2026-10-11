package main

import (
	"fmt"
	"os"
	"path/filepath"
)

// Run before the WebView creates its profile under the new home.
func migrateDesktopHome() error {
	if os.Getenv("XFRIEND_HOME") != "" || os.Getenv("OCTOP_HOME") != "" {
		return nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	destination := filepath.Join(home, ".xfriend")
	if _, err := os.Stat(destination); err == nil {
		return nil
	} else if !os.IsNotExist(err) {
		return err
	}
	for _, name := range []string{".singuxmate", ".octop"} {
		source := filepath.Join(home, name)
		if _, err := os.Stat(source); os.IsNotExist(err) {
			continue
		} else if err != nil {
			return err
		}
		if err := os.Rename(source, destination); err != nil {
			return fmt.Errorf("无法迁移旧版数据，请先退出旧版程序后重试：%w", err)
		}
		return nil
	}
	return nil
}
