package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"octop.desktop/internal/mobilebridge"
)

type mobileConnection struct {
	bridge *mobilebridge.Bridge
	server *http.Server
	cancel context.CancelFunc
	mode   string
	port   int
}
type mobileOptions struct {
	Mode        string `json:"mode"`
	Port        int    `json:"port"`
	Origin      string `json:"origin"`
	Certificate string `json:"certificate"`
	Key         string `json:"key"`
}

func mobileADB() (string, error) {
	if path, err := exec.LookPath("adb"); err == nil {
		return path, nil
	}
	name := "adb"
	defaultSDK := filepath.Join(os.Getenv("LOCALAPPDATA"), "Android", "Sdk")
	if runtime.GOOS == "windows" {
		name = "adb.exe"
	}
	if runtime.GOOS == "darwin" {
		home, _ := os.UserHomeDir()
		defaultSDK = filepath.Join(home, "Library", "Android", "sdk")
	}
	for _, root := range []string{os.Getenv("ANDROID_SDK_ROOT"), os.Getenv("ANDROID_HOME"), defaultSDK} {
		if !filepath.IsAbs(root) {
			continue
		}
		path := filepath.Join(root, "platform-tools", name)
		if info, err := os.Stat(path); err == nil && !info.IsDir() {
			return path, nil
		}
	}
	return "", fmt.Errorf("ADB unavailable")
}

func (s *desktopServer) mobileIdentity() string {
	if s.cloud == nil {
		return ""
	}
	s.cloud.mu.Lock()
	defer s.cloud.mu.Unlock()
	if _, err := s.cloud.validLocked(); err != nil || s.cloud.state == "logged_out" {
		return ""
	}
	sum := sha256.Sum256([]byte(s.cloud.profile.ServerURL + "\x00" + s.cloud.session.User.UserID + "\x00" + s.cloud.session.SpaceID))
	return fmt.Sprintf("%s:%d", hex.EncodeToString(sum[:]), s.cloud.epoch)
}
func (s *desktopServer) stopMobile() {
	s.mu.Lock()
	m := s.mobile
	s.mobile = nil
	s.mu.Unlock()
	if m != nil {
		m.cancel()
		m.bridge.Close()
		_ = m.server.Close()
	}
}

func (s *desktopServer) mobileHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	fail := func(code int, message string) {
		w.WriteHeader(code)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": message})
	}
	if r.Header.Get("Sec-Fetch-Site") == "cross-site" || (r.Header.Get("Origin") != "" && r.Header.Get("Origin") != strings.TrimRight(s.base, "/")) || (r.Method != "GET" && r.Header.Get("Origin") != strings.TrimRight(s.base, "/")) {
		fail(403, "无效请求来源")
		return
	}
	if s.mobileIdentity() == "" {
		fail(401, "请先登录电脑版云账号")
		return
	}
	action := strings.TrimPrefix(r.URL.Path, "/api/desktop/mobile/")
	if action == "status" && r.Method == "GET" {
		s.mu.RLock()
		m := s.mobile
		s.mu.RUnlock()
		if m != nil && !m.bridge.Active() {
			s.stopMobile()
			m = nil
		}
		if m == nil {
			_ = json.NewEncoder(w).Encode(map[string]any{"enabled": false})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"enabled": true, "mode": m.mode, "address": m.bridge.Origin, "code": m.bridge.Code, "connections": m.bridge.Count(), "port": m.port})
		return
	}
	if r.Method != "POST" {
		fail(405, "不支持的请求方法")
		return
	}
	if action == "stop" {
		s.stopMobile()
		_ = json.NewEncoder(w).Encode(map[string]bool{"enabled": false})
		return
	}
	if action == "usb" {
		s.mu.RLock()
		m := s.mobile
		s.mu.RUnlock()
		if m == nil || m.mode != "usb" {
			fail(400, "请先开启 USB 连接")
			return
		}
		adb, err := mobileADB()
		if err != nil {
			fail(400, "未找到 adb，请安装 Android Platform Tools 并加入 PATH，再重启电脑版")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		command := exec.CommandContext(ctx, adb, "reverse", "tcp:"+strconv.Itoa(m.port), "tcp:"+strconv.Itoa(m.port))
		configureProcGroup(command)
		if err = command.Run(); err != nil {
			fail(400, "USB 转发失败：请连接一台手机、开启 USB 调试并在手机确认授权")
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
		return
	}
	if action != "start" {
		fail(404, "未找到连接操作")
		return
	}
	var options mobileOptions
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 8192)).Decode(&options) != nil {
		fail(400, "连接配置无效")
		return
	}
	if options.Port == 0 {
		options.Port = 8765
	}
	if options.Port < 1024 || options.Port > 65535 {
		fail(400, "端口范围为 1024–65535")
		return
	}
	host := "127.0.0.1"
	origin := "http://127.0.0.1:" + strconv.Itoa(options.Port)
	var certificate tls.Certificate
	if options.Mode == "https" {
		if !strings.HasPrefix(options.Origin, "https://") {
			fail(400, "局域网连接必须使用 HTTPS 地址")
			return
		}
		host = "0.0.0.0"
		origin = strings.TrimRight(options.Origin, "/")
		var err error
		certificate, err = tls.LoadX509KeyPair(options.Certificate, options.Key)
		if err != nil {
			fail(400, "无法读取 HTTPS 证书或私钥，请检查文件路径")
			return
		}
	} else if options.Mode != "usb" {
		fail(400, "请选择 USB 或 HTTPS 连接")
		return
	}
	bridge, err := mobilebridge.New(strings.TrimRight(s.base, "/"), origin, s.mobileIdentity)
	if err != nil {
		fail(400, err.Error())
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.mobile != nil {
		fail(409, "请先关闭当前连接")
		return
	}
	listener, err := net.Listen("tcp4", net.JoinHostPort(host, strconv.Itoa(options.Port)))
	if err != nil {
		fail(400, "连接端口被占用，请选择其他端口")
		return
	}
	if options.Mode == "https" {
		listener = tls.NewListener(listener, &tls.Config{MinVersion: tls.VersionTLS12, Certificates: []tls.Certificate{certificate}})
	}
	server := &http.Server{Handler: bridge, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 32768}
	ctx, cancel := context.WithCancel(context.Background())
	m := &mobileConnection{bridge, server, cancel, options.Mode, options.Port}
	s.mobile = m
	go bridge.Watch(ctx)
	go func() { _ = server.Serve(listener); cancel() }()
	_ = json.NewEncoder(w).Encode(map[string]any{"enabled": true, "mode": m.mode, "address": origin, "code": bridge.Code, "connections": 0, "port": m.port})
}
