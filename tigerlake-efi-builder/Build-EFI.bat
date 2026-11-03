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
if exist "main.py" (
    set "PROJ_DIR=%~dp0"
) else if exist "tigerlake-efi-builder\main.py" (
    set "PROJ_DIR=%~dp0tigerlake-efi-builder"
) else (
    echo [ERROR] Could not find main.py!
    pause
    exit /b 1
)

echo [1/3] Verifying Python dependencies...
"%PYTHON_EXE%" -m pip install -q -r "%PROJ_DIR%\requirements.txt"

echo.
echo [2/3] Executing TigerLake EFI Builder...
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
echo [3/3] Opening generated EFI folder in File Explorer...
if exist "%PROJ_DIR%\dist\EFI" (
    start "" explorer "%PROJ_DIR%\dist\EFI"
)

echo.
echo ============================================================
echo   SUCCESS! The EFI folder is generated and opened for you.
echo   Simply copy and paste the 'EFI' folder onto your USB.
echo ============================================================
echo.
pause
