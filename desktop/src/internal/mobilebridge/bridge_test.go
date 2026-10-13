package mobilebridge

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func TestPairProxyAndRevoke(t *testing.T) {
	var identity atomic.Value
	identity.Store("owner:0")
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" || r.Header.Get("Forwarded") != "" {
			t.Error("browser credentials forwarded")
		}
		if r.URL.Path == "/socket" {
			c, err := websocket.Accept(w, r, &websocket.AcceptOptions{InsecureSkipVerify: true})
			if err != nil {
				return
			}
			defer c.CloseNow()
			kind, data, err := c.Read(r.Context())
			if err == nil {
				_ = c.Write(r.Context(), kind, data)
			}
			return
		}
		if r.URL.Path == "/stream" {
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = io.WriteString(w, "data: ready\n\n")
			w.(http.Flusher).Flush()
			<-r.Context().Done()
			return
		}
		w.Header().Set("Set-Cookie", "secret=do-not-forward")
		_, _ = io.Copy(w, r.Body)
	}))
	defer upstream.Close()
	server := httptest.NewUnstartedServer(nil)
	origin := "http://" + server.Listener.Addr().String()
	bridge, err := New(upstream.URL, origin, func() string { return identity.Load().(string) })
	if err != nil {
		t.Fatal(err)
	}
	server.Config.Handler = bridge
	server.Start()
	defer server.Close()
	defer bridge.Close()
	client := &http.Client{Timeout: 3 * time.Second, CheckRedirect: func(r *http.Request, v []*http.Request) error { return http.ErrUseLastResponse }}
	request := func(method, path, body string, cookie *http.Cookie, requestOrigin string) *http.Response {
		r, _ := http.NewRequest(method, origin+path, strings.NewReader(body))
		r.Header.Set("Origin", requestOrigin)
		r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		r.Header.Set("Authorization", "Bearer phone-supplied")
		r.Header.Set("Forwarded", "host=untrusted.example")
		if cookie != nil {
			r.AddCookie(cookie)
		}
		resp, e := client.Do(r)
		if e != nil {
			t.Fatal(e)
		}
		return resp
	}
	response := request("GET", "/mobile/status", "", nil, "")
	data, _ := io.ReadAll(response.Body)
	response.Body.Close()
	if strings.Contains(string(data), bridge.Code) || strings.Contains(string(data), "owner") || !strings.Contains(string(data), `"protocol":1`) {
		t.Fatal(string(data))
	}
	response = request("POST", "/mobile/pair", "code="+bridge.Code, nil, "null")
	response.Body.Close()
	if response.StatusCode != 303 {
		t.Fatal(response.StatusCode)
	}
	cookie := response.Cookies()[0]
	if !cookie.HttpOnly || cookie.SameSite != http.SameSiteStrictMode {
		t.Fatal(cookie)
	}
	for _, path := range []string{"/api/desktop/auth/logout", "/api/desktop/mobile/status", "/api/auth/login"} {
		response = request("POST", path, "", cookie, origin)
		response.Body.Close()
		if response.StatusCode != 403 {
			t.Fatal(path, response.StatusCode)
		}
	}
	response = request("POST", "/upload", "phone-file", cookie, "https://evil.example")
	response.Body.Close()
	if response.StatusCode != 403 {
		t.Fatal("cross-origin write accepted")
	}
	response = request("POST", "/upload", "phone-file", cookie, origin)
	data, _ = io.ReadAll(response.Body)
	response.Body.Close()
	if string(data) != "phone-file" || response.Header.Get("Set-Cookie") != "" {
		t.Fatal("upload or cookie filtering failed")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	c, _, err := websocket.Dial(ctx, strings.Replace(origin, "http:", "ws:", 1)+"/socket", &websocket.DialOptions{HTTPHeader: http.Header{"Origin": {origin}, "Cookie": {cookie.String()}}})
	if err != nil {
		t.Fatal(err)
	}
	if err = c.Write(ctx, websocket.MessageText, []byte("hello")); err != nil {
		t.Fatal(err)
	}
	_, data, err = c.Read(ctx)
	if err != nil || string(data) != "hello" {
		t.Fatal("websocket", err)
	}
	c.CloseNow()
	response = request("GET", "/stream", "", cookie, origin)
	stream := make([]byte, len("data: ready\n\n"))
	if _, err = io.ReadFull(response.Body, stream); err != nil {
		t.Fatal(err)
	}
	revoked := request("POST", "/mobile/logout", "", cookie, origin)
	revoked.Body.Close()
	if revoked.StatusCode != 204 {
		t.Fatal(revoked.StatusCode)
	}
	_, err = response.Body.Read(make([]byte, 1))
	response.Body.Close()
	if err == nil {
		t.Fatal("stream not cancelled")
	}
	response = request("GET", "/chat", "", cookie, origin)
	response.Body.Close()
	if response.StatusCode != 401 {
		t.Fatal("logout did not revoke")
	}
	response = request("POST", "/mobile/pair", "code="+bridge.Code, nil, origin)
	response.Body.Close()
	cookie = response.Cookies()[0]
	identity.Store("other:1")
	response = request("GET", "/chat", "", cookie, origin)
	response.Body.Close()
	if response.StatusCode != 401 {
		t.Fatal("identity switch did not revoke")
	}
}

func TestPairRateLimitAndAddressValidation(t *testing.T) {
	for _, origin := range []string{"http://192.168.1.1:8765", "https://user:pass@host", "https://host/path"} {
		if _, err := New("http://127.0.0.1:1234", origin, func() string { return "owner" }); err == nil {
			t.Fatal(origin)
		}
	}
	b, err := New("http://127.0.0.1:1234", "http://127.0.0.1:8765", func() string { return "owner" })
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	for i := 0; i < 6; i++ {
		r := httptest.NewRequest("POST", b.Origin+"/mobile/pair", strings.NewReader(url.Values{"code": {"wrong"}}.Encode()))
		r.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		w := httptest.NewRecorder()
		b.ServeHTTP(w, r)
		expected := 401
		if i == 5 {
			expected = 429
		}
		if w.Code != expected {
			t.Fatal(i, w.Code)
		}
	}
}
