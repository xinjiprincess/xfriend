//go:build windows

package main

import "os/exec"

func assistantSpeechCommand(path, wakeWord string) *exec.Cmd {
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path, "-WakeWord", wakeWord)
	hideConsole(cmd)
	return cmd
}
