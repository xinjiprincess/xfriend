package updates

import (
	"crypto/ed25519"
	"encoding/base64"
	"time"
)

const StableManifestURL = "https://www.ciyuancenter.cc/xfriendmanager/updates/stable/windows-x64/current.json"
const releaseKeyID = "bd7abf7f55057add"
const releasePublicKey = "LzSKYGWOuf5hb5MexrxRF6ld3HAFUlkd4UWCkYnwurE="

// StablePolicy binds the installed client to the release signing key. The
// publisher private key remains in the release operator's local key store.
func StablePolicy(minimumSequence uint64, now time.Time) Policy {
	key, err := base64.StdEncoding.DecodeString(releasePublicKey)
	if err != nil || len(key) != ed25519.PublicKeySize {
		panic("invalid embedded update trust root")
	}
	return Policy{
		Keys:    map[string]ed25519.PublicKey{releaseKeyID: ed25519.PublicKey(key)},
		Channel: "stable", Platform: "windows-amd64", MinimumSequence: minimumSequence,
		UpdaterVersion: 1, Now: now,
	}
}
