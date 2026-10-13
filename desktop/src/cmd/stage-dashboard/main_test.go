package main

import (
	"archive/zip"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStageExtractsDashboardWithoutPython(t *testing.T) {
	archive := filepath.Join(t.TempDir(), "portable.zip")
	file, err := os.Create(archive)
	if err != nil {
		t.Fatal(err)
	}
	writer := zip.NewWriter(file)
	for name, data := range map[string]string{
		"Xfriend/VERSION.txt":                                 "platform=windows-amd64\noctop_version=1.4.0\n",
		"Xfriend/packages/octop/dashboard/index.html":         "<html><body>test</body></html>",
		"Xfriend/packages/octop/dashboard/assets/app-test.js": "console.log('test')",
		"Xfriend/runtime/python.exe":                          "not copied",
	} {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(data)); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	file.Close()
	output := filepath.Join(t.TempDir(), "ui")
	if err := stage(archive, "", output, true); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(output, "dashboard-manifest.json"))
	if err != nil || !strings.Contains(string(data), `"version":"1.4.0"`) {
		t.Fatalf("%s %v", data, err)
	}
	if _, err := os.Stat(filepath.Join(output, "assets", "app-test.js")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(output, "runtime")); !os.IsNotExist(err) {
		t.Fatal("Python was extracted into the shell assets")
	}
}

func TestStageProductionCannotSilentlyUseDevelopmentAssets(t *testing.T) {
	t.Chdir(t.TempDir())
	if err := stage("", "missing", filepath.Join(t.TempDir(), "ui"), true); err == nil {
		t.Fatal("production package accepted an unspecified runtime")
	}
}
