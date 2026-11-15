# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/core/alarm.py
 FUNCIÓN: Catálogo de patrones acústicos y motor de evaluación de umbrales.
===============================================================================

 El motor es puro: recibe una lectura y un perfil, y devuelve una decisión
 inmutable. No toca GPIO, no envía Socket.IO y no guarda estado. Esta separación
 permite probar las alarmas de forma determinista y explica con claridad en la
 memoria qué condición genera cada patrón.

 Criterio de "múltiples variables":
   Se cuentan las magnitudes individuales fuera de rango (temperatura, humedad,
   eCO2 y TVOC). Si hay dos o más, prevalece el patrón MULTI de cinco pitidos.
   Así, eCO2 y TVOC altos a la vez también producen MULTI, no el patrón de aire.

 Los patrones usan milisegundos en el JSON y segundos en el controlador del
 buzzer. Las pausas no forman parte de la especificación del TFG; se fijan en
 180 ms entre pitidos normales, 100 ms en patrones rápidos y una repetición de
 1,8 s para que el operador distinga claramente el ciclo.
===============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from microclima.config import Profile
from microclima.sensors.base import SensorReading


@dataclass(frozen=True)
class AcousticPattern:
    """Descripción completa y serializable de un patrón de pitidos."""

    pattern_id: str
    label: str
    code: str
    beeps_ms: tuple[int, ...]
    gap_ms: int
    repeat_delay_ms: int
    description: str

    @property
    def is_silent(self) -> bool:
        """Un patrón sin pitidos representa el estado normal."""
        return not self.beeps_ms

    def to_payload(self) -> dict[str, Any]:
        """Convierte el patrón al formato que consume la interfaz web."""
        return {
            "id": self.pattern_id,
            "label": self.label,
            "code": self.code,
            "beeps_ms": list(self.beeps_ms),
            "gap_ms": self.gap_ms,
            "repeat_delay_ms": self.repeat_delay_ms,
            "description": self.description,
        }


SILENT = AcousticPattern(
    "silent",
    "Sin alarma",
    "",
    (),
    0,
    0,
    "Todas las variables dentro de los límites del perfil activo.",
)

TEMP_HIGH = AcousticPattern(
    "temperature_high",
    "Temperatura por encima del máximo",
    "— ·",
    (600, 200),
    180,
    1800,
    "1 pitido largo (0,6 s) + 1 pitido corto (0,2 s).",
)

TEMP_LOW = AcousticPattern(
    "temperature_low",
    "Temperatura por debajo del mínimo",
    "— · ·",
    (600, 200, 200),
    180,
    1800,
    "1 pitido largo (0,6 s) + 2 pitidos cortos (0,2 s).",
)

HUM_HIGH = AcousticPattern(
    "humidity_high",
    "Humedad relativa por encima del máximo",
    "— — ·",
    (600, 600, 200),
    180,
    1800,
    "2 pitidos largos (0,6 s) + 1 pitido corto (0,2 s).",
)

HUM_LOW = AcousticPattern(
    "humidity_low",
    "Humedad relativa por debajo del mínimo",
    "— — · ·",
    (600, 600, 200, 200),
    180,
    1800,
    "2 pitidos largos (0,6 s) + 2 pitidos cortos (0,2 s).",
)

AIR_HIGH = AcousticPattern(
    "air_quality_high",
    "Calidad de aire fuera de límite (eCO2 o TVOC)",
    "· · ·",
    (200, 200, 200),
    100,
    1500,
    "3 pitidos cortos rápidos (0,2 s).",
)

MULTI = AcousticPattern(
    "multiple_alerts",
    "Múltiples variables fuera de rango",
    "· · · · ·",
    (100, 100, 100, 100, 100),
    100,
    1000,
    "5 pitidos intermitentes rápidos (0,1 s).",
)


