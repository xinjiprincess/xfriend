# Singu Xfriend desktop (Wails v3 + green portable)

## 手机连接（Android 0.4 v1 协议）

登录电脑版后，在设置分组选择“连接手机”。服务默认关闭，按需在 Go 桌面进程内启动，不依赖独立 Python 连接桥。选择 USB、开启连接、连接一台已授权 USB 调试的手机，点击“配置 USB 转发”（需要 PATH 中的 Android Platform Tools / adb），然后在手机版“连接电脑”中填写页面给出的地址和配对码。

局域网模式需选择 HTTPS，填写手机信任的域名地址和电脑上的 PEM 证书/私钥路径；服务监听所选端口，防火墙由用户配置。不会忽略证书校验。关闭连接会撤销全部会话；会话最多 8 小时，账号退出或空间切换撤销连接和活动流。

协议包括 `/mobile/status`、`POST /mobile/pair`、`POST /mobile/logout`，以及配对后的原版页面、HTTP、WebSocket、SSE 和上传代理。电脑云令牌不发送到手机，手机无法通过此连接管理电脑云登录状态。`/mobile/export` 完整资源导出尚未接入；这不是离线双向同步。

验证：在 `desktop/src` 运行 `go test . ./internal/mobilebridge ./internal/updates ./cmd/...`（避免扫描 bundled 下保留的安装运行库）；前端连接页和侧边栏测试位于 `dashboard/src/pages/Settings/PhoneConnection.test.tsx` 与 `dashboard/src/layouts/sidebarNav*.test.ts`。真实 USB/Android 设备和受信任 HTTPS 证书仍需现场验收。

All desktop-client code lives here. This is **not** `src/octop/infra/desktop`
(remote desktop streaming).

| Path | Role |
|------|------|
| [`portable/`](portable/) | Green zip packaging (was `scripts/green/`) |
| [`src/`](src/) | Wails v3 shell: load bundled zip, spawn Octop, tray/settings |
| [`package-release.sh`](package-release.sh) | Native end-to-end portable + Wails release build |

## Data directory

`XFRIEND_HOME` takes precedence over the legacy `OCTOP_HOME` override; the default
is `~/.xfriend`. Legacy default directories migrate before the WebView is created.

- Windows 1.4.4 installed runtime → `<install>/runtimes/<version>/`
- Legacy/portable runtime extract → `<home>/portable/`
- Shell prefs → `<home>/desktop-settings.json`
- WebView profile → `<home>/webview2/`

Windows 1.4.1 can be built with `scripts/build_xfriend_windows.ps1
-LocalTrialDays 5` instead of a server license profile. Its continuous five-day
trial starts on first launch, independent of build date or installation path.
DPAPI-protected trial records live under `%LOCALAPPDATA%/Singu/Xfriend/License/`
and `HKCU/Software/Singu/Xfriend/License`, outside the replaceable runtime and
application data home. Upgrades retain the original expiry. Expiry blocks the
business workspace but does not delete account, conversation or configuration
data. This build does not contact the future activation server.

The 1.4.2 online-activation release uses `-LicenseProfile` with the public profile
exported by the production activation administrator. Before packaging, the build
checks HTTPS readiness and the public key against that trusted profile; HTML
fallback pages, redirects and mismatched keys stop the build. This is still a
complete desktop installer with a bundled runtime, not a network downloader.
The server grants new machines five trial days and retains existing server
entitlements across upgrades. Existing 1.4.1 offline-trial installers are retained.
Production packaging awaits deployment and the production verification key.

## Startup and local origin

The shell first acquires a Wails single-instance lock keyed by OS user and
canonical data directory. The second launch only restores the existing window;
IPC arguments never execute commands or change URLs. An additional OS file lock
in the user's cache covers multiple Windows login sessions. Both locks precede
data migration, settings writes, runtime replacement and Python startup.

Go serves the embedded dashboard immediately at `http://127.0.0.1:<settings.Port>`.
The port remains stable (default `18088`), including across runtime upgrades;
an occupied port reports an error rather than silently changing browser origin.
Python runs on a separate, randomly selected loopback port. Only `/api` requests
are proxied; request Host, Origin, cookies and authorization stay intact. The
proxy supports streaming SSE and WebSockets and does not add authentication.

