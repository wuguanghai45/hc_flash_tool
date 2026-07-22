# -*- mode: python ; coding: utf-8 -*-
import platform
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

esptool_datas = collect_data_files('esptool')
esptool_hiddenimports = collect_submodules('esptool')

jlink_library_by_platform = {
    'Windows': 'bin/win/JLink_x64.dll' if sys.maxsize > 2**32 else 'bin/win/JLinkARM.dll',
    'Darwin': 'bin/mac/libjlinkarm.dylib',
    'Linux': 'bin/linux/libjlinkarm.so',
}
jlink_library = jlink_library_by_platform.get(platform.system())
jlink_datas = (
    [(jlink_library, str(Path(jlink_library).parent))]
    if jlink_library and Path(jlink_library).is_file()
    else []
)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('bin/ST', 'bin/ST'),
        ('logo.svg', '.'),
    ] + jlink_datas + esptool_datas,
    hiddenimports=esptool_hiddenimports,
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
    name='main',
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
    icon='logo.ico',
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
    name='main',
)
# BUNDLE is a macOS-only target.  Keeping it conditional allows the same spec
# file to build the one-folder Windows package used by the GitHub Actions job.
if platform.system() == 'Darwin':
    app = BUNDLE(
        coll,
        name='main.app',
        icon='logo.ico',
        bundle_identifier='com.hc-embedded.flash-tool',
    )