@dataclass(frozen=True)
class AlarmDecision:
    """Resultado inmutable de evaluar una lectura contra un perfil."""

    pattern: AcousticPattern
    out_of_range: tuple[str, ...]
    directions: dict[str, str]
    statuses: dict[str, str]
    reasons: tuple[str, ...]

    @property
    def active(self) -> bool:
        """True si al menos una variable supera un límite."""
        return bool(self.out_of_range)

    def to_payload(self) -> dict[str, Any]:
        """Serializa la decisión; el estado de silencio lo añade el runtime."""
        return {
            "active": self.active,
            "out_of_range": list(self.out_of_range),
            "directions": dict(self.directions),
            "statuses": dict(self.statuses),
            "reasons": list(self.reasons),
            "pattern": self.pattern.to_payload(),
        }


class AlarmEvaluator:
    """Evalúa temperatura, humedad, eCO2 y TVOC contra un Profile."""

    @staticmethod
    def _value(reading: SensorReading) -> dict[str, float | None]:
        """Aplica el mismo redondeo que se muestra en la web.

        Evaluar el valor redondeado evita el caso confuso de una tarjeta que
        muestra 18,0 °C (el máximo) mientras la alarma está activa porque el
        valor interno era 18,004 °C.
        """
        return {
            "temperature": None
            if reading.temperature is None
            else round(float(reading.temperature), 2),
            "humidity": None
            if reading.humidity is None
            else round(float(reading.humidity), 2),
            "eco2": None if reading.eco2 is None else round(float(reading.eco2), 0),
            "tvoc": None if reading.tvoc is None else round(float(reading.tvoc), 0),
        }

    def evaluate(self, reading: SensorReading, profile: Profile) -> AlarmDecision:
        """Devuelve el patrón acústico y las razones legibles de la alarma.

        Parámetros:
            reading: última instantánea de sensores (puede tener valores None).
            profile: perfil normativo activo en ese instante.

        Lógica:
            1. Temperatura y humedad se comparan con mínimo y máximo.
            2. eCO2 y TVOC solo con máximo.
            3. Los valores None se marcan "unknown" y no generan alarma.
            4. Se elige un patrón específico o MULTI si hay >=2 variables.
        """
        values = self._value(reading)
        limits = {
            "temperature": profile.temperature,
            "humidity": profile.humidity,
            "eco2": profile.eco2,
            "tvoc": profile.tvoc,
        }
        labels = {
            "temperature": "Temperatura",
            "humidity": "Humedad relativa",
            "eco2": "eCO2",
            "tvoc": "TVOC",
        }

        statuses: dict[str, str] = {}
        directions: dict[str, str] = {}
        reasons: list[str] = []
        out_of_range: list[str] = []

        for variable in ("temperature", "humidity", "eco2", "tvoc"):
            value = values[variable]
            limit = limits[variable]
            if value is None:
                statuses[variable] = "unknown"
                continue

            status = "normal"
            if limit.minimum is not None and value < limit.minimum:
                status = "low"
                directions[variable] = "low"
                # Se usa "por debajo del mínimo" en lugar de "baja" para que la
                # frase funcione igual con sustantivos masculinos (TVOC), femeninos
                # (temperatura) y siglas (eCO2), sin forzar concordancia de género.
                reasons.append(
                    f"{labels[variable]} por debajo del mínimo: "
                    f"{value:g} {limit.unit} < {limit.minimum:g} {limit.unit}"
                )
            elif value > limit.maximum:
                status = "high"
                directions[variable] = "high"
                reasons.append(
                    f"{labels[variable]} por encima del máximo: "
                    f"{value:g} {limit.unit} > {limit.maximum:g} {limit.unit}"
                )

            statuses[variable] = status
            if status != "normal":
                out_of_range.append(variable)

        count = len(out_of_range)
        if count == 0:
            pattern = SILENT
        elif count >= 2:
            pattern = MULTI
        elif "temperature" in directions:
            pattern = TEMP_HIGH if directions["temperature"] == "high" else TEMP_LOW
        elif "humidity" in directions:
            pattern = HUM_HIGH if directions["humidity"] == "high" else HUM_LOW
        else:
            # Queda exactamente una variable de aire fuera de rango.
            pattern = AIR_HIGH

        return AlarmDecision(
            pattern=pattern,
            out_of_range=tuple(out_of_range),
            directions=directions,
            statuses=statuses,
            reasons=tuple(reasons),
        )
