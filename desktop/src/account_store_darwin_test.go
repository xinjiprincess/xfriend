//go:build darwin

package main

import (
	"bytes"
	"os"
	"testing"
)

func TestMacAccountKeychainRoundTrip(t *testing.T) {
	if os.Getenv("XFRIEND_TEST_KEYCHAIN") != "1" {
		t.Skip("requires an isolated macOS CI keychain")
	}
	original := []byte("test-refresh-credential")
	encrypted, err := protectAccount(original)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(encrypted, original) {
		t.Fatal("plaintext credential persisted")
	}
	result, err := unprotectAccount(encrypted)
	if err != nil || !bytes.Equal(result, original) {
		t.Fatalf("round trip: %v", err)
	}
	encrypted[len(encrypted)-1] ^= 1
	if _, err = unprotectAccount(encrypted); err == nil {
		t.Fatal("modified credential accepted")
	}
	code, err := cloudMachineCode()
	if err != nil || len(code) != 64 {
		t.Fatalf("machine identity: %v", err)
	}
}
