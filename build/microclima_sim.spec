# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# ARCHIVO: build/microclima_sim.spec
# FUNCIÓN: Receta PyInstaller reproducible del ejecutable de simulación.
# =============================================================================
# Uso desde la raíz del proyecto:
#   python3 -m PyInstaller --noconfirm --clean \
#       --distpath dist --workpath build/pyinstaller/sim build/microclima_sim.spec
#
# --onefile se representa con un único bloque EXE que incluye binarios y datos.
# --add-data "templates:templates" y "static:static" equivalen a las entradas
# datas de abajo. En Linux el separador origen:destino es dos puntos.
# =============================================================================
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(ROOT / "src" / "microclima_sim.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[
        (str(ROOT / "templates"), "templates"),
        (str(ROOT / "static"), "static"),
    ],
    hiddenimports=[
        "engineio.async_drivers.threading",
        "simple_websocket",
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
    name="microclima_sim",
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
