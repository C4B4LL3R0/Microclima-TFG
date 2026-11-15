# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/core/monitor.py
 FUNCIÓN: Hilo periódico de adquisición y publicación de medidas.
===============================================================================

 El monitor desacopla la frecuencia de lectura de los sensores de la atención
 que requieren Flask/Socket.IO. Su hilo:
   1. Llama a reader.read().
   2. Normaliza errores totales en una SensorReading vacía (la web sigue viva).
   3. Invoca un callback (el runtime) que evalúa alarmas y difunde el estado.

 El callback se ejecuta fuera de cualquier lock del monitor para no retenerlo
 mientras se serializa o se envía un WebSocket. La última lectura se guarda para
 que un cliente que se conecte tarde reciba datos inmediatos.
===============================================================================
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from microclima.sensors.base import SensorReader, SensorReading

LOGGER = logging.getLogger(__name__)

ReadingCallback = Callable[[SensorReading], None]


class SensorMonitor:
    """Adquisidor periódico thread-safe para cualquier SensorReader."""

    def __init__(
        self,
        reader: SensorReader,
        interval_seconds: float,
        on_reading: ReadingCallback,
    ) -> None:
        if interval_seconds < 0.2:
            raise ValueError("El intervalo de muestreo debe ser >= 0,2 s")
        self._reader = reader
        self._interval = float(interval_seconds)
        self._on_reading = on_reading
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._sample_lock = threading.Lock()
        self._last_reading: SensorReading | None = None
        self._last_sample_monotonic: float | None = None
        self._consecutive_errors = 0

    def start(self) -> None:
        """Arranca el hilo daemon si aún no está activo."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="microclima-sensores",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info(
            "Monitor de sensores iniciado (intervalo %.2f s, origen=%s).",
            self._interval,
            getattr(self._reader, "description", self._reader.__class__.__name__),
        )

    def stop(self) -> None:
        """Detiene el hilo y espera su finalización ordenada."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(2.0, self._interval + 1.0))
            if thread.is_alive():
                LOGGER.warning("El hilo de sensores no terminó en el tiempo esperado.")
        self._thread = None

    def _run(self) -> None:
        """Bucle principal: muestrear, publicar y esperar el intervalo."""
        LOGGER.debug("Hilo de sensores en marcha.")
        while not self._stop_event.is_set():
            started = time.monotonic()
            self._sample_and_publish()
            # Se descuenta el tiempo de lectura para mantener una cadencia
            # aproximadamente constante incluso si el bus I2C tarda en responder.
            elapsed = time.monotonic() - started
            wait = max(0.0, self._interval - elapsed)
            if self._stop_event.wait(wait):
                break
        LOGGER.debug("Hilo de sensores finalizado.")

    def _sample_and_publish(self) -> None:
        """Realiza una adquisición y llama al consumidor sin propagar excepciones."""
        with self._sample_lock:
            try:
                reading = self._reader.read()
                if not isinstance(reading, SensorReading):
                    raise TypeError("El backend no devolvió una SensorReading")
                with self._state_lock:
                    self._consecutive_errors = 0
            except Exception as exc:
                # Un fallo inesperado del driver no debe matar el hilo. Se
                # convierte en una lectura sin magnitudes y se informa en la web.
                LOGGER.exception("Fallo general leyendo sensores.")
                with self._state_lock:
                    self._consecutive_errors += 1
                    consecutive = self._consecutive_errors
                reading = SensorReading(
                    timestamp=datetime.now().astimezone(),
                    source=getattr(self._reader, "description", "sensor desconocido"),
                    errors=[
                        f"Error de adquisición (#{consecutive}): {exc}"
                    ],
                )

            with self._state_lock:
                self._last_reading = reading
                self._last_sample_monotonic = time.monotonic()

        # El callback se ejecuta fuera del lock para permitir que otras partes
        # consulten el estado mientras se difunde por WebSocket.
        try:
            self._on_reading(reading)
        except Exception:
            LOGGER.exception("El consumidor de lecturas lanzó una excepción.")

    def sample_now(self) -> SensorReading | None:
        """Fuerza una muestra inmediata (usado tras una inyección manual).

        Devuelve la última lectura conocida. En el simulador la lectura es
        instantánea; en la Pi, el bus puede tardar unos milisegundos.
        """
        self._sample_and_publish()
        return self.latest_reading()

    def latest_reading(self) -> SensorReading | None:
        """Devuelve la última lectura de forma segura entre hilos."""
        with self._state_lock:
            return self._last_reading

    def status(self) -> dict[str, object]:
        """Resumen de salud del adquisidor para /health y diagnósticos."""
        with self._state_lock:
            age = (
                None
                if self._last_sample_monotonic is None
                else round(time.monotonic() - self._last_sample_monotonic, 2)
            )
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval_seconds": self._interval,
                "last_sample_age_seconds": age,
                "consecutive_errors": self._consecutive_errors,
            }
