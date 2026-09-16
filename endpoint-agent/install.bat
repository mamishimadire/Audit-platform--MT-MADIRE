@echo off
REM Enrolls this device (if not already) and installs/repairs the Madire
REM Endpoint Agent as a protected Windows Service. Run from an elevated
REM (Administrator) Command Prompt, from the folder this was unzipped into.
REM
REM Usage: install.bat <registration-code> [platform-url-override]
REM   The platform this build talks to is baked in at build time
REM   (endpoint_agent/deployment_config.py) — a registration code is
REM   normally the only thing you ever need to type. Only pass a second
REM   argument if you genuinely need to point this install at a different
REM   platform instance than the one this build shipped with.
REM
REM Re-running this on an already-enrolled device (e.g. after updating
REM deployment_config.py and rebuilding) re-points platform_url.txt at
REM the new URL and re-applies the Windows Service — it does not
REM re-register, since the device's identity already exists.

setlocal
if "%~1"=="" (
    echo Usage: install.bat ^<registration-code^> [platform-url-override]
    exit /b 1
)

set REG_CODE=%~1
set URL_OVERRIDE=%~2
cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
echo Installing dependencies...
pip install -q -r requirements.txt || goto :error

if "%URL_OVERRIDE%"=="" (
    for /f "delims=" %%U in ('python -c "from endpoint_agent import deployment_config; print(deployment_config.PLATFORM_URL)"') do set EFFECTIVE_URL=%%U
) else (
    set EFFECTIVE_URL=%URL_OVERRIDE%
)

if exist .device_identity.json (
    echo Already enrolled — updating the platform URL this install points at and re-applying the service.
) else (
    if "%URL_OVERRIDE%"=="" (
        python -m endpoint_agent.register --code "%REG_CODE%" || goto :error
    ) else (
        python -m endpoint_agent.register --url "%EFFECTIVE_URL%" --code "%REG_CODE%" || goto :error
    )
)

REM register.py already writes platform_url.txt using the same effective
REM URL on a fresh registration — this covers the "already enrolled, skip
REM registration" branch above, where that line never runs.
echo %EFFECTIVE_URL%> platform_url.txt

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
