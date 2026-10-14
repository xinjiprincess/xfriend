package updates

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

func InstalledFlavor(directory string) (string, error) {
	data, err := os.ReadFile(filepath.Join(directory, "runtime-release.json"))
	if err != nil {
		return "", err
	}
	var release struct {
		Version string `json:"version"`
	}
	if json.Unmarshal(data, &release) != nil || !versionPattern.MatchString(release.Version) {
		return "", errors.New("invalid runtime selector")
	}
	data, err = os.ReadFile(filepath.Join(directory, "runtimes", release.Version, "desktop-features.json"))
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	var features struct {
		Flavor string `json:"flavor"`
	}
	if json.Unmarshal(data, &features) != nil || (features.Flavor != "standard" && features.Flavor != "dsh-slim") {
		return "", errors.New("invalid installed edition")
	}
	return features.Flavor, nil
}

func ManifestForFlavor(flavor string) string {
	if flavor == "" {
		return StableManifestURL
	}
	return "https://www.ciyuancenter.cc/xfriendmanager/updates/stable/windows-x64/" + flavor + "/current.json"
}
