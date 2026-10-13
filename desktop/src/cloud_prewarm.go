package main

import (
	"encoding/json"
	"io"
	"os/exec"
	"path/filepath"
)

// The waiting child imports shared libraries but never opens an account database.
// Activation travels through an inherited pipe, not an unauthenticated HTTP API.
type cloudWarmup struct {
	cmd   *exec.Cmd
	input io.WriteCloser
	done  chan struct{}
}

func startCloudWarmup(root string) (*cloudWarmup, error) {
	cmd := exec.Command(pythonExe(root), filepath.Join(root, "launch.py"), "--prewarm")
	cmd.Dir = root
	mustEnv(cmd, map[string]string{
		"PYTHONNOUSERSITE": "1", "PYTHONPATH": "", "PYTHONUTF8": "1",
		"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
	})
	configureProcGroup(cmd)
	cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	input, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		_ = input.Close()
		return nil, err
	}
	warm := &cloudWarmup{cmd: cmd, input: input, done: make(chan struct{})}
	go func() { _ = cmd.Wait(); close(warm.done) }()
	return warm, nil
}

func (w *cloudWarmup) activate(root, home string, port int, extra map[string]string) error {
	environment := map[string]string{
		"OCTOP_HOME": home, "OCTOP_GREEN_PACKAGES": filepath.Join(root, "packages"),
		"OCTOP_REQUIRE_SETUP_PASSWORD": "false", "XFRIEND_DESKTOP_RUNTIME": "1",
	}
	for key, value := range extra {
		environment[key] = value
	}
	err := json.NewEncoder(w.input).Encode(struct {
		Port        int               `json:"port"`
		Environment map[string]string `json:"environment"`
	}{port, environment})
	_ = w.input.Close()
	return err
}

func (w *cloudWarmup) close() {
	_ = w.input.Close()
	select {
	case <-w.done:
		return
	default:
	}
	stopOctop(w.cmd)
	<-w.done
}
