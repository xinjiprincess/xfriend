// Package mobilebridge implements the Android v1 paired desktop protocol.
package mobilebridge

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"
	"time"
)

type session struct {
	expires time.Time
	ctx     context.Context
	cancel  context.CancelFunc
}
type bucket struct {
	until time.Time
	count int
}
type Bridge struct {
	mu       sync.Mutex
	Code     string
	Origin   string
	owner    string
	identity func() string
	sessions map[string]session
	failures map[string]bucket
	proxy    *httputil.ReverseProxy
	closed   bool
}

func randomToken() string {
	var value [24]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(value[:])
}

func New(target, origin string, identity func() string) (*Bridge, error) {
	source, err := url.Parse(target)
	if err != nil || source.Scheme != "http" || source.Hostname() != "127.0.0.1" || source.Port() == "" || source.User != nil || source.RawQuery != "" || (source.Path != "" && source.Path != "/") {
		return nil, errors.New("invalid desktop target")
	}
	public, err := url.Parse(origin)
	if err != nil || public.Host == "" || public.User != nil || public.RawQuery != "" || public.Fragment != "" || public.Path != "" || (public.Scheme != "https" && !(public.Scheme == "http" && public.Hostname() == "127.0.0.1")) {
		return nil, errors.New("HTTPS required outside USB loopback")
	}
	owner := identity()
	if owner == "" {
		return nil, errors.New("请先登录电脑云账号")
	}
	b := &Bridge{Code: randomToken()[:16], Origin: origin, owner: owner, identity: identity, sessions: map[string]session{}, failures: map[string]bucket{}}
	b.proxy = &httputil.ReverseProxy{FlushInterval: -1, Rewrite: func(p *httputil.ProxyRequest) {
		p.SetURL(source)
		p.Out.Host = source.Host
		for _, h := range []string{"Cookie", "Authorization", "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP", "X-Xfriend-Cloud-Bridge"} {
			p.Out.Header.Del(h)
		}
		p.Out.Header.Set("Origin", strings.TrimRight(target, "/"))
		p.Out.Header.Set("Referer", strings.TrimRight(target, "/")+"/")
		p.Out.Header.Set("Sec-Fetch-Site", "same-origin")
		p.Out.Header.Set("X-Xfriend-Mobile-Scope", strings.SplitN(b.owner, ":", 2)[0])
		q := p.Out.URL.Query()
		q.Del("token")
		q.Del("access_token")
		p.Out.URL.RawQuery = q.Encode()
	}, ModifyResponse: func(r *http.Response) error {
		r.Header.Del("Set-Cookie")
		r.Header.Set("Cache-Control", "no-store")
		if location := r.Header.Get("Location"); strings.HasPrefix(location, target+"/") {
			r.Header.Set("Location", strings.TrimPrefix(location, target))
		}
		return nil
	}, ErrorHandler: func(w http.ResponseWriter, r *http.Request, e error) {
		http.Error(w, "电脑服务暂时不可用", 503)
	}}
	return b, nil
}

func (b *Bridge) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.closed = true
	for k, s := range b.sessions {
		s.cancel()
		delete(b.sessions, k)
	}
}

func (b *Bridge) Active() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return !b.closed
}
func (b *Bridge) Count() int { b.mu.Lock(); defer b.mu.Unlock(); b.prune(); return len(b.sessions) }
func (b *Bridge) prune() {
	for k, s := range b.sessions {
		if !time.Now().Before(s.expires) {
			s.cancel()
			delete(b.sessions, k)
		}
	}
}

