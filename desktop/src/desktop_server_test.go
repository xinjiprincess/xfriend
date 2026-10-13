package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"testing/fstest"
	"time"

	"github.com/coder/websocket"
)

func testDesktop(t *testing.T) *desktopServer {
	t.Helper()
	index := []byte(`<!doctype html><html lang="zh"><body>desktop shell</body></html>`)
	sum := sha256.Sum256(index)
	manifest, _ := json.Marshal(dashboardManifest{Version: "1.4.0", IndexSHA256: hex.EncodeToString(sum[:])})
	server, err := newDesktopServer(fstest.MapFS{
		"index.html": {Data: index}, "dashboard-manifest.json": {Data: manifest},
		"assets/app-test.js": {Data: []byte("console.log('loaded')")},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := server.listen(0); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.close)
	return server
}

func desktopGET(t *testing.T, target string) (int, string, http.Header) {
	t.Helper()
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get(target)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	return response.StatusCode, string(body), response.Header
}

func TestDesktopServesSPABeforePythonAndNeverAuthenticatesEarly(t *testing.T) {
	server := testDesktop(t)
	for _, route := range []string{"", "agents/test/chat"} {
		code, body, header := desktopGET(t, server.base+route)
		if code != 200 || !strings.Contains(body, `data-xfriend-desktop="1"`) || header.Get("Cache-Control") != "no-store" {
			t.Fatalf("SPA response: %d %s %v", code, body, header)
		}
	}
	code, body, _ := desktopGET(t, server.base+"assets/app-test.js")
	if code != 200 || !strings.Contains(body, "loaded") {
		t.Fatalf("asset: %d %s", code, body)
	}
	code, _, _ = desktopGET(t, server.base+"assets/missing.js")
	if code != 404 {
		t.Fatalf("missing asset returned %d", code)
	}
	code, body, header := desktopGET(t, server.base+"api/auth/me")
	if code != 503 || !strings.Contains(body, `"code":"SERVICE_STARTING"`) || header.Get("Set-Cookie") != "" {
		t.Fatalf("early auth: %d %s %v", code, body, header)
	}
	code, body, _ = desktopGET(t, server.base+"api/desktop/startup")
	var status desktopStartup
	if err := json.Unmarshal([]byte(body), &status); err != nil {
		t.Fatal(err)
	}
	if code != 200 || status.Application != "xfriend" || status.Protocol != 1 || status.State != "starting" || status.ElapsedMS < 0 {
		t.Fatalf("startup %d %+v", code, status)
	}
	server.update("failed", "preparing_runtime", "test extraction error")
	_, body, _ = desktopGET(t, server.base+"api/desktop/startup")
	if !strings.Contains(body, `"state":"failed"`) || !strings.Contains(body, "test extraction error") {
		t.Fatal(body)
	}
}

func TestDesktopProxyPreservesHostOriginCredentialsAndResponse(t *testing.T) {
	server := testDesktop(t)
	host := strings.TrimSuffix(strings.TrimPrefix(server.base, "http://"), "/")
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host != host || r.Header.Get("Origin") != strings.TrimSuffix(server.base, "/") || r.Header.Get("Authorization") != "Bearer test-token" || r.Header.Get("Cookie") != "session=existing" || r.URL.RawQuery != "page=2" {
			t.Errorf("headers changed: %s %+v %s", r.Host, r.Header, r.URL)
		}
		http.SetCookie(w, &http.Cookie{Name: "session", Value: "renewed", Path: "/", HttpOnly: true})
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"code":"TOKEN_EXPIRED"}`))
	}))
	defer backend.Close()
	if err := server.setBackend(backend.URL); err != nil {
		t.Fatal(err)
	}
	server.update("ready", "core_ready", "")
	request, _ := http.NewRequest(http.MethodGet, server.base+"api/auth/me?page=2", nil)
	request.Header.Set("Origin", strings.TrimSuffix(server.base, "/"))
	request.Header.Set("Authorization", "Bearer test-token")
	request.Header.Set("Cookie", "session=existing")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 401 || !strings.Contains(response.Header.Get("Set-Cookie"), "session=renewed") {
		t.Fatalf("backend auth response changed: %+v", response)
	}
}

func TestDesktopStreamsSSEWithoutWaitingForCompletion(t *testing.T) {
	server := testDesktop(t)
	finish := make(chan struct{})
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		select {
		case <-finish:
		case <-r.Context().Done():
		}
	}))
	defer backend.Close()
	defer close(finish)
	if err := server.setBackend(backend.URL); err != nil {
		t.Fatal(err)
	}
	server.update("ready", "core_ready", "")
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get(server.base + "api/events")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	line, err := bufio.NewReader(response.Body).ReadString('\n')
	if err != nil || line != "data: first\n" {
		t.Fatalf("stream did not flush: %q %v", line, err)
	}
}

func TestDesktopProxiesWebSocketHandshakeAndMessages(t *testing.T) {
	server := testDesktop(t)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer ws-token" || r.Header.Get("Origin") != strings.TrimSuffix(server.base, "/") {
			t.Error("websocket credentials/origin changed")
		}
		connection, err := websocket.Accept(w, r, nil)
		if err != nil {
			t.Error(err)
			return
		}
		defer connection.CloseNow()
		kind, message, err := connection.Read(r.Context())
		if err == nil {
			err = connection.Write(r.Context(), kind, message)
		}
		if err != nil {
			t.Error(err)
		}
	}))
	defer backend.Close()
	if err := server.setBackend(backend.URL); err != nil {
		t.Fatal(err)
	}
	server.update("ready", "core_ready", "")
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	connection, _, err := websocket.Dial(ctx, strings.Replace(server.base, "http://", "ws://", 1)+"api/desktop/ws", &websocket.DialOptions{HTTPHeader: http.Header{"Authorization": {"Bearer ws-token"}, "Origin": {strings.TrimSuffix(server.base, "/")}}})
	if err != nil {
		t.Fatal(err)
	}
	defer connection.CloseNow()
	if err := connection.Write(ctx, websocket.MessageText, []byte("hello")); err != nil {
		t.Fatal(err)
	}
	_, data, err := connection.Read(ctx)
	if err != nil || string(data) != "hello" {
		t.Fatalf("websocket: %q %v", data, err)
	}
}

func TestDesktopDoesNotChangePortOrUseUnrelatedListener(t *testing.T) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	server := &desktopServer{}
	if err := server.listen(listener.Addr().(*net.TCPAddr).Port); err == nil {
		server.close()
		t.Fatal("occupied stable port must fail instead of changing origin")
	}
}

func TestDesktopRejectsForeignHostAndRetiresOldWorker(t *testing.T) {
	server := testDesktop(t)
	request, _ := http.NewRequest(http.MethodGet, server.base+"api/desktop/startup", nil)
	request.Host = "attacker.example"
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusForbidden {
		t.Fatal(response.StatusCode)
	}
	code, worker, header := desktopGET(t, server.base+"sw.js")
	if code != 200 || !strings.Contains(worker, "self.registration.unregister()") || !strings.Contains(worker, "client.navigate(client.url)") || header.Get("Cache-Control") != "no-store" {
		t.Fatalf("worker %d %s %v", code, worker, header)
	}
}

func TestDesktopRequiresMatchingRuntimeVersionAndDashboard(t *testing.T) {
	server := testDesktop(t)
	root := t.TempDir()
	indexPath := filepath.Join(root, "packages", "octop", "dashboard", "index.html")
	if err := os.MkdirAll(filepath.Dir(indexPath), 0755); err != nil {
		t.Fatal(err)
	}
	index, _ := server.assets.Open("index.html")
	data, _ := io.ReadAll(index)
	index.Close()
	for _, test := range []struct {
		version, html string
		wantErr       bool
	}{
		{"1.4.0", string(data), false}, {"1.4.1", string(data), true}, {"1.4.0", "old dashboard", true},
	} {
		if err := os.WriteFile(filepath.Join(root, "VERSION.txt"), []byte("octop_version="+test.version), 0644); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(indexPath, []byte(test.html), 0644); err != nil {
			t.Fatal(err)
		}
		if err := server.verifyRuntime(root); (err != nil) != test.wantErr {
			t.Fatalf("version %s: %v", test.version, err)
		}
	}
}

func TestDesktopSameVersionRepackRefreshesUIAfterBackup(t *testing.T) {
	server := testDesktop(t)
	home := t.TempDir()
	t.Setenv("XFRIEND_HOME", home)
	oldZIP := filepath.Join(t.TempDir(), "old.zip")
	writeTestGreenZip(t, oldZIP, "1.4.0", "<html>old assets</html>")
	if err := unzipGreen(oldZIP, portableDir()); err != nil {
		t.Fatal(err)
	}
	index, err := fs.ReadFile(server.assets, "index.html")
	if err != nil {
		t.Fatal(err)
	}
	newZIP := filepath.Join(t.TempDir(), "new.zip")
	writeTestGreenZip(t, newZIP, "1.4.0", string(index))
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", newZIP)
	database := filepath.Join(home, "xfriend.db")
	if err := os.WriteFile(database, []byte("user data"), 0600); err != nil {
		t.Fatal(err)
	}
	backups := 0
	previous := runSQLiteBackup
	runSQLiteBackup = func(_, source, destination string) error {
		backups++
		if source != database || server.verifyRuntime(portableDir()) == nil {
			t.Error("backup must run before replacing the old runtime")
		}
		return os.WriteFile(destination, []byte("backup"), 0600)
	}
	t.Cleanup(func() { runSQLiteBackup = previous })
	if err := ensurePortableWithDashboard(LocaleZH, func(string) {}, server); err != nil {
		t.Fatal(err)
	}
	if err := server.verifyRuntime(portableDir()); err != nil {
		t.Fatal(err)
	}
	if backups != 1 {
		t.Fatalf("same-version refresh backups=%d", backups)
	}
	if data, err := os.ReadFile(database); err != nil || string(data) != "user data" {
		t.Fatalf("user database changed: %q %v", data, err)
	}
	if !launchReady(portableDir() + ".previous") {
		t.Fatal("previous runtime was removed before health check")
	}
}

func TestDesktopBackendDisconnectDoesNotBecomeAuthFailure(t *testing.T) {
	server := testDesktop(t)
	backend := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	if err := server.setBackend(backend.URL); err != nil {
		t.Fatal(err)
	}
	backend.Close()
	server.update("ready", "core_ready", "")
	code, body, _ := desktopGET(t, server.base+"api/auth/me")
	if code != 503 || !strings.Contains(body, "SERVICE_STARTING") {
		t.Fatalf("%d %s", code, body)
	}
}

func TestDesktopStatusEndpointCannotBeShadowedByBackend(t *testing.T) {
	server := testDesktop(t)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("desktop status reached Python") }))
	defer backend.Close()
	_ = server.setBackend(backend.URL)
	server.update("ready", "core_ready", "")
	_, body, _ := desktopGET(t, server.base+"api/desktop/startup")
	if !strings.Contains(body, `"state":"ready"`) {
		t.Fatal(body)
	}
	parsed, _ := url.Parse(server.base)
	if parsed.Hostname() != "127.0.0.1" {
		t.Fatal(fmt.Sprintf("non-loopback bind %s", parsed))
	}
}
