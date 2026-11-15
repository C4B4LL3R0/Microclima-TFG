# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# ARCHIVO: build/microclima_rpi.spec
# FUNCIÓN: Receta PyInstaller reproducible del ejecutable de Raspberry Pi.
# =============================================================================
# Uso EN LA RASPBERRY PI (no se puede compilar cruzado desde x86):
#   python3 -m PyInstaller --noconfirm --clean \
#       --distpath dist --workpath build/pyinstaller/rpi build/microclima_rpi.spec
#
# Requiere gpiozero y lgpio instalados en el entorno que ejecuta PyInstaller.
# La receta incluye smbus2, el driver threading de Engine.IO, simple-websocket
# y los módulos de gpiozero que usan importación dinámica de pin factories.
# =============================================================================
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(ROOT / "src" / "microclima_rpi.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "templates"), "templates"),
        (str(ROOT / "static"), "static"),
    ],
    hiddenimports=[
        "engineio.async_drivers.threading",
        "simple_websocket",
        "smbus2",
        "gpiozero",
        "gpiozero.pins.lgpio",
        "lgpio",
    ],
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
    name="microclima_rpi",
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
)
