# 获取内置 Git（MinGit）+ git-lfs 到 vendor\git
#
# 为什么要这个脚本：内置 Git 约 91 MB、git-lfs 约 13 MB，直接提交进仓库会让
# Git 历史永久变胖、每台电脑 clone 都变慢（见 commit 271613f 的教训）。
# 所以 vendor\git 不进库，由这个脚本按需获取，并强制校验 SHA256。
#
# 为什么还要 git-lfs：LFS 是 MinGit 唯一缺的功能。没有它，用了 LFS 的仓库
# （素材、模型、数据集）会把大文件本体当普通文件提交进仓库。
#
# 下载源顺序：自己的源站 → 国内镜像（不需要代理）→ 官方 GitHub（需要代理）。
#
# 用法：
#   pwsh -File tools\fetch_minigit.ps1                  # 缺什么补什么，已有就跳过
#   pwsh -File tools\fetch_minigit.ps1 -Force           # 强制重新获取
#   pwsh -File tools\fetch_minigit.ps1 -ZipPath D:\MinGit-2.56.0-64-bit.zip   # 用本地包，不联网
#
# 打包脚本 build_windows.ps1 会在缺 vendor\git 时自动调用它。

param(
    [string]$Version = "2.56.0",
    [string]$LfsVersion = "3.8.0",
    [string]$ZipPath = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$vendorDir = Join-Path $projectRoot "vendor"
$vendorGit = Join-Path $vendorDir "git"
$gitExe = Join-Path $vendorGit "cmd\git.exe"
$lfsExe = Join-Path $vendorGit "cmd\git-lfs.exe"

# 官方发布的 SHA256（改了版本号就得同时换哈希）
$expectedSha = "064b440ff870ed5198527e8f3a92cdf5bd2fd0fedf5e718af95e3fdaddeff718"
$lfsZipName = "git-lfs-windows-amd64-v$LfsVersion.zip"
$lfsExpectedSha = "b62e7b8ceddee635f691233d77de8eaa4b213e9209e0173811d8cfa77f7882c1"

$sources = @(
    "https://dudua.synology.me:8443/download/RepoPulse/MinGit-$Version-64-bit.zip",
    "https://registry.npmmirror.com/-/binary/git-for-windows/v$Version.windows.1/MinGit-$Version-64-bit.zip",
    "https://github.com/git-for-windows/git/releases/download/v$Version.windows.1/MinGit-$Version-64-bit.zip"
)
$lfsSources = @(
    "https://dudua.synology.me:8443/download/RepoPulse/$lfsZipName",
    "https://mirrors.tuna.tsinghua.edu.cn/github-release/git-lfs/git-lfs/v$LfsVersion/$lfsZipName",
    "https://github.com/git-lfs/git-lfs/releases/download/v$LfsVersion/$lfsZipName"
)

function Write-Step([string]$text) { Write-Host "[minigit] $text" }

function Get-Remote([string[]]$urls, [string]$target, [long]$minBytes) {
    foreach ($url in $urls) {
        try {
            Write-Step "下载：$url"
            & curl.exe -sS -f -L --max-time 900 -o $target $url
            if ($LASTEXITCODE -eq 0 -and (Test-Path $target) -and (Get-Item $target).Length -gt $minBytes) {
                return $true
            }
            Write-Step "这个源没拿到，试下一个"
        } catch {
            Write-Step "下载失败（$url）：$($_.Exception.Message)"
        }
    }
    return $false
}

function Assert-Sha([string]$file, [string]$expected) {
    Write-Step "校验 SHA256 ..."
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $expected) {
        throw "SHA256 不匹配，已中止。期望 $expected，实际 $actual"
    }
    Write-Step "SHA256 校验通过：$actual"
}

function Expand-Safe([string]$zip, [string]$staging) {
    if (Test-Path $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $staging -Force
}

# ---------------- 1) 内置 Git（MinGit） ----------------
if ((Test-Path $gitExe) -and (-not $Force)) {
    Write-Step "Git 已存在，跳过：$gitExe"
} else {
    if ($Force -and (Test-Path $vendorGit)) {
        Write-Step "Force：清理旧的 vendor\git"
        Remove-Item -LiteralPath $vendorGit -Recurse -Force
    }
    $tempZip = Join-Path ([System.IO.Path]::GetTempPath()) "MinGit-$Version-64-bit.zip"
    if ($ZipPath) {
        if (-not (Test-Path $ZipPath)) { throw "指定的压缩包不存在：$ZipPath" }
        Write-Step "使用本地压缩包：$ZipPath"
        $tempZip = $ZipPath
    } elseif (-not (Get-Remote $sources $tempZip 10MB)) {
        throw "所有下载源都失败了。可以开代理后重试，或用 -ZipPath 指定已下好的 MinGit-$Version-64-bit.zip。"
    }
    Assert-Sha $tempZip $expectedSha
    Write-Step "解压到 vendor\git ..."
    $staging = Join-Path $vendorDir "git.staging"
    Expand-Safe $tempZip $staging
    if (-not (Test-Path (Join-Path $staging "cmd\git.exe"))) {
        throw "压缩包结构不对：没找到 cmd\git.exe"
    }
    if (Test-Path $vendorGit) { Remove-Item -LiteralPath $vendorGit -Recurse -Force }
    Move-Item -LiteralPath $staging -Destination $vendorGit
    Write-Step "自检 ..."
    $reported = (& $gitExe --version 2>&1) -join " "
    if ($reported -notmatch "git version") { throw "内置 Git 无法运行：$reported" }
    Write-Step "Git 完成：$reported"
}

# ---------------- 2) git-lfs（独立检查：老 checkout 有 Git 但没 LFS） ----------------
if ((Test-Path $lfsExe) -and (-not $Force)) {
    Write-Step "git-lfs 已存在，跳过：$lfsExe"
} else {
    $lfsZip = Join-Path ([System.IO.Path]::GetTempPath()) $lfsZipName
    if (-not (Get-Remote $lfsSources $lfsZip 3MB)) {
        Write-Step "警告：git-lfs 没取到，LFS 仓库会退化成提交文件本体（Git 本身仍可正常用）"
    } else {
        Assert-Sha $lfsZip $lfsExpectedSha
        $lfsStaging = Join-Path $vendorDir "lfs.staging"
        Expand-Safe $lfsZip $lfsStaging
        $found = Get-ChildItem -LiteralPath $lfsStaging -Recurse -Filter "git-lfs.exe" | Select-Object -First 1
        if (-not $found) { throw "压缩包结构不对：没找到 git-lfs.exe" }
        Copy-Item -LiteralPath $found.FullName -Destination $lfsExe -Force
        Remove-Item -LiteralPath $lfsStaging -Recurse -Force
        $lfsReported = (& $lfsExe version 2>&1) -join " "
        Write-Step "git-lfs 完成：$lfsReported"
    }
}

Write-Step "内置 Git 位置：$vendorGit"
Write-Step "注意：vendor\git 已在 .gitignore 里，不会入库"