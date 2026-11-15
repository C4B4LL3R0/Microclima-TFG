# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/sensors/simulator.py
 FUNCIÓN: Backend de sensores simulado para Kubuntu x86_64 y para pruebas.
===============================================================================

 El simulador implementa exactamente el mismo contrato SensorReader que el
 backend I2C real. De este modo, el motor de alarmas, la persistencia, el buzzer
 y la web no contienen ningún `if simulacion` disperso: solo cambia la pieza que
 entrega las medidas.

 Dos modos:
   - AUTO: genera valores aleatorios suaves alrededor del perfil normativo
     seleccionado, con una onda sinusoidal y ruido. Sirve para una demostración
     sin interacción.
   - MANUAL: el operador fija temperatura, humedad, eCO2 y TVOC desde la web.
     Los valores se mantienen hasta que se cambian o se vuelve a AUTO.

 Todos los accesos están protegidos por RLock porque el hilo de muestreo y los
 manejadores Socket.IO pueden coincidir.
===============================================================================
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from microclima.config import Profile

from .base import SensorReading

# Rangos de los controles del panel de inyección. Se envían al navegador para
# que los sliders y los campos numéricos se configuren con la misma fuente de
# verdad que valida el servidor.
SIM_RANGES: dict[str, dict[str, float | str]] = {
    "temperature": {"min": -10.0, "max": 50.0, "step": 0.1, "unit": "°C"},
    "humidity": {"min": 0.0, "max": 100.0, "step": 0.1, "unit": "%"},
    "eco2": {"min": 400.0, "max": 3000.0, "step": 1.0, "unit": "ppm"},
    "tvoc": {"min": 0.0, "max": 2000.0, "step": 1.0, "unit": "ppb"},
}


@dataclass(frozen=True)
class SimulationUpdate:
    """Resultado de una inyección manual validada."""

    mode: str
    values: dict[str, float]


