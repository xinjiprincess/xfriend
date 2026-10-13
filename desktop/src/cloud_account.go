package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type cloudProfile struct {
	CloudAccounts bool   `json:"cloud_accounts"`
	ServerURL     string `json:"server_url"`
	PublicKey     string `json:"public_key"`
}

type cloudSignedLease struct {
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}
type cloudLease struct {
	Schema       int    `json:"schema_version"`
	Product      string `json:"product"`
	UserID       string `json:"user_id"`
	SpaceID      string `json:"space_id"`
	DeviceID     string `json:"device_id"`
	IssuedAt     int64  `json:"issued_at"`
	NotBefore    int64  `json:"not_before"`
	ExpiresAt    int64  `json:"expires_at"`
	OfflineUntil int64  `json:"offline_until"`
}
type cloudUser struct {
	UserID     string `json:"user_id"`
	Name       string `json:"name"`
	Identifier string `json:"identifier"`
}
type cloudSpace struct {
	SpaceID string `json:"space_id"`
	Name    string `json:"name"`
	Kind    string `json:"kind"`
	Role    string `json:"role"`
}
type cloudSession struct {
	AccessToken    string           `json:"access_token"`
	RefreshToken   string           `json:"refresh_token"`
	AccessExpires  int64            `json:"access_expires_at"`
	SessionExpires int64            `json:"session_expires_at"`
	ServerTime     int64            `json:"server_time"`
	User           cloudUser        `json:"user"`
	Spaces         []cloudSpace     `json:"spaces"`
	SpaceID        string           `json:"space_id"`
	Lease          cloudSignedLease `json:"lease"`
}
type cloudSaved struct {
	Seen            bool          `json:"seen"`
	RememberAccount bool          `json:"remember_account"`
	KeepLogin       bool          `json:"keep_login"`
	Identifier      string        `json:"identifier"`
	LoggedOut       bool          `json:"logged_out"`
	LastSeen        int64         `json:"last_seen"`
	Session         *cloudSession `json:"session,omitempty"`
}
type cloudStatus struct {
	Enabled         bool         `json:"enabled"`
	State           string       `json:"state"`
	RememberAccount bool         `json:"remember_account"`
	KeepLogin       bool         `json:"keep_login"`
	Identifier      string       `json:"identifier"`
	User            *cloudUser   `json:"user,omitempty"`
	Spaces          []cloudSpace `json:"spaces"`
	SpaceID         string       `json:"space_id,omitempty"`
	OfflineUntil    int64        `json:"offline_until,omitempty"`
	Message         string       `json:"message,omitempty"`
}
type cloudAuth struct {
	mu        sync.Mutex
	operation sync.Mutex
	epoch     uint64
	profile   cloudProfile
	store     accountStore
	client    *http.Client
	device    string
	saved     cloudSaved
	session   *cloudSession
	state     string
	message   string
	changes   chan struct{}
	clock     func() time.Time
}

