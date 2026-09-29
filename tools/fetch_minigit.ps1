# 获取内置 Git（MinGit）到 vendor\git
#
# 为什么要这个脚本：内置 Git 有 91 MB 左右，直接提交进仓库会让 Git 历史永久变胖、
# 每台电脑 clone 都变慢（见 commit 271613f 的教训）。所以 vendor\git 不进库，
# 由这个脚本按需获取：优先你自己的源站，回退 GitHub 官方发布，并强制校验 SHA256。
#
# 用法：
#   pwsh -File tools\fetch_minigit.ps1                  # 没有就下载，已有就跳过
#   pwsh -File tools\fetch_minigit.ps1 -Force           # 强制重新获取
#   pwsh -File tools\fetch_minigit.ps1 -ZipPath D:\MinGit-2.56.0-64-bit.zip   # 用本地包，不联网
#
# 打包脚本 build_windows.ps1 会在缺 vendor\git 时自动调用它。

param(
    [string]$Version = "2.56.0",
    [string]$ZipPath = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$vendorDir = Join-Path $projectRoot "vendor"
$vendorGit = Join-Path $vendorDir "git"
$gitExe = Join-Path $vendorGit "cmd\git.exe"

# 官方发布的 SHA256（git-for-windows v$Version.windows.1 的 MinGit-$Version-64-bit.zip）
$expectedSha = "064b440ff870ed5198527e8f3a92cdf5bd2fd0fedf5e718af95e3fdaddeff718"

$sources = @(
    "https://dudua.synology.me:8443/download/RepoPulse/MinGit-$Version-64-bit.zip",
    "https://github.com/git-for-windows/git/releases/download/v$Version.windows.1/MinGit-$Version-64-bit.zip"
)

function Write-Step([string]$text) { Write-Host "[minigit] $text" }

if ((Test-Path $gitExe) -and (-not $Force)) {
    Write-Step "已存在，跳过：$gitExe"
    exit 0
}

if ($Force -and (Test-Path $vendorGit)) {
    Write-Step "Force：清理旧的 vendor\git"
    Remove-Item -LiteralPath $vendorGit -Recurse -Force
}

$tempZip = Join-Path ([System.IO.Path]::GetTempPath()) "MinGit-$Version-64-bit.zip"

if ($ZipPath) {
    if (-not (Test-Path $ZipPath)) { throw "指定的压缩包不存在：$ZipPath" }
    Write-Step "使用本地压缩包：$ZipPath"
    $tempZip = $ZipPath
} else {
    $downloaded = $false
    foreach ($url in $sources) {
        try {
            Write-Step "下载：$url"
            & curl.exe -sS -L --max-time 900 -o $tempZip $url
            if ($LASTEXITCODE -eq 0 -and (Test-Path $tempZip) -and (Get-Item $tempZip).Length -gt 10MB) {
                $downloaded = $true
                break
            }
            Write-Step "这个源没拿到，试下一个"
        } catch {
            Write-Step "下载失败（$url）：$($_.Exception.Message)"
        }
    }
    if (-not $downloaded) {
        throw "所有下载源都失败了。可以开代理后重试，或用 -ZipPath 指定已下好的 MinGit-$Version-64-bit.zip。"
    }
}

Write-Step "校验 SHA256 ..."
$actualSha = (Get-FileHash -LiteralPath $tempZip -Algorithm SHA256).Hash.ToLower()
if ($actualSha -ne $expectedSha) {
    throw "SHA256 不匹配，已中止。期望 $expectedSha，实际 $actualSha"
}
Write-Step "SHA256 校验通过：$actualSha"

Write-Step "解压到 vendor\git ..."
$staging = Join-Path $vendorDir "git.staging"
if (Test-Path $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging -Force | Out-Null
Expand-Archive -LiteralPath $tempZip -DestinationPath $staging -Force

$stagedExe = Join-Path $staging "cmd\git.exe"
if (-not (Test-Path $stagedExe)) {
    throw "压缩包结构不对：没找到 cmd\git.exe"
}

if (Test-Path $vendorGit) { Remove-Item -LiteralPath $vendorGit -Recurse -Force }
Move-Item -LiteralPath $staging -Destination $vendorGit

Write-Step "自检 ..."
$reported = (& $gitExe --version 2>&1) -join " "
if ($reported -notmatch "git version") { throw "内置 Git 无法运行：$reported" }
Write-Step "完成：$reported"
Write-Step "内置 Git 位置：$vendorGit"
Write-Step "注意：vendor\git 已在 .gitignore 里，不会入库"