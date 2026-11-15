# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/sensors/bme280.py
 FUNCIÓN: Driver I2C directo del sensor Bosch BME280 (0x76) para Raspberry Pi.
===============================================================================

 ¿Por qué un driver directo y no una librería de alto nivel?
   - El proyecto se compila con PyInstaller y debe funcionar sin conexión. Un
     driver propio con smbus2 reduce dependencias y deja explícito en la memoria
     del TFG el procedimiento de compensación del datasheet.
   - Se controlan los tiempos de espera y los errores I2C de forma didáctica.

 Conexionado (Raspberry Pi 3B, bus I2C1):
   - VIN  -> 3V3 (pin 1)          - GND -> GND (pin 6)
   - SDA  -> GPIO2 / SDA1 (pin 3) - SCL -> GPIO3 / SCL1 (pin 5)
   - Dirección 0x76 si SDO está a GND; 0x77 si SDO está a 3V3.

 El BME280 entrega temperatura, humedad y presión. La compensación utiliza los
 coeficientes de calibración grabados en su OTP. Se implementan las fórmulas
 enteras oficiales de Bosch; `_cdiv` reproduce la división truncada hacia cero
 del lenguaje C (Python // redondea hacia abajo y daría diferencias negativas).

La presión se calcula con la variante oficial de 64 bits, que devuelve el
resultado en formato Q24.8 (24 bits enteros y 8 fraccionarios). Es la única que
conserva la precisión de las centésimas de hPa que muestra la interfaz; la
variante de 32 bits trunca a pascales enteros y acumula un error de ~0,13 hPa
en el vector de calibración del datasheet (1006,40 frente a 1006,5325 hPa).
===============================================================================
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .base import SensorError

LOGGER = logging.getLogger(__name__)

# Dirección I2C por defecto del BME280 cuando SDO se conecta a GND.
BME280_ADDRESS = 0x76

# Registros principales del BME280 (ver datasheet, tabla de mapas de memoria).
REG_CALIB_00 = 0x88       # Inicio de calibración de temperatura y presión.
REG_CHIP_ID = 0xD0
REG_RESET = 0xE0
REG_CALIB_26 = 0xE1       # Inicio de calibración de humedad.
REG_CTRL_HUM = 0xF2
REG_STATUS = 0xF3
REG_CTRL_MEAS = 0xF4
REG_CONFIG = 0xF5
REG_DATA = 0xF7           # press[3], temp[3], hum[2]

CHIP_ID_BME280 = 0x60


def _cdiv(numerator: int, denominator: int) -> int:
    """División entera con truncamiento hacia cero, como el operador C.

    Las fórmulas del datasheet están escritas en C. Para números negativos,
    Python // se comporta distinto; esta función evita desviaciones en presión.
    """
    if denominator == 0:
        raise ZeroDivisionError("división entera por cero")
    if (numerator >= 0) == (denominator >= 0):
        return numerator // denominator
    return -((-numerator) // denominator)


@dataclass(frozen=True)
class Bme280Sample:
    """Resultado validado de una conversión completa del BME280."""

    temperature_c: float
    humidity_pct: float
    pressure_hpa: float


class BME280:
    """Driver mínimo y explícito del BME280 sobre un bus smbus2 compartido.

    Parámetros:
        bus: instancia abierta de smbus2.SMBus. Se comparte con el SGP30 y el
             DS3231 para no abrir tres descriptores del mismo bus.
        address: 0x76 o 0x77 según la conexión del pin SDO.
    """

    def __init__(self, bus: Any, address: int = BME280_ADDRESS) -> None:
        self._bus = bus
        self.address = address
        self._dig_t1 = self._dig_t2 = self._dig_t3 = 0
        self._dig_p1 = self._dig_p2 = self._dig_p3 = self._dig_p4 = 0
        self._dig_p5 = self._dig_p6 = self._dig_p7 = self._dig_p8 = self._dig_p9 = 0
        self._dig_h1 = self._dig_h2 = self._dig_h3 = 0
        self._dig_h4 = self._dig_h5 = self._dig_h6 = 0
        self._t_fine = 0

        try:
            self._verify_chip()
            self._soft_reset()
            self._read_calibration()
            self._configure()
        except OSError as exc:
            raise SensorError(
                f"BME280 0x{address:02X}: error de comunicación I2C ({exc})"
            ) from exc

    # ------------------------------------------------------------- inicialización
    def _read_byte(self, register: int) -> int:
        """Lee un byte del registro indicado mediante SMBus."""
        return int(self._bus.read_byte_data(self.address, register))

    def _verify_chip(self) -> None:
        """Comprueba el ID de chip para no aplicar calibración de otro sensor."""
        chip_id = self._read_byte(REG_CHIP_ID)
        if chip_id != CHIP_ID_BME280:
            raise SensorError(
                f"BME280 0x{self.address:02X}: chip ID 0x{chip_id:02X}, "
                f"se esperaba 0x{CHIP_ID_BME280:02X}. "
                "¿Es un BMP280 (sin humedad) o la dirección es incorrecta?"
            )

    def _soft_reset(self) -> None:
        """Aplica el reset por software y espera el tiempo de arranque del datasheet."""
        self._bus.write_byte_data(self.address, REG_RESET, 0xB6)
        time.sleep(0.01)

    def _read_calibration(self) -> None:
        """Lee y descodifica los coeficientes OTP de temperatura, presión y humedad.

        Los registros 0x88..0xA1 son 26 bytes consecutivos. Los coeficientes son
        enteros little-endian con signo excepto T1, P1 y H1..H3. H4 y H5 son
        campos de 12 bits con signo ensamblados a partir de 0xE4/0xE5/0xE6.
        """
        calib = self._bus.read_i2c_block_data(self.address, REG_CALIB_00, 26)

        def u16(offset: int) -> int:
            return calib[offset] | (calib[offset + 1] << 8)

        def s16(offset: int) -> int:
            value = u16(offset)
            return value - 0x10000 if value & 0x8000 else value

        self._dig_t1 = u16(0x88 - REG_CALIB_00)
        self._dig_t2 = s16(0x8A - REG_CALIB_00)
        self._dig_t3 = s16(0x8C - REG_CALIB_00)
        self._dig_p1 = u16(0x8E - REG_CALIB_00)
        self._dig_p2 = s16(0x90 - REG_CALIB_00)
        self._dig_p3 = s16(0x92 - REG_CALIB_00)
        self._dig_p4 = s16(0x94 - REG_CALIB_00)
        self._dig_p5 = s16(0x96 - REG_CALIB_00)
        self._dig_p6 = s16(0x98 - REG_CALIB_00)
        self._dig_p7 = s16(0x9A - REG_CALIB_00)
        self._dig_p8 = s16(0x9C - REG_CALIB_00)
        self._dig_p9 = s16(0x9E - REG_CALIB_00)
        self._dig_h1 = calib[0xA1 - REG_CALIB_00]

        hum_calib = self._bus.read_i2c_block_data(self.address, REG_CALIB_26, 7)
        # hum_calib[0:2] = H2, [2] = H3, [3:6] = H4/H5, [6] = H6
        h2_raw = hum_calib[0] | (hum_calib[1] << 8)
        self._dig_h2 = h2_raw - 0x10000 if h2_raw & 0x8000 else h2_raw
        self._dig_h3 = hum_calib[2]
        h4 = (hum_calib[3] << 4) | (hum_calib[4] & 0x0F)
        self._dig_h4 = h4 - 0x1000 if h4 & 0x0800 else h4
        h5 = (hum_calib[5] << 4) | (hum_calib[4] >> 4)
        self._dig_h5 = h5 - 0x1000 if h5 & 0x0800 else h5
        self._dig_h6 = hum_calib[6] - 0x100 if hum_calib[6] & 0x80 else hum_calib[6]

        if self._dig_p1 == 0:
            raise SensorError("BME280: coeficiente de calibración P1 inválido (0)")

    def _configure(self) -> None:
        """Configura sobremuestreo x1, modo normal y filtro desactivado.

        El orden importa: el datasheet exige escribir ctrl_hum antes que
        ctrl_meas para que el sobremuestreo de humedad quede registrado.
        """
        self._bus.write_byte_data(self.address, REG_CONFIG, 0x00)
        self._bus.write_byte_data(self.address, REG_CTRL_HUM, 0x01)
        self._bus.write_byte_data(self.address, REG_CTRL_MEAS, 0x27)  # temp/pres x1
        time.sleep(0.01)

    # ------------------------------------------------------------------ lectura
    def _read_raw(self) -> tuple[int, int, int]:
        """Lee los 8 bytes de datos y extrae ADC de presión, temperatura y humedad."""
        data = self._bus.read_i2c_block_data(self.address, REG_DATA, 8)
        adc_p = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        adc_t = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
        adc_h = (data[6] << 8) | data[7]
        return adc_p, adc_t, adc_h

    def _compensate_temperature(self, adc_t: int) -> float:
        """Compensa temperatura y actualiza t_fine (necesario para P y H)."""
        var1 = ((((adc_t >> 3) - (self._dig_t1 << 1))) * self._dig_t2) >> 11
        var2 = (
            (
                (((adc_t >> 4) - self._dig_t1) * ((adc_t >> 4) - self._dig_t1))
                >> 12
            )
            * self._dig_t3
        ) >> 14
        self._t_fine = var1 + var2
        return ((self._t_fine * 5 + 128) >> 8) / 100.0

    def _compensate_pressure(self, adc_p: int) -> float:
        """Aplica la fórmula oficial de presión de 64 bits y devuelve hPa.

        Se transcribe literalmente `bme280_compensate_P_int64` del datasheet de
        Bosch. Todas las variables son enteros de 64 bits; en Python no hay
        desbordamiento, pero se conservan los desplazamientos y la división
        truncada para obtener exactamente el mismo resultado que el driver C.

        El valor devuelto por Bosch está en Q24.8: 24 bits enteros y 8 bits
        fraccionarios. Por eso se divide entre 256 para pasar a pascales y entre
        100 para expresar la salida en hectopascales.
        """
        # var1 y var2 acumulan los términos de calibración de presión.
        var1 = self._t_fine - 128000
        var2 = var1 * var1 * self._dig_p6
        var2 = var2 + ((var1 * self._dig_p5) << 17)
        var2 = var2 + (self._dig_p4 << 35)
        var1 = ((var1 * var1 * self._dig_p3) >> 8) + ((var1 * self._dig_p2) << 12)
        var1 = (((1 << 47) + var1) * self._dig_p1) >> 33

        # Un divisor nulo indica calibración corrupta; se evita dividir por cero.
        if var1 == 0:
            raise SensorError("BME280: compensación de presión inválida (divisor 0)")

        # Presión diferencial respecto al fondo de escala del ADC (20 bits).
        pressure = 1048576 - adc_p
        pressure = _cdiv(((pressure << 31) - var2) * 3125, var1)

        # Correcciones de segundo orden por no linealidad y sensibilidad.
        var1 = (self._dig_p9 * (pressure >> 13) * (pressure >> 13)) >> 25
        var2 = (self._dig_p8 * pressure) >> 19
        pressure = ((pressure + var1 + var2) >> 8) + (self._dig_p7 << 4)

        # Q24.8 -> Pa -> hPa.
        return pressure / 25600.0

    def _compensate_humidity(self, adc_h: int) -> float:
        """Aplica la compensación entera de humedad y la limita a 0..100 %."""
        value = self._t_fine - 76800
        value = (
            (
                (
                    (adc_h << 14)
                    - (self._dig_h4 << 20)
                    - (self._dig_h5 * value)
                    + 16384
                )
                >> 15
            )
            * (
                (
                    (
                        (
                            ((value * self._dig_h6) >> 10)
                            * (((value * self._dig_h3) >> 11) + 32768)
                        )
                        >> 10
                    )
                    + 2097152
                )
                * self._dig_h2
                + 8192
            )
            >> 14
        )
        correction = (
            ((((value >> 15) * (value >> 15)) >> 7) * self._dig_h1) >> 4
        )
        value = value - correction
        value = max(0, min(value, 419430400))
        return (value >> 12) / 1024.0

    def read(self) -> Bme280Sample:
        """Realiza una lectura completa, valida rangos físicos y devuelve el dato.

        Se lanza SensorError ante valores imposibles para evitar que un fallo
        de cableado se interprete como una alarma ambiental real.
        """
        try:
            adc_p, adc_t, adc_h = self._read_raw()
        except OSError as exc:
            raise SensorError(f"BME280: fallo leyendo registros de datos ({exc})") from exc

        temperature = self._compensate_temperature(adc_t)
        pressure = self._compensate_pressure(adc_p)
        humidity = self._compensate_humidity(adc_h)

        if not -40.0 <= temperature <= 85.0:
            raise SensorError(f"BME280: temperatura fuera de rango físico ({temperature:.2f} °C)")
        if not 0.0 <= humidity <= 100.0:
            raise SensorError(f"BME280: humedad fuera de rango físico ({humidity:.2f} %)")
        if not 300.0 <= pressure <= 1100.0:
            raise SensorError(f"BME280: presión fuera de rango físico ({pressure:.2f} hPa)")

        return Bme280Sample(
            temperature_c=round(temperature, 2),
            humidity_pct=round(humidity, 2),
            pressure_hpa=round(pressure, 2),
        )
