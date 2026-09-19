@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "APP_DIR=%~dp0"

set "PYTHON_EXE="
set "PYTHON_ARGS="

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
) else (
    where python.exe >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=python.exe"
    ) else (
        where py.exe >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_EXE=py.exe"
            set "PYTHON_ARGS=-3"
        )
    )
)

if not defined PYTHON_EXE (
    echo [ERROR] Python was not found. Please install Python 3.12 or newer.
    pause
    exit /b 1
)

"%PYTHON_EXE%" %PYTHON_ARGS% -c "import PyQt6" >nul 2>&1
if errorlevel 1 (
    echo [INFO] PyQt6 was not found. Installing dependencies...
    "%PYTHON_EXE%" %PYTHON_ARGS% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)

echo [INFO] Starting RepoPulse...
"%PYTHON_EXE%" %PYTHON_ARGS% "%APP_DIR%main.py"
if errorlevel 1 (
    echo.
    echo [ERROR] The application exited with an error.
    pause
)

endlocal
