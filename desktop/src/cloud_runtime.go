package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

func (s *desktopServer) configureCloud() error {
	profile := s.manifest.AccountProfile
	if profile == nil || !profile.CloudAccounts {
		return nil
	}
	device, err := cloudMachineCode()
	if err != nil {
		return err
	}
	// The profile is compiled into the executable; upgrades retain the protected store.
	s.cloud, err = newCloudAuth(*profile, protectedAccountStore{filepath.Join(octopHome(), "account-session.bin")}, device)
	return err
}

func exchangeCloud(base, secret string, lease cloudSignedLease) (string, error) {
	req, err := http.NewRequest(http.MethodPost, base+"api/auth/cloud-session", bytes.NewReader(accountJSON(map[string]any{"lease": lease})))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Xfriend-Cloud-Bridge", secret)
	client := &http.Client{Timeout: 8 * time.Second}
	response, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	var result struct {
		AccessToken string `json:"access_token"`
	}
	if response.StatusCode != 200 || json.NewDecoder(io.LimitReader(response.Body, 65536)).Decode(&result) != nil || result.AccessToken == "" {
		return "", fmt.Errorf("账号的本机身份初始化失败，请稍后重试")
	}
	return result.AccessToken, nil
}

// One child serves one account-space directory. Stop the old child before switching.
func (a *App) bootCloud(root string, locale Locale) {
	auth := a.desktop.cloud
	warm, _ := startCloudWarmup(root)
	defer func() {
		if warm != nil {
			warm.close()
		}
	}()
	stop := make(chan struct{})
	defer close(stop)
	go auth.run(stop)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		a.mu.Lock()
		quitting := a.quitting
		a.mu.Unlock()
		if quitting {
			return
		}
		key, lease, valid := auth.context()
		if !valid {
			a.desktop.update("starting", "awaiting_account", "")
			select {
			case <-auth.changes:
			case <-ticker.C:
			}
			continue
		}
		port, err := availableLocalPort(0)
		if err != nil {
			a.desktop.update("failed", "starting_service", err.Error())
			return
		}
		base, secret := dashboardURL(port), cloudBridgeSecret()
		a.desktop.update("starting", "starting_service", desktopText(locale, copyStatusStartingService))
		home := cloudHome(key)
		if err = os.MkdirAll(home, 0700); err != nil {
			a.desktop.update("failed", "starting_service", err.Error())
			return
		}
		environment := map[string]string{
			"XFRIEND_CLOUD_BRIDGE_SECRET":  secret,
			"XFRIEND_CLOUD_SPACE_KEY":      key,
			"XFRIEND_HOME":                 home,
			"XFRIEND_DATABASE_DRIVER":      "sqlite",
			"XFRIEND_DATABASE_SQLITE_PATH": filepath.Join(home, "xfriend.db"),
			"XFRIEND_DATABASE_URL":         "",
		}
		var cmd *exec.Cmd
		var done chan struct{}
		if warm != nil {
			err = warm.activate(root, home, port, environment)
			if err == nil {
				cmd, done = warm.cmd, warm.done
			} else {
				warm.close()
			}
			warm = nil
		}
		if cmd == nil {
			cmd, err = startOctopHome(root, port, home, environment)
			if err == nil {
				done = make(chan struct{})
				go func() { _ = cmd.Wait(); close(done) }()
			}
		}
		if err != nil {
			a.desktop.update("failed", "starting_service", err.Error())
			return
		}
		a.mu.Lock()
		a.cmd, a.cmdDone = cmd, done
		a.mu.Unlock()
		client := &http.Client{Timeout: time.Second}
		started, expired, ready := time.Now(), false, false
		var token string
		for !ready {
			a.mu.Lock()
			quitting = a.quitting
			a.mu.Unlock()
			next, _, ok := auth.context()
			if quitting || !ok || next != key {
				expired = true
				break
			}
			if backendCoreReady(context.Background(), client, base) {
				_, lease, _ = auth.context()
				token, err = exchangeCloud(base, secret, lease)
				if err == nil {
					ready = true
					break
				}
			}
			if time.Since(started) > 2*time.Minute {
				err = fmt.Errorf("当前账号的本机服务未及时就绪")
				break
			}
			select {
			case <-done:
				err = fmt.Errorf("当前账号的本机服务已退出")
				expired = true
			case <-ticker.C:
			}
			if expired {
				break
			}
		}
		if ready {
			_ = a.desktop.setBackend(base)
			a.desktop.mu.Lock()
			a.desktop.cloudContext, a.desktop.cloudToken = key, token
			a.desktop.mu.Unlock()
			a.desktop.update("ready", "core_ready", "")
			a.showDashboard(a.desktop.base)
			lastLease := lease.Payload
			for {
				a.mu.Lock()
				quitting = a.quitting
				a.mu.Unlock()
				next, current, ok := auth.context()
				if quitting || !ok || next != key {
					break
				}
				if current.Payload != lastLease {
					if refreshed, refreshErr := exchangeCloud(base, secret, current); refreshErr == nil {
						a.desktop.mu.Lock()
						a.desktop.cloudToken = refreshed
						a.desktop.mu.Unlock()
						lastLease = current.Payload
					}
				}
				select {
				case <-done:
					expired = true
				case <-auth.changes:
				case <-ticker.C:
				}
				if expired {
					break
				}
			}
		}
		a.desktop.mu.Lock()
		a.desktop.cloudToken, a.desktop.cloudContext = "", ""
		a.desktop.proxy = nil
		a.desktop.mu.Unlock()
		stopOctop(cmd)
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			if cmd.Process != nil {
				_ = cmd.Process.Kill()
			}
			<-done
		}
		a.mu.Lock()
		if a.cmd == cmd {
			a.cmd = nil
			a.cmdDone = nil
		}
		a.mu.Unlock()
		if !ready && !expired && err != nil {
			a.desktop.update("failed", "starting_service", err.Error())
			return
		}
	}
}
