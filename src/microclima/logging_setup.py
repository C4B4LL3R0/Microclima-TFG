# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/logging_setup.py
 FUNCIÓN: Configuración uniforme de logs para consola y archivo rotativo.
===============================================================================

 En un equipo de conservación que puede llevar semanas encendido, los logs son
 imprescindibles para diagnosticar caídas del bus I2C, cambios de perfil o
 reinicios. Se escribe un archivo microclima.log junto a config.json y se rota
 al alcanzar 1 MiB, conservando cinco copias. Si el directorio no es escribible,
 la aplicación continúa solo con salida por consola.
===============================================================================
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from microclima.config import default_config_path

_LOG_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> Path | None:
    """Instala handlers de consola y archivo una sola vez.

    Parámetros:
        level: nivel mínimo de logging (INFO por defecto).

    Devuelve:
        Ruta del archivo de log o None si no se pudo crear.
    """
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return None

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(level)
    root.addHandler(console)

    log_path: Path | None = None
    override = os.environ.get("MICROCLIMA_LOG", "").strip()
    candidate = (
        Path(override).expanduser().resolve()
        if override
        else default_config_path().parent / "microclima.log"
    )
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            candidate,
            maxBytes=1_048_576,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
        log_path = candidate
    except OSError as exc:
        root.warning("No se pudo abrir el archivo de log %s: %s", candidate, exc)

    # Werkzeug imprime una línea por petición HTTP; en una red cerrada resulta
    # ruidoso. Se deja en WARNING para conservar solo los avisos relevantes.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.WARNING)

    _LOG_CONFIGURED = True
    if log_path is not None:
        logging.getLogger(__name__).info("Registro de actividad: %s", log_path)
    return log_path
