@echo off
setlocal

set "PROJECT_DIR=%~dp0group_assignment_system"
set "VENV_ACTIVATE=%~dp0.venv\Scripts\activate.bat"

if not exist "%VENV_ACTIVATE%" (
    echo ERROR: SGAMS virtual environment not found at "%~dp0.venv".
    exit /b 1
)

if not exist "%PROJECT_DIR%\manage.py" (
    echo ERROR: SGAMS manage.py not found in "%PROJECT_DIR%".
    exit /b 1
)

call "%VENV_ACTIVATE%"
if errorlevel 1 (
    echo ERROR: Could not activate the SGAMS virtual environment.
    exit /b 1
)

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo ERROR: Could not navigate to the SGAMS Django project directory.
    exit /b 1
)

powershell -NoProfile -Command "$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse('127.0.0.1'), 8002); try { $listener.Start(); $listener.Stop(); exit 0 } catch { Write-Host 'ERROR: SGAMS cannot start because port 8002 is already in use on 127.0.0.1.'; exit 1 }"
if errorlevel 1 exit /b 1

python manage.py runserver 127.0.0.1:8002
