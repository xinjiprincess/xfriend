//go:build darwin

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os/exec"
	"regexp"
	"time"
)

func cloudMachineCode() (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	data, err := exec.CommandContext(ctx, "/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice").Output()
	if err != nil {
		return "", err
	}
	match := regexp.MustCompile(`"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{36})"`).FindSubmatch(data)
	if len(match) != 2 {
		return "", fmt.Errorf("无法读取 Mac 机器标识")
	}
	sum := sha256.Sum256(append([]byte("singu-xfriend:mac:v1:"), match[1]...))
	return hex.EncodeToString(sum[:]), nil
}
