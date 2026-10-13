//go:build windows

package main

import (
	"crypto/sha256"
	"encoding/hex"
	"golang.org/x/sys/windows/registry"
	"strings"
)

func cloudMachineCode() (string, error) {
	key, err := registry.OpenKey(registry.LOCAL_MACHINE, `SOFTWARE\Microsoft\Cryptography`, registry.READ|registry.WOW64_64KEY)
	if err != nil {
		return "", err
	}
	defer key.Close()
	identity, _, err := key.GetStringValue("MachineGuid")
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256([]byte("singu-xfriend:v1:" + strings.TrimSpace(identity)))
	return hex.EncodeToString(sum[:]), nil
}
