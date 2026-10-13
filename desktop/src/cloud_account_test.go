package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type memoryAccountStore struct{ data []byte }

func (s *memoryAccountStore) load() ([]byte, error) {
	if s.data == nil {
		return nil, io.EOF
	}
	return s.data, nil
}
func (s *memoryAccountStore) save(data []byte) error {
	s.data = append([]byte(nil), data...)
	return nil
}

func TestCloudRestorationLogoutAndOfflineExpiry(t *testing.T) {
	public, private, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Now().Unix()
	lease := cloudLease{Schema: 2, Product: "singu-xfriend", UserID: "user", SpaceID: "personal", DeviceID: "device", IssuedAt: now, NotBefore: now, ExpiresAt: now + 5*86400, OfflineUntil: now + 72*3600}
	payload := accountJSON(lease)
	session := cloudSession{RefreshToken: "private-refresh", AccessToken: "private-access", SessionExpires: now + 30*86400, ServerTime: now, User: cloudUser{UserID: "user", Identifier: "13812345678"}, SpaceID: "personal", Lease: cloudSignedLease{Payload: base64.RawURLEncoding.EncodeToString(payload), Signature: base64.RawURLEncoding.EncodeToString(ed25519.Sign(private, payload))}}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "login") {
			_ = json.NewEncoder(w).Encode(session)
			return
		}
		http.Error(w, `{"message":"offline"}`, 503)
	}))
	defer server.Close()
	profile := cloudProfile{CloudAccounts: true, ServerURL: server.URL, PublicKey: base64.RawURLEncoding.EncodeToString(public)}
	store := &memoryAccountStore{}
	corrupt, corruptErr := newCloudAuth(profile, &memoryAccountStore{data: []byte("invalid JSON")}, "device")
	if corruptErr != nil || corrupt.status().State != "reauth_required" {
		t.Fatal("unreadable account record must keep login available")
	}
	auth, err := newCloudAuth(profile, store, "device")
	if err != nil {
		t.Fatal(err)
	}
	if auth.status().State != "first_register" {
		t.Fatal("first use must register")
	}
	if err = auth.login("login", map[string]any{}, true, true); err != nil {
		t.Fatal(err)
	}
	restored, err := newCloudAuth(profile, store, "device")
	if err != nil {
		t.Fatal(err)
	}
	if restored.status().State != "authenticated" {
		t.Fatal("valid lease must restore immediately")
	}
	exposed := string(accountJSON(restored.status()))
	if strings.Contains(exposed, "private-") {
		t.Fatal("token exposed to page")
	}
	if err = restored.refresh(""); err == nil {
		t.Fatal("expected offline error")
	}
	if restored.status().State != "authenticated" {
		t.Fatal("network failure cleared valid session")
	}
	restored.clock = func() time.Time { return time.Unix(now+72*3600, 0) }
	if restored.status().State != "reauth_required" {
		t.Fatal("offline lease must expire")
	}
	if err = auth.logout(false); err != nil {
		t.Fatal(err)
	}
	loggedOut, _ := newCloudAuth(profile, store, "device")
	if loggedOut.status().State != "logged_out" || loggedOut.status().Identifier != "13812345678" {
		t.Fatal("logout must retain identifier only")
	}
	if strings.Contains(string(store.data), "private-") {
		t.Fatal("logout left credentials")
	}
	if err = loggedOut.logout(true); err != nil {
		t.Fatal(err)
	}
	if loggedOut.status().Identifier != "" {
		t.Fatal("forget must clear identifier")
	}
	if err = auth.login("login", map[string]any{}, true, false); err != nil {
		t.Fatal(err)
	}
	notKept, _ := newCloudAuth(profile, store, "device")
	if notKept.status().State == "authenticated" {
		t.Fatal("keep login disabled must not restore")
	}
}
