#!/usr/bin/env bash
# Native Apple Silicon build. Inputs are already verified, public release assets.
set -euo pipefail
test "$(uname -s)" = Darwin
test "$(uname -m)" = arm64
: "${XFRIEND_COMPONENTS_DIR:?Set the verified darwin-arm64 components directory}"
: "${XFRIEND_RELEASE_LICENSE_PROFILE:?Set the public cloud account release profile}"
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
version="$(uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
export GREEN_ROOT="${GREEN_ROOT:-$root/.artifacts/portable-macos-$version}"
export GREEN_HOST_PLAT=darwin-arm64
export PBS_BASE_URL=https://github.com/astral-sh/python-build-standalone/releases/download/20251209
if [[ "${XFRIEND_FRONTEND_READY:-0}" != 1 ]]; then
  npm --prefix dashboard ci
  npm --prefix dashboard run build
fi
bash desktop/portable/bootstrap-runtime.sh darwin-arm64
bash desktop/portable/package.sh darwin-arm64
portable="$GREEN_ROOT/release/Octop-portable-darwin-arm64-$version.zip"
cd desktop/src
wails3 task darwin:build ARCH=arm64 VERSION="$version" PRODUCTION=true PORTABLE_ZIP="$portable"
output="$root/dist/$version-macos-arm64"
mkdir -p "$output"
for flavor in standard dsh-slim; do
  app="$root/.artifacts/macos-app-$version/$flavor/Xfriend.app"
  test ! -e "$app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/runtimes"
  runtime="$app/Contents/Resources/runtimes/$version"
  uv run --project "$root" python "$root/scripts/slim_desktop_runtime.py" --source "$GREEN_ROOT/release/Octop-darwin-arm64" --output "$runtime" --flavor "$flavor" > "$output/$flavor-size.json"
  cp bin/Xfriend "$app/Contents/MacOS/Xfriend"
  cp build/darwin/icons.icns "$app/Contents/Resources/icons.icns"
  cp build/darwin/Info.plist "$app/Contents/Info.plist"
  python3 build/stamp_version.py plist "$app/Contents/Info.plist" "$version"
  /usr/libexec/PlistBuddy -c 'Set :LSMinimumSystemVersion 14.0' "$app/Contents/Info.plist"
  # Predeploy code: no first-launch runtime extraction.
  printf '{"version":"%s"}\n' "$version" > "$app/Contents/Resources/runtime-release.json"
  codesign --force --sign - "$app"
  codesign --verify --deep --strict "$app"
  uv run --project "$root" python "$root/scripts/smoke_macos_desktop.py" --app "$app"
  "$app/Contents/Resources/runtimes/$version/runtime/bin/python3" -S "$root/scripts/smoke_macos_dsh.py" --runtime "$app/Contents/Resources/runtimes/$version"
  staging="$(dirname "$app")"
  ln -s /Applications "$staging/Applications"
  dmg="$output/Singu-Xfriend-$version-$flavor-macos-arm64.dmg"
  hdiutil create -volname 'Singu Xfriend' -srcfolder "$staging" -ov -format UDZO "$dmg"
  hdiutil verify "$dmg"
  rm -rf "$staging"
done
cd "$output"
shasum -a 256 ./*.dmg > SHA256SUMS.txt
