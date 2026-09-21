# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['win32timezone', 'pymysql', 'pymssql', 'psycopg', 'psycopg_binary', 'win32com.shell', 'win32com.shell.shell', 'win32com.shell.shellcon']
hiddenimports += collect_submodules('sqlalchemy.dialects')
hiddenimports += collect_submodules('pymongo')
hiddenimports += collect_submodules('bson')
hiddenimports += collect_submodules('dns')  # dnspython — required for mongodb+srv:// (Atlas) URIs
# Oracle (python-oracledb, thin mode), SAP HANA (hdbcli + sqlalchemy-hana) and Snowflake (connector + sqlalchemy dialect).
# The dialects and drivers are loaded by name at run time, which PyInstaller cannot see, so they are listed.
hiddenimports += ['oracledb', 'hdbcli', 'pyhdbcli', 'sqlalchemy_hana']
hiddenimports += collect_submodules('oracledb')
hiddenimports += collect_submodules('sqlalchemy_hana')
hiddenimports += collect_submodules('snowflake')


a = Analysis(
    ['build_entry.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MadireGateway',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
