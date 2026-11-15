# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/hardware/buzzer.py
 FUNCIÓN: Abstracción del buzzer y reproducción no bloqueante de patrones.
===============================================================================

 ¿Por qué un hilo dedicado?
   Si el patrón se reprodujera con time.sleep() en el hilo de Flask o en el de
   lectura de sensores, la web dejaría de responder durante los pitidos y se
   perderían muestras. El BuzzerController posee un único hilo trabajador que
   espera en una Condition; los cambios de alarma o de silencio solo modifican
   el estado y despiertan al hilo. Así el servidor y los sensores siguen
   operando con normalidad.

 Implementaciones:
   - GpioBuzzer: buzzer activo en GPIO17 (pin físico 11), gpiozero o RPi.GPIO.
   - ConsoleBuzzer: emulación visual para Kubuntu, sin hardware.
   - NullBuzzer: mantiene la aplicación viva si GPIO no está disponible.
===============================================================================
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

from microclima.core.alarm import SILENT, AcousticPattern

LOGGER = logging.getLogger(__name__)

DEFAULT_BUZZER_PIN = 17  # GPIO17 = pin físico 11 de la Raspberry Pi.


class BuzzerDriver(Protocol):
    """Interfaz mínima que debe ofrecer cualquier salida acústica."""

    available: bool
    description: str

    def on(self) -> None: ...
    def off(self) -> None: ...
    def close(self) -> None: ...


class ConsoleBuzzer:
    """Buzzer simulado: dibuja en consola el estado del pin y cada pitido."""

    available = True
    description = "Emulación visual por consola"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._is_on = False

    def on(self) -> None:
        """Muestra visualmente el paso a nivel activo del buzzer."""
        with self._lock:
            if self._is_on:
                return
            self._is_on = True
            # El bloque representa la señal sonora; no se emite sonido real para
            # no molestar durante las pruebas en el PC de desarrollo.
            print("\n🔊 [BUZZER] ████████████████  PITIDO ACTIVO", flush=True)

    def off(self) -> None:
        """Muestra el retorno al estado de silencio."""
        with self._lock:
            if not self._is_on:
                return
            self._is_on = False
            print("🔇 [BUZZER] ················  silencio\n", flush=True)

    def close(self) -> None:
        """Garantiza que la consola termine en estado de silencio."""
        self.off()


class GpioBuzzer:
    """Buzzer activo conectado a GPIO17, con gpiozero y respaldo RPi.GPIO.

    Un buzzer activo solo necesita nivel alto/bajo; no se genera PWM. Si el
    módulo es de tipo activo-bajo, se puede construir con active_high=False o
    definir MICROCLIMA_BUZZER_ACTIVE_LOW=1 en el entorno.
    """

    available = True

    def __init__(self, pin: int = DEFAULT_BUZZER_PIN, active_high: bool = True) -> None:
        self.pin = pin
        self.active_high = active_high
        self._gpiozero_device = None
        self._rpi_gpio = None
        self._backend = ""
        try:
            self._init_gpiozero()
        except Exception as gpiozero_error:
            LOGGER.warning(
                "gpiozero no pudo inicializar GPIO%d (%s); se prueba RPi.GPIO.",
                pin,
                gpiozero_error,
            )
            try:
                self._init_rpi_gpio()
            except Exception as rpi_error:
                raise RuntimeError(
                    f"No se pudo controlar el buzzer GPIO{pin}. "
                    f"gpiozero: {gpiozero_error}; RPi.GPIO: {rpi_error}"
                ) from rpi_error
        self.description = (
            f"GPIO{self.pin} (pin físico 11) · backend {self._backend}"
        )

    def _init_gpiozero(self) -> None:
        """Inicializa gpiozero.Buzzer, usado por Raspberry Pi OS moderno."""
        from gpiozero import Buzzer

        self._gpiozero_device = Buzzer(
            self.pin,
            active_high=self.active_high,
            initial_value=False,
        )
        self._backend = "gpiozero"

    def _init_rpi_gpio(self) -> None:
        """Respaldo para instalaciones antiguas que solo tienen RPi.GPIO."""
        import RPi.GPIO as GPIO

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        self._inactive_level = GPIO.LOW if self.active_high else GPIO.HIGH
        self._active_level = GPIO.HIGH if self.active_high else GPIO.LOW
        GPIO.setup(self.pin, GPIO.OUT, initial=self._inactive_level)
        self._rpi_gpio = GPIO
        self._backend = "RPi.GPIO"

    def on(self) -> None:
        """Activa el buzzer."""
        if self._gpiozero_device is not None:
            self._gpiozero_device.on()
        elif self._rpi_gpio is not None:
            self._rpi_gpio.output(self.pin, self._active_level)

    def off(self) -> None:
        """Desactiva el buzzer."""
        if self._gpiozero_device is not None:
            self._gpiozero_device.off()
        elif self._rpi_gpio is not None:
            self._rpi_gpio.output(self.pin, self._inactive_level)

    def close(self) -> None:
        """Apaga y libera el pin para no dejarlo sonando al terminar."""
        try:
            self.off()
        finally:
            if self._gpiozero_device is not None:
                self._gpiozero_device.close()
                self._gpiozero_device = None
            if self._rpi_gpio is not None:
                try:
                    self._rpi_gpio.cleanup(self.pin)
                finally:
                    self._rpi_gpio = None


class NullBuzzer:
    """Salida nula que permite arrancar y avisar por la web si GPIO falla."""

    available = False

    def __init__(self, reason: str) -> None:
        self.description = f"Buzzer no disponible: {reason}"
        LOGGER.error(self.description)

    def on(self) -> None:
        return None

    def off(self) -> None:
        return None

    def close(self) -> None:
        return None


