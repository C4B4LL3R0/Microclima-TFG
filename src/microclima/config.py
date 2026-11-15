# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/config.py
 PROYECTO: Monitor de Microclima Edge para Conservación Preventiva
 FUNCIÓN: Modelo y persistencia de los perfiles normativos de conservación.
===============================================================================

 Este módulo es la única fuente de verdad para los límites ambientales que
 utiliza el motor de alarmas. Los límites NO se duplican en el frontend: la web
 recibe este catálogo por Socket.IO y dibuja los rangos que el servidor está
 aplicando realmente. Así se evita que la interfaz y el buzzer evalúen con
 criterios distintos.

 Persistencia:
   - En desarrollo (código fuente): <raíz del proyecto>/config.json.
   - En el ejecutable PyInstaller: <carpeta del ejecutable>/config.json, porque
     el directorio temporal _MEIPASS se destruye al cerrar el programa.
   - La variable de entorno MICROCLIMA_CONFIG permite usar otra ruta (útil para
     pruebas o para una unidad de solo lectura con /var/lib/microclima).

 La escritura es atómica (fichero temporal + fsync + os.replace) y está
 protegida por un RLock. En un equipo empotrado, un corte de corriente durante
 el guardado no debe dejar un JSON truncado ni perder el perfil seleccionado.
===============================================================================
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Identificador de esquema. Se incrementará si en el futuro cambia el formato
# del archivo y fuese necesario aplicar una migración explícita.
SCHEMA_VERSION = 1

# Perfil que se selecciona la primera vez que arranca el sistema. Es el más
# restrictivo de los tres y el apropiado para fondos documentales.
DEFAULT_PROFILE_ID = "ISO_11799"


@dataclass(frozen=True)
class VariableLimit:
    """Límite normativo de una variable ambiental.

    Atributos:
        minimum: valor mínimo permitido, o None si la norma solo fija un máximo
                 (es el caso de eCO2 y TVOC, donde no existe un "mínimo" útil).
        maximum: valor máximo permitido.
        unit:    unidad mostrada en la interfaz.
    """

    minimum: float | None
    maximum: float
    unit: str

    def to_dict(self) -> dict[str, Any]:
        """Serializa el límite al formato JSON consumido por el navegador."""
        return {"min": self.minimum, "max": self.maximum, "unit": self.unit}


@dataclass(frozen=True)
class Profile:
    """Perfil normativo completo (temperatura, humedad, eCO2 y TVOC)."""

    profile_id: str
    name: str
    description: str
    temperature: VariableLimit
    humidity: VariableLimit
    eco2: VariableLimit
    tvoc: VariableLimit

    def to_dict(self) -> dict[str, Any]:
        """Convierte el perfil en un diccionario apto para JSON/Socket.IO."""
        return {
            "id": self.profile_id,
            "name": self.name,
            "description": self.description,
            "limits": {
                "temperature": self.temperature.to_dict(),
                "humidity": self.humidity.to_dict(),
                "eco2": self.eco2.to_dict(),
                "tvoc": self.tvoc.to_dict(),
            },
        }


def _profile_catalog() -> dict[str, Profile]:
    """Devuelve los tres perfiles normativos exigidos por el proyecto.

    Se construyen en una función (y no como constante mutable global) para que
    cada llamada entregue objetos inmutables independientes. Los valores son
    los publicados en el enunciado del TFG y constituyen los valores de
    fábrica; config.json puede contener exactamente este mismo contenido.
    """

    return {
        "ISO_11799": Profile(
            profile_id="ISO_11799",
            name="ISO 11799 - Archivos y Bibliotecas",
            description=(
                "Perfil de conservación preventiva para fondos documentales, "
                "bibliográficos y archivos permanentes."
            ),
            temperature=VariableLimit(14.0, 18.0, "°C"),
            humidity=VariableLimit(35.0, 50.0, "%"),
            eco2=VariableLimit(None, 800.0, "ppm"),
            tvoc=VariableLimit(None, 200.0, "ppb"),
        ),
        "EN_15757": Profile(
            profile_id="EN_15757",
            name="EN 15757 - Orgánicos e Higroscópicos",
            description=(
                "Perfil para materiales orgánicos sensibles a variaciones de "
                "humedad: papel, pergamino, cuero, textiles y madera."
            ),
            temperature=VariableLimit(18.0, 22.0, "°C"),
            humidity=VariableLimit(45.0, 55.0, "%"),
            eco2=VariableLimit(None, 1000.0, "ppm"),
            tvoc=VariableLimit(None, 300.0, "ppb"),
        ),
        "METALES": Profile(
            profile_id="METALES",
            name="Conservación de Metales Arqueológicos",
            description=(
                "Perfil de baja humedad para limitar la corrosión de metales "
                "arqueológicos y piezas con sales higroscópicas."
            ),
            temperature=VariableLimit(15.0, 24.0, "°C"),
            humidity=VariableLimit(0.0, 35.0, "%"),
            eco2=VariableLimit(None, 1200.0, "ppm"),
            tvoc=VariableLimit(None, 500.0, "ppb"),
        ),
    }


