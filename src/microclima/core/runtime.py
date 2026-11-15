# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/core/runtime.py
 FUNCIÓN: Orquestador único de configuración, sensores, alarmas y estado web.
===============================================================================

 El runtime es el punto donde se unen las piezas del sistema. Mantiene una
 instantánea coherente de:
   - el perfil normativo activo (persistido en config.json),
   - la última lectura de sensores,
   - la decisión de alarma y el patrón acústico,
   - el estado del simulador (solo en Kubuntu).

 Flask/Socket.IO no habla directamente con los sensores: pide el estado al
 runtime y le envía eventos de control. Esto simplifica las pruebas y evita
 condiciones de carrera entre el hilo de adquisición y los clientes web.

 Los métodos públicos que cambian estado (perfil, mute, simulación) lo hacen de
 forma atómica y notifican al buzzer antes de publicar el nuevo estado.
===============================================================================
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

from microclima.config import ConfigStore, Profile
from microclima.core.alarm import AlarmDecision, AlarmEvaluator, SILENT
from microclima.core.monitor import SensorMonitor
from microclima.hardware.buzzer import BuzzerController, BuzzerDriver
from microclima.sensors.base import SensorReader, SensorReading
from microclima.sensors.simulator import SimulatedSensorArray, SimulationUpdate

LOGGER = logging.getLogger(__name__)

StateCallback = Callable[[dict[str, Any]], None]


