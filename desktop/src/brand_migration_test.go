package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestBrandMigrationPreservesExistingData(t *testing.T) {
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)
	t.Setenv("OCTOP_HOME", "")
	t.Setenv("XFRIEND_HOME", "")
	old := filepath.Join(home, ".singuxmate")
	if err := os.MkdirAll(old, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(old, "config.json"), []byte("existing data"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := migrateDesktopHome(); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(octopHome(), "config.json"))
	if err != nil || string(data) != "existing data" {
		t.Fatalf("data lost: %q, %v", data, err)
	}
	if err := migrateDesktopHome(); err != nil {
		t.Fatal(err)
	}
}

func TestBrandMigrationKeepsExplicitHome(t *testing.T) {
	home := t.TempDir()
	t.Setenv("XFRIEND_HOME", home)
	t.Setenv("OCTOP_HOME", filepath.Join(home, "unused"))
	if err := migrateDesktopHome(); err != nil {
		t.Fatal(err)
	}
	if got := octopHome(); got != home {
		t.Fatalf("explicit home ignored: %s", got)
	}
}
