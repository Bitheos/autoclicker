# -*- mode: python ; coding: utf-8 -*-
# Autoclicker.spec — onedir, Python 3.13, sin firma Apple Developer ID

from PyInstaller.utils.hooks import collect_submodules

pynput_hidden = collect_submodules('pynput')

a = Analysis(
    ['src/autoclicker.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/icon.png',  '.'),
        ('assets/icon.icns', '.'),
    ],
    hiddenimports=pynput_hidden + [
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._darwin',
        'pynput.mouse',
        'pynput.mouse._darwin',
        'Quartz',
        'Quartz.CoreGraphics',
        'ApplicationServices',
        'Cocoa',
        'CoreFoundation',
        'PyObjCTools',
        'PyObjCTools.AppHelper',
        'objc',
        'objc._bridgesupport',
        'ctypes',
        'ctypes.util',
        # xml es requerido por objc._bridgesupport — NO excluir
        'xml',
        'xml.etree',
        'xml.etree.ElementTree',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # ⚠️  No excluir xml, html, email, urllib — pyobjc los usa internamente
    excludes=[
        'tkinter',
        'unittest',
        'pydoc',
        'doctest',
        'difflib',
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
    name='Autoclicker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.icns'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Autoclicker',
)

app = BUNDLE(
    coll,
    name='Autoclicker.app',
    icon='assets/icon.icns',
    bundle_identifier='com.personal.autoclicker',
    info_plist={
        'LSUIElement': True,
        'CFBundleName':               'Autoclicker',
        'CFBundleDisplayName':        'Autoclicker',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion':            '1',
        'CFBundlePackageType':        'APPL',
        'NSAppleEventsUsageDescription':
            'Autoclicker necesita Apple Events para detectar atajos de teclado.',
        'NSAccessibilityUsageDescription':
            'Autoclicker necesita Accesibilidad para controlar el mouse y el teclado.',
        'LSMinimumSystemVersion': '10.15',
    },
)
