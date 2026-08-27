# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
ocr_data = collect_data_files("rapidocr_onnxruntime")
runtime_dlls = sorted((root / "runtime-compat").glob("*.dll"))
runtime_names = {path.name.casefold() for path in runtime_dlls}

a = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=ocr_data,
    hiddenimports=[
        "backports",
        "backports.tarfile",
        "rapidocr_onnxruntime",
        "onnxruntime",
        "pywinauto",
        "pywinauto.controls.uiawrapper",
        "pystray._win32",
        "PIL._tkinter_finder",
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "pandas", "scipy"],
    noarchive=False,
    optimize=1,
)
# PyInstaller otherwise copies whichever VC++ runtime is installed on the build
# host. ONNX Runtime 1.22.1 is validated with the pinned redistributable files.
a.binaries = [
    entry for entry in a.binaries if Path(entry[0]).name.casefold() not in runtime_names
]
a.binaries += [(path.name, str(path), "BINARY") for path in runtime_dlls]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MiaoxiangComputerAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(root / "assets" / "miaoxiang.ico"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
)
