# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for voice-cmds.

Build with:
    conda activate voice-cmds
    pyinstaller voice-cmds.spec --clean --noconfirm

Output: dist/voice-cmds/voice-cmds.exe (+ _internal/ with deps)

Models (STT ~280MB + embedder ONNX ~95MB) are NOT bundled — they download to
./models/ next to the exe on first run.

The embedder runs through `tokenizers` + a minimal ctypes binding to the
onnxruntime.dll sherpa-onnx already ships for STT (voice_cmds/ort_ffi.py) —
no torch, no separate onnxruntime Python package.
"""
from PyInstaller.utils.hooks import (
    collect_dynamic_libs,
    collect_submodules,
    collect_data_files,
)

import sys

# Heavy deps that PyInstaller's auto-detection misses bits of
hidden_imports = []
hidden_imports += collect_submodules("sherpa_onnx")
hidden_imports += collect_submodules("tokenizers")
hidden_imports += collect_submodules("pypinyin")
hidden_imports += [
    "sounddevice",
]
if sys.platform == "win32":
    hidden_imports += ["win32com", "win32com.client", "winreg"]

# Native runtime DLLs for sherpa-onnx + tokenizers. (The embedder drives
# sherpa's bundled onnxruntime.dll through the C API — no separate
# onnxruntime package.)
binaries = []
binaries += collect_dynamic_libs("sherpa_onnx")
binaries += collect_dynamic_libs("tokenizers")

# Data files for sherpa-onnx
datas = []
datas += collect_data_files("sherpa_onnx", include_py_files=False)
# Bundle default config + sample script so first-run is functional
datas += [
    ("config/settings.json", "config"),
    ("config/apps.json", "config"),
    ("config/commands.json", "config"),
    ("config/tasks.json", "config"),
    ("scripts/.keep", "scripts"),
    ("README.md", "."),
    ("DESIGN.md", "."),
]


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Reduce bloat. onnxruntime's optional training modules statically
        # reference torch/transformers/sklearn/scipy (modulegraph records the
        # try/except imports even though they never execute) — excluding them
        # is safe because the app only uses onnxruntime's CPU inference.
        "matplotlib",
        "tkinter",
        "notebook",
        "IPython",
        "jupyter",
        "torch",
        "torchvision",
        "torchaudio",
        "torchtext",
        "torchao",
        "transformers",
        "sentence_transformers",
        "safetensors",
        "huggingface_hub",
        "hf_xet",
        "sklearn",
        "scipy",
        "win32ui",      # Pythonwin — only the makepy/combrowse dev tools use it
        "Pythonwin",
    ],
    noarchive=False,
    optimize=0,
)

# --- Slim the bundle -----------------------------------------------------
# hook-PySide6 collects every Qt6 module / plugin / translation shipped in
# the package, but the app only uses QtCore/QtGui/QtWidgets with the native
# platform plugin (no QML/Pdf/OpenGL/Network/Svg/VirtualKeyboard, no
# QTranslator — Qt standard strings stay English as shipped). Also drop
# Pythonwin's win32ui.pyd+mfc140u.dll (only the makepy/combrowse dev tools
# need them) and dbghelp.dll (a stray system copy — nothing imports it).
#
# Windows DLLs are filtered by basename. macOS ships Qt as frameworks under
# PySide6/Qt/lib/*.framework — matched by name fragment, and plugins are
# left alone (paths differ per Qt build; the cocoa plugin is essential).
import sys as _sys_spec

if _sys_spec.platform == "darwin":
    _BAD_FRAMEWORKS = (
        "qtquick", "qtqml", "qtpdf", "qtopengl", "qtnetwork", "qtsvg",
        "qtvirtualkeyboard",
    )

    def _drop_binary(name: str) -> bool:
        p = name.lower().replace("\\", "/")
        return any(bad in p for bad in _BAD_FRAMEWORKS)

    a.binaries = [b for b in a.binaries if not _drop_binary(b[0])]
    # Drop unused framework data dirs the same way.
    a.datas = [
        d for d in a.datas
        if not any(bad in d[0].lower().replace("\\", "/")
                   for bad in _BAD_FRAMEWORKS)
    ]
else:
    _DROP_BINARIES = {
        "opengl32sw.dll",  # software-GL last resort; RHI/D3D11 always works on Win10/11
        "Qt6Quick.dll",
        "Qt6Qml.dll",
        "Qt6QmlModels.dll",
        "Qt6QmlMeta.dll",
        "Qt6QmlWorkerScript.dll",
        "Qt6Pdf.dll",
        "Qt6OpenGL.dll",
        "Qt6Network.dll",
        "QtNetwork.pyd",
        "Qt6Svg.dll",
        "QtSvg.pyd",
        "Qt6VirtualKeyboard.dll",
        "dbghelp.dll",
        "win32ui.pyd",
        "mfc140u.dll",
    }
    a.binaries = [
        b for b in a.binaries
        if b[0].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] not in _DROP_BINARIES
    ]

    def _keep_binary(name: str) -> bool:
        p = name.lower().replace("\\", "/")
        if not p.startswith("pyside6/plugins/"):
            return True
        return p in {
            "pyside6/plugins/platforms/qwindows.dll",
            "pyside6/plugins/styles/qmodernwindowsstyle.dll",
            "pyside6/plugins/imageformats/qico.dll",
        }

    a.binaries = [b for b in a.binaries if _keep_binary(b[0])]


def _keep_data(name: str) -> bool:
    p = name.lower().replace("\\", "/")
    # No QTranslator is installed, so all Qt translation .qm files are dead
    # weight (~6 MB). All app strings are hardcoded Chinese already.
    return not p.startswith("pyside6/translations/")


a.datas = [d for d in a.datas if _keep_data(d[0])]

# --- Force the env's OpenSSL (the build matching _ssl.pyd) ---------------
# Windows conda keeps the correct OpenSSL in <prefix>/Library/bin, but
# PyInstaller's DLL scanner can miss it or pick up stale/mismatched copies
# from elsewhere — a frozen app then fails every HTTPS download with
# "Can't connect to HTTPS URL because the SSL module is not available".
# Drop any libssl/libcrypto the analysis found and always bundle these.
import os as _os

_ssl_dlls = {}
if _sys_spec.platform == "win32":
    for _name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll"):
        _p = _os.path.join(_sys_spec.prefix, "Library", "bin", _name)
        if _os.path.exists(_p):
            _ssl_dlls[_name] = _p
if _ssl_dlls:
    a.binaries = [b for b in a.binaries if b[0] not in _ssl_dlls]
    for _name, _p in _ssl_dlls.items():
        a.binaries.append((_name, _p, "BINARY"))

pyz = PYZ(a.pure)

exe_kwargs = {
    "exclude_binaries": True,
    "name": "voice-cmds",
    "debug": False,
    "bootloader_ignore_signals": False,
    "strip": False,
    "upx": False,           # UPX often breaks Qt; keep off
    "console": False,       # GUI app — no console window
    "disable_windowed_traceback": False,
    "argv_emulation": False,
    "target_arch": None,
    "codesign_identity": None,
    "entitlements_file": None,
}
if sys.platform == "win32":
    exe_kwargs["icon"] = "assets/app.ico"

exe = EXE(pyz, a.scripts, [], **exe_kwargs)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="voice-cmds",
)
