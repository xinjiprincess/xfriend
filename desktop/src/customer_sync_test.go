package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func syncDesktop(t *testing.T, handle func(http.ResponseWriter, *http.Request)) *desktopServer {
	t.Helper()
	public, private, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Now().Unix()
	lease := cloudLease{Schema: 2, Product: "singu-xfriend", UserID: "user", SpaceID: "space", DeviceID: "device", IssuedAt: now, NotBefore: now, ExpiresAt: now + 86400, OfflineUntil: now + 86400}
	data := accountJSON(lease)
	session := cloudSession{AccessToken: "native-access", RefreshToken: "native-refresh", User: cloudUser{UserID: "user"}, SpaceID: "space", SessionExpires: now + 86400, Lease: cloudSignedLease{Payload: base64.RawURLEncoding.EncodeToString(data), Signature: base64.RawURLEncoding.EncodeToString(ed25519.Sign(private, data))}}
	cloud := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v2/auth/refresh" {
			session.AccessToken = "renewed"
			_ = json.NewEncoder(w).Encode(session)
			return
		}
		handle(w, r)
	}))
	t.Cleanup(cloud.Close)
	auth, err := newCloudAuth(cloudProfile{CloudAccounts: true, ServerURL: cloud.URL, PublicKey: base64.RawURLEncoding.EncodeToString(public)}, &memoryAccountStore{}, "device")
	if err != nil {
		t.Fatal(err)
	}
	auth.session = &session
	return &desktopServer{cloud: auth, base: "http://127.0.0.1:18888"}
}

func syncRequest(s *desktopServer, method, path, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, "http://127.0.0.1:18888/api/desktop/sync/"+path, strings.NewReader(body))
	r.Header.Set("X-Xfriend-Sync-Scope", "user:space")
	r.Header.Set("Authorization", "Bearer local-jwt-must-not-leak")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	return w
}

func TestCustomerSyncRefreshAndNativeToken(t *testing.T) {
	calls := 0
	s := syncDesktop(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path != "/api/v2/sync/pull" || r.URL.Query().Get("space_id") != "space" || r.URL.Query().Has("token") {
			t.Error("unexpected wire request")
		}
		if calls == 1 {
			if r.Header.Get("Authorization") != "Bearer native-access" {
				t.Error("wrong first token")
			}
			w.WriteHeader(401)
			return
		}
		if r.Header.Get("Authorization") != "Bearer renewed" {
			t.Error("wrong refreshed token")
		}
		_, _ = w.Write([]byte(`{"changes":[],"cursor":0,"has_more":false}`))
	})
	w := syncRequest(s, "GET", "pull?space_id=space&cursor=0&token=ignored", "")
	if w.Code != 200 || calls != 2 || strings.Contains(w.Body.String(), "native-") {
		t.Fatalf("status=%d calls=%d response=%s", w.Code, calls, w.Body.String())
	}
}

func TestCustomerSyncBoundary(t *testing.T) {
	calls := 0
	s := syncDesktop(t, func(w http.ResponseWriter, r *http.Request) { calls++; w.WriteHeader(500) })
	for _, tc := range []struct {
		method, path, body string
		code               int
	}{
		{"GET", "auth/login", "", 404}, {"POST", "pull", "", 405},
		{"GET", "pull?space_id=other", "", 409},
		{"POST", "push", `{"space_id":"space","owner":"user","operations":[{}]}`, 400},
		{"POST", "push", `{"space_id":"other","operations":[{}]}`, 400},
	} {
		if w := syncRequest(s, tc.method, tc.path, tc.body); w.Code != tc.code {
			t.Errorf("%s: %d", tc.path, w.Code)
		}
	}
	r := httptest.NewRequest("GET", "http://127.0.0.1:18888/api/desktop/sync/pull?space_id=space", nil)
	r.Header.Set("Origin", "https://attacker.invalid")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 403 || calls != 0 {
		t.Fatal("unsafe request reached cloud")
	}
	w = httptest.NewRecorder()
	r.Header.Del("Origin")
	s.ServeHTTP(w, r)
	if w.Code != 409 {
		t.Fatal("missing pinned scope accepted")
	}
}

func TestCustomerSyncPushIdentityAndFailure(t *testing.T) {
	s := syncDesktop(t, func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["device_id"] != "device" || body["space_id"] != "space" {
			t.Error("identity not native")
		}
		w.WriteHeader(503)
		_, _ = w.Write([]byte(`{"message":"unavailable"}`))
	})
	w := syncRequest(s, "POST", "push", `{"space_id":"space","device_id":"spoofed","operations":[{"operation_id":"stable"}]}`)
	if w.Code != 503 {
		t.Fatal("cloud failure became success")
	}
}

func TestCustomerSyncRefreshOnlyOnce(t *testing.T) {
	calls := 0
	s := syncDesktop(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(401)
		_, _ = w.Write([]byte(`{}`))
	})
	if w := syncRequest(s, "GET", "pull?space_id=space", ""); w.Code != 401 || calls != 2 {
		t.Fatalf("status=%d calls=%d", w.Code, calls)
	}
}

func TestCustomerSyncDiscardsResponseAfterAccountChange(t *testing.T) {
	var s *desktopServer
	s = syncDesktop(t, func(w http.ResponseWriter, r *http.Request) {
		s.cloud.mu.Lock()
		s.cloud.epoch++
		s.cloud.mu.Unlock()
		_, _ = w.Write([]byte(`{"changes":[],"cursor":5,"has_more":false}`))
	})
	if w := syncRequest(s, "GET", "pull?space_id=space", ""); w.Code != 409 || strings.Contains(w.Body.String(), "changes") {
		t.Fatal("stale response exposed")
	}
}

func TestCustomerSyncPinsLocalJournalRequests(t *testing.T) {
	s := syncDesktop(t, func(w http.ResponseWriter, r *http.Request) { t.Fatal("must not contact cloud") })
	r := httptest.NewRequest("POST", "http://127.0.0.1:18888/api/customer-sync/entities?space_id=space", nil)
	r.Header.Set("X-Xfriend-Sync-Scope", "old-user:space")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 409 {
		t.Fatal("stale local edit accepted")
	}
	r.Header.Set("X-Xfriend-Sync-Scope", "user:space")
	if !s.customerSyncLocalScope(httptest.NewRecorder(), r) {
		t.Fatal("current account rejected")
	}
	key, _, _ := s.cloud.context()
	if r.Header.Get("X-Xfriend-Mobile-Scope") != key {
		t.Fatal("backend dispatch did not retain original account scope")
	}
}
