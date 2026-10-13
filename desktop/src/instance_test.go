package main

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestDesktopInstanceIdentityIsStableBeforeMigration(t *testing.T) {
	parent := t.TempDir()
	home := filepath.Join(parent, ".xfriend")
	id, err := instanceIDForUser("user-a", home)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(home); !os.IsNotExist(err) {
		t.Fatal("identity lookup created the migration destination")
	}
	if err := os.MkdirAll(home, 0700); err != nil {
		t.Fatal(err)
	}
	for _, alias := range []string{home, filepath.Join(home, "."), filepath.Join(home, "child", "..")} {
		other, err := instanceIDForUser("user-a", alias)
		if err != nil || other != id {
			t.Fatalf("same home changed identity %s %v", alias, err)
		}
	}
	if runtime.GOOS == "windows" {
		other, err := instanceIDForUser("user-a", strings.ToUpper(home))
		if err != nil || other != id {
			t.Fatalf("case changed identity %s %v", other, err)
		}
	}
	other, _ := instanceIDForUser("user-b", home)
	if other == id {
		t.Fatal("different OS users share the same Wails instance key")
	}
	other, _ = instanceIDForUser("user-a", filepath.Join(parent, "other-data"))
	if other == id {
		t.Fatal("separate data directories cannot be opened independently")
	}
}

func TestDesktopDataLockExcludesSecondInstanceAndReleases(t *testing.T) {
	path := filepath.Join(t.TempDir(), "instance.lock")
	first, err := lockInstanceFile(path)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	if second, err := lockInstanceFile(path); err == nil {
		second.Close()
		t.Fatal("two processes may mutate one data directory")
	}
	first.Close()
	second, err := lockInstanceFile(path)
	if err != nil {
		t.Fatal(err)
	}
	second.Close()
}

func TestDesktopDataLockIsReleasedAfterCrash(t *testing.T) {
	path := filepath.Join(t.TempDir(), "instance.lock")
	child := exec.Command(os.Args[0], "-test.run=^TestDesktopLockChild$")
	child.Env = append(os.Environ(), "XFRIEND_TEST_LOCK="+path)
	out, err := child.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	in, err := child.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	defer in.Close()
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = child.Process.Kill(); _ = child.Wait() })
	line, err := bufio.NewReader(out).ReadString('\n')
	if err != nil || strings.TrimSpace(line) != "locked" {
		t.Fatalf("child %q %v", line, err)
	}
	if lock, err := lockInstanceFile(path); err == nil {
		lock.Close()
		t.Fatal("child lock was ignored")
	}
	_ = child.Process.Kill()
	_ = child.Wait()
	lock, err := lockInstanceFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lock.Close()
}

func TestDesktopLockChild(t *testing.T) {
	path := os.Getenv("XFRIEND_TEST_LOCK")
	if path == "" {
		return
	}
	lock, err := lockInstanceFile(path)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	fmt.Println("locked")
	var buffer [1]byte
	_, _ = os.Stdin.Read(buffer[:])
}
