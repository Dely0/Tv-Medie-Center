# Update drpys to latest main (re-download tarball, extract over, reinstall deps).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$sidecar = Join-Path $root 'sidecar'
$drpysDir = Join-Path $sidecar 'drpys'
$tar = Join-Path $sidecar 'drpys.tar.gz'
$tmp = Join-Path $sidecar 'drpys_tmp'
$py = (Get-Command python -ErrorAction SilentlyContinue).Source

Write-Host 'Downloading latest drpys...'
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
if (-not $ok) { Write-Error 'Download failed'; exit 1 }

New-Item -ItemType Directory -Force -Path $tmp | Out-Null
& $py -c "import tarfile,sys; tarfile.open(sys.argv[1],'r:gz').extractall(sys.argv[2])" $tar $tmp
$inner = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
if (-not $inner) { Write-Error 'Extract failed'; exit 1 }

# Backup old node_modules and swap source
$bak = Join-Path $sidecar ('drpys.bak.' + (Get-Date -Format 'yyyyMMddHHmmss'))
if (Test-Path $drpysDir) { Move-Item -LiteralPath $drpysDir -Destination $bak }
New-Item -ItemType Directory -Force -Path $drpysDir | Out-Null
Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $drpysDir -Recurse -Force
if (Test-Path (Join-Path $bak 'node_modules')) {
    Move-Item -LiteralPath (Join-Path $bak 'node_modules') -Destination (Join-Path $drpysDir 'node_modules')
}
Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $tar -Force -ErrorAction SilentlyContinue
Write-Host "Updated. Old copy kept at: $bak"
