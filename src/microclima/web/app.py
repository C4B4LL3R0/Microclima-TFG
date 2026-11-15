# -*- coding: utf-8 -*-
"""
===============================================================================
 ARCHIVO: src/microclima/web/app.py
 FUNCIÓN: Servidor Flask + Flask-SocketIO y contrato de eventos en tiempo real.
===============================================================================

 Esta capa expone la interfaz web y traduce eventos de Socket.IO en llamadas al
 MicroclimaRuntime. No implementa lógica de alarmas ni de sensores.

 Eventos servidor -> cliente:
   initial_state  : estado completo al conectar (solo al nuevo cliente).
   state_update   : estado completo tras cada muestra o cambio.
   profile_changed: perfil normativo cambiado (broadcast a TODOS).
   mute_changed   : estado de silencio cambiado (broadcast a TODOS).
   sim_changed    : estado del panel de simulación (solo modo sim).
   server_error   : error de validación dirigido al cliente que lo provocó.

 Eventos cliente -> servidor:
   select_profile {profile_id}
   set_mute       {muted}
   sim_control    {mode?, temperature?, humidity?, eco2?, tvoc?}
   request_state  {}  (recuperación explícita tras reconexión)

 El servidor usa async_mode="threading" y simple-websocket. Se evita eventlet o
 gevent para simplificar PyInstaller y porque la carga de una red cerrada con
 pocos navegadores es mínima. Werkzeug sirve la aplicación en el puerto 5000 de
 la red AP 192.168.4.1.
===============================================================================
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request
from flask_socketio import SocketIO, emit

from microclima.core.runtime import MicroclimaRuntime

LOGGER = logging.getLogger(__name__)

APP_VERSION = "1.0.0"


def _resource_base() -> Path:
    """Localiza templates/static tanto en código fuente como en PyInstaller.

    En un ejecutable --onefile, Flask no puede usar rutas relativas al módulo
    porque este vive dentro de _MEIPASS. PyInstaller define sys._MEIPASS y las
    opciones --add-data copian allí templates/ y static/.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    # web/app.py -> web -> microclima -> src -> raíz del proyecto
    return Path(__file__).resolve().parents[3]


