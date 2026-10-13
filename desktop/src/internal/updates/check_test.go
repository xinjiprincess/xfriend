package updates

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestCheckSignedReleaseOverTLS(t *testing.T) {
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/stable.json" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		_, _ = w.Write([]byte("pending"))
	}))
	defer server.Close()
	release := Release{Format: 1, Application: "xfriend", Platform: "windows-amd64", Channel: "stable", Version: "1.5.0", Sequence: 17, Expires: now.Add(time.Hour), MinimumUpdater: 1, Installer: Artifact{ID: "installer", URL: server.URL + "/setup.exe", SHA256: strings.Repeat("a", 64), Size: 5}}
	payload, _ := json.Marshal(release)
	envelope, _ := json.Marshal(Envelope{KeyID: "release", Payload: base64.StdEncoding.EncodeToString(payload), Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(private, payload))})
	server.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(envelope) })
	policy := Policy{Keys: map[string]ed25519.PublicKey{"release": public}, Channel: "stable", Platform: "windows-amd64", MinimumSequence: 17, UpdaterVersion: 1, Now: now}
	got, err := Check(context.Background(), server.Client(), server.URL+"/stable.json", policy)
	if err != nil || got.Sequence != 17 {
		t.Fatalf("valid release: %v", err)
	}
	if _, err := Check(context.Background(), server.Client(), "http://example.com/stable.json", policy); err == nil {
		t.Fatal("accepted plain HTTP")
	}
	policy.MinimumSequence = 18
	if _, err := Check(context.Background(), server.Client(), server.URL+"/stable.json", policy); err == nil {
		t.Fatal("accepted rollback")
	}
}

func TestCheckRejectsLargeMetadataAndUnsafeRedirect(t *testing.T) {
	large := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = fmt.Fprint(w, strings.Repeat("x", MaxManifestBytes+1))
	}))
	defer large.Close()
	if _, err := Check(context.Background(), large.Client(), large.URL, Policy{}); err == nil {
		t.Fatal("accepted oversized metadata")
	}
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("metadata")) }))
	defer target.Close()
	redirect := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, http.StatusFound) }))
	defer redirect.Close()
	if _, err := Check(context.Background(), redirect.Client(), redirect.URL, Policy{}); err == nil {
		t.Fatal("followed HTTPS to HTTP redirect")
	}
}
