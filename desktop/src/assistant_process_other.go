//go:build !windows

package main

import "os/exec"

// StartAssistantVoice rejects unsupported systems before reaching this helper.
func assistantSpeechCommand(path, wakeWord string) *exec.Cmd {
	return exec.Command("powershell", "-File", path, "-WakeWord", wakeWord)
}