class WebApplication:
    """Aplicación web completa, lista para ejecutarse con .run()."""

    def __init__(self, runtime: MicroclimaRuntime) -> None:
        self.runtime = runtime
        base = _resource_base()
        self.app = Flask(
            __name__,
            template_folder=str(base / "templates"),
            static_folder=str(base / "static"),
            static_url_path="/static",
        )
        self.app.config.update(
            JSON_SORT_KEYS=False,
            SEND_FILE_MAX_AGE_DEFAULT=0,
            TEMPLATES_AUTO_RELOAD=False,
        )

        # threading + simple-websocket: sin dependencias de servidores
        # asíncronos que compliquen el empaquetado en ARM. El tráfico previsto
        # (unos pocos clientes en una red AP aislada) es muy bajo.
        self.socketio = SocketIO(
            self.app,
            async_mode="threading",
            cors_allowed_origins=[],
            allow_upgrades=True,
            ping_interval=25,
            ping_timeout=60,
            max_http_buffer_size=1_000_000,
            logger=False,
            engineio_logger=False,
        )

        self._clients: set[str] = set()
        self._clients_lock = threading.Lock()
        self._shutdown_requested = False

        self._register_http_routes()
        self._register_socket_events()
        # Cada lectura del monitor llamará a este difusor desde su propio hilo.
        # Flask-SocketIO es thread-safe para emit en modo threading.
        self.runtime.set_state_callback(self._broadcast_state)

    # -------------------------------------------------------------------- HTTP
    def _register_http_routes(self) -> None:
        """Registra la página principal y los endpoints de diagnóstico."""

        @self.app.after_request
        def security_headers(response: Response) -> Response:
            """Añade cabeceras defensivas y evita caché de la interfaz dinámica."""
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; "
                "connect-src 'self' ws: wss:; "
                "font-src 'self'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'"
            )
            if request.path == "/":
                response.headers["Cache-Control"] = "no-store"
            return response

        @self.app.get("/")
        def index() -> str:
            """Sirve la SPA offline con el modo de ejecución inyectado."""
            return render_template(
                "index.html",
                mode=self.runtime.mode,
                mode_label=self.runtime.mode_label,
                app_version=APP_VERSION,
            )

        @self.app.get("/health")
        def health() -> tuple[Response, int]:
            """Diagnóstico para scripts de arranque, systemd o curl."""
            payload = self.runtime.health_payload()
            status_code = 200 if payload["status"] == "ok" else 503
            return jsonify(payload), status_code

        @self.app.get("/api/state")
        def api_state() -> Response:
            """Estado JSON de solo lectura para depuración o integraciones."""
            return jsonify(self.runtime.build_state())

        @self.app.get("/favicon.ico")
        def favicon() -> Response:
            """Evita un 404 molesto en los registros del servidor."""
            return Response(status=204)

    # ------------------------------------------------------------------ Socket.IO
    def _register_socket_events(self) -> None:
        """Define los manejadores de eventos y sus validaciones."""

        @self.socketio.on("connect")
        def on_connect(auth: Any = None) -> None:
            """Añade el cliente y le envía el estado actual solo a él.

            No se difunde initial_state a todos porque cada cliente ya tiene su
            propia instantánea; el broadcast se reserva para cambios reales.
            """
            sid = request.sid
            with self._clients_lock:
                self._clients.add(sid)
                count = len(self._clients)
            LOGGER.info("Cliente web conectado (%s). Total=%d.", sid, count)
            emit("initial_state", self.runtime.build_state(), to=sid)

        @self.socketio.on("disconnect")
        def on_disconnect(reason: Any = None) -> None:
            """Elimina el cliente del registro al cerrar la pestaña o red."""
            sid = request.sid
            with self._clients_lock:
                self._clients.discard(sid)
                count = len(self._clients)
            LOGGER.info("Cliente web desconectado (%s). Total=%d.", sid, count)

        @self.socketio.on("request_state")
        def on_request_state(data: Any = None) -> dict[str, Any]:
            """Permite recuperar el estado de forma explícita."""
            emit("initial_state", self.runtime.build_state(), to=request.sid)
            return {"ok": True}

        @self.socketio.on("select_profile")
        def on_select_profile(data: Any = None) -> dict[str, Any]:
            """Cambia el perfil, lo persiste y lo difunde a todos los clientes.

            El orden es deliberado:
              1. Persistir (si falla, no se anuncia nada).
              2. Reajustar alarma/buzzer con los nuevos límites.
              3. Emitir profile_changed a todos los clientes.
              4. Emitir state_update para refrescar tarjetas.

            `socketio.emit(...)` sin destinatario difunde a todas las sesiones
            conectadas. En Flask-SocketIO 5.7 la opción `broadcast=True` ya no
            se acepta en este contexto y provocaría un TypeError.
            """
            if not isinstance(data, dict):
                return self._server_error("Formato de perfil no válido.")
            profile_id = str(data.get("profile_id", "")).strip()
            if not profile_id:
                return self._server_error("Falta profile_id.")
            try:
                profile = self.runtime.select_profile(profile_id)
            except KeyError:
                return self._server_error(f"El perfil '{profile_id}' no existe.")
            except OSError as exc:
                LOGGER.exception("No se pudo persistir el perfil.")
                return self._server_error(
                    f"No se pudo guardar config.json: {exc}"
                )
            except Exception as exc:
                LOGGER.exception("Error inesperado al seleccionar el perfil.")
                return self._server_error(f"Error al cambiar el perfil: {exc}")

            payload = self.runtime.profile_event_payload()
            # Sin `to=` ni `broadcast=`: Flask-SocketIO difunde el evento a todas
            # las sesiones conectadas, de modo que el resto de dispositivos
            # actualiza el desplegable y los límites sin recargar la página.
            self.socketio.emit("profile_changed", payload)
            self.runtime.publish_state()
            return {"ok": True, "selected_profile": profile.profile_id}

        @self.socketio.on("set_mute")
        def on_set_mute(data: Any = None) -> dict[str, Any]:
            """Silencia o reactiva la alarma para todos los navegadores."""
            if not isinstance(data, dict) or not isinstance(data.get("muted"), bool):
                return self._server_error("El campo muted debe ser booleano.")
            try:
                effective = self.runtime.set_muted(data["muted"])
            except Exception as exc:
                LOGGER.exception("Error cambiando el estado de silencio.")
                return self._server_error(f"No se pudo cambiar el sonido: {exc}")
            self.socketio.emit("mute_changed", {"muted": effective})
            self.runtime.publish_state()
            return {"ok": True, "muted": effective}

        @self.socketio.on("sim_control")
        def on_sim_control(data: Any = None) -> dict[str, Any]:
            """Aplica los sliders del modo pruebas y difunde el nuevo estado."""
            if self.runtime.mode != "sim":
                return self._server_error(
                    "El panel de simulación solo está disponible en microclima_sim."
                )
            if not isinstance(data, dict):
                return self._server_error("Formato de simulación no válido.")
            try:
                update = self.runtime.update_simulation(data)
            except (ValueError, TypeError) as exc:
                return self._server_error(str(exc))
            except Exception as exc:
                LOGGER.exception("Error aplicando la simulación manual.")
                return self._server_error(f"Error de simulación: {exc}")

            state = self.runtime.build_state()
            # El cambio ya se ha difundido por state_update al forzar la muestra;
            # sim_changed permite a clientes especializados escuchar solo el panel.
            self.socketio.emit("sim_changed", state.get("sim"))
            return {"ok": True, "mode": update.mode, "values": update.values}

    def _server_error(self, message: str) -> dict[str, Any]:
        """Envía un error al cliente actual y devuelve el ACK de Socket.IO."""
        LOGGER.warning("Evento rechazado: %s", message)
        emit("server_error", {"message": message}, to=request.sid)
        return {"ok": False, "error": message}

    # ----------------------------------------------------------------- difusión
    def _broadcast_state(self, state: dict[str, Any]) -> None:
        """Difunde el estado completo a todos los clientes conectados."""
        self.socketio.emit("state_update", state)

    def client_count(self) -> int:
        """Número de navegadores conectados (diagnóstico)."""
        with self._clients_lock:
            return len(self._clients)

    # ----------------------------------------------------------------- ejecución
    def run(self, host: str = "0.0.0.0", port: int = 5000) -> None:
        """Arranca el runtime y bloquea con el servidor HTTP/WebSocket.

        Se instala un manejador de SIGTERM para que systemd o `kill` ejecuten el
        bloque finally y se apague el buzzer/GPIO de forma limpia.
        """
        previous_handler = None

        def _request_shutdown(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt

        try:
            previous_handler = signal.signal(signal.SIGTERM, _request_shutdown)
        except (ValueError, OSError):
            # No estamos en el hilo principal (por ejemplo, en un test).
            LOGGER.debug("No se pudo instalar el manejador de SIGTERM.")

        try:
            self.runtime.start()
            LOGGER.info(
                "Interfaz disponible en http://%s:%d (modo=%s).",
                "127.0.0.1" if host == "0.0.0.0" else host,
                port,
                self.runtime.mode,
            )
            # allow_unsafe_werkzeug: Flask-SocketIO lo exige al usar el servidor
            # de desarrollo con threading. En esta red local aislada y con pocos
            # clientes es suficiente; no se expone a Internet.
            self.socketio.run(
                self.app,
                host=host,
                port=port,
                debug=False,
                use_reloader=False,
                allow_unsafe_werkzeug=True,
            )
        except KeyboardInterrupt:
            LOGGER.info("Interrupción solicitada; cerrando aplicación.")
        finally:
            self.runtime.stop()
            if previous_handler is not None:
                try:
                    signal.signal(signal.SIGTERM, previous_handler)
                except (ValueError, OSError):
                    pass