// Watch also cancels active WebSockets/SSE when the desktop changes identity.
func (b *Bridge) Watch(ctx context.Context) {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			b.Close()
			return
		case <-tick.C:
			if b.identity() != b.owner {
				b.Close()
				return
			}
			b.Count()
		}
	}
}
func (b *Bridge) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	public, _ := url.Parse(b.Origin)
	if r.Host != public.Host {
		http.Error(w, "invalid host", 403)
		return
	}
	same := r.Header.Get("Origin") == b.Origin
	b.mu.Lock()
	closed := b.closed
	b.mu.Unlock()
	valid := !closed && b.identity() == b.owner
	if !valid {
		b.Close()
	}
	if r.URL.Path == "/mobile/status" && r.Method == "GET" {
		state := "ready"
		if !valid {
			state = "login_required"
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"application": "xfriend", "protocol": 1, "state": state, "implementation": "native-desktop", "entry_path": "/chat", "pairing_required": true, "session_ttl_seconds": 28800, "transports": []string{"http", "websocket", "sse", "upload"}})
		return
	}
	if !valid {
		http.Error(w, "电脑账号已退出或切换，请重新开启连接", 401)
		return
	}
	if r.URL.Path == "/mobile/pair" {
		if r.Method != "POST" {
			w.WriteHeader(405)
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" && origin != "null" && !same {
			w.WriteHeader(403)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 2048)
		if r.ParseForm() != nil {
			w.WriteHeader(400)
			return
		}
		ip, _, _ := net.SplitHostPort(r.RemoteAddr)
		b.mu.Lock()
		defer b.mu.Unlock()
		now := time.Now()
		for k, v := range b.failures {
			if now.After(v.until) {
				delete(b.failures, k)
			}
		}
		attempt := b.failures[ip]
		if attempt.count >= 5 || len(b.failures) >= 1024 {
			w.WriteHeader(429)
			return
		}
		if subtle.ConstantTimeCompare([]byte(r.PostForm.Get("code")), []byte(b.Code)) != 1 {
			if attempt.count == 0 {
				attempt.until = now.Add(time.Minute)
			}
			attempt.count++
			b.failures[ip] = attempt
			w.WriteHeader(401)
			return
		}
		b.prune()
		if b.closed || len(b.sessions) >= 16 {
			w.WriteHeader(429)
			return
		}
		token := randomToken()
		expires := now.Add(8 * time.Hour)
		ctx, cancel := context.WithDeadline(context.Background(), expires)
		b.sessions[token] = session{expires, ctx, cancel}
		http.SetCookie(w, &http.Cookie{Name: "xfriend_mobile", Value: token, Path: "/", HttpOnly: true, Secure: public.Scheme == "https", SameSite: http.SameSiteStrictMode, MaxAge: 28800})
		http.Redirect(w, r, "/chat", http.StatusSeeOther)
		return
	}
	cookie, err := r.Cookie("xfriend_mobile")
	if err != nil {
		w.WriteHeader(401)
		return
	}
	b.mu.Lock()
	b.prune()
	s, ok := b.sessions[cookie.Value]
	b.mu.Unlock()
	if !ok {
		w.WriteHeader(401)
		return
	}
	if r.Header.Get("Sec-Fetch-Site") == "cross-site" || ((r.Method != "GET" && r.Method != "HEAD" || strings.EqualFold(r.Header.Get("Upgrade"), "websocket")) && !same) {
		w.WriteHeader(403)
		return
	}
	if r.URL.Path == "/mobile/logout" {
		if r.Method != "POST" {
			w.WriteHeader(405)
			return
		}
		b.mu.Lock()
		s.cancel()
		delete(b.sessions, cookie.Value)
		b.mu.Unlock()
		http.SetCookie(w, &http.Cookie{Name: "xfriend_mobile", Path: "/", MaxAge: -1, HttpOnly: true})
		w.WriteHeader(204)
		return
	}
	if (strings.HasPrefix(r.URL.Path, "/api/desktop/") && r.URL.Path != "/api/desktop/auth/status" && r.URL.Path != "/api/desktop/startup") || (strings.HasPrefix(r.URL.Path, "/api/auth/") && r.URL.Path != "/api/auth/me") || strings.HasPrefix(r.URL.Path, "/mobile/") {
		w.WriteHeader(403)
		return
	}
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	stop := context.AfterFunc(s.ctx, cancel)
	defer stop()
	r = r.Clone(ctx)
	r.Body = http.MaxBytesReader(w, r.Body, 100*1024*1024)
	b.proxy.ServeHTTP(w, r)
}
