package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFirstLaunchUsesChinese(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	if got := loadSettings(); got.Locale != LocaleZH || got.Port != 18088 {
		t.Fatalf("unexpected first-launch settings: %+v", got)
	}
}

func TestDesktopHomeDoesNotReuseOctopData(t *testing.T) {
	t.Setenv("OCTOP_HOME", "")
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	if got := octopHome(); got != filepath.Join(home, ".xfriend") {
		t.Fatalf("desktop must use its own data directory: %s", got)
	}
	override := t.TempDir()
	t.Setenv("OCTOP_HOME", override)
	if got := octopHome(); got != override {
		t.Fatalf("explicit home must be respected: %s", got)
	}
}

func TestLoadSettingsMigratesLegacyPreventSleepMac(t *testing.T) {
	temp := t.TempDir()
	t.Setenv("OCTOP_HOME", temp)
	if err := os.WriteFile(
		filepath.Join(temp, "desktop-settings.json"),
		[]byte(`{"preventSleepMac":true}`),
		0o644,
	); err != nil {
		t.Fatal(err)
	}

	if !loadSettings().PreventSleep {
		t.Fatal("legacy preventSleepMac should migrate to preventSleep")
	}
}
