# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src/autoclicker.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/icon.png', '.')],
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
    name='Autoclicker',
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
    icon=['assets/icon.icns'],
)
app = BUNDLE(
    exe,
    name='Autoclicker.app',
    icon='assets/icon.icns',
    bundle_identifier=None,
    info_plist={
        'LSUIElement': True, 
        'CFBundleName': 'Autoclicker',
        'CFBundleDisplayName': 'Autoclicker',
        'CFBundleShortVersionString': '1.0.0',
    }
)
