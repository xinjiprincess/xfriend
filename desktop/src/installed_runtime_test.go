package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInstalledRuntime(t *testing.T) {
	dir := t.TempDir()
	if _, installed, err := installedRuntime(dir); installed || err != nil {
		t.Fatalf("development fallback: installed=%v err=%v", installed, err)
	}
	manifest := filepath.Join(dir, "runtime-release.json")
	for _, data := range []string{`{`, `{"version":"../../outside"}`, `{"version":"1.4.4"}`} {
		if err := os.WriteFile(manifest, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		if _, installed, err := installedRuntime(dir); !installed || err == nil {
			t.Fatalf("invalid/incomplete runtime %q was accepted", data)
		}
	}
	root := filepath.Join(dir, "runtimes", "1.4.4")
	if err := os.MkdirAll(filepath.Dir(pythonExe(root)), 0755); err != nil {
		t.Fatal(err)
	}
	for path, data := range map[string][]byte{
		pythonExe(root):                    make([]byte, 1024),
		filepath.Join(root, "launch.py"):   []byte("# launch"),
		filepath.Join(root, "VERSION.txt"): []byte("octop_version=1.4.4\n"),
	} {
		if err := os.WriteFile(path, data, 0755); err != nil {
			t.Fatal(err)
		}
	}
	if got, installed, err := installedRuntime(dir); got != root || !installed || err != nil {
		t.Fatalf("got=%s installed=%v err=%v", got, installed, err)
	}
	contents := filepath.Join(t.TempDir(), "Xfriend.app", "Contents")
	if err := os.MkdirAll(contents, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(dir, filepath.Join(contents, "Resources")); err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(contents, "Resources", "runtimes", "1.4.4")
	if got, installed, err := installedRuntime(filepath.Join(contents, "MacOS")); got != want || !installed || err != nil {
		t.Fatalf("Mac bundle: got=%s installed=%v err=%v", got, installed, err)
	}
}

func TestInstalledUpgradeBacksUpOnlyOnVersionChange(t *testing.T) {
	home := t.TempDir()
	t.Setenv("XFRIEND_HOME", home)
	t.Setenv("OCTOP_DATABASE_URL", "")
	t.Setenv("OCTOP_DATABASE_DRIVER", "sqlite")
	t.Setenv("OCTOP_DATABASE_SQLITE_PATH", "")
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "VERSION.txt"), []byte("octop_version=1.4.4\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "xfriend.db"), []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	previous := runInstalledSQLiteBackup
	t.Cleanup(func() { runInstalledSQLiteBackup = previous })
	calls := 0
	runInstalledSQLiteBackup = func(python, source, destination string) error {
		calls++
		if source != filepath.Join(home, "xfriend.db") {
			t.Fatalf("unexpected source %s", source)
		}
		return os.WriteFile(destination, []byte("backup"), 0600)
	}
	if err := backupInstalledRuntimeUpgrade(root); err != nil || calls != 1 {
		t.Fatalf("initial upgrade: calls=%d err=%v", calls, err)
	}
	if err := os.WriteFile(installedRuntimeVersionPath(), []byte("1.4.4"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := backupInstalledRuntimeUpgrade(root); err != nil || calls != 1 {
		t.Fatalf("repeat launch: calls=%d err=%v", calls, err)
	}
}
