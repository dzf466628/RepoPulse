$ErrorActionPreference = "Stop"

# RepoPulse 一键打包：PyInstaller onedir -> Inno Setup 安装包
# 版本号唯一来源 app/__init__.py 的 __version__；安装包输出到 dist\RepoPulse-Setup-v<ver>.exe
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

Write-Host "[1/3] Build PyInstaller onedir directory (v$version) ..."
& $python -m PyInstaller --noconfirm --clean --distpath $distRoot --workpath (Join-Path $projectRoot "build") RepoPulse.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
if (-not (Test-Path (Join-Path $distApp "RepoPulse.exe"))) {
    throw "PyInstaller did not create dist\RepoPulse\RepoPulse.exe"
}

Write-Host "[2/3] Compile Inno Setup installer ..."
if (Test-Path $installerPath) {
    Remove-Item -LiteralPath $installerPath -Force
}
& $iscc "/DAppVersion=$version" (Join-Path $projectRoot "installer.iss")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $installerPath)) {
    throw "Inno Setup compilation failed or did not create $installerPath"
}

Write-Host "[3/3] Build complete"
Write-Host "Installer: $installerPath"
