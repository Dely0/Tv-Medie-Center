# Setup drpyS sidecar (all files on D:/project, keep C: clean).
# 1) portable Node (npmmirror)  2) drpys source (ghfast.top -> ghproxy.cn)  3) npm deps (npmmirror)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$sidecar = Join-Path $root 'sidecar'
$nodeDir = Join-Path $sidecar 'node'
$drpysDir = Join-Path $sidecar 'drpys'
$npmCache = Join-Path $sidecar 'npm-cache'
$logs = Join-Path $sidecar 'logs'
$nodeVer = 'v22.23.2'

New-Item -ItemType Directory -Force -Path $nodeDir, $drpysDir, $npmCache, $logs | Out-Null

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Error 'python not found in PATH'; exit 1 }

# ---------- 1. Node ----------
$nodeExe = Get-ChildItem -Path $nodeDir -Filter 'node.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $nodeExe) {
    Write-Host '[1/3] Downloading portable Node...'
    $zip = Join-Path $sidecar 'node.zip'
    $urls = @(
        "https://npmmirror.com/mirrors/node/$nodeVer/node-$nodeVer-win-x64.zip",
        "https://nodejs.org/dist/$nodeVer/node-$nodeVer-win-x64.zip"
    )
    $ok = $false
    foreach ($u in $urls) {
        try {
            curl.exe -sL --retry 3 --retry-delay 2 --max-time 900 -o $zip $u
            if ((Get-Item $zip).Length -gt 10MB) { $ok = $true; break }
        } catch { }
    }
    if (-not $ok) { Write-Error 'Node download failed'; exit 1 }
    & $py -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" $zip $nodeDir
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    $nodeExe = Get-ChildItem -Path $nodeDir -Filter 'node.exe' -Recurse | Select-Object -First 1
    if (-not $nodeExe) { Write-Error 'Node extract failed'; exit 1 }
}
Write-Host "[1/3] Node ready: $($nodeExe.FullName)"

# ---------- 2. drpys source ----------
if (-not (Test-Path (Join-Path $drpysDir 'index.js'))) {
    Write-Host '[2/3] Downloading drpys...'
    $tar = Join-Path $sidecar 'drpys.tar.gz'
    $urls = @(
        'https://ghfast.top/https://github.com/tvfuns/drpys/archive/refs/heads/main.tar.gz',
        'https://ghproxy.cn/https://github.com/tvfuns/drpys/archive/refs/heads/main.tar.gz'
    )
    $ok = $false
    foreach ($u in $urls) {
        try {
            curl.exe -sL --retry 3 --retry-delay 2 --max-time 900 -o $tar $u
            if ((Get-Item $tar).Length -gt 5MB) { $ok = $true; break }
        } catch { }
    }
    if (-not $ok) { Write-Error 'drpys download failed'; exit 1 }
    # Extract with Python tarfile so UTF-8 filenames are preserved (Windows tar mangles them)
    $tmp = Join-Path $sidecar 'drpys_tmp'
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    & $py -c "import tarfile,sys; tarfile.open(sys.argv[1],'r:gz').extractall(sys.argv[2])" $tar $tmp
    Remove-Item -LiteralPath $tar -Force -ErrorAction SilentlyContinue
    $inner = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
    if (-not $inner) { Write-Error 'drpys extract failed'; exit 1 }
    Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $drpysDir -Recurse -Force
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host '[2/3] drpys ready'

# ---------- 3. npm install ----------
if (-not (Test-Path (Join-Path $drpysDir 'node_modules'))) {
    Write-Host '[3/3] Installing npm dependencies (npmmirror)...'
    $env:PATH = (Split-Path $nodeExe.FullName) + ';' + $env:PATH
    Push-Location $drpysDir
    try {
        & $nodeExe.FullName (Join-Path (Split-Path $nodeExe.FullName) 'node_modules\npm\bin\npm-cli.js') install `
            --registry=https://registry.npmmirror.com --cache $npmCache --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { Write-Error 'npm install failed'; exit 1 }
    } finally { Pop-Location }
}
Write-Host '[3/3] npm dependencies ready'
Write-Host 'Setup complete. Start with:  scripts\start_drpys.bat'
