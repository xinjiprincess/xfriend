//go:build !windows && !darwin

package main

import "errors"

// Online account releases currently target Windows. Never silently persist a
// refresh credential in plaintext on an unsupported operating system.
func protectAccount([]byte) ([]byte, error) {
	return nil, errors.New("此平台尚未配置安全账号存储")
}
func unprotectAccount([]byte) ([]byte, error) {
	return nil, errors.New("此平台尚未配置安全账号存储")
}
