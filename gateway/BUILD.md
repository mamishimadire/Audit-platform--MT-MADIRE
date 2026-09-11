# Building MadireGateway.exe

The platform's "Download for Windows" button serves a pre-built
`dist/MadireGateway.exe` — it is not rebuilt per-request (PyInstaller
builds take ~2 minutes). Rebuild it whenever `gateway/gateway/*.py`
changes and the Windows package needs to pick that up.

```bash
cd gateway
.venv\Scripts\python.exe -m pip install pyinstaller pywin32
.venv\Scripts\python.exe .venv\Scripts\pywin32_postinstall.py -install   # first time only

.venv\Scripts\python.exe -m PyInstaller --onefile --name MadireGateway --console ^
  --hidden-import win32timezone ^
  --hidden-import pymysql ^
  --hidden-import pymssql ^
  --hidden-import psycopg ^
  --hidden-import psycopg_binary ^
  --collect-submodules sqlalchemy.dialects ^
  build_entry.py
```

Output: `gateway/dist/MadireGateway.exe`. `backend/app/services/gateway_download_service.py`
picks it up from that exact path automatically.

## Known limitation: not code-signed

This build is **not code-signed**. On a Windows machine with strict
endpoint security (Attack Surface Reduction rules blocking new/unsigned
executables, Smart App Control, an aggressively-configured antivirus),
`MadireGateway.exe` may be silently blocked from running at all — this is
the endpoint security correctly treating an unrecognized binary with
caution, not a bug in the build. Confirmed on this project's own
development machine: a freshly-built, unsigned exe (and even a bare copy of
a trusted system binary placed in a new location) was blocked from
executing entirely by the machine's managed endpoint policy, while
signed binaries running from their original trusted locations worked fine.

**The real fix is a code-signing certificate** (from a CA such as
DigiCert/Sectigo/SSL.com — standard OV certs run roughly $100–300/year,
EV certs get instant SmartScreen trust but cost more and require stricter
business verification). Until that's in place, `install.bat` and
`WINDOWS_README.md` tell the client's IT administrator how to allow it
once (SmartScreen "Run anyway", or an antivirus/ASR allow-list entry) —
the same one-time step needed for any new internal line-of-business
Windows application before it's signed.
