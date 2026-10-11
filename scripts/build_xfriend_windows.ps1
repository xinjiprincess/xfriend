param(
    [Parameter(Mandatory = $true)][string]$DshDirectory,
    [Parameter(Mandatory = $true)][string]$BrowsersDirectory,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) { throw 'Choose a new output directory to preserve earlier releases.' }
foreach ($program in @('uv', 'bash', 'npm', 'go', 'wails3', 'makensis')) {
    if (-not (Get-Command $program -ErrorAction SilentlyContinue)) { throw "Required build tool is missing: $program" }
}
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
Push-Location $repository
try {
    $versionLine = Get-Content -LiteralPath 'pyproject.toml' | Where-Object { $_ -match '^version\s*=\s*"([^"]+)"' } | Select-Object -First 1
    if ($versionLine -notmatch '^version\s*=\s*"([^"]+)"') { throw 'Missing application version' }
    $version = $Matches[1]
    $components = Join-Path $repository ('.artifacts/components/{0}/windows-amd64-{1}' -f $version, [Guid]::NewGuid().ToString('N'))
    Invoke-Checked 'uv' @('run', 'python', 'scripts/desktop_components.py', 'stage', '--dsh', ([IO.Path]::GetFullPath($DshDirectory)), '--browsers', ([IO.Path]::GetFullPath($BrowsersDirectory)), '--output', $components)
    $env:XFRIEND_COMPONENTS_DIR = $components.Replace('\', '/')
    $env:GREEN_ROOT = (Join-Path $repository ".artifacts/portable-$version").Replace('\', '/')
    Invoke-Checked 'npm' @('--prefix', 'dashboard', 'ci')
    Invoke-Checked 'npm' @('--prefix', 'dashboard', 'run', 'build')
    Invoke-Checked 'bash' @('desktop/portable/bootstrap-runtime.sh', 'windows-amd64')
    Invoke-Checked 'bash' @('desktop/portable/package.sh', 'windows-amd64')
    $portable = Join-Path $env:GREEN_ROOT "release/Octop-portable-windows-amd64-$version.zip"
    Push-Location (Join-Path $repository 'desktop/src')
    try {
        Invoke-Checked 'wails3' @('task', 'windows:package', 'ARCH=amd64', "VERSION=$version", "PORTABLE_ZIP=$portable")
    } finally { Pop-Location }
    New-Item -ItemType Directory -Path $output | Out-Null
    $installer = Join-Path $repository "desktop/src/bin/Xfriend-desktop-windows-amd64-$version.exe"
    if ((Get-Item -LiteralPath $installer).VersionInfo.ProductVersion -ne $version) { throw 'Installer version does not match the application.' }
    if ((Get-Item -LiteralPath (Join-Path $repository 'desktop/src/bin/Xfriend.exe')).VersionInfo.ProductVersion -ne $version) { throw 'Desktop executable version does not match the application.' }
    Copy-Item -LiteralPath $installer -Destination (Join-Path $output "Singu-Xfriend-Setup-$version-windows-x64.exe")
    Copy-Item -LiteralPath $portable -Destination (Join-Path $output "Singu-Xfriend-Portable-$version-windows-x64.zip")
    Get-ChildItem -LiteralPath $output -File | ForEach-Object {
        $hash = Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
        '{0}  {1}' -f $hash.Hash.ToLowerInvariant(), $_.Name
    } | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ascii
} finally { Pop-Location }
