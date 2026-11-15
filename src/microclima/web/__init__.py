# -*- coding: utf-8 -*-
"""Capa web del Monitor de Microclima Edge.

Expone la interfaz offline y el contrato Socket.IO. `app` construye Flask y
Flask-SocketIO, registra los endpoints HTTP y traduce los eventos de los
navegadores en llamadas al `MicroclimaRuntime`; no contiene lógica de alarmas ni
de sensores.
"""
