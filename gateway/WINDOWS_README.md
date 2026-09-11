# Madire Audit Data Gateway — Windows

A real Windows application — no Python install needed. `MadireGateway.exe`
runs inside your own network, connects to your database with read-only
credentials that never leave this machine, and only ever makes outbound
calls to the Madire platform.

## Install (run once, as Administrator)

1. Unzip this folder anywhere permanent (e.g. `C:\Program Files\Madire Gateway\`).
2. Open Command Prompt **as Administrator**, `cd` into that folder, then:

   ```
   install.bat https://your-platform.example.com/api/v1 AT7F-9K2D-4B6M
   ```

   (get the URL and registration code from the platform's Gateways screen —
   the code is single-use and expires in 15 minutes)

3. Edit the `config.yaml` it creates: add your database connection(s) and
   the `connection_id` shown on the platform's Data Sources screen.
4. Run `install.bat` again with the same arguments to install and start it
   as a Windows Service.

Once installed, **"Madire Audit Data Gateway" runs as a real Windows
Service** — visible in `services.msc` or Task Manager's Services tab. It
starts automatically on boot and keeps running after you log off. To stop,
remove, or reinstall it:

```
MadireGateway.exe service stop
MadireGateway.exe service remove
```

## If double-clicking MadireGateway.exe does nothing, or shows

**"Windows cannot access the specified device, path, or file"**

This is not a broken download — every browser tags a downloaded file as
coming "from the internet" (Mark of the Web), and Windows refuses to run an
unsigned exe carrying that tag until you unblock it once. `install.bat`
already does this automatically. If you're double-clicking the exe directly
instead of using `install.bat`, unblock it yourself first:

- Right-click `MadireGateway.exe` → **Properties** → tick **Unblock** at the
  bottom (next to "This file came from another computer...") → **OK**. Then
  double-click it again.
- Or in PowerShell: `Unblock-File .\MadireGateway.exe`

## If you still see a SmartScreen prompt or an antivirus block

On a strictly managed corporate laptop, that's your company's endpoint
security correctly treating a new, unsigned executable with caution — not a
bug. This build is not yet code-signed. Your IT administrator can allow it:

- Click **"More info" → "Run anyway"** if a SmartScreen dialog appears.
- Add `MadireGateway.exe` to your antivirus / Attack Surface Reduction
  allow-list (the same step you'd take for any new internal line-of-business
  application before code-signing is in place).

Once we obtain a code-signing certificate neither step will be necessary.

## Manual (no install.bat) usage

```
MadireGateway.exe register --url <platform-url> --code <code>
MadireGateway.exe run --loop          REM run in the foreground, for testing
MadireGateway.exe service install     REM install as a Windows Service
MadireGateway.exe service start
```
