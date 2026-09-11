@echo off
REM Registers the Gateway and installs it as a Windows Service — run this
REM from an elevated (Administrator) Command Prompt, from the folder
REM MadireGateway.exe was unzipped into.
REM Usage: install.bat <platform-url> <registration-code>
REM Example: install.bat https://your-platform.example.com/api/v1 AT7F-9K2D-4B6M

setlocal
if "%~1"=="" (
    echo Usage: install.bat ^<platform-url^> ^<registration-code^>
    exit /b 1
)
if "%~2"=="" (
    echo Usage: install.bat ^<platform-url^> ^<registration-code^>
    exit /b 1
)

cd /d "%~dp0"

REM Browsers tag every download with "Mark of the Web" (a hidden
REM Zone.Identifier marking it as from the internet). Windows then refuses
REM to run an unsigned exe carrying that mark — the exact cause of
REM "Windows cannot access the specified device, path, or file" when
REM double-clicking it straight after unzipping. Unblock it before first use.
powershell -NoProfile -Command "Unblock-File -Path 'MadireGateway.exe'" >nul 2>&1

if exist .gateway_identity.json (
    echo Already registered — skipping registration.
) else (
    MadireGateway.exe register --url "%~1" --code "%~2" || goto :error
)

if not exist config.yaml (
    copy config.example.yaml config.yaml >nul
    echo.
    echo Created config.yaml — edit it now: set your database connection(s) and
    echo the connection_id shown on the platform's Data Sources screen.
    echo Then run install.bat again to install the service, or:
    echo   MadireGateway.exe service install
    echo   MadireGateway.exe service start
    exit /b 0
)

echo Installing the Windows Service...
MadireGateway.exe service install || goto :error
MadireGateway.exe service start || goto :error
echo.
echo Done. "Madire Audit Data Gateway" is now running as a Windows Service —
echo check services.msc, or Task Manager's Services tab, to see it.
exit /b 0

:error
echo.
echo Setup failed — see the error above. If Windows blocked MadireGateway.exe
echo from running at all ("this app can't run on your PC" / SmartScreen /
echo antivirus), see README.md's "First run on a managed corporate laptop"
echo section — your IT administrator needs to allow it once.
exit /b 1
