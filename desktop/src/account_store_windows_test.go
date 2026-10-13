//go:build windows

package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestProtectedAccountStoreUsesDPAPI(t *testing.T) {
	store := protectedAccountStore{path: filepath.Join(t.TempDir(), "account-session.bin")}
	clear := []byte(`{"refresh_token":"test-private-refresh"}`)
	for i := 0; i < 2; i++ {
		if err := store.save(clear); err != nil {
			t.Fatal(err)
		}
		encrypted, err := os.ReadFile(store.path)
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Contains(encrypted, []byte("test-private-refresh")) {
			t.Fatal("plaintext credential persisted")
		}
		restored, err := store.load()
		if err != nil || !bytes.Equal(restored, clear) {
			t.Fatalf("credential restoration failed: %v", err)
		}
	}
	if err := os.WriteFile(store.path, []byte("invalid encrypted record"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := store.load(); err == nil {
		t.Fatal("tampered record was accepted")
	}
}
