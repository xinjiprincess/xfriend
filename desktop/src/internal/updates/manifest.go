// Package updates verifies publisher metadata before any release is downloaded.
// This versioned envelope is not an implementation of the TUF protocol.
package updates

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const MaxManifestBytes = 1024 * 1024

type Artifact struct {
	ID     string `json:"id"`
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
}

type Release struct {
	Format         int        `json:"format"`
	Application    string     `json:"application"`
	Platform       string     `json:"platform"`
	Channel        string     `json:"channel"`
	Version        string     `json:"version"`
	Sequence       uint64     `json:"sequence"`
	Expires        time.Time  `json:"expires"`
	MinimumUpdater int        `json:"minimum_updater"`
	Installer      Artifact   `json:"installer"`
	Components     []Artifact `json:"components"`
}

type Envelope struct {
	KeyID     string `json:"key_id"`
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}

type Policy struct {
	Keys            map[string]ed25519.PublicKey
	Channel         string
	Platform        string
	MinimumSequence uint64
	UpdaterVersion  int
	Now             time.Time
}

func decode(data []byte, target any) error {
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if err := d.Decode(target); err != nil {
		return err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return errors.New("trailing metadata")
	}
	return nil
}

var versionPattern = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$`)
var idPattern = regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`)

// Verify authenticates exact payload bytes, then validates the complete release.
// The caller must persist the accepted sequence before making it installable.
func Verify(data []byte, policy Policy) (Release, error) {
	var release Release
	if len(data) == 0 || len(data) > MaxManifestBytes {
		return release, errors.New("metadata size invalid")
	}
	var envelope Envelope
	if err := decode(data, &envelope); err != nil {
		return release, err
	}
	key := policy.Keys[envelope.KeyID]
	payload, err := base64.StdEncoding.Strict().DecodeString(envelope.Payload)
	if err != nil {
		return release, errors.New("invalid payload encoding")
	}
	signature, err := base64.StdEncoding.Strict().DecodeString(envelope.Signature)
	if err != nil || len(key) != ed25519.PublicKeySize || !ed25519.Verify(key, payload, signature) {
		return release, errors.New("untrusted release signature")
	}
	if err := decode(payload, &release); err != nil {
		return Release{}, err
	}
	if release.Format != 1 || release.Application != "xfriend" || release.Platform != policy.Platform || release.Channel != policy.Channel || !versionPattern.MatchString(release.Version) {
		return Release{}, errors.New("incompatible release")
	}
	if release.Sequence == 0 || release.Sequence < policy.MinimumSequence || policy.Now.IsZero() || !release.Expires.After(policy.Now) {
		return Release{}, errors.New("expired or rolled back release")
	}
	if release.MinimumUpdater < 1 || release.MinimumUpdater > policy.UpdaterVersion || len(release.Components) > 32 {
		return Release{}, errors.New("unsupported updater requirements")
	}
	seen := map[string]bool{}
	for _, artifact := range append([]Artifact{release.Installer}, release.Components...) {
		u, err := url.Parse(artifact.URL)
		digest, digestErr := hex.DecodeString(artifact.SHA256)
		if !idPattern.MatchString(artifact.ID) || seen[artifact.ID] || artifact.Size <= 0 || artifact.Size > 32*1024*1024*1024 || digestErr != nil || len(digest) != 32 || strings.ToLower(artifact.SHA256) != artifact.SHA256 || err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.Fragment != "" {
			return Release{}, errors.New("invalid release artifact")
		}
		seen[artifact.ID] = true
	}
	return release, nil
}
