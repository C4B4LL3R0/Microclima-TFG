# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/sensors/ds3231.py
 FUNCIÓN: Lectura del RTC DS3231 (0x68) y sincronización del reloj del sistema.
===============================================================================

 La Raspberry Pi 3B no incorpora RTC de batería. En una red de conservación
 aislada, sin NTP ni Internet, el reloj arranca con una fecha arbitraria hasta
 que el usuario la ajusta. El DS3231 mantiene la hora con una batería CR2032 y
 permite que los registros del monitor tengan una cronología fiable.

 Este módulo NO escribe la hora del sistema en el RTC: solo lee el RTC y, si la
 diferencia es significativa, ajusta el reloj Linux. Para poder hacerlo el
 proceso necesita privilegios (root o capacidad CAP_SYS_TIME). Si no los tiene,
 registra el aviso y el servidor sigue funcionando: nunca se debe impedir la
 monitorización ambiental por un problema de permisos horarios.

 Conexionado: VCC 3V3, GND, SDA GPIO2 (pin 3), SCL GPIO3 (pin 5), batería CR2032.
 Dirección I2C fija 0x68. Puede convivir en el bus con BME280 y SGP30.
===============================================================================
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .base import SensorError

LOGGER = logging.getLogger(__name__)

DS3231_ADDRESS = 0x68
REG_SECONDS = 0x00
REG_STATUS = 0x0F
OSF_BIT = 0x80
MIN_SYNC_DRIFT_SECONDS = 2.0


@dataclass(frozen=True)
class RtcSyncResult:
    """Resultado de un intento de sincronización, apto para logs y diagnóstico."""

    success: bool
    rtc_time: datetime | None
    drift_seconds: float | None
    message: str


def _bcd_to_int(value: int) -> int:
    """Convierte un byte BCD (p. ej. 0x59) a entero decimal (59)."""
    high = (value >> 4) & 0x0F
    low = value & 0x0F
    if high > 9 or low > 9:
        raise SensorError(f"DS3231: BCD inválido 0x{value:02X}")
    return high * 10 + low


class DS3231:
    """Driver de lectura del DS3231 sobre un bus smbus2 compartido."""

    def __init__(self, bus: Any, address: int = DS3231_ADDRESS) -> None:
        self._bus = bus
        self.address = address

    def oscillator_stopped(self) -> bool:
        """Indica si el bit OSF está activo (oscilador detenido / hora inválida).

        OSF (Oscillator Stop Flag) se activa si el DS3231 pierde alimentación
        principal y batería. Cuando está a 1 no es seguro usar la hora leída.
        """
        try:
            status = int(self._bus.read_byte_data(self.address, REG_STATUS))
        except OSError as exc:
            raise SensorError(f"DS3231: no se pudo leer el registro de estado ({exc})") from exc
        return bool(status & OSF_BIT)

    def read_datetime(self) -> datetime:
        """Lee fecha y hora del RTC y las devuelve con la zona local del sistema.

        El DS3231 guarda hora local (no UTC). Se le asigna la zona horaria local
        configurada en Raspberry Pi para que timestamp() sea coherente con los
        registros del monitor. Se soportan los modos de 12 y 24 horas.
        """
        if self.oscillator_stopped():
            raise SensorError(
                "DS3231: OSF activo; la hora del RTC no es fiable. "
                "Sustituya la batería y vuelva a ajustar el RTC."
            )

        try:
            data = self._bus.read_i2c_block_data(self.address, REG_SECONDS, 7)
        except OSError as exc:
            raise SensorError(f"DS3231: fallo leyendo la hora por I2C ({exc})") from exc

        seconds = _bcd_to_int(data[0] & 0x7F)
        minutes = _bcd_to_int(data[1] & 0x7F)
        hour_register = data[2]
        if hour_register & 0x40:  # Modo 12 h: bit 5 = PM
            hour_raw = _bcd_to_int(hour_register & 0x1F)
            if not 1 <= hour_raw <= 12:
                raise SensorError(f"DS3231: hora de 12 h inválida ({hour_raw})")
            hours = hour_raw % 12 + (12 if hour_register & 0x20 else 0)
        else:
            hours = _bcd_to_int(hour_register & 0x3F)

        day = _bcd_to_int(data[4] & 0x3F)
        month = _bcd_to_int(data[5] & 0x1F)
        century = 2000 if data[5] & 0x80 else 1900
        year = century + _bcd_to_int(data[6])

        try:
            local_tz = datetime.now().astimezone().tzinfo
            rtc_time = datetime(year, month, day, hours, minutes, seconds, tzinfo=local_tz)
        except ValueError as exc:
            raise SensorError(f"DS3231: fecha/hora imposible ({exc})") from exc

        if rtc_time.year < 2020:
            raise SensorError(
                f"DS3231: año {rtc_time.year} poco razonable; "
                "configure el RTC antes de usar el equipo."
            )
        return rtc_time

    def sync_system_clock(self, min_drift: float = MIN_SYNC_DRIFT_SECONDS) -> RtcSyncResult:
        """Ajusta el reloj Linux a la hora del DS3231 si la deriva supera min_drift.

        Estrategia:
          1. Leer RTC y comparar con time.time().
          2. Si la diferencia es pequeña, no tocar el reloj (evita saltos inútiles).
          3. Intentar time.clock_settime (sin procesos externos).
          4. Si no está disponible, usar `date -s @epoch` como respaldo.
          5. Nunca propagar una excepción al arranque: se devuelve un resultado
             diagnóstico y la monitorización continúa.

        Parámetros:
            min_drift: segundos mínimos de diferencia para aplicar el ajuste.
        """
        try:
            rtc_time = self.read_datetime()
        except SensorError as exc:
            LOGGER.warning("Sincronización RTC omitida: %s", exc)
            return RtcSyncResult(False, None, None, str(exc))

        rtc_epoch = rtc_time.timestamp()
        drift = rtc_epoch - time.time()
        if abs(drift) < min_drift:
            message = (
                f"Reloj del sistema ya sincronizado con DS3231 "
                f"(deriva {drift:+.1f} s)."
            )
            LOGGER.info(message)
            return RtcSyncResult(True, rtc_time, drift, message)

        # time.clock_settime(CLOCK_REALTIME, segundos) es la vía nativa de Linux
        # y evita depender del binario date. Requiere CAP_SYS_TIME.
        try:
            clock_settime = getattr(time, "clock_settime", None)
            if clock_settime is None:
                raise OSError("time.clock_settime no disponible")
            clock_settime(time.CLOCK_REALTIME, rtc_epoch)
        except (OSError, PermissionError) as exc:
            LOGGER.warning(
                "No se pudo ajustar el reloj con clock_settime (%s); "
                "se intenta date -s como respaldo.",
                exc,
            )
            try:
                subprocess.run(
                    ["date", "-s", f"@{rtc_epoch:.0f}"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError) as fallback_exc:
                message = (
                    "No se pudo sincronizar el reloj del sistema desde el DS3231 "
                    f"({fallback_exc}). Ejecute con sudo o conceda CAP_SYS_TIME."
                )
                LOGGER.error(message)
                return RtcSyncResult(False, rtc_time, drift, message)

        message = (
            f"Reloj sincronizado desde DS3231: {rtc_time.isoformat(timespec='seconds')} "
            f"(ajuste {drift:+.1f} s)."
        )
        LOGGER.info(message)
        return RtcSyncResult(True, rtc_time, drift, message)
