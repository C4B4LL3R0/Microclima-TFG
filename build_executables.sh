#!/usr/bin/env bash
# =============================================================================
# ARCHIVO: build_executables.sh
# PROYECTO: Monitor de Microclima Edge para Conservación Preventiva
# FUNCIÓN: Compilar microclima_sim (Kubuntu x86_64) y microclima_rpi (ARM).
# =============================================================================
#
# ¿Por qué no se compilan ambos en el PC?
#   PyInstaller NO es un compilador cruzado: genera un binario para el sistema
#   operativo y la arquitectura donde se ejecuta. El microclima_sim se compila
#   aquí, en Kubuntu x86_64. Para microclima_rpi hay que copiar el proyecto a la
#   Raspberry Pi 3B y ejecutar este mismo script con --rpi-only, o usar el spec
#   build/microclima_rpi.spec.
#
# Uso:
#   ./build_executables.sh              # auto: sim en x86; sim+rpi en ARM
#   ./build_executables.sh --sim-only   # solo microclima_sim
#   ./build_executables.sh --rpi-only   # solo microclima_rpi (debe ser ARM)
#   ./build_executables.sh --all        # ambos (debe ser ARM)
#
# Requisitos:
#   - Python 3 con Flask, Flask-SocketIO, simple-websocket, smbus2 y PyInstaller
#     (pip install -r requirements.txt).
#   - En Raspberry Pi, además gpiozero/lgpio para el buzzer GPIO17.
#
# Salidas:
#   dist/microclima_sim       ejecutable autónomo para Kubuntu
#   dist/microclima_rpi       ejecutable autónomo para Raspberry Pi (si ARM)
#   build/pyinstaller/...     artefactos intermedios de PyInstaller
#   build/generated/...       spec autogenerado por esta ejecución
# =============================================================================

set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_CMD="$PYTHON_BIN"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_CMD="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_CMD="python3"
fi

MODE="auto"
case "${1:-}" in
  ""|--auto) MODE="auto" ;;
  --sim-only) MODE="sim" ;;
  --rpi-only) MODE="rpi" ;;
  --all) MODE="all" ;;
  -h|--help)
    sed -n '1,42p' "$0"
    exit 0
    ;;
  *)
    echo "Opción desconocida: $1" >&2
    echo "Use --help para ver las opciones." >&2
    exit 2
    ;;
esac

ARCH="$(uname -m)"
IS_ARM=0
case "$ARCH" in
  armv7l|aarch64|arm64) IS_ARM=1 ;;
esac

