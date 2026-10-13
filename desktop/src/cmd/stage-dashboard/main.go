// stage-dashboard copies only the SPA out of a portable ZIP into the Go embed
// tree. It does not extract Python or install dependencies on the user's machine.
package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strings"
)

func main() {
	portable := flag.String("portable", "", "portable ZIP used for this release")
	production := flag.Bool("production", false, "require the matching portable ZIP")
	source := flag.String("source", "../../src/octop/dashboard", "built dashboard for development")
	output := flag.String("output", "ui", "generated embed directory")
	flag.Parse()
	if err := stage(*portable, *source, *output, *production); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func stage(portable, source, output string, production bool) error {
	if portable == "" {
		if _, err := os.Stat("bundled/portable.zip"); err == nil {
			portable = "bundled/portable.zip"
		}
	}
	files := map[string][]byte{}
	version := ""
	var accountProfile json.RawMessage
	if portable != "" {
		reader, err := zip.OpenReader(portable)
		if err != nil {
			return err
		}
		defer reader.Close()
		for _, entry := range reader.File {
			if entry.FileInfo().IsDir() {
				continue
			}
			name := strings.ReplaceAll(entry.Name, "\\", "/")
			_, rel, isDashboard := strings.Cut(name, "packages/octop/dashboard/")
			isVersion := path.Base(name) == "VERSION.txt"
			isProfile := strings.HasSuffix(name, "packages/octop/infra/licensing/release-profile.json")
			if !isDashboard && !isVersion && !isProfile {
				continue
			}
			r, err := entry.Open()
			if err != nil {
				return err
			}
			data, err := io.ReadAll(r)
			r.Close()
			if err != nil {
				return err
			}
			if isVersion {
				version = parseVersion(string(data))
				continue
			}
			if isProfile {
				var profile struct {
					CloudAccounts bool `json:"cloud_accounts"`
				}
				if err := json.Unmarshal(data, &profile); err != nil {
					return err
				}
				if profile.CloudAccounts {
					accountProfile = json.RawMessage(data)
				}
				continue
			}
			if !fs.ValidPath(rel) || strings.Contains(rel, ":") {
				return fmt.Errorf("invalid dashboard entry %q", rel)
			}
			if strings.HasSuffix(rel, ".py") || strings.Contains(rel, "__pycache__/") {
				continue
			}
			files[rel] = data
		}
	} else {
		if production {
			return fmt.Errorf("production desktop builds require the matching portable ZIP")
		}
		if err := filepath.WalkDir(source, func(p string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if entry.IsDir() {
				if entry.Name() == "__pycache__" {
					return filepath.SkipDir
				}
				return nil
			}
			if strings.HasSuffix(p, ".py") || strings.HasPrefix(entry.Name(), ".") {
				return nil
			}
			rel, err := filepath.Rel(source, p)
			if err != nil {
				return err
			}
			files[filepath.ToSlash(rel)], err = os.ReadFile(p)
			return err
		}); err != nil {
			return err
		}
		data, err := os.ReadFile("../../pyproject.toml")
		if err != nil {
			return err
		}
		match := regexp.MustCompile(`(?m)^version\s*=\s*"([^"]+)"`).FindStringSubmatch(string(data))
		if len(match) > 1 {
			version = match[1]
		}
	}
	index := files["index.html"]
	if len(index) == 0 || version == "" {
		return fmt.Errorf("portable ZIP must include dashboard/index.html and VERSION.txt")
	}
	if !strings.Contains(string(index), "<html") {
		return fmt.Errorf("dashboard index is not an HTML document")
	}
	sum := sha256.Sum256(index)
	manifestValues := map[string]any{"version": version, "index_sha256": hex.EncodeToString(sum[:])}
	if accountProfile != nil {
		manifestValues["account_profile"] = accountProfile
	}
	manifest, err := json.Marshal(manifestValues)
	if err != nil {
		return err
	}
	files["dashboard-manifest.json"] = manifest
	if err := os.MkdirAll(output, 0755); err != nil {
		return err
	}
	// Only remove generated entries in the explicit output directory; preserve
	// the tracked README that keeps the embed tree available in clean checkouts.
	entries, err := os.ReadDir(output)
	if err != nil {
		return err
	}
	outputRoot, err := filepath.Abs(output)
	if err != nil {
		return err
	}
	if outputRoot == filepath.Dir(outputRoot) {
		return fmt.Errorf("refusing to clear a filesystem root")
	}
	if len(entries) > 0 {
		_, manifestErr := os.Stat(filepath.Join(outputRoot, "dashboard-manifest.json"))
		readme, readmeErr := os.ReadFile(filepath.Join(outputRoot, "README.txt"))
		if manifestErr != nil && (readmeErr != nil || !strings.Contains(string(readme), "cmd/stage-dashboard")) {
			return fmt.Errorf("refusing to replace a directory without generated dashboard metadata: %s", outputRoot)
		}
	}
	for _, entry := range entries {
		if entry.Name() != "README.txt" {
			target := filepath.Join(outputRoot, entry.Name())
			relative, err := filepath.Rel(outputRoot, target)
			if err != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
				return fmt.Errorf("invalid generated dashboard cleanup path: %s", target)
			}
			if err := os.RemoveAll(target); err != nil {
				return err
			}
		}
	}
	for name, data := range files {
		target := filepath.Join(output, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(target), 0755); err != nil {
			return err
		}
		if err := os.WriteFile(target, data, 0644); err != nil {
			return err
		}
	}
	fmt.Printf("Embedded dashboard %s (%d files) from %s\n", version, len(files), portable)
	return nil
}

func parseVersion(text string) string {
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "octop_version=") || strings.HasPrefix(line, "xfriend_version=") {
			_, line, _ = strings.Cut(line, "=")
			line = strings.TrimSpace(line)
		}
		for _, prefix := range []string{"Xfriend ", "Singu Xfriend ", "Octop ", "Version: ", "version: "} {
			line = strings.TrimPrefix(line, prefix)
		}
		if regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*$`).MatchString(line) {
			return line
		}
	}
	return ""
}
