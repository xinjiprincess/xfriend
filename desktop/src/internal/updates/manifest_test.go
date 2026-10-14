package updates

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestReleaseTrustBoundary(t *testing.T) {
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	policy := Policy{Keys: map[string]ed25519.PublicKey{"release": public}, Channel: "stable", Platform: "windows-amd64", MinimumSequence: 10, UpdaterVersion: 1, Now: now}
	base := Release{Format: 1, Application: "xfriend", Platform: "windows-amd64", Channel: "stable", Version: "1.5.0", Sequence: 11, Expires: now.Add(time.Hour), MinimumUpdater: 1, Installer: Artifact{ID: "installer", URL: "https://example.com/setup.exe", Size: 123, SHA256: strings.Repeat("a", 64)}}
	sign := func(r Release) []byte {
		payload, _ := json.Marshal(r)
		data, _ := json.Marshal(Envelope{KeyID: "release", Payload: base64.StdEncoding.EncodeToString(payload), Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(private, payload))})
		return data
	}
	if got, err := Verify(sign(base), policy); err != nil || got.Version != base.Version {
		t.Fatalf("valid release: %v", err)
	}
	policy.Flavor = "standard"
	if _, err := Verify(sign(base), policy); err == nil {
		t.Fatal("accepted legacy edition for edition-bound client")
	}
	base.Flavor = "dsh-slim"
	if _, err := Verify(sign(base), policy); err == nil {
		t.Fatal("accepted another edition")
	}
	base.Flavor = "standard"
	if _, err := Verify(sign(base), policy); err != nil {
		t.Fatal(err)
	}
	policy.Flavor = ""
	for name, mutate := range map[string]func(*Release){
		"expired":             func(r *Release) { r.Expires = now },
		"rollback":            func(r *Release) { r.Sequence = 9 },
		"wrong application":   func(r *Release) { r.Application = "other" },
		"wrong platform":      func(r *Release) { r.Platform = "linux-amd64" },
		"wrong channel":       func(r *Release) { r.Channel = "preview" },
		"new updater":         func(r *Release) { r.MinimumUpdater = 2 },
		"insecure URL":        func(r *Release) { r.Installer.URL = "http://example.com/setup.exe" },
		"bad digest":          func(r *Release) { r.Installer.SHA256 = "abc" },
		"duplicate component": func(r *Release) { r.Components = []Artifact{r.Installer} },
		"unsafe version":      func(r *Release) { r.Version = "../1.5.0" },
	} {
		t.Run(name, func(t *testing.T) {
			r := base
			mutate(&r)
			if _, err := Verify(sign(r), policy); err == nil {
				t.Fatal("accepted invalid signed release")
			}
		})
	}
	t.Run("tampering", func(t *testing.T) {
		var envelope Envelope
		_ = json.Unmarshal(sign(base), &envelope)
		envelope.Payload = base64.StdEncoding.EncodeToString([]byte(`{}`))
		data, _ := json.Marshal(envelope)
		if _, err := Verify(data, policy); err == nil {
			t.Fatal("accepted tampered payload")
		}
	})
	t.Run("unknown key", func(t *testing.T) {
		policy.Keys = nil
		if _, err := Verify(sign(base), policy); err == nil {
			t.Fatal("accepted unknown publisher")
		}
	})
}
