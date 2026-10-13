package updates

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/url"
	"time"
)

// Check retrieves only small signed metadata. Call it after the main window
// is ready; a slow update server must never delay account or expert startup.
func Check(ctx context.Context, client *http.Client, manifestURL string, policy Policy) (Release, error) {
	release, _, err := FetchSigned(ctx, client, manifestURL, policy)
	return release, err
}

// FetchSigned returns the exact signed bytes for handoff to the independent
// installer, which must verify them again after the desktop process exits.
func FetchSigned(ctx context.Context, client *http.Client, manifestURL string, policy Policy) (Release, []byte, error) {
	var empty Release
	u, err := url.Parse(manifestURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Fragment != "" {
		return empty, nil, errors.New("invalid update URL")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, manifestURL, nil)
	if err != nil {
		return empty, nil, err
	}
	req.Header.Set("Accept", "application/json")
	if client == nil {
		client = http.DefaultClient
	}
	bounded := *client
	bounded.Timeout = 10 * time.Second
	previousRedirect := bounded.CheckRedirect
	bounded.CheckRedirect = func(next *http.Request, via []*http.Request) error {
		if next.URL.Scheme != "https" || next.URL.User != nil || len(via) >= 5 {
			return errors.New("unsafe update redirect")
		}
		if previousRedirect != nil {
			return previousRedirect(next, via)
		}
		return nil
	}
	response, err := bounded.Do(req)
	if err != nil {
		return empty, nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return empty, nil, errors.New("update metadata unavailable")
	}
	if response.ContentLength > MaxManifestBytes {
		return empty, nil, errors.New("update metadata too large")
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, MaxManifestBytes+1))
	if err != nil {
		return empty, nil, err
	}
	release, err := Verify(data, policy)
	if err != nil {
		return empty, nil, err
	}
	return release, data, nil
}
