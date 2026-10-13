//go:build !windows && !darwin

package main

import "errors"

func cloudMachineCode() (string, error) {
	return "", errors.New("在线账号发行版当前仅支持 Windows")
}
