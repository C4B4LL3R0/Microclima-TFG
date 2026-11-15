# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/sensors/sgp30.py
 FUNCIÓN: Driver I2C del sensor de calidad de aire Sensirion SGP30 (0x58).
===============================================================================

 El SGP30 mide compuestos orgánicos volátiles totales (TVOC) y calcula un
 equivalente de CO2 (eCO2). No es un sensor NDIR: su valor de eCO2 es una
 estimación basada en TVOC y en su algoritmo de línea base. Por eso la alarma
 se interpreta como indicador de calidad de aire, no como medida metrológica de
 CO2.

 Protocolo:
   - Comandos de 16 bits sin registro: se envían como dos bytes MSB/LSB.
   - Cada palabra de 16 bits va seguida de un CRC-8 (polinomio 0x31, init 0xFF).
   - La medida se solicita con 0x2008 y tarda unos 12 ms.
   - Tras iaq_init (0x2003), el algoritmo necesita 15 s de calentamiento; hasta
     entonces los valores son preliminares y se marcan como warmup=True.

 Conexionado (bus I2C1): VIN 3V3, GND, SDA GPIO2 (pin 3), SCL GPIO3 (pin 5).
 Dirección fija 0x58.
===============================================================================
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .base import SensorError

LOGGER = logging.getLogger(__name__)

SGP30_ADDRESS = 0x58

CMD_IAQ_INIT = 0x2003
CMD_MEASURE_IAQ = 0x2008
MEASURE_DELAY_S = 0.012
WARMUP_SECONDS = 15.0


@dataclass(frozen=True)
class Sgp30Sample:
    """Medida de calidad de aire del SGP30."""

    eco2_ppm: int
    tvoc_ppb: int
    warmup: bool


class SGP30:
    """Driver del SGP30 sobre un bus smbus2 compartido.

    Parámetros:
        bus: bus I2C abierto (smbus2.SMBus).
        address: 0x58, dirección fija del sensor.
    """

    def __init__(self, bus: Any, address: int = SGP30_ADDRESS) -> None:
        self._bus = bus
        self.address = address
        self._started_monotonic = 0.0
        try:
            self._send_command(CMD_IAQ_INIT)
            time.sleep(0.010)
            self._started_monotonic = time.monotonic()
        except OSError as exc:
            raise SensorError(
                f"SGP30 0x{address:02X}: no responde al comando iaq_init ({exc})"
            ) from exc

    def _send_command(self, command: int) -> None:
        """Envía un comando de 16 bits MSB primero.

        Se usa i2c_msg.write para que el bus transmita exactamente dos bytes,
        sin el byte de cuenta que añadiría un block write SMBus. Es importante
        para respetar el protocolo propietario de Sensirion.
        """
        from smbus2 import i2c_msg  # import local: smbus2 solo existe en la Pi

        message = i2c_msg.write(self.address, [command >> 8, command & 0xFF])
        self._bus.i2c_rdwr(message)

    def _read_words(self, count: int) -> list[int]:
        """Lee `count` palabras de 16 bits con sus CRC desde el sensor.

        Una lectura I2C pura no envía un registro previo; el SGP30 devuelve el
        resultado de la última orden de medida. Se valida cada CRC y se lanza
        SensorError si hay corrupción, en lugar de aceptar datos erróneos.
        """
        from smbus2 import i2c_msg

        total_bytes = count * 3
        message = i2c_msg.read(self.address, total_bytes)
        self._bus.i2c_rdwr(message)
        data = list(message)
        words: list[int] = []
        for index in range(count):
            msb = data[index * 3]
            lsb = data[index * 3 + 1]
            received_crc = data[index * 3 + 2]
            expected_crc = self._crc8((msb, lsb))
            if received_crc != expected_crc:
                raise SensorError(
                    f"SGP30: CRC inválido en palabra {index} "
                    f"(recibido 0x{received_crc:02X}, esperado 0x{expected_crc:02X})"
                )
            words.append((msb << 8) | lsb)
        return words

    @staticmethod
    def _crc8(data: tuple[int, int]) -> int:
        """CRC-8 Sensirion: polinomio x^8+x^5+x^4+1 (0x31), init 0xFF."""
        crc = 0xFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x31) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    def read(self) -> Sgp30Sample:
        """Solicita una medida IAQ y devuelve eCO2, TVOC y estado de calentamiento."""
        try:
            self._send_command(CMD_MEASURE_IAQ)
            time.sleep(MEASURE_DELAY_S)
            words = self._read_words(2)
        except OSError as exc:
            raise SensorError(f"SGP30: fallo de comunicación I2C ({exc})") from exc

        eco2, tvoc = words
        # El datasheet define eCO2 en 400..60000 ppm y TVOC en 0..60000 ppb.
        # Se admite 0 como lectura transitoria de arranque, pero se rechazan los
        # valores negativos o superiores al fondo de escala para detectar tramas
        # corruptas no cubiertas por el CRC (por ejemplo, un sensor mal alimentado).
        if not 0 <= eco2 <= 60000:
            raise SensorError(f"SGP30: eCO2 inválido ({eco2} ppm)")
        if not 0 <= tvoc <= 60000:
            raise SensorError(f"SGP30: TVOC inválido ({tvoc} ppb)")

        warmup = (time.monotonic() - self._started_monotonic) < WARMUP_SECONDS
        return Sgp30Sample(eco2_ppm=int(eco2), tvoc_ppb=int(tvoc), warmup=warmup)
