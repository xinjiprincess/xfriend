[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$RuntimeSource,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [switch]$Resume,
    [switch]$PackageOnly
)
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
function Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed ($LASTEXITCODE)" }
}
Push-Location $repository
try {
    $version = (& uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])' | Out-String).Trim()
    $staging = Join-Path $repository ".artifacts/variants-$version"
    $output = [IO.Path]::GetFullPath($OutputDirectory)
    if (-not $PackageOnly) {
        if (((Test-Path $staging) -and -not $Resume) -or (Test-Path $output)) { throw 'Use fresh staging and output directories.' }
        Checked 'uv' @('build','--wheel','--out-dir',".artifacts/wheels-$version")
        $wheel = Join-Path $repository ".artifacts/wheels-$version/octop-$version-py3-none-any.whl"
        $prepareArgs = @('run','python','scripts/prepare_desktop_release.py','--source',([IO.Path]::GetFullPath($RuntimeSource)),'--wheel',$wheel,'--output',$staging)
        if ($Resume) { $prepareArgs += '--resume' }
        Checked 'uv' $prepareArgs
    } elseif (-not (Test-Path (Join-Path $staging 'size-report.json'))) { throw 'Prepared variants are missing.' }
    Push-Location desktop/src
    try {
        Checked 'wails3' @('task','windows:build','ARCH=amd64',"VERSION=$version",'PRODUCTION=true',"PORTABLE_ZIP=$staging/dashboard.zip")
        Checked 'wails3' @('task','windows:build:updater')
        Checked 'wails3' @('generate','webview2bootstrapper','-dir','build/windows/nsis')
        Checked 'python' @('build/stamp_version.py','nsis-defines',$version,'build/windows/nsis/version_defines.nsh')
        New-Item -ItemType Directory -Force -Path $output | Out-Null
        foreach ($flavor in @('standard','dsh-slim')) {
            $runtime = Join-Path $staging $flavor
            @{version=$version} | ConvertTo-Json -Compress | Set-Content bundled/runtime-release.json -Encoding utf8NoBOM
            $installer = Join-Path $output "Singu-Xfriend-Setup-$version-$flavor-windows-x64.exe"
            Push-Location build/windows/nsis
            try {
                Checked 'makensis' @('/INPUTCHARSET','UTF8',"-DARG_WAILS_AMD64_BINARY=$repository\desktop\src\bin\Xfriend.exe","-DARG_INSTALLED_RUNTIME=\\?\$runtime","-DINSTALLER_OUTFILE=$installer",'project.nsi')
            } finally { Pop-Location }
        }
    } finally { Pop-Location }
    Copy-Item (Join-Path $staging 'size-report.json') $output
    Get-ChildItem $output -Filter '*.exe' | ForEach-Object { '{0}  {1}' -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(),$_.Name } | Set-Content (Join-Path $output 'SHA256SUMS.txt') -Encoding ascii
} finally { Pop-Location }
