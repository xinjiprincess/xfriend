package updates

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
)

// Downloader serializes callers externally; its cache must be private to the
// current user. Installation must reverify bytes in the protected destination.
type Downloader struct {
	Client *http.Client
	Cache  string
}

// VerifyArtifact checks the final size and digest before an installer is run.
func VerifyArtifact(path string, artifact Artifact) bool {
	f, err := os.Open(path)
	if err != nil {
		return false
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() != artifact.Size {
		return false
	}
	h := sha256.New()
	_, err = io.Copy(h, f)
	return err == nil && hex.EncodeToString(h.Sum(nil)) == artifact.SHA256
}

func (d Downloader) Fetch(ctx context.Context, artifact Artifact) (string, error) {
	digest, err := hex.DecodeString(artifact.SHA256)
	u, urlErr := url.Parse(artifact.URL)
	if err != nil || len(digest) != 32 || urlErr != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || artifact.Size <= 0 || artifact.Size > 32*1024*1024*1024 {
		return "", errors.New("invalid download artifact")
	}
	if err := os.MkdirAll(d.Cache, 0700); err != nil {
		return "", err
	}
	target := filepath.Join(d.Cache, artifact.SHA256)
	partial := target + ".partial"
	for _, path := range []string{target, partial} {
		if info, err := os.Lstat(path); err == nil && !info.Mode().IsRegular() {
			return "", errors.New("unsafe cache entry")
		} else if err != nil && !os.IsNotExist(err) {
			return "", err
		}
	}
	if VerifyArtifact(target, artifact) {
		return target, nil
	}
	if err := os.Remove(target); err != nil && !os.IsNotExist(err) {
		return "", err
	}
	if VerifyArtifact(partial, artifact) {
		return target, os.Rename(partial, target)
	}
	f, err := os.OpenFile(partial, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return "", err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return "", err
	}
	offset := info.Size()
	if offset >= artifact.Size {
		offset = 0
		if err := f.Truncate(0); err != nil {
			return "", err
		}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, artifact.URL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Accept-Encoding", "identity")
	if offset > 0 {
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-", offset))
	}
	client := http.Client{}
	if d.Client != nil {
		client = *d.Client
	}
	previousRedirect := client.CheckRedirect
	client.CheckRedirect = func(req *http.Request, via []*http.Request) error {
		if req.URL.Scheme != "https" || req.URL.User != nil || len(via) >= 5 {
			return errors.New("unsafe download redirect")
		}
		if previousRedirect != nil {
			return previousRedirect(req, via)
		}
		return nil
	}
	response, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.Header.Get("Content-Encoding") != "" && response.Header.Get("Content-Encoding") != "identity" {
		return "", errors.New("encoded artifact response")
	}
	switch response.StatusCode {
	case http.StatusOK:
		offset = 0
		if err := f.Truncate(0); err != nil {
			return "", err
		}
	case http.StatusPartialContent:
		if offset == 0 || response.Header.Get("Content-Range") != fmt.Sprintf("bytes %d-%d/%d", offset, artifact.Size-1, artifact.Size) {
			return "", errors.New("invalid download range")
		}
	default:
		return "", fmt.Errorf("download returned HTTP %d", response.StatusCode)
	}
	if _, err := f.Seek(offset, io.SeekStart); err != nil {
		return "", err
	}
	if response.ContentLength >= 0 && response.ContentLength != artifact.Size-offset {
		return "", errors.New("artifact length mismatch")
	}
	n, err := io.Copy(f, io.LimitReader(response.Body, artifact.Size-offset+1))
	if err != nil {
		return "", err
	}
	if n != artifact.Size-offset {
		return "", errors.New("incomplete or oversized artifact")
	}
	if err := f.Sync(); err != nil {
		return "", err
	}
	if err := f.Close(); err != nil {
		return "", err
	}
	if !VerifyArtifact(partial, artifact) {
		_ = os.Remove(partial)
		return "", errors.New("artifact checksum mismatch")
	}
	if err := os.Rename(partial, target); err != nil {
		return "", err
	}
	return target, nil
}
