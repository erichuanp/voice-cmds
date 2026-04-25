# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for voice-cmds.

Build with:
    conda activate voice-cmds
    pyinstaller voice-cmds.spec --clean --noconfirm

Output: dist/voice-cmds/voice-cmds.exe (+ _internal/ with deps)

Models (STT ~511MB + embedder ~96MB) are NOT bundled — they download to
./models/ next to the exe on first run.
"""
from PyInstaller.utils.hooks import (
    collect_dynamic_libs,
    collect_submodules,
    collect_data_files,
)

# Heavy deps that PyInstaller's auto-detection misses bits of
hidden_imports = []
hidden_imports += collect_submodules("sherpa_onnx")
hidden_imports += collect_submodules("sentence_transformers")
hidden_imports += collect_submodules("transformers")
hidden_imports += collect_submodules("tokenizers")
hidden_imports += collect_submodules("huggingface_hub")
hidden_imports += collect_submodules("safetensors")
hidden_imports += [
    "sounddevice",
    "soundfile",
    "win32com",
    "win32com.client",
    "winreg",
    "keyboard",
    "PySide6.QtSvg",
    "PySide6.QtNetwork",
]

# Native runtime DLLs for sherpa-onnx + onnxruntime
binaries = []
binaries += collect_dynamic_libs("sherpa_onnx")
binaries += collect_dynamic_libs("onnxruntime")
binaries += collect_dynamic_libs("torch")
binaries += collect_dynamic_libs("tokenizers")

# Data files for transformers / sentence_transformers (vocab files etc)
datas = []
datas += collect_data_files("sentence_transformers", include_py_files=False)
datas += collect_data_files("transformers", include_py_files=False)
datas += collect_data_files("sherpa_onnx", include_py_files=False)
# Bundle default config + sample script so first-run is functional
datas += [
    ("config/settings.json", "config"),
    ("config/apps.json", "config"),
    ("config/commands.json", "config"),
    ("scripts/del_des_png.bat", "scripts"),
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
        # Reduce bloat — these are pulled in transitively but unused.
        # Do NOT exclude stdlib modules (unittest, test, doctest, pydoc) —
        # torch.utils._config_module / transformers / others import them
        # at runtime and the app crashes on first embedder load.
        "matplotlib",
        "tkinter",
        "notebook",
        "IPython",
        "jupyter",
        "torchvision",
        "torchaudio",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="voice-cmds",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX often breaks Qt; keep off
    console=False,       # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="voice-cmds",
)
