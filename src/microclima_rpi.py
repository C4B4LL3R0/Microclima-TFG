# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima_rpi.py
 EJECUTABLE: microclima_rpi (Raspberry Pi 3 Model B/B+, ARMv7/ARM64)
 FUNCIÓN: Punto de entrada de producción con sensores I2C y buzzer GPIO.
===============================================================================

 Secuencia de arranque:
   1. Configurar logging y leer config.json (o crearlo).
   2. Abrir I2C1, inicializar BME280 (0x76) y SGP30 (0x58).
   3. Sincronizar el reloj del sistema desde el DS3231 (0x68).
   4. Inicializar el buzzer activo en GPIO17 (pin físico 11).
   5. Arrancar Flask-SocketIO en 0.0.0.0:5000; en la red AP se accede por
      http://192.168.4.1:5000.

 Permisos recomendados:
   - El usuario debe pertenecer al grupo i2c.
   - Para ajustar el reloj: ejecutar con sudo o conceder CAP_SYS_TIME al
     ejecutable (ver README). Si no, el RTC se lee pero no se cambia la hora.

 Variables de entorno:
   MICROCLIMA_HOST, MICROCLIMA_PORT, MICROCLIMA_SAMPLE_INTERVAL,
   MICROCLIMA_CONFIG, MICROCLIMA_I2C_BUS, MICROCLIMA_BUZZER_PIN,
   MICROCLIMA_BUZZER_ACTIVE_LOW=1.
===============================================================================
"""

from __future__ import annotations

import logging
import os
import platform
import sys

from microclima.config import ConfigStore
from microclima.core.runtime import MicroclimaRuntime
from microclima.hardware.buzzer import create_hardware_buzzer
from microclima.logging_setup import configure_logging
from microclima.sensors.rpi import RpiSensorArray
from microclima.web.app import APP_VERSION, WebApplication

LOGGER = logging.getLogger(__name__)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    """Obtiene un float del entorno, con valor por defecto y acotación."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("%s='%s' no es numérico; se usa %s.", name, raw, default)
        return default
    return max(minimum, min(maximum, value))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Obtiene un entero del entorno, con validación de rango."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("%s='%s' no es entero; se usa %s.", name, raw, default)
        return default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool = False) -> bool:
    """Interpreta variables de entorno booleanas habituales."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "si", "sí", "on")


def main() -> int:
    """Inicializa hardware, runtime y servidor web de producción."""
    configure_logging()
    host = os.environ.get("MICROCLIMA_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = _env_int("MICROCLIMA_PORT", 5000, 1, 65535)
    interval = _env_float("MICROCLIMA_SAMPLE_INTERVAL", 1.0, 0.2, 60.0)
    bus_number = _env_int("MICROCLIMA_I2C_BUS", 1, 0, 20)
    buzzer_pin = _env_int("MICROCLIMA_BUZZER_PIN", 17, 0, 27)
    buzzer_active_high = not _env_bool("MICROCLIMA_BUZZER_ACTIVE_LOW", False)

    print(
        "\n"
        "============================================================\n"
        f" MONITOR DE MICROCLIMA EDGE · PRODUCCIÓN v{APP_VERSION}\n"
        " Conservación preventiva · Raspberry Pi 3B / I2C1\n"
        "============================================================\n"
        f" Plataforma: {platform.platform()}\n"
        f" Interfaz:   http://192.168.4.1:{port}\n"
        f" Sensores:   BME280 0x76 + SGP30 0x58 (I2C{bus_number})\n"
        f" RTC:        DS3231 0x68\n"
        f" Buzzer:     GPIO{buzzer_pin} (pin físico 11)\n"
        " Pulse Ctrl+C para detener el servidor.\n"
        "============================================================\n",
        flush=True,
    )

    try:
        config = ConfigStore()
        sensors = RpiSensorArray(bus_number=bus_number)
        rtc_result = sensors.sync_rtc_clock()
        buzzer = create_hardware_buzzer(
            pin=buzzer_pin,
            active_high=buzzer_active_high,
        )
        hardware_info = {
            "platform": platform.platform(),
            "sensor_backend": f"I2C{bus_number}: BME280 0x76 + SGP30 0x58",
            "rtc": rtc_result.message,
            "rtc_ok": rtc_result.success,
            "buzzer": buzzer.description,
            "gpio": f"GPIO{buzzer_pin} / pin físico 11",
        }
        runtime = MicroclimaRuntime(
            config_store=config,
            reader=sensors,
            buzzer_driver=buzzer,
            mode="rpi",
            simulator=None,
            sample_interval=interval,
            hardware_info=hardware_info,
        )
        web = WebApplication(runtime)
        web.run(host=host, port=port)
        return 0
    except Exception:
        LOGGER.exception("No se pudo arrancar microclima_rpi.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
