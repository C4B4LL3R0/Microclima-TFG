# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/sensors/rpi.py
 FUNCIÓN: Composición de los tres dispositivos reales del bus I2C1 de la Pi.
===============================================================================

 Esta clase es el adaptador entre el hardware y el contrato SensorReader. Abre
 un único bus I2C1 (SDA=GPIO2, SCL=GPIO3), inicializa BME280, SGP30 y DS3231 y
 combina sus resultados en una sola SensorReading.

 Filosofía de tolerancia a fallos:
   - Que un sensor falle no debe tumbar el servidor web: se conserva la lectura
     de los demás y se añade el error a la lista que ve el operador.
   - Si el bus completo no abre (por ejemplo, I2C deshabilitado), la aplicación
     sigue arrancando y muestra la causa en la interfaz, en lugar de entrar en
     un bucle de reinicio.
   - El RTC se sincroniza una vez al arrancar. Un fallo de permisos se registra
     y no impide monitorizar el clima.
===============================================================================
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .base import SensorError, SensorReading
from .bme280 import BME280
from .ds3231 import DS3231, RtcSyncResult
from .sgp30 import SGP30

LOGGER = logging.getLogger(__name__)


class RpiSensorArray:
    """Backend de producción con BME280 + SGP30 + DS3231 sobre I2C1."""

    def __init__(self, bus_number: int = 1) -> None:
        self._bus_number = bus_number
        self._bus: Any = None
        self._bme280: BME280 | None = None
        self._sgp30: SGP30 | None = None
        self._rtc: DS3231 | None = None
        self._initial_errors: list[str] = []
        self.rtc_sync_result: RtcSyncResult | None = None

        try:
            from smbus2 import SMBus  # import local para dar un error legible

            self._bus = SMBus(bus_number)
        except Exception as exc:  # ImportError, OSError, permisos, etc.
            message = (
                f"No se pudo abrir I2C{bus_number}: {exc}. "
                "Active I2C con raspi-config y compruebe que el usuario "
                "pertenece al grupo i2c."
            )
            LOGGER.error(message)
            self._initial_errors.append(message)
            return

        try:
            self._bme280 = BME280(self._bus, address=0x76)
            LOGGER.info("BME280 inicializado en I2C%d dirección 0x76.", bus_number)
        except Exception as exc:
            message = f"BME280 no disponible: {exc}"
            LOGGER.error(message)
            self._initial_errors.append(message)

        try:
            self._sgp30 = SGP30(self._bus, address=0x58)
            LOGGER.info("SGP30 inicializado en I2C%d dirección 0x58.", bus_number)
        except Exception as exc:
            message = f"SGP30 no disponible: {exc}"
            LOGGER.error(message)
            self._initial_errors.append(message)

        try:
            # DS3231 no comunica en el constructor; se valida al leer.
            self._rtc = DS3231(self._bus, address=0x68)
        except Exception as exc:
            message = f"DS3231 no disponible: {exc}"
            LOGGER.error(message)
            self._initial_errors.append(message)

    def sync_rtc_clock(self) -> RtcSyncResult:
        """Sincroniza el reloj Linux desde el DS3231 y conserva el diagnóstico."""
        if self._rtc is None:
            result = RtcSyncResult(
                False,
                None,
                None,
                "DS3231 no inicializado; no se puede sincronizar el reloj.",
            )
        else:
            result = self._rtc.sync_system_clock()
        self.rtc_sync_result = result
        return result

    def read(self) -> SensorReading:
        """Lee BME280 y SGP30 y compone una instantánea tolerante a fallos."""
        errors = list(self._initial_errors)
        temperature = humidity = pressure = None
        eco2 = tvoc = None
        warmup = False

        if self._bme280 is not None:
            try:
                sample = self._bme280.read()
                temperature = sample.temperature_c
                humidity = sample.humidity_pct
                pressure = sample.pressure_hpa
            except Exception as exc:
                message = f"Lectura BME280 fallida: {exc}"
                LOGGER.warning(message)
                errors.append(message)

        if self._sgp30 is not None:
            try:
                sample = self._sgp30.read()
                eco2 = float(sample.eco2_ppm)
                tvoc = float(sample.tvoc_ppb)
                warmup = sample.warmup
            except Exception as exc:
                message = f"Lectura SGP30 fallida: {exc}"
                LOGGER.warning(message)
                errors.append(message)

        return SensorReading(
            temperature=temperature,
            humidity=humidity,
            pressure=pressure,
            eco2=eco2,
            tvoc=tvoc,
            timestamp=datetime.now().astimezone(),
            source=f"I2C{self._bus_number} · BME280 0x76 + SGP30 0x58",
            errors=errors,
            warmup=warmup,
        )

    def close(self) -> None:
        """Cierra el bus I2C compartido y libera el descriptor del kernel."""
        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:
                LOGGER.exception("Error cerrando el bus I2C%d.", self._bus_number)
            finally:
                self._bus = None
