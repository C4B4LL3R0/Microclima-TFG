# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/sensors/base.py
 FUNCIÓN: Contratos y modelos de datos comunes a todos los sensores.
===============================================================================

 El resto del sistema (monitor, motor de alarmas y web) no necesita saber si
 los datos vienen de un BME280 físico, de un SGP30 o del simulador de Kubuntu.
 Todos los backends implementan `read()` y devuelven un `SensorReading`.

 Se permite que una magnitud sea None: si el BME280 falla pero el SGP30 sigue
 respondiendo, es preferible conservar la medida de gases y mostrar el error del
 primer sensor que apagar toda la interfaz. El motor de alarmas ignora los
 valores desconocidos y lo indica en la web.
===============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class SensorError(RuntimeError):
    """Error controlado de lectura/validación de un sensor I2C."""


@dataclass
class SensorReading:
    """Instantánea ambiental normalizada, independiente del hardware.

    Atributos:
        temperature: temperatura en °C.
        humidity: humedad relativa en %.
        pressure: presión barométrica en hPa (informativa, no genera alarma).
        eco2: eCO2 equivalente en ppm.
        tvoc: compuestos orgánicos volátiles en ppb.
        timestamp: fecha/hora local de la lectura.
        source: descripción legible del origen (I2C o simulador).
        errors: lista de errores parciales que se mostrarán al operador.
        warmup: True mientras el SGP30 cumple sus primeros 15 s de calibración.
    """

    temperature: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    eco2: float | None = None
    tvoc: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now().astimezone())
    source: str = "desconocido"
    errors: list[str] = field(default_factory=list)
    warmup: bool = False

    def to_payload(self) -> dict[str, Any]:
        """Serializa la lectura con redondeo y sin exponer objetos datetime.

        El redondeo a dos decimales es también el valor que evalúa el motor de
        alarmas (ver core/alarm.py), de forma que lo mostrado y lo evaluado
        coincidan incluso cerca de un umbral.
        """
        return {
            "temperature": _round_or_none(self.temperature, 2),
            "humidity": _round_or_none(self.humidity, 2),
            "pressure": _round_or_none(self.pressure, 2),
            "eco2": _round_or_none(self.eco2, 0),
            "tvoc": _round_or_none(self.tvoc, 0),
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
            "source": self.source,
            "errors": list(self.errors),
            "warmup": bool(self.warmup),
        }

    @property
    def has_any_value(self) -> bool:
        """True si al menos una magnitud es válida."""
        return any(
            value is not None
            for value in (self.temperature, self.humidity, self.eco2, self.tvoc)
        )


class SensorReader(Protocol):
    """Contrato mínimo que debe cumplir un backend de sensores."""

    def read(self) -> SensorReading:
        """Realiza una lectura completa y devuelve el modelo normalizado."""
        ...

    def close(self) -> None:
        """Libera buses, GPIO o recursos del backend."""
        ...


def _round_or_none(value: float | None, digits: int) -> float | None:
    """Redondea un valor opcional evitando errores con None."""
    return None if value is None else round(float(value), digits)


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Limita un valor al intervalo cerrado [minimum, maximum]."""
    return max(minimum, min(maximum, value))
