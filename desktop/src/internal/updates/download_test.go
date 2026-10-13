package updates

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestDownloadResumeAndCache(t *testing.T) {
	for _, supportsRange := range []bool{true, false} {
		t.Run(fmt.Sprint(supportsRange), func(t *testing.T) {
			body := []byte("verified installer bytes")
			calls := 0
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Header.Get("Range") != "bytes=4-" {
					t.Error("missing resume offset")
				}
				if supportsRange {
					w.Header().Set("Content-Range", fmt.Sprintf("bytes 4-%d/%d", len(body)-1, len(body)))
					w.WriteHeader(http.StatusPartialContent)
					_, _ = w.Write(body[4:])
				} else {
					_, _ = w.Write(body)
				}
			}))
			defer server.Close()
			artifact := Artifact{URL: server.URL, SHA256: fmt.Sprintf("%x", sha256.Sum256(body)), Size: int64(len(body))}
			d := Downloader{Client: server.Client(), Cache: t.TempDir()}
			if err := os.WriteFile(filepath.Join(d.Cache, artifact.SHA256+".partial"), body[:4], 0600); err != nil {
				t.Fatal(err)
			}
			path, err := d.Fetch(context.Background(), artifact)
			if err != nil {
				t.Fatal(err)
			}
			if !VerifyArtifact(path, artifact) {
				t.Fatal("invalid completed artifact")
			}
			if _, err := d.Fetch(context.Background(), artifact); err != nil || calls != 1 {
				t.Fatalf("cache not reused: %v, calls %d", err, calls)
			}
		})
	}
}

func TestDownloadRejectsCorruption(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("wrong")) }))
	defer server.Close()
	artifact := Artifact{URL: server.URL, SHA256: fmt.Sprintf("%x", sha256.Sum256([]byte("right"))), Size: 5}
	d := Downloader{Client: server.Client(), Cache: t.TempDir()}
	if _, err := d.Fetch(context.Background(), artifact); err == nil {
		t.Fatal("accepted corrupt artifact")
	}
	if _, err := os.Stat(filepath.Join(d.Cache, artifact.SHA256)); !os.IsNotExist(err) {
		t.Fatal("published unverified artifact")
	}
}