func newCloudAuth(profile cloudProfile, store accountStore, device string) (*cloudAuth, error) {
	u, err := url.Parse(profile.ServerURL)
	key, keyErr := base64.RawURLEncoding.DecodeString(profile.PublicKey)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" ||
		(u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost"))) || keyErr != nil || len(key) != ed25519.PublicKeySize {
		return nil, errors.New("云端账号发布配置无效，请重新安装")
	}
	a := &cloudAuth{profile: profile, store: store, device: device, state: "first_register",
		client:  &http.Client{Timeout: 8 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
		changes: make(chan struct{}, 1), clock: time.Now,
		saved: cloudSaved{RememberAccount: true, KeepLogin: true}}
	data, loadErr := store.load()
	if loadErr == nil {
		if json.Unmarshal(data, &a.saved) != nil {
			a.saved = cloudSaved{RememberAccount: true, KeepLogin: true}
			a.state = "reauth_required"
			a.message = "账号记录无法读取，请重新登录或忘记此账号。"
			return a, nil
		}
		if a.saved.Seen {
			a.state = "reauth_required"
		}
		if a.saved.LoggedOut {
			a.state = "logged_out"
		}
		if a.saved.KeepLogin && !a.saved.LoggedOut && a.saved.Session != nil {
			a.session = a.saved.Session
			if _, err := a.validLocked(); err == nil {
				a.state = "authenticated"
			} else {
				a.state = "restoring"
			}
		}
	} else if !errors.Is(loadErr, io.EOF) && !os.IsNotExist(loadErr) {
		// Keep the registration/login UI available, but never trust unreadable credentials.
		a.state = "reauth_required"
		a.message = "保存的登录状态无法读取，请重新登录。"
	}
	return a, nil
}

func (a *cloudAuth) notify() {
	select {
	case a.changes <- struct{}{}:
	default:
	}
}

func (a *cloudAuth) validLocked() (cloudLease, error) {
	var lease cloudLease
	if a.session == nil {
		return lease, errors.New("请先登录")
	}
	data, err := base64.RawURLEncoding.DecodeString(a.session.Lease.Payload)
	sig, sigErr := base64.RawURLEncoding.DecodeString(a.session.Lease.Signature)
	key, _ := base64.RawURLEncoding.DecodeString(a.profile.PublicKey)
	if err != nil || sigErr != nil || !ed25519.Verify(key, data, sig) || json.Unmarshal(data, &lease) != nil {
		return lease, errors.New("授权签名无效，请联网登录")
	}
	now := a.clock().Unix()
	if lease.Schema != 2 || lease.Product != "singu-xfriend" || lease.DeviceID != a.device ||
		lease.UserID != a.session.User.UserID || lease.SpaceID != a.session.SpaceID || lease.UserID == "" || lease.SpaceID == "" ||
		lease.NotBefore > now || lease.IssuedAt > now+300 || now+300 < a.saved.LastSeen ||
		lease.OfflineUntil > lease.IssuedAt+72*3600 || lease.OfflineUntil > lease.ExpiresAt ||
		now >= lease.OfflineUntil || now >= a.session.SessionExpires {
		return lease, errors.New("登录或离线授权已到期，请联网验证")
	}
	return lease, nil
}

func (a *cloudAuth) status() cloudStatus {
	a.mu.Lock()
	defer a.mu.Unlock()
	s := cloudStatus{Enabled: true, State: a.state, RememberAccount: a.saved.RememberAccount, KeepLogin: a.saved.KeepLogin,
		Identifier: a.saved.Identifier, Message: a.message, Spaces: []cloudSpace{}}
	if _, err := a.validLocked(); err == nil && a.state != "logged_out" {
		lease, _ := a.validLocked()
		s.State, s.User, s.SpaceID, s.OfflineUntil = "authenticated", &a.session.User, a.session.SpaceID, lease.OfflineUntil
		s.Spaces = append(s.Spaces, a.session.Spaces...)
	} else if s.State == "authenticated" {
		s.State = "reauth_required"
	}
	return s
}

func (a *cloudAuth) persistLocked() error {
	a.saved.Session = nil
	if a.saved.KeepLogin && !a.saved.LoggedOut {
		a.saved.Session = a.session
	}
	return a.store.save(accountJSON(a.saved))
}

func (a *cloudAuth) request(endpoint string, body any, result any) (int, error) {
	request, err := http.NewRequest(http.MethodPost, strings.TrimRight(a.profile.ServerURL, "/")+"/api/v2/auth/"+endpoint, bytes.NewReader(accountJSON(body)))
	if err != nil {
		return 0, err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := a.client.Do(request)
	if err != nil {
		return 0, errors.New("账号服务器暂时无法连接，请稍后重试")
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 65537))
	if err != nil || len(data) > 65536 {
		return response.StatusCode, errors.New("账号服务响应异常")
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		var failure struct {
			Message string `json:"message"`
		}
		_ = json.Unmarshal(data, &failure)
		if failure.Message == "" {
			failure.Message = "账号服务暂时不可用，请稍后重试"
		}
		return response.StatusCode, errors.New(failure.Message)
	}
	if result != nil && response.StatusCode != http.StatusNoContent && json.Unmarshal(data, result) != nil {
		return response.StatusCode, errors.New("账号服务响应格式错误")
	}
	return response.StatusCode, nil
}

func (a *cloudAuth) login(endpoint string, input map[string]any, remember, keep bool) error {
	a.operation.Lock()
	defer a.operation.Unlock()
	a.mu.Lock()
	epoch := a.epoch
	a.mu.Unlock()
	input["device_id"] = a.device
	var next cloudSession
	if _, err := a.request(endpoint, input, &next); err != nil {
		return err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if epoch != a.epoch {
		return errors.New("账号操作已取消")
	}
	old := a.session
	a.session = &next
	if _, err := a.validLocked(); err != nil {
		a.session = old
		return err
	}
	a.saved.Seen, a.saved.LoggedOut, a.saved.RememberAccount, a.saved.KeepLogin = true, false, remember, keep
	a.saved.Identifier = ""
	if remember {
		a.saved.Identifier = next.User.Identifier
	}
	a.saved.LastSeen = max(a.saved.LastSeen, next.ServerTime)
	a.state, a.message = "authenticated", ""
	if err := a.persistLocked(); err != nil {
		a.session = nil
		a.state = "reauth_required"
		return errors.New("无法安全保存账号状态，请检查用户目录权限")
	}
	a.notify()
	return nil
}

func (a *cloudAuth) refresh(space string) error {
	a.operation.Lock()
	defer a.operation.Unlock()
	a.mu.Lock()
	if a.session == nil {
		a.mu.Unlock()
		return nil
	}
	epoch := a.epoch
	oldContext := a.session.User.UserID + ":" + a.session.SpaceID
	body := map[string]any{"refresh_token": a.session.RefreshToken, "device_id": a.device}
	if space != "" {
		body["space_id"] = space
	}
	a.mu.Unlock()
	var next cloudSession
	status, err := a.request("refresh", body, &next)
	a.mu.Lock()
	defer a.mu.Unlock()
	if epoch != a.epoch {
		return errors.New("账号操作已取消")
	}
	if err != nil {
		a.message = err.Error()
		if status == 401 || (status == 403 && space == "") {
			a.session, a.saved.Session, a.state = nil, nil, "reauth_required"
			_ = a.persistLocked()
			a.notify()
		} else if a.state == "restoring" {
			a.state = "reauth_required"
		}
		return err
	}
	old := a.session
	a.session = &next
	if _, err = a.validLocked(); err != nil {
		a.session, a.saved.Session, a.state, a.message = nil, nil, "reauth_required", err.Error()
		_ = a.persistLocked()
		a.notify()
		return err
	}
	if next.User.UserID != old.User.UserID || (space != "" && next.SpaceID != space) {
		a.session = nil
		a.state = "reauth_required"
		_ = a.persistLocked()
		a.notify()
		return errors.New("账号服务返回了不匹配的用户或空间")
	}
	a.saved.LastSeen = max(a.saved.LastSeen, next.ServerTime)
	a.state, a.message = "authenticated", ""
	if err = a.persistLocked(); err != nil {
		a.session = nil
		a.state = "reauth_required"
		a.notify()
		return errors.New("登录状态保存失败，请重新登录")
	}
	if oldContext != next.User.UserID+":"+next.SpaceID {
		a.epoch++
	}
	a.notify()
	return nil
}

func (a *cloudAuth) run(stop <-chan struct{}) {
	_ = a.refresh("")
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-stop:
			return
		case <-ticker.C:
			_ = a.refresh("")
		}
	}
}

