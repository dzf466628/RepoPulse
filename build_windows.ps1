$ErrorActionPreference = "Stop"

# RepoPulse 一键打包：PyInstaller onedir -> Inno Setup 安装包
# 版本号唯一来源 app/__init__.py 的 __version__；每次打包自动把 patch 位 +1，
# 安装包输出到 dist\RepoPulse-Setup-v<ver>.exe
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python was not found. Run start_RepoPulse.bat once or create .venv first."
    }
    $python = $pythonCommand.Source
}

$version = (& $python -c "from app import __version__; print(__version__)").Trim()
if (-not $version) { throw "Failed to read __version__ from app/__init__.py" }
# 自动把 patch 位 +1（如 1.0.1 -> 1.0.2），写回 app/__init__.py（UTF8 无 BOM）
$initPath = Join-Path $projectRoot "app\__init__.py"
$initText = [System.IO.File]::ReadAllText($initPath)
$vm = [regex]::Match($initText, '__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"')
if (-not $vm.Success) { throw "Could not parse __version__ in app/__init__.py" }
$newVersion = "{0}.{1}.{2}" -f $vm.Groups[1].Value, $vm.Groups[2].Value, ([int]$vm.Groups[3].Value + 1)
$initText = [regex]::Replace($initText, '__version__\s*=\s*"[^"]+"', "__version__ = `"$newVersion`"")
[System.IO.File]::WriteAllText($initPath, $initText, (New-Object System.Text.UTF8Encoding($false)))
$version = $newVersion
Write-Host "[auto] build version bumped -> $version"
$distApp = Join-Path $projectRoot "dist\RepoPulse"
$distRoot = Join-Path $projectRoot "dist"
$installerPath = Join-Path $distRoot ("RepoPulse-Setup-v" + $version + ".exe")

$isccCandidates = @(
    (Join-Path ${env:ProgramFiles} "Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
)
$isccCommand = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($isccCommand) { $isccCandidates += $isccCommand.Source }
$iscc = $isccCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 ISCC.exe was not found. Install Inno Setup 6 first."
}

# 内置 Git（MinGit）：没就位就先取一份；取不到直接中止，
# 否则会做出一个用户装完点"新建项目"就报 WinError 2 的安装包。
$vendorGit = Join-Path $projectRoot "vendor\git\cmd\git.exe"
if (-not (Test-Path $vendorGit)) {
    Write-Host "[0/3] Bundled Git missing, fetching via tools\fetch_minigit.ps1 ..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot "tools\fetch_minigit.ps1")
    if (-not (Test-Path $vendorGit)) {
        throw "Bundled Git is missing (vendor\git). Run tools\fetch_minigit.ps1 first, then build again."
    }
}
Write-Host "[0/3] Bundled Git ready: $vendorGit"

Write-Host "[1/3] Build PyInstaller onedir directory (v$version) ..."
# PyInstaller/ISCC 把进度日志写到 stderr；合并到 stdout 并临时放宽策略，
# 避免 $ErrorActionPreference="Stop" 把日志误判成 NativeCommandError（假失败）。
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $python -m PyInstaller --noconfirm --clean --distpath $distRoot --workpath (Join-Path $projectRoot "build") RepoPulse.spec 2>&1 | ForEach-Object { "$_" } | Out-Host
$pyiCode = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pyiCode -ne 0) { throw "PyInstaller build failed." }
if (-not (Test-Path (Join-Path $distApp "RepoPulse.exe"))) {
    throw "PyInstaller did not create dist\RepoPulse\RepoPulse.exe"
}

# 体积自检：Qt 运行时只该留一份（PyInstaller 的 PyQt6 hook 收到 _internal\PyQt6\Qt6\bin）。
# 顶层若又冒出来同名同内容的副本，说明 spec 里的 qt_hook_collected 需要更新了。
$qtTop = Join-Path $distApp "_internal\PyQt6"
$qtBin = Join-Path $qtTop "Qt6\bin"
$wasted = 0
if (Test-Path $qtBin) {
    foreach ($dll in Get-ChildItem -LiteralPath $qtTop -File -Filter *.dll) {
        $twin = Join-Path $qtBin $dll.Name
        if (Test-Path $twin) {
            $hashA = (Get-FileHash -LiteralPath $dll.FullName -Algorithm SHA256).Hash
            $hashB = (Get-FileHash -LiteralPath $twin -Algorithm SHA256).Hash
            if ($hashA -eq $hashB) { $wasted += $dll.Length }
        }
    }
}
if ($wasted -gt 0) {
    Write-Host ("[2/3] WARNING: duplicated Qt DLLs waste {0:N1} MB - update qt_hook_collected in RepoPulse.spec" -f ($wasted / 1MB))
} else {
    Write-Host "[2/3] Qt runtime duplicated: none"
}
$bundleSize = (Get-ChildItem -LiteralPath $distApp -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("[2/3] Bundle size: {0:N1} MB" -f ($bundleSize / 1MB))

Write-Host "[2/3] Compile Inno Setup installer ..."
if (Test-Path $installerPath) {
    Remove-Item -LiteralPath $installerPath -Force
}
$ErrorActionPreference = "Continue"
& $iscc "/DAppVersion=$version" (Join-Path $projectRoot "installer.iss") 2>&1 | ForEach-Object { "$_" } | Out-Host
$isccCode = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($isccCode -ne 0 -or -not (Test-Path $installerPath)) {
    throw "Inno Setup compilation failed or did not create $installerPath"
}

Write-Host "[3/3] Build complete"
Write-Host "Installer: $installerPath"