The shell's `GET /api/desktop/startup` returns application `xfriend`, protocol `1`,
state `starting`, `ready` or `failed`, phase, optional message, and `elapsed_ms`.
The embedded HTML carries `data-xfriend-desktop="1"`. Until the shell-launched
Python health endpoint confirms `core_ready: true` (or legacy `ready: true`),
other API requests return HTTP 503 with `code: SERVICE_STARTING` and
`retry_after_ms: 500`. Runtime errors remain visible in the interactive startup
screen. Since 1.4.4, the initial route is `/login`; login/setup render before core
readiness, and submit waits for the real authentication API. Startup failure is
shown on the login form. Ready does not reload the page or discard input. `/sw.js` retires older
dashboard service workers so cached pages cannot hide this startup screen.

Only the main WebView is created at application startup. Settings are created on
the first tray opening; the assistant is created on demand. Its visible state is
remembered, so an enabled assistant restores after core readiness, without
automatically enabling the microphone. Existing settings without the visibility
field retain the old restore-on-ready behavior. Session handover to the lazy
assistant stays in memory and waits for its iframe to load.

`common:build:frontend` runs `go run ./cmd/stage-dashboard`. Release builds stage
only the UI from the **same portable ZIP** being packaged, without extracting
Python. Runtime startup checks its version and index SHA256 against this build
manifest; the index references content-hashed assets. Same-version repacks with
changed UI trigger the existing backup/replacement flow. Failed updates cannot
silently combine a new interface with an incompatible previous runtime.

For an isolated development test, set `XFRIEND_HOME` to a new temporary directory
and choose a free `Port` in its `desktop-settings.json`. This also isolates the
WebView profile and single-instance key. `OCTOP_DESKTOP_PORTABLE_ZIP` can select
a matching test runtime. A plain development `go build` does not embed Python;
run `go run ./cmd/stage-dashboard` first after building the dashboard. Production
staging requires a portable ZIP. Run `go test ./...` and `go vet ./...` in
`desktop/src` to verify the shell, proxy and build staging behavior.

## Build green zip

From repo root:

```bash
make -f desktop/portable/Makefile green
```

CI: `.github/workflows/octop-desktop.yml` builds native platform/arch variants.
`v*` tags and `workflow_dispatch` (platforms=`all`) run all six; pull requests
that touch `desktop/` build `darwin-*` and `windows-*` (amd64 + arm64). Each job
first creates the green zip and then packages the matching Wails application.

## Build a complete desktop release

Run the end-to-end script on the matching native host. It builds the Dashboard,
creates and verifies the portable runtime, packages it with Wails, and produces
the final native package:

```bash
desktop/package-release.sh
# Reuse an existing desktop/portable/release/Octop-portable-<plat>-<version>.zip:
desktop/package-release.sh darwin-arm64 --reuse-portable
```

