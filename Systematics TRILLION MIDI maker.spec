# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['trillion.py'],
    pathex=[],
    binaries=[],
    datas=[('CORE_FILES/assets', 'CORE_FILES/assets'), ('CORE_FILES/lang', 'CORE_FILES/lang'), ('CORE_FILES/icon.png', 'CORE_FILES'), ('CORE_FILES/icon.ico', 'CORE_FILES')],
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
    a.binaries,
    a.datas,
    [],
    name='Systematics TRILLION MIDI maker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['CORE_FILES/icon.ico'],
)