func (a *cloudAuth) context() (string, cloudSignedLease, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if _, err := a.validLocked(); err != nil {
		return "", cloudSignedLease{}, false
	}
	sum := sha256.Sum256([]byte(a.profile.ServerURL + "\x00" + a.session.User.UserID + "\x00" + a.session.SpaceID))
	return hex.EncodeToString(sum[:]), a.session.Lease, true
}

func (a *cloudAuth) logout(forget bool) error {
	a.mu.Lock()
	a.epoch++
	var token string
	if a.session != nil {
		token = a.session.RefreshToken
	}
	a.session, a.saved.Session, a.saved.LoggedOut, a.state = nil, nil, true, "logged_out"
	if forget {
		a.saved.Identifier = ""
		a.saved.RememberAccount = false
	}
	err := a.persistLocked()
	a.notify()
	a.mu.Unlock()
	if token != "" {
		go func() { _, _ = a.request("logout", map[string]string{"refresh_token": token}, nil) }()
	}
	return err
}

func (s *desktopServer) cloudHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	if r.Header.Get("Sec-Fetch-Site") == "cross-site" || (r.Header.Get("Origin") != "" && r.Header.Get("Origin") != strings.TrimRight(s.base, "/")) {
		http.Error(w, "invalid desktop origin", http.StatusForbidden)
		return
	}
	a := s.cloud
	if a == nil {
		_ = json.NewEncoder(w).Encode(map[string]bool{"enabled": false})
		return
	}
	endpoint := strings.TrimPrefix(r.URL.Path, "/api/desktop/auth/")
	if endpoint == "status" && r.Method == http.MethodGet {
		_ = json.NewEncoder(w).Encode(a.status())
		return
	}
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	var input map[string]any
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384)).Decode(&input) != nil {
		w.WriteHeader(400)
		return
	}
	var err error
	switch endpoint {
	case "login", "register":
		s.stopMobile()
		remember, _ := input["remember_account"].(bool)
		keep, _ := input["keep_login"].(bool)
		delete(input, "remember_account")
		delete(input, "keep_login")
		err = a.login(endpoint, input, remember, keep)
	case "refresh", "switch-space":
		space, _ := input["space_id"].(string)
		if space != "" {
			s.stopMobile()
		}
		err = a.refresh(space)
	case "logout", "forget":
		s.stopMobile()
		err = a.logout(endpoint == "forget")
	case "challenges", "password-reset":
		var result any
		_, err = a.request(endpoint, input, &result)
		if endpoint == "password-reset" && err == nil {
			err = a.logout(false)
		}
		if err == nil {
			_ = json.NewEncoder(w).Encode(result)
			return
		}
	default:
		w.WriteHeader(404)
		return
	}
	if err != nil {
		w.WriteHeader(400)
		_ = json.NewEncoder(w).Encode(map[string]string{"message": err.Error()})
		return
	}
	_ = json.NewEncoder(w).Encode(a.status())
}

func cloudBridgeSecret() string {
	data := make([]byte, 32)
	_, _ = rand.Read(data)
	return hex.EncodeToString(data)
}
func cloudHome(key string) string { return filepath.Join(octopHome(), "accounts", key) }
