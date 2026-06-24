# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Agent Manager."""

import sys
from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH)
SRC = ROOT / "src"
ASSETS = SRC / "ai_session_manager" / "assets"

a = Analysis(
    [str(SRC / "ai_session_manager" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(ASSETS), "ai_session_manager/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Platform-specific executable settings
is_win = sys.platform.startswith("win")
is_mac = sys.platform == "darwin"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Agent管理器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=is_mac,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico") if (is_win and (ROOT / "assets" / "icon.ico").exists()) else None,
)

# macOS app bundle
if is_mac:
    mac_icon = ROOT / "assets" / "icon.icns"
    if not mac_icon.exists():
        mac_icon = ROOT / "assets" / "icon.png"
    app = BUNDLE(
        exe,
        name="Agent管理器.app",
        icon=str(mac_icon) if mac_icon.exists() else None,
        bundle_identifier="com.aisessionmanager.app",
    )
