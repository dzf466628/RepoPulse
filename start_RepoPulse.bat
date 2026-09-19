@echo off
setlocal EnableExtensions

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

set "RUNNING_PID="
for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$app = [IO.Path]::GetFullPath((Join-Path '%APP_DIR%' 'main.py')); $p = Get-CimInstance Win32_Process -Filter 'Name = ''python.exe''' | Where-Object { $_.CommandLine -match [regex]::Escape($app) }; if ($p) { $p[0].ProcessId }"`) do set "RUNNING_PID=%%P"
if defined RUNNING_PID (
    echo RepoPulse is already running. PID %RUNNING_PID%
    exit /b 0
)

set "PYTHON_EXE=%APP_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    where python.exe >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=python.exe"
)
if not exist "%PYTHON_EXE%" if /i not "%PYTHON_EXE%"=="python.exe" set "PYTHON_EXE="

if not defined PYTHON_EXE (
    echo [ERROR] Python 3.12 or newer was not found.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import PyQt6" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing RepoPulse dependencies...
    "%PYTHON_EXE%" -m pip install -r "%APP_DIR%requirements.txt"
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

echo [INFO] Starting RepoPulse from %APP_DIR%
"%PYTHON_EXE%" "%APP_DIR%main.py"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] RepoPulse exited with code %EXIT_CODE%.
    pause
)
endlocal & exit /b %EXIT_CODE%
