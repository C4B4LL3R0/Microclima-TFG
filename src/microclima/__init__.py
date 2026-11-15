# -*- coding: utf-8 -*-
"""Paquete principal del Monitor de Microclima Edge (TFG de Mantenimiento Electrónico).

La versión se mantiene aquí para que la web, los logs y PyInstaller compartan
un único identificador. El paquete se divide en:
  - config: perfiles normativos y persistencia atómica.
  - sensors: drivers I2C y simulador.
  - hardware: abstracción del buzzer.
  - core: monitor periódico, motor de alarmas y runtime.
  - web: Flask-SocketIO y contrato de eventos.
"""

__version__ = "1.0.0"
