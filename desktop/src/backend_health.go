package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"
)

// A living Python process can lose its HTTP listener. Probe independently of
// the proxy gate so a repaired listener can become ready again. Never retry a
// user request here: it may already have changed data or started an agent task.
func (s *desktopServer) monitorBackend(ctx context.Context, base string, interval time.Duration) {
	// Probe the listener itself, not an old keep-alive connection which can
	// remain healthy after Windows has closed the listening socket.
	transport := &http.Transport{DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Timeout: 2 * time.Second, Transport: transport}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			ready := backendCoreReady(ctx, client, base)
			if ctx.Err() != nil {
				return
			}
			s.mu.Lock()
			// Never overwrite an explicit child exit or startup/version failure.
			if s.status.State == "ready" || s.status.Phase == "reconnecting" {
				if ready {
					s.status.State, s.status.Phase, s.status.Message = "ready", "core_ready", ""
				} else {
					s.status.State, s.status.Phase, s.status.Message = "starting", "reconnecting", "本机服务连接中断，正在等待恢复。"
				}
			}
			s.mu.Unlock()
		}
	}
}

func backendCoreReady(ctx context.Context, client *http.Client, base string) bool {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(base, "/")+"/api/health", nil)
	if err != nil {
		return false
	}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	var health struct {
		OK          bool   `json:"ok"`
		Application string `json:"application"`
		Protocol    int    `json:"protocol"`
		Ready       bool   `json:"ready"`
		CoreReady   bool   `json:"core_ready"`
	}
	err = json.NewDecoder(io.LimitReader(resp.Body, 65536)).Decode(&health)
	return err == nil && resp.StatusCode == http.StatusOK && health.OK &&
		health.Application == "xfriend" && health.Protocol == 1 && (health.CoreReady || health.Ready)
}