Wails requires native packaging, so all six variants are produced by the CI
matrix on macOS, Linux, and Windows runners rather than cross-compiled locally.
Windows packaging also needs [NSIS](https://nsis.sourceforge.io/) (`makensis`)
so the `.exe` is an installer rather than a portable single-file binary.

Windows 1.4.4 uses the `installed_runtime` Go build tag. NSIS expands the entire
runtime during installation and writes `runtime-release.json` last. The small
desktop executable launches that version directly; it never extracts a runtime
ZIP or falls back to downloading one. The Go-embedded dashboard is still staged
from the same release ZIP and checked against the selected installed runtime.
Accounts, conversations, workspaces and trial anchors remain outside the install
directory. The first successful upgrade records the selected runtime version;
before a version change, SQLite is backed up using its online backup API.
Plugin dependency installs target `<home>/python-dependencies`, which the launcher
adds to the Python path, so they do not require writes into Program Files.

The Windows release build applies a hash-pinned import patch to deepagents 0.7.9
in staging only, precompiles immutable bytecode, and reads dependency bytecode
from `startup-code.zip` into memory once. Module paths and resource access still
point at their real installation directories. Original sources, native extensions
and package metadata remain installed. Dependency source drift stops packaging
for review. Rebuild the release after modifying bundled code; unchecked bytecode
is deliberately unsuitable for editing installed source in place.

Use `scripts/benchmark_desktop_startup.py --runtime <runtime-root> --output <dir>`
for isolated, real TCP startup samples. Add `--provider-db <existing-xfriend.db>`
and `--model <provider/model>` to measure a real configured expert in a disposable
home. It reads provider settings without modifying the original database or
workspace. It does not call a remote model or measure WebView paint time.

## Build the Wails shell

Run these from **`desktop/src`** (that directory contains `Taskfile.yml` and
`build/config.yml`). Requires **Go 1.25+**, [Wails v3](https://v3.wails.io/)
`v3.0.0-beta.13`.

```bash
go install github.com/wailsapp/wails/v3/cmd/wails3@v3.0.0-beta.13
cd desktop/src
go mod tidy
wails3 build            # development binary under desktop/src/bin/
wails3 task package ARCH=arm64 VERSION=<version> \
  PORTABLE_ZIP=../portable/release/Octop-portable-darwin-arm64-<version>.zip
```

Dev against an already-running Octop (skips the bundled green zip):

```bash
cd desktop/src
OCTOP_DESKTOP_URL=http://127.0.0.1:8088 wails3 dev
```

Without `OCTOP_DESKTOP_URL`, first launch uses `~/.xfriend/portable/` if valid,
otherwise extracts the matching zip shipped with the desktop package (embedded
in the Windows and Linux binaries, under `Contents/Resources` on macOS). The
Wails shell never downloads Octop. For local runtime debugging, set
`OCTOP_DESKTOP_PORTABLE_ZIP=/absolute/path/Octop-portable-<plat>-<version>.zip`.
On later launches, a newer bundled portable version replaces the extracted
runtime after creating a consistent SQLite backup under `~/.xfriend/backups/`.
The upgraded Octop process then applies the normal database migrations during
startup. Newer extracted runtimes are never downgraded; an incompatible desktop
UI instead requires installing the matching complete desktop release. PostgreSQL remains
externally managed and is not copied by the desktop shell.

GitHub Release names follow `Octop-<kind>-<os>-<arch>-<version>.<ext>`:

- Desktop GUI: `Octop-desktop-<plat>-<version>.dmg` (macOS; open and drag
  `Octop.app` into Applications), `.exe` (Windows NSIS installer — copies
  into `Program Files\Octop` and creates Start Menu + desktop shortcuts),
  `.tar.gz` (Linux)
- Green runtime zip: `Octop-portable-<plat>-<version>.zip`
- PyPI wheels stay `octop-<version>-py3-none-any.whl` (PEP 427)

The Linux tar.gz contains only the GUI binary; it has no separate portable zip or
server terminal process. Runtime upgrades remain owned by Octop:
the shell sets `OCTOP_GREEN_PACKAGES`, so `octop update` upgrades the extracted
`packages/` directory through Octop's existing `--target` logic.

Linux also needs GTK4 + WebKitGTK 6 to link. macOS 12+.

## Icons

| File | Used for | Rule |
|------|----------|------|
| `src/build/appicon.png` | Windows `.ico`, Linux | Full-bleed 512x512 artwork |
| `src/build/appicon-macos.png` | macOS `.icns` | 1024x1024 canvas, artwork 824x824 centred |
| `src/assets/tray-icon.png` | Tray + app icon on Windows/Linux | Full-bleed |
| `src/assets/tray-icon-template.png` | macOS menu bar | 88px canvas, 64px black-on-transparent glyph |

macOS sizes both surfaces to a fixed box, so the padding has to live in the
artwork: the Dock follows Apple's 824/1024 icon grid, and Wails scales the menu
bar image to the full `NSStatusBar` thickness (22pt) where the glyph should be
~16pt. Full-bleed sources on either surface render a size bigger than every
other app. On macOS the Dock icon comes from the bundle's `icons.icns` only —
see `applyAppIcon` in `src/icons_darwin.go`.
