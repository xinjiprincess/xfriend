package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestBackendMonitorDetectsLossAndRecoversThroughIndependentProbe(t *testing.T) {
	var healthy atomic.Bool
	healthy.Store(true)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/api/health" {
			t.Errorf("monitor replayed a business request: %s %s", r.Method, r.URL.Path)
		}
		if !healthy.Load() {
			w.WriteHeader(503)
			return
		}
		_, _ = w.Write([]byte(`{"ok":true,"application":"xfriend","protocol":1,"core_ready":true}`))
	}))
	defer backend.Close()
	shell := testDesktop(t)
	if err := shell.setBackend(backend.URL); err != nil {
		t.Fatal(err)
	}
	shell.update("ready", "core_ready", "")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { defer close(done); shell.monitorBackend(ctx, backend.URL, 10*time.Millisecond) }()
	waitPhase := func(phase string) {
		t.Helper()
		deadline := time.Now().Add(2 * time.Second)
		for time.Now().Before(deadline) {
			if shell.snapshot().Phase == phase {
				return
			}
			time.Sleep(time.Millisecond)
		}
		t.Fatalf("wanted %s, got %+v", phase, shell.snapshot())
	}
	healthy.Store(false)
	waitPhase("reconnecting")
	if code, _, _ := desktopGET(t, shell.base+"api/health"); code != 503 {
		t.Fatalf("got %d", code)
	}
	healthy.Store(true)
	waitPhase("core_ready")
	if code, _, _ := desktopGET(t, shell.base+"api/health"); code != 200 {
		t.Fatalf("got %d", code)
	}
	shell.update("failed", "service_exited", "exit")
	time.Sleep(40 * time.Millisecond)
	if shell.snapshot().State != "failed" {
		t.Fatal("health probe hid process exit")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("monitor did not stop")
	}
}

func TestBackendProbeRejectsUnrelatedService(t *testing.T) {
	for _, body := range []string{`<html>ok</html>`, `{"ok":true,"application":"other","protocol":1,"ready":true}`, `{"ok":true,"application":"xfriend","protocol":1,"core_ready":false}`} {
		backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(body)) }))
		if backendCoreReady(context.Background(), backend.Client(), backend.URL) {
			t.Errorf("accepted %s", body)
		}
		backend.Close()
	}
}

func TestBackendMonitorProbesNewConnectionsAfterListenerLoss(t *testing.T) {
	var requests atomic.Int32
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		_, _ = w.Write([]byte(`{"ok":true,"application":"xfriend","protocol":1,"core_ready":true}`))
	}))
	defer backend.Close()
	shell := testDesktop(t)
	shell.update("ready", "core_ready", "")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { defer close(done); shell.monitorBackend(ctx, backend.URL, 10*time.Millisecond) }()
	deadline := time.Now().Add(2 * time.Second)
	for requests.Load() < 2 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if requests.Load() < 2 {
		t.Fatal("monitor never contacted backend")
	}
	// Keep handlers/connections alive while only the listening socket is lost.
	backend.Listener.Close()
	deadline = time.Now().Add(2 * time.Second)
	for shell.snapshot().Phase != "reconnecting" && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if shell.snapshot().Phase != "reconnecting" {
		t.Fatal("reused connection hid the lost listener")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("monitor did not stop")
	}
}
