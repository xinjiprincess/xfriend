package main

import (
	"bytes"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Dashboard assets are staged from the same portable ZIP at build time. They can
// be served directly from the executable before the Python runtime is extracted.
//
//go:embed all:ui
var dashboardAssets embed.FS

type dashboardManifest struct {
	Version        string        `json:"version"`
	IndexSHA256    string        `json:"index_sha256"`
	AccountProfile *cloudProfile `json:"account_profile,omitempty"`
}

type desktopStartup struct {
	Application string `json:"application"`
	Protocol    int    `json:"protocol"`
	State       string `json:"state"`
	Phase       string `json:"phase"`
	Message     string `json:"message,omitempty"`
	ElapsedMS   int64  `json:"elapsed_ms"`
}

type desktopServer struct {
	mu           sync.RWMutex
	started      time.Time
	status       desktopStartup
	proxy        *httputil.ReverseProxy
	assets       fs.FS
	manifest     dashboardManifest
	server       *http.Server
	base         string
	cloud        *cloudAuth
	cloudContext string
	cloudToken   string
	mobile       *mobileConnection
}

func newDesktopServer(files fs.FS) (*desktopServer, error) {
	data, err := fs.ReadFile(files, "dashboard-manifest.json")
	if err != nil {
		return nil, fmt.Errorf("桌面界面资源缺失，请重新安装完整安装包：%w", err)
	}
	var manifest dashboardManifest
	if err = json.Unmarshal(data, &manifest); err != nil {
		return nil, err
	}
	index, err := fs.ReadFile(files, "index.html")
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(index)
	if manifest.Version == "" || manifest.IndexSHA256 != hex.EncodeToString(sum[:]) {
		return nil, fmt.Errorf("桌面界面资源校验失败，请重新安装完整安装包")
	}
	return &desktopServer{started: time.Now(), assets: files, manifest: manifest,
		status: desktopStartup{Application: "xfriend", Protocol: 1, State: "starting", Phase: "preparing_runtime"}}, nil
}

func (s *desktopServer) listen(port int) error {
	listener, err := net.Listen("tcp4", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)))
	if err != nil {
		return fmt.Errorf("本机端口 %d 已被占用，请退出占用程序后重试：%w", port, err)
	}
	s.base = dashboardURL(listener.Addr().(*net.TCPAddr).Port)
	s.server = &http.Server{Handler: s, ReadHeaderTimeout: 10 * time.Second}
	go func() {
		if err := s.server.Serve(listener); err != nil && err != http.ErrServerClosed {
			s.update("failed", "http_server", err.Error())
		}
	}()
	return nil
}

func (s *desktopServer) close() {
	if s != nil {
		s.stopMobile()
	}
	if s != nil && s.server != nil {
		_ = s.server.Close()
	}
}

func (s *desktopServer) update(state, phase, message string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.status.State, s.status.Phase, s.status.Message = state, phase, message
}

func (s *desktopServer) snapshot() desktopStartup {
	s.mu.RLock()
	defer s.mu.RUnlock()
	status := s.status
	status.ElapsedMS = time.Since(s.started).Milliseconds()
	return status
}

func (s *desktopServer) setBackend(base string) error {
	target, err := url.Parse(base)
	if err != nil {
		return err
	}
	if target.Scheme != "http" || target.Hostname() != "127.0.0.1" || target.Port() == "" {
		return fmt.Errorf("invalid desktop backend address")
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	// The default Director changes URL.Host but preserves request.Host and Origin.
	// Authentication/cookies remain owned by Python; no credentials are injected.
	proxy.FlushInterval = -1
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, _ error) {
		serviceStarting(w, "本机服务暂时无法连接，请稍后重试。")
	}
	s.mu.Lock()
	s.proxy = proxy
	s.mu.Unlock()
	return nil
}

func (s *desktopServer) verifyRuntime(root string) error {
	if portableVersion(root) != s.manifest.Version {
		return fmt.Errorf("桌面界面与服务版本不匹配（界面 %s，服务 %s），请重新安装完整安装包", s.manifest.Version, portableVersion(root))
	}
	index, err := os.ReadFile(filepath.Join(root, "packages", "octop", "dashboard", "index.html"))
	if err != nil {
		return err
	}
	sum := sha256.Sum256(index)
	if hex.EncodeToString(sum[:]) != s.manifest.IndexSHA256 {
		return fmt.Errorf("桌面界面与服务资源不匹配，请重新安装完整安装包")
	}
	return nil
}

func serviceStarting(w http.ResponseWriter, message string) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Retry-After", "1")
	w.WriteHeader(http.StatusServiceUnavailable)
	_ = json.NewEncoder(w).Encode(map[string]any{"code": "SERVICE_STARTING", "message": message, "retry_after_ms": 500})
}

