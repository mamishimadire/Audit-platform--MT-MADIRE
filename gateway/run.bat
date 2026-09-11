@echo off
REM One-shot Gateway installer for Windows.
REM Usage: run.bat <platform-url> <registration-code> [gateway-name]
REM Example: run.bat https://your-platform.example.com/api/v1 AT7F-9K2D-4B6M "Finance DB Gateway"

setlocal
if "%~1"=="" (
    echo Usage: run.bat ^<platform-url^> ^<registration-code^> [gateway-name]
    echo Get the platform URL and registration code from the Gateways screen.
    exit /b 1
)
if "%~2"=="" (
    echo Usage: run.bat ^<platform-url^> ^<registration-code^> [gateway-name]
    exit /b 1
)

set PLATFORM_URL=%~1
set REG_CODE=%~2
set GATEWAY_NAME=%~3

cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv || goto :error
)

call .venv\Scripts\activate.bat
echo Installing dependencies...
pip install -q -r requirements.txt || goto :error

if exist .gateway_identity.json (
    echo Already registered ^(.gateway_identity.json exists^) — skipping registration.
) else (
    echo Registering with the platform...
    if "%GATEWAY_NAME%"=="" (
        python -m gateway.register --url "%PLATFORM_URL%" --code "%REG_CODE%" || goto :error
    ) else (
        python -m gateway.register --url "%PLATFORM_URL%" --code "%REG_CODE%" --name "%GATEWAY_NAME%" || goto :error
    )
)

if not exist config.yaml (
    copy config.example.yaml config.yaml >nul
    echo.
    echo Created config.yaml — edit it now: set your database connection(s) and
    echo the connection_id shown on the platform's Data Sources screen.
    echo.
)

echo.
echo Setup complete. To run the Gateway:
echo   .venv\Scripts\activate
echo   python -m gateway.main --config config.yaml --loop
echo For production, schedule the one-pass form (no --loop) via Task Scheduler instead.
exit /b 0

:error
echo.
echo Setup failed — see the error above.
exit /b 1
