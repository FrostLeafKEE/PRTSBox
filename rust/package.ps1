param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
$rustRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $rustRoot
$dist = Join-Path $projectRoot 'dist'
if (-not $SkipBuild) {
    & cargo build --release --locked --offline --manifest-path (Join-Path $rustRoot 'Cargo.toml')
    if ($LASTEXITCODE -ne 0) { throw 'Rust release build failed' }
}
$binary = Join-Path $rustRoot 'target\release\prtsbox.exe'
if (-not (Test-Path -LiteralPath $binary)) { throw 'Missing Rust release executable' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$name = "PRTSBox-Rust-preview-$stamp-win64"
$stage = Join-Path $dist $name
$archive = Join-Path $dist "$name.zip"
New-Item -ItemType Directory -Path $stage -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath $binary -Destination (Join-Path $stage 'PRTSBox.exe')
Copy-Item -LiteralPath (Join-Path $rustRoot 'assets') -Destination (Join-Path $stage 'assets') -Recurse
@'
PRTSBox Rust 预览版

运行 PRTSBox.exe。设置与下载的本地模型会保存在本目录的 data 文件夹。
首次使用本地翻译前，请在设置中下载 llama.cpp 运行时与 GGUF 模型。
从旧版复制 data 时，未带校验记录的 llama.cpp 运行时需在设置中重新下载一次；GGUF 模型无需重复下载。
本包无需 Python；不包含约 1 GB 的 GGUF 模型或 llama.cpp 运行时。
主副屏不同 DPI 的桌面窗口移动、前台游戏抓帧已验证；备用抓帧会包含同屏悬浮窗口。游戏译文覆盖层、各翻译平台真实账号及 OCR 精度仍需继续验证。
'@ | Set-Content -LiteralPath (Join-Path $stage 'README.txt') -Encoding utf8
Compress-Archive -LiteralPath $stage -DestinationPath $archive -CompressionLevel Optimal
Write-Output "STAGE=$stage"
Write-Output "ZIP=$archive"