log()  { printf '\n\033[1;36m[build]\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[aviso]\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Validaciones previas. Se comprueba también que existan los recursos offline;
# un ejecutable sin templates/static no serviría aunque PyInstaller terminase.
# ---------------------------------------------------------------------------
[[ -f "src/microclima_sim.py" && -f "src/microclima_rpi.py" ]] || \
  die "No se encuentran los puntos de entrada en src/."
[[ -f "templates/index.html" ]] || die "Falta templates/index.html."
[[ -f "static/js/socket.io.min.js" ]] || \
  die "Falta static/js/socket.io.min.js (Socket.IO debe servirse localmente)."
[[ -f "static/js/app.js" && -f "static/css/style.css" ]] || \
  die "Faltan recursos estáticos (app.js/style.css)."

if ! "$PYTHON_CMD" -c "import PyInstaller" >/dev/null 2>&1; then
  die "PyInstaller no está instalado en $PYTHON_CMD. Ejecute:
  $PYTHON_CMD -m pip install -r requirements.txt
  o defina PYTHON_BIN=/ruta/al/python."
fi

mkdir -p dist build/pyinstaller build/generated

# ---------------------------------------------------------------------------
# Compilación común. Los flags --add-data copian templates/ y static/ dentro del
# ejecutable onefile. PyInstaller los extrae en sys._MEIPASS al arrancar, y
# web/app.py localiza esa ruta para que Flask encuentre la interfaz offline.
# ---------------------------------------------------------------------------
build_sim() {
  log "Compilando microclima_sim para $ARCH..."
  "$PYTHON_CMD" -m PyInstaller \
    --noconfirm \
    --clean \
    --onefile \
    --name microclima_sim \
    --distpath "$ROOT_DIR/dist" \
    --workpath "$ROOT_DIR/build/pyinstaller/sim" \
    --specpath "$ROOT_DIR/build/generated" \
    --paths "$ROOT_DIR/src" \
    --add-data "$ROOT_DIR/templates:templates" \
    --add-data "$ROOT_DIR/static:static" \
    --hidden-import engineio.async_drivers.threading \
    --hidden-import simple_websocket \
    "$ROOT_DIR/src/microclima_sim.py"
  [[ -x "dist/microclima_sim" ]] || die "PyInstaller no generó dist/microclima_sim."
  log "microclima_sim listo: $(du -h dist/microclima_sim | cut -f1)"
  sha256sum dist/microclima_sim || true
}

build_rpi() {
  if [[ "$IS_ARM" -ne 1 ]]; then
    die "microclima_rpi no puede compilarse en $ARCH: PyInstaller no es un compilador cruzado.
Copie el proyecto a la Raspberry Pi 3B, instale requirements.txt y ejecute:
  PYTHON_BIN=.venv/bin/python ./build_executables.sh --rpi-only
También puede usar el spec:
  .venv/bin/python -m PyInstaller --noconfirm --clean \\
    --distpath dist --workpath build/pyinstaller/rpi build/microclima_rpi.spec"
  fi

  log "Compilando microclima_rpi para $ARCH..."
  local extra_imports=(
    --hidden-import gpiozero
    --hidden-import gpiozero.pins.lgpio
    --hidden-import lgpio
    --hidden-import smbus2
  )
  # RPi.GPIO es un respaldo opcional; se incluye solo si está instalado.
  if "$PYTHON_CMD" -c "import RPi.GPIO" >/dev/null 2>&1; then
    extra_imports+=(--hidden-import RPi.GPIO)
  fi

  "$PYTHON_CMD" -m PyInstaller \
    --noconfirm \
    --clean \
    --onefile \
    --name microclima_rpi \
    --distpath "$ROOT_DIR/dist" \
    --workpath "$ROOT_DIR/build/pyinstaller/rpi" \
    --specpath "$ROOT_DIR/build/generated" \
    --paths "$ROOT_DIR/src" \
    --add-data "$ROOT_DIR/templates:templates" \
    --add-data "$ROOT_DIR/static:static" \
    --hidden-import engineio.async_drivers.threading \
    --hidden-import simple_websocket \
    "${extra_imports[@]}" \
    "$ROOT_DIR/src/microclima_rpi.py"
  [[ -x "dist/microclima_rpi" ]] || die "PyInstaller no generó dist/microclima_rpi."
  log "microclima_rpi listo: $(du -h dist/microclima_rpi | cut -f1)"
  sha256sum dist/microclima_rpi || true
}

show_rpi_instructions() {
  cat <<EOF

=============================================================================
 microclima_sim compilado para $ARCH. Falta compilar microclima_rpi en la Pi
=============================================================================
PyInstaller no permite compilación cruzada. En la Raspberry Pi 3B:

  1) Copie el proyecto (sin dist/ ni .venv/ del PC):
       rsync -av --exclude dist --exclude .venv ./ pi@raspberrypi:~/microclima_edge/

  2) Instale dependencias (en la Pi, con Internet temporal o paquetes apt):
       cd ~/microclima_edge
       python3 -m venv .venv
       source .venv/bin/activate
       pip install -r requirements.txt

  3) Compile:
       PYTHON_BIN=.venv/bin/python ./build_executables.sh --rpi-only

  Alternativa con spec reproducible:
       .venv/bin/python -m PyInstaller --noconfirm --clean \\
         --distpath dist --workpath build/pyinstaller/rpi \\
         build/microclima_rpi.spec

  El resultado será dist/microclima_rpi. Copie config.json junto al ejecutable
  o deje que lo cree en el primer arranque. Para sincronizar el RTC, ejecute
  con sudo o conceda CAP_SYS_TIME (ver README.md).

=============================================================================
EOF
}

# ---------------------------------------------------------------------------
# Flujo principal. En x86 se compila solo sim; en ARM se compilan ambos.
# ---------------------------------------------------------------------------
case "$MODE" in
  sim)
    build_sim
    ;;
  rpi)
    build_rpi
    ;;
  all)
    build_sim
    build_rpi
    ;;
  auto)
    if [[ "$IS_ARM" -eq 1 ]]; then
      build_sim
      build_rpi
    else
      build_sim
      show_rpi_instructions
    fi
    ;;
esac

log "Proceso de empaquetado finalizado."