DEFAULT_PROFILES: dict[str, Profile] = _profile_catalog()


def default_config_path() -> Path:
    """Calcula la ruta persistente de config.json según el contexto de ejecución.

    PyInstaller --onefile descomprime la aplicación en un directorio temporal
    (_MEIPASS) que se elimina al terminar. Guardar allí el perfil haría que la
    selección se perdiera en cada reinicio, por eso cuando sys.frozen es True se
    usa el directorio del propio ejecutable.
    """

    override = os.environ.get("MICROCLIMA_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.json"

    # config.py está en src/microclima/, por lo que parents[2] es la raíz del
    # proyecto cuando se ejecuta desde el código fuente.
    return Path(__file__).resolve().parents[2] / "config.json"


def _default_config_document() -> dict[str, Any]:
    """Construye el documento JSON inicial completo y autocontenido."""
    return {
        "schema_version": SCHEMA_VERSION,
        "selected_profile": DEFAULT_PROFILE_ID,
        "profiles": {pid: profile.to_dict() for pid, profile in DEFAULT_PROFILES.items()},
    }


def _as_float(value: Any, *, field_name: str) -> float:
    """Convierte un valor JSON a float rechazando booleanos y NaN/infinito.

    bool es subclase de int en Python; aceptarlo convertiría true en 1.0 y
    ocultaría un error de configuración. Tampoco se permite NaN porque no es
    JSON válido y rompería las comparaciones del motor de alarmas.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name}: se esperaba un número, no un booleano")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name}: el número debe ser finito")
    return number


def _parse_limit(raw: Any, *, variable: str) -> VariableLimit:
    """Valida un bloque {"min": ..., "max": ..., "unit": ...} de config.json."""
    if not isinstance(raw, dict):
        raise ValueError(f"límite {variable}: se esperaba un objeto")
    if "max" not in raw:
        raise ValueError(f"límite {variable}: falta el campo max")
    maximum = _as_float(raw["max"], field_name=f"{variable}.max")
    minimum_raw = raw.get("min")
    minimum = None if minimum_raw is None else _as_float(
        minimum_raw, field_name=f"{variable}.min"
    )
    if minimum is not None and minimum > maximum:
        raise ValueError(f"límite {variable}: min ({minimum}) > max ({maximum})")
    unit = str(raw.get("unit", "")).strip()
    if not unit:
        raise ValueError(f"límite {variable}: la unidad no puede estar vacía")
    return VariableLimit(minimum, maximum, unit)


def _parse_profile(profile_id: str, raw: Any) -> Profile:
    """Convierte y valida un perfil cargado desde disco."""
    if not isinstance(raw, dict):
        raise ValueError(f"perfil {profile_id}: se esperaba un objeto")
    limits = raw.get("limits")
    if not isinstance(limits, dict):
        raise ValueError(f"perfil {profile_id}: falta el objeto limits")
    required = ("temperature", "humidity", "eco2", "tvoc")
    for variable in required:
        if variable not in limits:
            raise ValueError(f"perfil {profile_id}: falta el límite {variable}")

    return Profile(
        profile_id=profile_id,
        name=str(raw.get("name") or profile_id).strip(),
        description=str(raw.get("description") or "").strip(),
        temperature=_parse_limit(limits["temperature"], variable="temperature"),
        humidity=_parse_limit(limits["humidity"], variable="humidity"),
        eco2=_parse_limit(limits["eco2"], variable="eco2"),
        tvoc=_parse_limit(limits["tvoc"], variable="tvoc"),
    )


class ConfigStore:
    """Gestor thread-safe de config.json y del perfil seleccionado.

    El objeto se comparte entre el hilo lector de sensores, los manejadores de
    Socket.IO y el hilo del buzzer. Por ello todas las lecturas y escrituras se
    serializan con un RLock reentrante.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_config_path()
        self._lock = threading.RLock()
        self._profiles: dict[str, Profile] = copy.deepcopy(DEFAULT_PROFILES)
        self._selected_profile = DEFAULT_PROFILE_ID
        self._load_or_create()

    # ------------------------------------------------------------------ carga
    def _load_or_create(self) -> None:
        """Carga config.json o crea una versión de fábrica si no existe/es inválido.

        Si el archivo está corrupto se conserva una copia con extensión
        .corrupt-<fecha> antes de regenerarlo. Nunca se elimina silenciosamente
        una configuración potencialmente recuperable.
        """
        with self._lock:
            if not self.path.exists():
                LOGGER.info("No existe %s; creando configuración inicial.", self.path)
                self._profiles = copy.deepcopy(DEFAULT_PROFILES)
                self._selected_profile = DEFAULT_PROFILE_ID
                self._save_locked()
                return

            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    document = json.load(handle)
                self._apply_document(document)
                LOGGER.info(
                    "Configuración cargada desde %s (perfil=%s).",
                    self.path,
                    self._selected_profile,
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                backup = self.path.with_name(
                    f"{self.path.name}.corrupt-{datetime.now():%Y%m%d-%H%M%S}"
                )
                try:
                    self.path.replace(backup)
                    LOGGER.error(
                        "config.json inválido (%s). Copia conservada en %s. "
                        "Se restauran los valores de fábrica.",
                        exc,
                        backup,
                    )
                except OSError:
                    LOGGER.exception(
                        "config.json inválido (%s) y no se pudo crear la copia de "
                        "seguridad; se restauran los valores de fábrica.",
                        exc,
                    )
                self._profiles = copy.deepcopy(DEFAULT_PROFILES)
                self._selected_profile = DEFAULT_PROFILE_ID
                self._save_locked()

    def _apply_document(self, document: Any) -> None:
        """Valida el documento completo antes de sustituir el estado en memoria.

        Se valida primero en variables locales. Si algo falla, el estado
        anterior permanece intacto y el llamador puede regenerar el archivo.
        """
        if not isinstance(document, dict):
            raise ValueError("la raíz del JSON debe ser un objeto")

        profiles_raw = document.get("profiles")
        if not isinstance(profiles_raw, dict) or not profiles_raw:
            raise ValueError("falta el objeto profiles o está vacío")

        parsed: dict[str, Profile] = {}
        for profile_id, raw_profile in profiles_raw.items():
            key = str(profile_id).strip()
            if not key:
                raise ValueError("existe un perfil con identificador vacío")
            parsed[key] = _parse_profile(key, raw_profile)

        selected = str(document.get("selected_profile", "")).strip()
        if selected not in parsed:
            LOGGER.warning(
                "El perfil seleccionado '%s' no existe; se usará %s.",
                selected,
                DEFAULT_PROFILE_ID,
            )
            selected = DEFAULT_PROFILE_ID if DEFAULT_PROFILE_ID in parsed else next(iter(parsed))

        # Se garantiza que los tres perfiles normativos estén presentes. Si un
        # operador elimina uno del JSON, se recupera el de fábrica sin tocar los
        # límites de los perfiles que sí estaban definidos.
        for pid, default_profile in DEFAULT_PROFILES.items():
            if pid not in parsed:
                LOGGER.warning("Falta el perfil %s; se restaura su definición.", pid)
                parsed[pid] = default_profile

        self._profiles = parsed
        self._selected_profile = selected

    # ---------------------------------------------------------------- guardado
    def _save_locked(self) -> None:
        """Escribe config.json de forma atómica. El llamador debe poseer el lock."""
        document = {
            "schema_version": SCHEMA_VERSION,
            "selected_profile": self._selected_profile,
            "profiles": {pid: profile.to_dict() for pid, profile in self._profiles.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            # El fichero temporal se crea en el MISMO directorio para que
            # os.replace sea una operación atómica dentro del mismo sistema de
            # archivos. flush + fsync evitan que un corte deje datos en caché.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o644)
            os.replace(temp_name, self.path)
            temp_name = None

            # fsync del directorio: hace persistente la propia operación de
            # renombrado. En algunos sistemas de archivos puede no estar
            # permitido; no es un error funcional y se registra en depuración.
            try:
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                LOGGER.debug("No se pudo sincronizar el directorio de configuración.")
        except OSError:
            LOGGER.exception("No se pudo guardar la configuración en %s.", self.path)
            raise
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    # ----------------------------------------------------------------- lectura
    def profiles_payload(self) -> list[dict[str, Any]]:
        """Devuelve el catálogo de perfiles en orden estable para la web."""
        with self._lock:
            return [profile.to_dict() for profile in self._profiles.values()]

    def selected_profile_id(self) -> str:
        """Devuelve el identificador del perfil activo."""
        with self._lock:
            return self._selected_profile

    def selected_profile(self) -> Profile:
        """Devuelve una copia inmutable del perfil activo."""
        with self._lock:
            return self._profiles[self._selected_profile]

    def get_profile(self, profile_id: str) -> Profile | None:
        """Busca un perfil por identificador; None si no existe."""
        with self._lock:
            return self._profiles.get(profile_id)

    # ---------------------------------------------------------------- escritura
    def set_selected_profile(self, profile_id: str) -> Profile:
        """Cambia el perfil activo y lo persiste antes de confirmar el cambio.

        Si el guardado falla se restaura el identificador anterior en memoria y
        se propaga la excepción. De este modo, la web nunca muestra como
        seleccionado un perfil que no quedó guardado en disco.
        """
        candidate = str(profile_id).strip()
        with self._lock:
            if candidate not in self._profiles:
                raise KeyError(f"Perfil desconocido: {candidate}")
            previous = self._selected_profile
            if candidate == previous:
                return self._profiles[candidate]
            self._selected_profile = candidate
            try:
                self._save_locked()
            except OSError:
                self._selected_profile = previous
                raise
            LOGGER.info("Perfil activo cambiado y persistido: %s", candidate)
            return self._profiles[candidate]


def make_initial_config_document() -> dict[str, Any]:
    """Expone el documento de fábrica para herramientas de instalación/tests."""
    return _default_config_document()