class SimulatedSensorArray:
    """Generador de medidas coherente con la interfaz SensorReader."""

    def __init__(self, profile_provider: Callable[[], Profile]) -> None:
        self._profile_provider = profile_provider
        self._lock = threading.RLock()
        self._rng = random.Random()
        self._mode = "auto"
        self._phase = self._rng.uniform(0.0, math.tau)
        self._last_tick = time.monotonic()
        self._profile = profile_provider()
        self._values = self._generate_auto()
        self._manual_values = dict(self._values)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _center(limit: Any) -> float:
        """Centro del intervalo de un límite; para gases usa el 60 % del máximo."""
        if limit.minimum is None:
            return limit.maximum * 0.60
        return (limit.minimum + limit.maximum) / 2.0

    @staticmethod
    def _span(limit: Any) -> float:
        """Amplitud del intervalo; para gases usa el 45 % del máximo."""
        if limit.minimum is None:
            return limit.maximum * 0.45
        return max(limit.maximum - limit.minimum, 0.1)

    def _generate_auto(self) -> dict[str, float]:
        """Genera una medida suave alrededor del perfil activo.

        Se emplea una onda lenta (para que la UI no parezca ruido puro) más un
        pequeño jitter aleatorio. La amplitud permite que, de vez en cuando, la
        temperatura o la humedad crucen el umbral y se pueda observar la alarma
        sin tocar los controles.
        """
        now = time.monotonic()
        dt = max(0.0, min(now - self._last_tick, 5.0))
        self._last_tick = now
        self._phase = (self._phase + dt * 0.30) % math.tau
        profile = self._profile

        temp_center = self._center(profile.temperature)
        temp_span = self._span(profile.temperature)
        hum_center = self._center(profile.humidity)
        hum_span = self._span(profile.humidity)
        eco2_center = self._center(profile.eco2)
        eco2_span = self._span(profile.eco2)
        tvoc_center = self._center(profile.tvoc)
        tvoc_span = self._span(profile.tvoc)

        temperature = (
            temp_center
            + math.sin(self._phase) * temp_span * 0.55
            + self._rng.uniform(-0.18, 0.18)
        )
        humidity = (
            hum_center
            + math.sin(self._phase * 0.72 + 1.1) * hum_span * 0.55
            + self._rng.uniform(-1.0, 1.0)
        )
        eco2 = (
            eco2_center
            + math.sin(self._phase * 0.42 + 0.4) * eco2_span * 0.35
            + self._rng.uniform(-12.0, 12.0)
        )
        tvoc = (
            tvoc_center
            + math.sin(self._phase * 0.51 + 2.0) * tvoc_span * 0.35
            + self._rng.uniform(-8.0, 8.0)
        )
        pressure = 1013.25 + math.sin(self._phase * 0.08) * 4.5 + self._rng.uniform(-0.3, 0.3)

        return {
            "temperature": round(max(-10.0, min(50.0, temperature)), 1),
            "humidity": round(max(0.0, min(100.0, humidity)), 1),
            "eco2": round(max(400.0, min(3000.0, eco2)), 0),
            "tvoc": round(max(0.0, min(2000.0, tvoc)), 0),
            "pressure": round(pressure, 2),
        }

    def set_profile(self, profile: Profile) -> None:
        """Actualiza el perfil que guía la generación automática."""
        with self._lock:
            self._profile = profile
            self._last_tick = time.monotonic()

    # ------------------------------------------------------------------- lectura
    def read(self) -> SensorReading:
        """Devuelve la medida actual (auto o manual) como SensorReading."""
        with self._lock:
            if self._mode == "auto":
                self._values = self._generate_auto()
            values = dict(self._values)
            return SensorReading(
                temperature=float(values["temperature"]),
                humidity=float(values["humidity"]),
                pressure=float(values["pressure"]),
                eco2=float(values["eco2"]),
                tvoc=float(values["tvoc"]),
                source="Simulador Kubuntu (x86_64)",
                errors=[],
                warmup=False,
            )

    # ---------------------------------------------------------- control manual
    def _validate_value(self, key: str, raw: Any) -> float:
        """Valida que una magnitud esté dentro del rango del panel de pruebas."""
        if key not in SIM_RANGES:
            raise ValueError(f"Magnitud desconocida: {key}")
        if isinstance(raw, bool):
            raise ValueError(f"{key}: se esperaba un número")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key}: valor no numérico") from exc
        if not math.isfinite(value):
            raise ValueError(f"{key}: el valor debe ser finito")
        limits = SIM_RANGES[key]
        minimum = float(limits["min"])
        maximum = float(limits["max"])
        if not minimum <= value <= maximum:
            raise ValueError(
                f"{key}: {value} fuera del rango permitido "
                f"[{minimum:g}, {maximum:g}]"
            )
        return value

    def apply_update(self, payload: dict[str, Any]) -> SimulationUpdate:
        """Aplica un cambio de modo y/o valores manuales procedente de Socket.IO.

        El diccionario puede contener:
            mode: "auto" | "manual".
            temperature, humidity, eco2, tvoc: valores numéricos opcionales.

        En modo AUTO se ignoran los valores numéricos; el generador vuelve a
        tomar el control. En MANUAL, los valores no enviados conservan el valor
        anterior, lo que permite actualizar un único slider.
        """
        if not isinstance(payload, dict):
            raise ValueError("El control de simulación debe ser un objeto JSON")

        with self._lock:
            mode = payload.get("mode")
            if mode is not None:
                mode = str(mode)
                if mode not in ("auto", "manual"):
                    raise ValueError("mode debe ser 'auto' o 'manual'")

            requested: dict[str, float] = {}
            for key in SIM_RANGES:
                if key in payload and payload[key] is not None:
                    requested[key] = self._validate_value(key, payload[key])

            if mode == "manual" and self._mode != "manual":
                # Al entrar en manual se congelan los valores actuales. Si el
                # cliente envía además valores (lo normal al mover un slider),
                # se aplican justo después.
                self._manual_values = dict(self._values)
            if mode is not None:
                self._mode = mode

            if self._mode == "manual":
                self._manual_values.update(requested)
                self._values = dict(self._manual_values)
            elif mode == "auto":
                # Se refresca inmediatamente para que el cambio de modo se vea
                # sin esperar al siguiente ciclo de muestreo.
                self._last_tick = time.monotonic()
                self._values = self._generate_auto()

            return SimulationUpdate(mode=self._mode, values=dict(self._values))

    def payload(self) -> dict[str, Any]:
        """Estado serializable que consume el panel de pruebas del navegador."""
        with self._lock:
            return {
                "enabled": True,
                "mode": self._mode,
                "ranges": SIM_RANGES,
                "values": {
                    "temperature": self._values["temperature"],
                    "humidity": self._values["humidity"],
                    "eco2": self._values["eco2"],
                    "tvoc": self._values["tvoc"],
                },
            }

    def close(self) -> None:
        """El simulador no tiene recursos externos que liberar."""
        return None
