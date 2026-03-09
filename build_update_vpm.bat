@echo off
setlocal

rem Simple builder for update_vpm / update_vpm_gui (PyInstaller)
rem Usage: double-click this .bat or run from PowerShell/cmd.

cd /d "%~dp0"

echo [1/3] Installing / updating PyInstaller...
py -m pip install --upgrade pyinstaller >nul 2>nul
if errorlevel 1 goto pip_error

echo [2/3] Building update_vpm (CLI)...
py -m PyInstaller --onefile --name update_vpm update_vpm.py
if errorlevel 1 goto build_error

echo [3/3] Building update_vpm_gui (GUI)...
py -m PyInstaller --onefile --noconsole --name update_vpm_gui update_vpm_gui.py
if errorlevel 1 goto build_error

echo.
echo Build finished. See dist\update_vpm.exe and dist\update_vpm_gui.exe
goto :eof

:pip_error
echo Failed to install PyInstaller. Check that Python and pip are on PATH.
pause
goto :eof

:build_error
echo Build failed.
pause
goto :eof

