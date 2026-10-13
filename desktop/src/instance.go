package main

import (
	"crypto/sha256"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
)

// The key is independent of application version and executable location, so an
// upgraded installer cannot start a second service against the same user data.
// Compute it without creating the home: migration must happen after the lock.
func desktopInstanceID(home string) (string, error) {
	current, err := user.Current()
	if err != nil {
		return "", err
	}
	return instanceIDForUser(current.Uid, home)
}

func instanceIDForUser(uid, home string) (string, error) {
	absolute, err := filepath.Abs(home)
	if err != nil {
		return "", err
	}
	// Resolve the existing parent as well when the destination has not yet been
	// created (fresh installation or legacy data migration).
	base := absolute
	var suffix []string
	for {
		resolved, resolveErr := filepath.EvalSymlinks(base)
		if resolveErr == nil {
			absolute = resolved
			for i := len(suffix) - 1; i >= 0; i-- {
				absolute = filepath.Join(absolute, suffix[i])
			}
			break
		}
		if !os.IsNotExist(resolveErr) {
			return "", resolveErr
		}
		parent := filepath.Dir(base)
		if parent == base {
			return "", resolveErr
		}
		suffix = append(suffix, filepath.Base(base))
		base = parent
	}
	absolute = filepath.Clean(absolute)
	if runtime.GOOS == "windows" {
		absolute = strings.ToLower(absolute)
	}
	sum := sha256.Sum256([]byte(uid + "\x00" + absolute))
	return fmt.Sprintf("xfriend-%x", sum[:]), nil
}
