# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\Administrator\\LLMProjects\\voice-cmds\\_ssl_diag\\diag.py'],
    pathex=[],
    binaries=[('C:\\ProgramData\\miniconda3\\envs\\voice-cmds\\Library\\bin\\libssl-3-x64.dll', '.'), ('C:\\ProgramData\\miniconda3\\envs\\voice-cmds\\Library\\bin\\libcrypto-3-x64.dll', '.')],
    datas=[],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='ssl_diag',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ssl_diag',
)