def create_hardware_buzzer(
    pin: int = DEFAULT_BUZZER_PIN,
    active_high: bool = True,
) -> BuzzerDriver:
    """Crea el driver GPIO y degrada a NullBuzzer si no hay hardware."""
    try:
        return GpioBuzzer(pin=pin, active_high=active_high)
    except Exception as exc:
        return NullBuzzer(str(exc))


class BuzzerController:
    """Hilo dedicado que reproduce el patrón vigente sin bloquear la aplicación."""

    def __init__(self, driver: BuzzerDriver) -> None:
        self._driver = driver
        self._condition = threading.Condition()
        self._pattern: AcousticPattern = SILENT
        self._muted = False
        self._stop = False
        self._is_beeping = False
        self._thread: threading.Thread | None = None
        self._worker_lock = threading.Lock()

    # --------------------------------------------------------------- ciclo de vida
    def start(self) -> None:
        """Arranca el hilo daemon una sola vez."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="microclima-buzzer",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info("Controlador de buzzer iniciado (%s).", self._driver.description)

    def stop(self) -> None:
        """Detiene el hilo, apaga el buzzer y cierra el driver."""
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._safe_off()
        try:
            self._driver.close()
        except Exception:
            LOGGER.exception("Error cerrando el driver del buzzer.")

    # ------------------------------------------------------------------- estado
    def set_pattern(self, pattern: AcousticPattern) -> None:
        """Cambia el patrón vigente y despierta al hilo solo si es distinto."""
        with self._condition:
            changed = pattern.pattern_id != self._pattern.pattern_id
            self._pattern = pattern
            if changed:
                if pattern.is_silent:
                    LOGGER.info("Alarma acústica: sin pitidos (estado normal).")
                else:
                    LOGGER.info(
                        "Alarma acústica: %s [%s] · %s",
                        pattern.label,
                        pattern.code,
                        pattern.description,
                    )
                self._condition.notify_all()

    def set_muted(self, muted: bool) -> None:
        """Silencia o reactiva el sonido compartido entre todos los clientes."""
        with self._condition:
            muted = bool(muted)
            if self._muted == muted:
                return
            self._muted = muted
            LOGGER.info("Alarma sonora %s.", "SILENCIADA" if muted else "REACTIVADA")
            self._condition.notify_all()

    @property
    def muted(self) -> bool:
        """Estado actual de silencio."""
        with self._condition:
            return self._muted

    def snapshot(self) -> dict[str, object]:
        """Estado del hardware para incluir en el JSON de la web."""
        with self._condition:
            return {
                "muted": self._muted,
                "available": bool(self._driver.available),
                "description": self._driver.description,
                "is_beeping": self._is_beeping,
            }

    # -------------------------------------------------------------------- worker
    def _interrupted(self, pattern: AcousticPattern) -> bool:
        """True si hay que abandonar el patrón actual (stop, mute o cambio)."""
        return (
            self._stop
            or self._muted
            or self._pattern.pattern_id != pattern.pattern_id
        )

    def _wait_interruptible(self, seconds: float, pattern: AcousticPattern) -> bool:
        """Espera un intervalo sin bloquear la reacción a mute/cambio de patrón.

        Se usa Condition.wait con un máximo de 100 ms. Aunque llegase una
        notificación perdida, el hilo revisa el estado con frecuencia suficiente
        para que el silenciamiento se perciba como inmediato.
        """
        deadline = time.monotonic() + seconds
        with self._condition:
            while not self._interrupted(pattern):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(remaining, 0.1))
            return True

    def _safe_on(self) -> None:
        """Enciende el driver aislando cualquier fallo de GPIO."""
        try:
            self._driver.on()
        except Exception:
            LOGGER.exception("Fallo activando el buzzer; se continúa sin sonido.")
            with self._condition:
                self._muted = True
                self._condition.notify_all()

    def _safe_off(self) -> None:
        """Apaga el driver aislando errores de hardware."""
        with self._worker_lock:
            try:
                self._driver.off()
            except Exception:
                LOGGER.exception("Fallo apagando el buzzer.")
            finally:
                with self._condition:
                    self._is_beeping = False

    def _run(self) -> None:
        """Bucle del hilo: espera, reproduce un patrón y respeta silencio/cambios."""
        LOGGER.debug("Hilo del buzzer en marcha.")
        while True:
            with self._condition:
                while not self._stop and (self._muted or self._pattern.is_silent):
                    self._condition.wait()
                if self._stop:
                    break
                pattern = self._pattern

            for index, beep_ms in enumerate(pattern.beeps_ms):
                with self._condition:
                    if self._interrupted(pattern):
                        break
                    self._is_beeping = True
                self._safe_on()
                interrupted = self._wait_interruptible(beep_ms / 1000.0, pattern)
                self._safe_off()
                if interrupted:
                    break

                # Pausa entre pitidos (no después del último del ciclo).
                if index < len(pattern.beeps_ms) - 1:
                    if self._wait_interruptible(pattern.gap_ms / 1000.0, pattern):
                        break

            if pattern.is_silent:
                continue
            # Pausa larga antes de repetir el patrón. Se puede interrumpir por un
            # cambio de alarma o por el botón de silencio.
            if not self._stop:
                self._wait_interruptible(pattern.repeat_delay_ms / 1000.0, pattern)

        self._safe_off()
        LOGGER.debug("Hilo del buzzer finalizado.")