func (s *desktopServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Bind only to loopback and reject arbitrary Host headers (DNS rebinding).
	if s.base != "" && r.Host != strings.TrimSuffix(strings.TrimPrefix(s.base, "http://"), "/") {
		http.Error(w, "invalid desktop host", http.StatusForbidden)
		return
	}
	if scope := r.Header.Get("X-Xfriend-Mobile-Scope"); scope != "" {
		if s.cloud == nil {
			http.Error(w, "mobile account unavailable", http.StatusUnauthorized)
			return
		}
		key, _, valid := s.cloud.context()
		if !valid || scope != key {
			http.Error(w, "mobile account changed", http.StatusUnauthorized)
			return
		}
	}
	if r.URL.Path == "/api/desktop/startup" {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		_ = json.NewEncoder(w).Encode(s.snapshot())
		return
	}
	if strings.HasPrefix(r.URL.Path, "/api/desktop/auth/") {
		s.cloudHTTP(w, r)
		return
	}
	if strings.HasPrefix(r.URL.Path, "/api/desktop/sync/") {
		s.customerSyncHTTP(w, r)
		return
	}
	if strings.HasPrefix(r.URL.Path, "/api/desktop/mobile/") {
		s.mobileHTTP(w, r)
		return
	}
	if r.URL.Path == "/api" || strings.HasPrefix(r.URL.Path, "/api/") {
		if (r.URL.Path == "/api/customer-sync" || strings.HasPrefix(r.URL.Path, "/api/customer-sync/")) && !s.customerSyncLocalScope(w, r) {
			return
		}
		if s.cloud != nil {
			if r.Header.Get("Sec-Fetch-Site") == "cross-site" || (r.Header.Get("Origin") != "" && r.Header.Get("Origin") != strings.TrimRight(s.base, "/")) {
				http.Error(w, "invalid desktop origin", http.StatusForbidden)
				return
			}
			key, _, valid := s.cloud.context()
			if scope := r.Header.Get("X-Xfriend-Mobile-Scope"); scope != "" && scope != key {
				http.Error(w, "mobile account changed", http.StatusUnauthorized)
				return
			}
			s.mu.RLock()
			expected, token := s.cloudContext, s.cloudToken
			s.mu.RUnlock()
			if !valid {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(401)
				_ = json.NewEncoder(w).Encode(map[string]string{"code": "AUTH_FAILED"})
				return
			}
			if key != expected || token == "" {
				serviceStarting(w, "正在恢复当前账号的本机服务。")
				return
			}
			// The renderer never receives the local service bearer or cloud refresh credential.
			r = r.Clone(r.Context())
			r.Header.Set("Authorization", "Bearer "+token)
			r.Header.Del("X-Xfriend-Cloud-Bridge")
			r.Header.Del("X-Xfriend-Mobile-Scope")
			query := r.URL.Query()
			query.Del("access_token")
			query.Del("token")
			r.URL.RawQuery = query.Encode()
			if r.URL.Path == "/api/auth/login" || strings.HasPrefix(r.URL.Path, "/api/setup/") || r.URL.Path == "/api/auth/cloud-session" {
				w.WriteHeader(http.StatusForbidden)
				return
			}
		}
		s.mu.RLock()
		ready, proxy := s.status.State == "ready", s.proxy
		s.mu.RUnlock()
		if !ready || proxy == nil {
			serviceStarting(w, "本机服务正在启动，请稍候。")
			return
		}
		proxy.ServeHTTP(w, r)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	if r.URL.Path == "/sw.js" {
		// An older dashboard worker can serve an old cached index before the new
		// desktop marker runs. Retire it and reload its controlled windows once.
		w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Service-Worker-Allowed", "/")
		if r.Method != http.MethodHead {
			_, _ = w.Write([]byte(retiredDesktopWorker))
		}
		return
	}
	name := strings.TrimPrefix(path.Clean("/"+r.URL.Path), "/")
	if name == "" {
		name = "index.html"
	}
	data, err := fs.ReadFile(s.assets, name)
	if err != nil {
		if strings.HasPrefix(name, "assets/") || path.Ext(name) != "" {
			http.NotFound(w, r)
			return
		}
		name = "index.html"
		data, err = fs.ReadFile(s.assets, name)
		if err != nil {
			http.Error(w, "desktop assets unavailable", http.StatusInternalServerError)
			return
		}
	}
	if name == "index.html" {
		if r.Header.Get("X-Xfriend-Mobile-Scope") != "" {
			data = bytes.Replace(data, []byte("<html"), []byte(`<html data-xfriend-mobile="1"`), 1)
		}
		data = bytes.Replace(data, []byte("<html"), []byte(`<html data-xfriend-desktop="1"`), 1)
		if s.cloud != nil {
			data = bytes.Replace(data, []byte("<html"), []byte(`<html data-xfriend-cloud="1"`), 1)
		}
		w.Header().Set("Cache-Control", "no-store")
	} else if strings.HasPrefix(name, "assets/") {
		w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	} else {
		w.Header().Set("Cache-Control", "no-cache")
	}
	http.ServeContent(w, r, name, time.Time{}, bytes.NewReader(data))
}

const retiredDesktopWorker = `self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil((async()=>{
  await self.registration.unregister();
  const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
  await Promise.all(windows.filter(client=>new URL(client.url).origin===self.location.origin).map(client=>client.navigate(client.url)));
})()));`
