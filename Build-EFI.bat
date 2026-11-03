@echo off
setlocal EnableDelayedExpansion
title Tiger Lake EFI Builder - Automated Runner

echo ============================================================
echo         TigerLake-EFI-Builder - One-Click Build
echo ============================================================
echo.

set "PYTHON_EXE="

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
) else if exist "C:\Program Files\Python312\python.exe" (
    set "PYTHON_EXE=C:\Program Files\Python312\python.exe"
) else (
    where py >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=py"
    ) else (
        where python >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON_EXE=python"
        )
    )
)

if "%PYTHON_EXE%"=="" (
    echo [ERROR] Python was not found on your system!
    echo Please ensure Python 3.12 is installed.
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0"
if exist "tigerlake-efi-builder\main.py" (
    set "PROJ_DIR=%~dp0tigerlake-efi-builder"
) else if exist "main.py" (
    set "PROJ_DIR=%~dp0"
) else (
    echo [ERROR] Could not find tigerlake-efi-builder project!
    pause
    exit /b 1
)

echo [1/2] Verifying Python dependencies...
"%PYTHON_EXE%" -m pip install -q -r "%PROJ_DIR%\requirements.txt"

echo.
echo [2/2] Running TigerLake EFI Builder...
echo.
"%PYTHON_EXE%" "%PROJ_DIR%\main.py" %*
if !errorlevel! neq 0 (
    echo.
    echo [ERROR] Build encountered an error. Please see the output above.
    echo.
    pause
    exit /b !errorlevel!
)

echo.
pause
