package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const sqliteBackupScript = `
import contextlib, hashlib, json, os, pathlib, shutil, sqlite3, sys
source, destination, home = map(pathlib.Path, sys.argv[1:4])
def backup_database(src, dst):
    with contextlib.closing(sqlite3.connect(src)) as reader, contextlib.closing(sqlite3.connect(dst)) as writer:
        reader.backup(writer)
        if writer.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite backup integrity check failed')
backup_database(source, destination)
snapshot = destination.with_suffix('.data')
snapshot.mkdir(mode=0o700)
excluded = {'backups', 'portable', 'portable.previous', 'portable.new', 'logs', 'webview2', 'cache', 'runtimes'}
for current, dirs, files in os.walk(home, followlinks=False):
    current = pathlib.Path(current)
    if current == home:
        dirs[:] = [name for name in dirs if name not in excluded]
    dirs[:] = [name for name in dirs if not (current / name).is_symlink() and (current / name).resolve() != snapshot.resolve()]
    target = snapshot / current.relative_to(home)
    target.mkdir(parents=True, exist_ok=True)
    for name in files:
        src = current / name
        if src.resolve() in {source.resolve(), destination.resolve(), destination.with_suffix('.manifest.json').resolve()} or src.is_symlink() or name.endswith(('-wal', '-shm', '.tmp')):
            continue
        dst = target / name
        with src.open('rb') as stream:
            is_database = stream.read(16) == b'SQLite format 3\x00'
        if is_database:
            backup_database(src, dst)
        else:
            shutil.copy2(src, dst)
def describe(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return {'size': path.stat().st_size, 'sha256': digest.hexdigest()}
manifest = {
    'format': 1,
    'application': 'xfriend',
    'source_home': str(home.resolve()),
    'source_database': str(source.resolve()),
    'consistency': 'per_database_online_backup',
    'database': describe(destination),
    'files': {p.relative_to(snapshot).as_posix(): describe(p) for p in sorted(snapshot.rglob('*')) if p.is_file()},
    'excluded': sorted(excluded),
    'scope': 'managed_home_and_control_database',
}
destination.with_suffix('.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
`

var runSQLiteBackup = func(python, source, destination string) error {
	cmd := exec.Command(python, "-c", sqliteBackupScript, source, destination, octopHome())
	if output, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("SQLite backup failed: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

// Installed code does not replace user workspaces. Back up the database before
// schema migration without copying every attachment and workspace at launch.
var runInstalledSQLiteBackup = func(python, source, destination string) error {
	const script = `import contextlib, sqlite3, sys
with contextlib.closing(sqlite3.connect(sys.argv[1])) as source, contextlib.closing(sqlite3.connect(sys.argv[2])) as target:
    source.backup(target)
    if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise RuntimeError('SQLite backup integrity check failed')
`
	cmd := exec.Command(python, "-c", script, source, destination)
	if output, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("SQLite backup failed: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

type desktopDatabaseConfig struct {
	Database struct {
		Driver     string `json:"driver"`
		SQLitePath string `json:"sqlite_path"`
	} `json:"database"`
}

func desktopSQLitePath(home string) (string, bool, error) {
	driver := "sqlite"
	sqlitePath := "xfriend.db"
	configPath := filepath.Join(home, "config.json")
	if data, err := os.ReadFile(configPath); err == nil {
		var config desktopDatabaseConfig
		if err := json.Unmarshal(data, &config); err != nil {
			return "", false, fmt.Errorf("read database config: %w", err)
		}
		if config.Database.Driver != "" {
			driver = strings.ToLower(strings.TrimSpace(config.Database.Driver))
		}
		if config.Database.SQLitePath != "" {
			sqlitePath = config.Database.SQLitePath
		}
	} else if !os.IsNotExist(err) {
		return "", false, fmt.Errorf("read database config: %w", err)
	}

	if os.Getenv("OCTOP_DATABASE_URL") != "" {
		driver = "postgresql"
	}
	if value := strings.TrimSpace(os.Getenv("OCTOP_DATABASE_DRIVER")); value != "" {
		driver = strings.ToLower(value)
	}
	if value := os.Getenv("OCTOP_DATABASE_SQLITE_PATH"); value != "" {
		sqlitePath = value
	}
	if driver == "postgresql" {
		return "", false, nil
	}
	if driver != "sqlite" {
		return "", false, fmt.Errorf("unsupported database driver %q", driver)
	}
	if !filepath.IsAbs(sqlitePath) {
		sqlitePath = filepath.Join(home, sqlitePath)
	}
	return filepath.Clean(sqlitePath), true, nil
}

func safeVersion(value string) string {
	var result strings.Builder
	for _, char := range value {
		if (char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') ||
			(char >= '0' && char <= '9') || char == '.' || char == '-' {
			result.WriteRune(char)
		}
	}
	if result.Len() == 0 {
		return "unknown"
	}
	return result.String()
}

func backupSQLiteBeforeUpgrade(root, currentVersion, nextVersion string) (string, error) {
	return backupSQLiteBeforeUpgradeWith(root, currentVersion, nextVersion, runSQLiteBackup)
}

func backupSQLiteBeforeUpgradeWith(root, currentVersion, nextVersion string, backup func(string, string, string) error) (string, error) {
	home := octopHome()
	source, isSQLite, err := desktopSQLitePath(home)
	if err != nil || !isSQLite {
		return "", err
	}
	if _, err := os.Stat(source); os.IsNotExist(err) {
		return "", nil
	} else if err != nil {
		return "", fmt.Errorf("inspect SQLite database: %w", err)
	}

	backupDir := filepath.Join(home, "backups")
	if err := os.MkdirAll(backupDir, 0o700); err != nil {
		return "", fmt.Errorf("create backup directory: %w", err)
	}
	name := fmt.Sprintf(
		"xfriend-desktop-pre-upgrade-%s-to-%s-%s.db",
		safeVersion(currentVersion),
		safeVersion(nextVersion),
		time.Now().UTC().Format("20060102T150405.000000000Z"),
	)
	destination := filepath.Join(backupDir, name)
	if err := backup(pythonExe(root), source, destination); err != nil {
		_ = os.Remove(destination)
		return "", err
	}
	if err := os.Chmod(destination, 0o600); err != nil {
		_ = os.Remove(destination)
		return "", fmt.Errorf("secure SQLite backup: %w", err)
	}
	return destination, nil
}
