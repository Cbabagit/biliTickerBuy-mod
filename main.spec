# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all, copy_metadata

datas = []
datas.append(("assets/*", "assets"))
datas.append(("pyproject.toml", "."))

# Collect all data from heavy dependencies + certifi
for pkg in ["gradio", "gradio_client", "safehttpx", "groovy"]:
    datas += collect_data_files(pkg)
datas += collect_data_files("certifi")
datas += copy_metadata("certifi")

project_root = os.path.abspath(".")

# Force-collect all needed modules with full tree
hiddenimports = []

for pkg in [
    "tyro", "starlette", "requests", "tinydb", "loguru",
    "gradio", "ntplib", "playsound3", "qrcode", "httpx",
    "yaml", "fastapi", "rich", "textual",
]:
    # Try collect_all first - includes binaries, datas and submodules
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        hiddenimports += pkg_hidden
    except Exception:
        hiddenimports.append(pkg)

# Collect our package tree
for mod in ["app_cmd", "task", "tab", "util", "interface", "cptoken"]:
    hiddenimports += collect_submodules(mod)

# httpx HTTP/2 support
hiddenimports += collect_submodules("h2")

a = Analysis(
    ["main.py"],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    module_collection_mode={
        "gradio": "py",
        "gradio_log": "py",
        "loguru": "py",
    },
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(project_root, "ssl_runtime_hook.py")],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="biliTickerBuy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.abspath("assets/icon.ico") if os.path.exists("assets/icon.ico") else None,
)
