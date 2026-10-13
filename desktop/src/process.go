package main

import (
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
)

func mustEnv(cmd *exec.Cmd, extra map[string]string) {
	cmd.Env = os.Environ()
	for key, value := range extra {
		cmd.Env = append(cmd.Env, key+"="+value)
	}
}

func startOctop(root string, port int) (*exec.Cmd, error) {
	return startOctopHome(root, port, octopHome(), nil)
}

func startOctopHome(root string, port int, home string, extra map[string]string) (*exec.Cmd, error) {
	py := pythonExe(root)
	launch := filepath.Join(root, "launch.py")
	cmd := exec.Command(py, launch, "run", "--host", "127.0.0.1", "--port", strconv.Itoa(port))
	cmd.Dir = root
	environment := map[string]string{
		"OCTOP_HOME":                   home,
		"OCTOP_GREEN_PACKAGES":         filepath.Join(root, "packages"),
		"OCTOP_REQUIRE_SETUP_PASSWORD": "false",
		"PYTHONNOUSERSITE":             "1",
		"PYTHONPATH":                   "",
		"PYTHONUTF8":                   "1",
		"PYTHONUNBUFFERED":             "1",
		"PYTHONDONTWRITEBYTECODE":      "1",
		"XFRIEND_DESKTOP_RUNTIME":      "1",
	}
	for key, value := range extra {
		environment[key] = value
	}
	mustEnv(cmd, environment)
	configureProcGroup(cmd)
	if runtime.GOOS == "windows" {
		if err := os.MkdirAll(filepath.Dir(serviceLogPath()), 0o700); err != nil {
			return nil, err
		}
		logFile, err := os.OpenFile(serviceLogPath(), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
		if err != nil {
			return nil, err
		}
		defer logFile.Close()
		cmd.Stdout = logFile
		cmd.Stderr = logFile
	} else if runtime.GOOS == "linux" {
		// The Linux desktop release has no server terminal; the shell owns status
		// presentation just like the Windows GUI executable.
		cmd.Stdout = io.Discard
		cmd.Stderr = io.Discard
	} else if runtime.GOOS != "windows" {
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	return cmd, nil
}

func serviceLogPath() string {
	return filepath.Join(octopHome(), "logs", "desktop-service.log")
}

func stopOctop(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	killProcessTree(cmd)
}

func dashboardURL(port int) string {
	return fmt.Sprintf("http://127.0.0.1:%d/", port)
}

// Never connect to an unrelated service when the configured port is occupied.
func availableLocalPort(preferred int) (int, error) {
	listener, err := net.Listen("tcp4", net.JoinHostPort("127.0.0.1", strconv.Itoa(preferred)))
	if err != nil {
		listener, err = net.Listen("tcp4", "127.0.0.1:0")
	}
	if err != nil {
		return 0, err
	}
	defer listener.Close()
	return listener.Addr().(*net.TCPAddr).Port, nil
}
