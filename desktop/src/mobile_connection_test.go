package main

import (
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestMobileManagementRequiresDesktopLoginAndOrigin(t *testing.T) {
	s := &desktopServer{base: "http://127.0.0.1:1234"}
	for _, tc := range []struct {
		method, origin string
		want           int
	}{{"GET", "", 401}, {"POST", "https://other.example", 403}, {"POST", "", 403}, {"POST", s.base, 401}} {
		request := httptest.NewRequest(tc.method, s.base+"/api/desktop/mobile/status", nil)
		request.Header.Set("Origin", tc.origin)
		response := httptest.NewRecorder()
		s.ServeHTTP(response, request)
		if response.Code != tc.want {
			t.Fatalf("%s %s: %d", tc.method, tc.origin, response.Code)
		}
	}
}

func TestMobileADBFindsConfiguredSDK(t *testing.T) {
	t.Setenv("PATH", "")
	t.Setenv("ANDROID_HOME", "")
	t.Setenv("LOCALAPPDATA", "")
	root := t.TempDir()
	t.Setenv("ANDROID_SDK_ROOT", root)
	name := "adb"
	if runtime.GOOS == "windows" {
		name = "adb.exe"
	}
	path := filepath.Join(root, "platform-tools", name)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("fixture"), 0600); err != nil {
		t.Fatal(err)
	}
	actual, err := mobileADB()
	if err != nil || actual != path {
		t.Fatal(actual, err)
	}
}

func TestMobileScopeCannotReadUnauthenticatedCloudStatus(t *testing.T) {
	s := &desktopServer{base: "http://127.0.0.1:1234"}
	r := httptest.NewRequest("GET", s.base+"/api/desktop/auth/status", nil)
	r.Header.Set("X-Xfriend-Mobile-Scope", "old-account")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 401 {
		t.Fatalf("old pairing read account status: %d", w.Code)
	}
}
