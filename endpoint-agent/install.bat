@echo off
REM Enrolls this device and installs the Madire Endpoint Agent as a
REM protected Windows Service. Run from an elevated (Administrator)
REM Command Prompt, from the folder this was unzipped into.
REM Usage: install.bat <platform-url> <registration-code>

setlocal
if "%~1"=="" (
    echo Usage: install.bat ^<platform-url^> ^<registration-code^>
    exit /b 1
)
if "%~2"=="" (
    echo Usage: install.bat ^<platform-url^> ^<registration-code^>
    exit /b 1
)

set PLATFORM_URL=%~1
set REG_CODE=%~2
cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
echo Installing dependencies...
pip install -q -r requirements.txt || goto :error

if exist .device_identity.json (
    echo Already enrolled — skipping registration.
) else (
    python -m endpoint_agent.register --url "%PLATFORM_URL%" --code "%REG_CODE%" || goto :error
)

echo %PLATFORM_URL% > platform_url.txt

echo Installing the Windows Service (requires Administrator)...
python -m endpoint_agent.service install || goto :error
python -m endpoint_agent.service start || goto :error

echo.
echo Done. "Madire Endpoint Agent" is now running as a Windows Service —
echo check services.msc to see it. A standard user account cannot stop or
echo remove it; only an administrator can, via:
echo   python -m endpoint_agent.service stop
echo   python -m endpoint_agent.service remove
exit /b 0

:error
echo Setup failed — see the error above.
exit /b 1
