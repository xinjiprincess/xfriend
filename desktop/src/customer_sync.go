package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
)

// Only these cloud resources are reachable. Renderer credentials and URLs are
// never forwarded; CloudAccount owns the access token and its single refresh.
func (s *desktopServer) customerSyncHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	fail := func(status int, code string) {
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(map[string]string{"code": code})
	}
	if r.Header.Get("Sec-Fetch-Site") == "cross-site" || (r.Header.Get("Origin") != "" && r.Header.Get("Origin") != strings.TrimRight(s.base, "/")) {
		fail(403, "SYNC_ORIGIN")
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/api/desktop/sync/")
	endpoint, method := "", http.MethodGet
	switch path {
	case "push":
		endpoint, method = "/api/v2/sync/push", http.MethodPost
	case "pull":
		endpoint = "/api/v2/sync/pull"
	case "catalog/experts":
		endpoint = "/api/v2/catalog/experts"
	case "catalog/skills":
		endpoint = "/api/v2/catalog/skills"
	default:
		fail(404, "SYNC_PATH")
		return
	}
	if r.Method != method {
		fail(405, "SYNC_METHOD")
		return
	}
	if s.cloud == nil {
		fail(503, "SYNC_UNAVAILABLE")
		return
	}
	a := s.cloud
	a.mu.Lock()
	_, validErr := a.validLocked()
	if validErr != nil || a.session.AccessToken == "" {
		a.mu.Unlock()
		fail(401, "SYNC_LOGIN")
		return
	}
	owner, space, epoch := a.session.User.UserID, a.session.SpaceID, a.epoch
	a.mu.Unlock()
	if r.Header.Get("X-Xfriend-Sync-Scope") != owner+":"+space {
		fail(409, "SYNC_SCOPE_CHANGED")
		return
	}
	query := url.Values{}
	var body []byte
	if path == "push" {
		data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 4*1024*1024))
		var input struct {
			Space      string            `json:"space_id"`
			Device     string            `json:"device_id"`
			Operations []json.RawMessage `json:"operations"`
		}
		decoder := json.NewDecoder(bytes.NewReader(data))
		decoder.DisallowUnknownFields()
		if err != nil || !json.Valid(data) || decoder.Decode(&input) != nil || input.Space != space || len(input.Operations) == 0 || len(input.Operations) > 100 {
			fail(400, "SYNC_REQUEST")
			return
		}
		input.Device = a.device
		body, _ = json.Marshal(input)
	} else if path == "pull" {
		if r.URL.Query().Get("space_id") != space {
			fail(409, "SYNC_SCOPE_CHANGED")
			return
		}
		query.Set("space_id", space)
		query.Set("cursor", r.URL.Query().Get("cursor"))
		limit := 100
		if supplied := r.URL.Query().Get("limit"); supplied != "" {
			parsed, err := strconv.Atoi(supplied)
			if err != nil || parsed < 1 || parsed > 100 {
				fail(400, "SYNC_REQUEST")
				return
			}
			limit = parsed
		}
		query.Set("limit", strconv.Itoa(limit))
	} else {
		query.Set("q", r.URL.Query().Get("q"))
		if path == "catalog/experts" {
			query.Set("scene", r.URL.Query().Get("scene"))
		}
	}
	for attempt := 0; attempt < 2; attempt++ {
		a.mu.Lock()
		if a.session == nil || a.epoch != epoch || a.session.User.UserID != owner || a.session.SpaceID != space {
			a.mu.Unlock()
			fail(409, "SYNC_SCOPE_CHANGED")
			return
		}
		token := a.session.AccessToken
		a.mu.Unlock()
		req, err := http.NewRequestWithContext(r.Context(), method, strings.TrimRight(a.profile.ServerURL, "/")+endpoint+"?"+query.Encode(), bytes.NewReader(body))
		if err != nil {
			fail(502, "SYNC_REQUEST")
			return
		}
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		resp, err := a.client.Do(req)
		if err != nil {
			fail(502, "SYNC_OFFLINE")
			return
		}
		data, readErr := io.ReadAll(io.LimitReader(resp.Body, 8*1024*1024+1))
		_ = resp.Body.Close()
		if resp.StatusCode == 401 && attempt == 0 {
			if a.refresh("") != nil {
				fail(401, "SYNC_LOGIN")
				return
			}
			continue
		}
		a.mu.Lock()
		unchanged := a.session != nil && a.epoch == epoch && a.session.User.UserID == owner && a.session.SpaceID == space
		a.mu.Unlock()
		if !unchanged {
			fail(409, "SYNC_SCOPE_CHANGED")
			return
		}
		if readErr != nil || len(data) > 8*1024*1024 || !json.Valid(data) {
			fail(502, "SYNC_RESPONSE")
			return
		}
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			fail(resp.StatusCode, "SYNC_CLOUD_REJECTED")
			return
		}
		w.WriteHeader(resp.StatusCode)
		_, _ = w.Write(data)
		return
	}
}

// Delayed journal writes must not land in a newly selected account or space.
func (s *desktopServer) customerSyncLocalScope(w http.ResponseWriter, r *http.Request) bool {
	if s.cloud == nil {
		return true
	}
	s.cloud.mu.Lock()
	defer s.cloud.mu.Unlock()
	_, err := s.cloud.validLocked()
	if err == nil && r.Header.Get("X-Xfriend-Sync-Scope") == s.cloud.session.User.UserID+":"+s.cloud.session.SpaceID && r.URL.Query().Get("space_id") == s.cloud.session.SpaceID {
		// Reuse the native proxy's existing scope guard. It checks this again
		// when selecting the Python backend, closing the account-switch gap.
		sum := sha256.Sum256([]byte(s.cloud.profile.ServerURL + "\x00" + s.cloud.session.User.UserID + "\x00" + s.cloud.session.SpaceID))
		r.Header.Set("X-Xfriend-Mobile-Scope", hex.EncodeToString(sum[:]))
		return true
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusConflict)
	_ = json.NewEncoder(w).Encode(map[string]string{"code": "SYNC_SCOPE_CHANGED"})
	return false
}
