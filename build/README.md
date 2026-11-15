# Carpeta `build/` — Empaquetado y artefactos de PyInstaller

Esta carpeta no contiene código de ejecución: agrupa las recetas reproducibles
de PyInstaller y los productos intermedios del empaquetado.

| Ruta | Contenido |
|---|---|
| `microclima_sim.spec` | Receta `--onefile` para Kubuntu x86_64. Incluye `templates/` y `static/`. |
| `microclima_rpi.spec` | Receta `--onefile` para Raspberry Pi 3B. Añade `gpiozero`, `lgpio`, `smbus2` y el driver threading de Engine.IO. |
| `generated/` | Spec autogenerado por `build_executables.sh` (se crea al compilar). |
| `pyinstaller/sim/` | Directorio de trabajo temporal del build de simulación. |
| `pyinstaller/rpi/` | Directorio de trabajo temporal del build de la Pi. |

## Comandos

Desde la raíz del proyecto:

```bash
# Kubuntu: genera dist/microclima_sim
./build_executables.sh --sim-only

# Raspberry Pi 3B: genera dist/microclima_rpi
PYTHON_BIN=.venv/bin/python ./build_executables.sh --rpi-only

# Alternativa usando la receta directa
.venv/bin/python -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller/sim \
  build/microclima_sim.spec
```

Los ejecutables onefile llevan dentro `templates/` y `static/`. Al arrancar se
extraen en un directorio temporal (`sys._MEIPASS`) y Flask los localiza mediante
`src/microclima/web/app.py::_resource_base`.

`config.json` **no** se empaqueta: se crea junto al ejecutable en el primer
arranque para que sea persistente y editable. Si el directorio es de solo
lectura, use `MICROCLIMA_CONFIG=/var/lib/microclima/config.json`.
