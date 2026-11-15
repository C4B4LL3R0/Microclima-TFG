# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima_sim.py
 EJECUTABLE: microclima_sim (Kubuntu x86_64 / cualquier PC sin hardware)
 FUNCIÓN: Punto de entrada del entorno de pruebas y simulación.
===============================================================================

 Este ejecutable:
   - Usa un generador de medidas en lugar de BME280/SGP30.
   - Emula el buzzer dibujando los pitidos en la consola.
   - Habilita el Panel de Inyección Manual (sliders y campos numéricos).
   - Comparte el 100 % de la lógica de alarmas, perfiles y web con la Pi.

 Uso:
   python3 src/microclima_sim.py
   MICROCLIMA_PORT=5000 python3 src/microclima_sim.py
   ./dist/microclima_sim

 Variables de entorno:
   MICROCLIMA_HOST              (0.0.0.0 por defecto)
   MICROCLIMA_PORT              (5000)
   MICROCLIMA_SAMPLE_INTERVAL   (1.0 segundos)
   MICROCLIMA_CONFIG            (ruta alternativa de config.json)
===============================================================================
"""

from __future__ import annotations

import logging
import os
import platform
import sys

from microclima.config import ConfigStore
from microclima.core.runtime import MicroclimaRuntime
from microclima.hardware.buzzer import ConsoleBuzzer
from microclima.logging_setup import configure_logging
from microclima.sensors.simulator import SimulatedSensorArray
from microclima.web.app import APP_VERSION, WebApplication

LOGGER = logging.getLogger(__name__)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    """Lee un float del entorno y lo acota, avisando si el valor no es válido."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("%s='%s' no es numérico; se usa %s.", name, raw, default)
        return default
    if value < minimum or value > maximum:
        LOGGER.warning(
            "%s=%s fuera de [%s, %s]; se acota.", name, value, minimum, maximum
        )
    return max(minimum, min(maximum, value))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Lee un entero del entorno con validación de rango."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("%s='%s' no es entero; se usa %s.", name, raw, default)
        return default
    return max(minimum, min(maximum, value))


def main() -> int:
    """Construye el runtime simulado, arranca la web y espera hasta Ctrl+C."""
    configure_logging()
    host = os.environ.get("MICROCLIMA_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = _env_int("MICROCLIMA_PORT", 5000, 1, 65535)
    interval = _env_float("MICROCLIMA_SAMPLE_INTERVAL", 1.0, 0.2, 60.0)

    print(
        "\n"
        "============================================================\n"
        f" MONITOR DE MICROCLIMA EDGE · SIMULACIÓN v{APP_VERSION}\n"
        " Conservación preventiva · Panel de pruebas para Kubuntu\n"
        "============================================================\n"
        f" Plataforma: {platform.platform()}\n"
        f" Interfaz:   http://127.0.0.1:{port}\n"
        f" Muestreo:   cada {interval:.1f} s\n"
        " Buzzer:     emulación visual en esta consola\n"
        " Los sliders manuales están en el Panel de Inyección de la web.\n"
        " Pulse Ctrl+C para detener el servidor.\n"
        "============================================================\n",
        flush=True,
    )

    try:
        config = ConfigStore()
        simulator = SimulatedSensorArray(config.selected_profile)
        buzzer = ConsoleBuzzer()
        hardware_info = {
            "platform": platform.platform(),
            "sensor_backend": "Simulador interno (sin I2C)",
            "rtc": "No aplica en simulación",
            "buzzer": buzzer.description,
            "gpio": "No utiliza GPIO",
        }
        runtime = MicroclimaRuntime(
            config_store=config,
            reader=simulator,
            buzzer_driver=buzzer,
            mode="sim",
            simulator=simulator,
            sample_interval=interval,
            hardware_info=hardware_info,
        )
        web = WebApplication(runtime)
        web.run(host=host, port=port)
        return 0
    except Exception:
        LOGGER.exception("No se pudo arrancar microclima_sim.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