class MicroclimaRuntime:
    """Núcleo de negocio compartido por microclima_rpi y microclima_sim."""

    def __init__(
        self,
        config_store: ConfigStore,
        reader: SensorReader,
        buzzer_driver: BuzzerDriver,
        mode: str,
        *,
        simulator: SimulatedSensorArray | None = None,
        sample_interval: float = 1.0,
        hardware_info: dict[str, Any] | None = None,
    ) -> None:
        if mode not in ("rpi", "sim"):
            raise ValueError("mode debe ser 'rpi' o 'sim'")
        if mode == "sim" and simulator is None:
            raise ValueError("El modo simulación requiere un SimulatedSensorArray")

        self.mode = mode
        self.config_store = config_store
        self._reader = reader
        self._simulator = simulator
        self._sample_interval = sample_interval
        self._hardware_info = dict(hardware_info or {})
        self._evaluator = AlarmEvaluator()
        self._buzzer = BuzzerController(buzzer_driver)
        self._lock = threading.RLock()
        self._started_at = time.monotonic()
        self._last_reading: SensorReading | None = None
        self._last_decision: AlarmDecision = self._silent_decision()
        self._active_profile: Profile = config_store.selected_profile()
        self._state_callback: StateCallback | None = None
        self._stopped = False

        if simulator is not None:
            simulator.set_profile(self._active_profile)

        self._monitor = SensorMonitor(
            reader=reader,
            interval_seconds=sample_interval,
            on_reading=self._handle_reading,
        )

    # ------------------------------------------------------------------ utilidades
    @staticmethod
    def _silent_decision() -> AlarmDecision:
        """Decisión neutra para el estado previo a la primera lectura."""
        return AlarmDecision(
            pattern=SILENT,
            out_of_range=(),
            directions={},
            statuses={
                "temperature": "unknown",
                "humidity": "unknown",
                "eco2": "unknown",
                "tvoc": "unknown",
            },
            reasons=(),
        )

    @property
    def mode_label(self) -> str:
        """Etiqueta legible mostrada en la cabecera de la web."""
        if self.mode == "sim":
            return "Simulación · Kubuntu x86_64"
        return "Producción · Raspberry Pi 3B / I2C1"

    def set_state_callback(self, callback: StateCallback | None) -> None:
        """Registra el difusor Socket.IO que recibirá cada nuevo estado."""
        self._state_callback = callback

    # --------------------------------------------------------------- ciclo de vida
    def start(self) -> None:
        """Arranca buzzer, realiza una primera muestra y lanza el monitor."""
        self._buzzer.start()
        # Primera lectura síncrona: garantiza que /health y el primer cliente
        # reciban datos reales y no un estado vacío durante el primer segundo.
        self._monitor.sample_now()
        self._monitor.start()
        LOGGER.info(
            "Runtime %s iniciado. Perfil=%s; intervalo=%.2fs.",
            self.mode,
            self._active_profile.profile_id,
            self._sample_interval,
        )

    def stop(self) -> None:
        """Detiene adquisición y sonido y libera el backend de sensores."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        LOGGER.info("Deteniendo runtime de microclima...")
        self._monitor.stop()
        self._buzzer.stop()
        try:
            self._reader.close()
        except Exception:
            LOGGER.exception("Error cerrando el backend de sensores.")
        if self._simulator is not None:
            self._simulator.close()

    # -------------------------------------------------------------------- sensores
    def _handle_reading(self, reading: SensorReading) -> None:
        """Callback del monitor: evalúa, actualiza el buzzer y publica el estado."""
        profile = self.config_store.selected_profile()
        decision = self._evaluator.evaluate(reading, profile)
        with self._lock:
            self._last_reading = reading
            self._last_decision = decision
            self._active_profile = profile
        # set_pattern es no bloqueante: solo actualiza la Condition del hilo del
        # buzzer. Nunca espera a que termine el patrón.
        self._buzzer.set_pattern(decision.pattern)
        self.publish_state()

    def _reevaluate_latest(self) -> None:
        """Reevalúa la última lectura con el perfil recién seleccionado."""
        with self._lock:
            reading = self._last_reading
            profile = self._active_profile
        if reading is None:
            reading = SensorReading(
                timestamp=datetime.now().astimezone(),
                source=getattr(self._reader, "description", "sensor"),
                errors=["Esperando la primera lectura de sensores."],
            )
        decision = self._evaluator.evaluate(reading, profile)
        with self._lock:
            self._last_decision = decision
        self._buzzer.set_pattern(decision.pattern)

    # ------------------------------------------------------------------- perfiles
    def select_profile(self, profile_id: str) -> Profile:
        """Persiste el perfil, lo aplica al simulador y reajusta la alarma.

        No emite directamente el estado: el manejador Socket.IO emite primero
        `profile_changed` y después llama a publish_state(), garantizando que
        todos los navegadores actualicen el desplegable antes de recibir los
        nuevos límites.
        """
        profile = self.config_store.set_selected_profile(profile_id)
        with self._lock:
            self._active_profile = profile
        if self._simulator is not None:
            self._simulator.set_profile(profile)
        self._reevaluate_latest()
        LOGGER.info("Perfil aplicado en runtime: %s", profile.profile_id)
        return profile

    def profile_event_payload(self) -> dict[str, Any]:
        """Datos del evento `profile_changed` para sincronizar todos los clientes."""
        profile = self.config_store.selected_profile()
        return {
            "selected_profile": profile.profile_id,
            "profile": profile.to_dict(),
            "profiles": self.config_store.profiles_payload(),
        }

    # --------------------------------------------------------------------- sonido
    def set_muted(self, muted: bool) -> bool:
        """Silencia/reactiva el buzzer y devuelve el estado efectivo."""
        self._buzzer.set_muted(muted)
        return self._buzzer.muted

    @property
    def muted(self) -> bool:
        """Estado actual de silencio."""
        return self._buzzer.muted

    # ----------------------------------------------------------------- simulación
    def update_simulation(self, payload: dict[str, Any]) -> SimulationUpdate:
        """Valida y aplica una inyección manual y fuerza una muestra inmediata."""
        if self._simulator is None:
            raise RuntimeError("El control de simulación no está disponible en este modo")
        update = self._simulator.apply_update(payload)
        # sample_now publica automáticamente el nuevo estado mediante el
        # callback del monitor, por lo que el cambio se ve sin esperar 1 s.
        self._monitor.sample_now()
        return update

    # ---------------------------------------------------------------------- estado
    def publish_state(self) -> None:
        """Construye y entrega el estado al callback Socket.IO, si existe."""
        callback = self._state_callback
        if callback is None:
            return
        try:
            callback(self.build_state())
        except Exception:
            LOGGER.exception("Error publicando el estado por Socket.IO.")

    def build_state(self) -> dict[str, Any]:
        """Construye el JSON completo que consumen todos los navegadores."""
        with self._lock:
            reading = self._last_reading
            decision = self._last_decision
            profile = self._active_profile
            started_at = self._started_at

        if reading is None:
            reading = SensorReading(
                timestamp=datetime.now().astimezone(),
                source=getattr(self._reader, "description", "sensor"),
                errors=["Esperando la primera lectura de sensores."],
            )

        alarm_payload = decision.to_payload()
        alarm_payload.update(self._buzzer.snapshot())

        state: dict[str, Any] = {
            "mode": self.mode,
            "mode_label": self.mode_label,
            "server_time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "uptime_seconds": round(time.monotonic() - started_at, 1),
            "selected_profile": profile.profile_id,
            "profile": profile.to_dict(),
            "profiles": self.config_store.profiles_payload(),
            "reading": reading.to_payload(),
            "alarm": alarm_payload,
            "sim": self._simulator.payload() if self._simulator is not None else None,
            "system": {
                "sample_interval_seconds": self._sample_interval,
                "monitor": self._monitor.status(),
                "hardware": self._hardware_info,
            },
        }
        return state

    def health_payload(self) -> dict[str, Any]:
        """Información breve para el endpoint /health y para systemd."""
        with self._lock:
            reading = self._last_reading
            decision = self._last_decision
            profile = self._active_profile
        errors = list(reading.errors) if reading is not None else ["sin lectura"]
        healthy = reading is not None and reading.has_any_value and not errors
        return {
            "status": "ok" if healthy else "degraded",
            "mode": self.mode,
            "profile": profile.profile_id,
            "alarm_active": decision.active,
            "muted": self._buzzer.muted,
            "sensor_errors": errors,
            "monitor": self._monitor.status(),
        }
