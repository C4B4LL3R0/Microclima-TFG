#!/usr/bin/env bash
# =============================================================================
# ARCHIVO: scripts/setup_ap.sh
# PROYECTO: Monitor de Microclima Edge para Conservación Preventiva
# FUNCIÓN: Crear una red Wi-Fi cerrada (sin Internet) para la Raspberry Pi 3B.
# =============================================================================
#
# Resultado esperado:
#   - SSID: Microclima_Archivo_01 (configurable con AP_SSID).
#   - Seguridad WPA2-PSK con contraseña aleatoria la primera vez.
#   - IP estática 192.168.4.1/24 en wlan0.
#   - DHCP y DNS locales gestionados por dnsmasq (rango 192.168.4.10-100).
#   - Punto de acceso hostapd en 2,4 GHz (canal 6 por defecto).
#   - Interfaz web del monitor en http://192.168.4.1:5000.
#   - SIN NAT, SIN reenvío IP y SIN DNS ascendente: los clientes no tienen
#     salida a Internet, aunque el puerto 5000 del servidor es accesible.
#
# Uso:
#   sudo ./scripts/setup_ap.sh
#   sudo AP_PASSPHRASE='MiClaveSegura2026' ./scripts/setup_ap.sh
#   sudo ASSUME_YES=1 ./scripts/setup_ap.sh       # sin preguntas (laboratorio)
#   sudo FORCE=1 ./scripts/setup_ap.sh            # omitir comprobación de Pi
#
# ADVERTENCIA:
#   Este script reconfigura wlan0 y puede cortar una conexión SSH/Wi-Fi activa.
#   Se recomienda ejecutarlo desde consola local o por Ethernet. Hace copia de
#   seguridad de los ficheros modificados en /var/backups/microclima-ap-*.
#
# Idempotencia:
#   Se puede ejecutar varias veces. Mantiene la contraseña guardada en
#   /etc/microclima/ap.env (permisos 600) salvo que se indique AP_PASSPHRASE.
#
# Requisitos: Raspberry Pi OS (Bullseye o Bookworm), hostapd, dnsmasq, iw,
# rfkill e iproute2. El script instala los paquetes que falten con apt-get.
# =============================================================================

set -Eeuo pipefail

# -----------------------------------------------------------------------------
# 0. Comprobaciones de seguridad y privilegios.
# -----------------------------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: ejecute este script con sudo: sudo $0" >&2
  exit 1
fi

MODEL=""
if [[ -r /proc/device-tree/model ]]; then
  MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
fi
if [[ "${MODEL}" != *"Raspberry Pi"* && "${FORCE:-0}" != "1" ]]; then
  echo "ERROR: no se detecta una Raspberry Pi (modelo: '${MODEL:-desconocido}')." >&2
  echo "Este script modifica hostapd/dnsmasq y la red del equipo." >&2
  echo "Use FORCE=1 solo si sabe lo que hace." >&2
  exit 1
fi

# -----------------------------------------------------------------------------
# 1. Parámetros. Se puede sobrescribir cualquiera mediante variables de entorno.
#    La contraseña no se fija en el código: se conserva en /etc/microclima/ap.env
#    o se genera aleatoriamente en el primer despliegue.
# -----------------------------------------------------------------------------
AP_IFACE="${AP_IFACE:-wlan0}"
AP_SSID_ENV="${AP_SSID:-}"
AP_SSID="${AP_SSID:-Microclima_Archivo_01}"
AP_COUNTRY="${AP_COUNTRY:-ES}"
AP_CHANNEL="${AP_CHANNEL:-6}"
AP_IP="${AP_IP:-192.168.4.1}"
AP_PREFIX="${AP_PREFIX:-24}"
DHCP_START="${DHCP_START:-192.168.4.10}"
DHCP_END="${DHCP_END:-192.168.4.100}"
DHCP_LEASE="${DHCP_LEASE:-12h}"
WEB_PORT="${WEB_PORT:-5000}"
STATE_DIR="/etc/microclima"
STATE_FILE="${STATE_DIR}/ap.env"
HOSTAPD_CONF="/etc/hostapd/hostapd.conf"
DNSMASQ_CONF="/etc/dnsmasq.d/microclima.conf"
NM_CONF="/etc/NetworkManager/conf.d/99-microclima-unmanaged.conf"
IP_UNIT="/etc/systemd/system/microclima-ap-ip.service"
BACKUP_DIR="/var/backups/microclima-ap-$(date +%Y%m%d-%H%M%S)"

# Recuperar la contraseña previa solo si el operador no la ha pasado por entorno.
if [[ -f "${STATE_FILE}" ]]; then
  if [[ -z "${AP_SSID_ENV}" ]]; then
    saved_ssid="$(sed -n 's/^AP_SSID=//p' "${STATE_FILE}" | head -n 1)"
    [[ -n "${saved_ssid}" ]] && AP_SSID="${saved_ssid}"
  fi
  if [[ -z "${AP_PASSPHRASE:-}" ]]; then
    saved_pass="$(sed -n 's/^AP_PASSPHRASE=//p' "${STATE_FILE}" | head -n 1)"
    [[ -n "${saved_pass}" ]] && AP_PASSPHRASE="${saved_pass}"
  fi
fi
AP_PASSPHRASE="${AP_PASSPHRASE:-}"

if [[ -z "${AP_PASSPHRASE}" ]]; then
  # Contraseña aleatoria. Se evita un pipe infinito porque con pipefail un
  # SIGPIPE de tr abortaría el script. openssl rand -hex no depende del azar de
  # cuántos bytes entrega el buffer; el fallback usa una entrada acotada.
  if command -v openssl >/dev/null 2>&1; then
    AP_PASSPHRASE="$(openssl rand -hex 12)"
  else
    AP_PASSPHRASE="$(dd if=/dev/urandom bs=64 count=1 2>/dev/null | base64 | tr -dc 'A-Za-z0-9' | cut -c1-20)"
  fi
fi

# Validaciones de longitud y formato exigidas por hostapd/WPA2.
if (( ${#AP_PASSPHRASE} < 8 || ${#AP_PASSPHRASE} > 63 )); then
  echo "ERROR: AP_PASSPHRASE debe tener entre 8 y 63 caracteres." >&2
  exit 1
fi
if [[ "${AP_PASSPHRASE}" == *$'\n'* || "${AP_PASSPHRASE}" == *$'\r'* ]]; then
  echo "ERROR: AP_PASSPHRASE no puede contener saltos de línea." >&2
  exit 1
fi
SSID_BYTES="$(printf '%s' "${AP_SSID}" | wc -c)"
if (( SSID_BYTES < 1 || SSID_BYTES > 32 )); then
  echo "ERROR: AP_SSID debe ocupar entre 1 y 32 bytes (UTF-8)." >&2
  exit 1
fi
if [[ "${AP_SSID}" == *$'\n'* || "${AP_SSID}" == *$'\r'* ]]; then
  echo "ERROR: AP_SSID no puede contener saltos de línea." >&2
  exit 1
fi
if ! [[ "${AP_CHANNEL}" =~ ^([1-9]|1[0-3])$ ]]; then
  echo "ERROR: AP_CHANNEL debe ser un canal 2,4 GHz entre 1 y 13." >&2
  exit 1
fi

# -----------------------------------------------------------------------------
# 2. Aviso y confirmación. Si la sesión actual entra por wlan0, el cambio de
#    configuración cortará la conexión. No se puede evitar: es la propia
#    interfaz que se convierte en punto de acceso.
# -----------------------------------------------------------------------------
DEFAULT_IFACE="$(ip route show default 2>/dev/null | awk 'NR==1 {print $5}')"
echo "=============================================================="
echo " Configuración de red Wi-Fi cerrada para Microclima Edge"
echo "=============================================================="
echo " Interfaz:     ${AP_IFACE}"
echo " SSID:         ${AP_SSID}"
echo " Seguridad:    WPA2-PSK"
echo " IP del AP:    ${AP_IP}/${AP_PREFIX}"
echo " DHCP:         ${DHCP_START} - ${DHCP_END}"
echo " Web:          http://${AP_IP}:${WEB_PORT}"
echo " Internet:     NO (sin NAT, sin forwarding, sin DNS ascendente)"
echo "--------------------------------------------------------------"
if [[ "${DEFAULT_IFACE}" == "${AP_IFACE}" ]]; then
  echo " AVISO: la conexión por defecto usa ${AP_IFACE}."
  echo "        Si está conectado por SSH, la sesión se interrumpirá."
fi
if [[ "${ASSUME_YES:-0}" != "1" ]]; then
  if [[ ! -t 0 ]]; then
    echo "ERROR: sin terminal interactiva. Use ASSUME_YES=1 para automatizar." >&2
    exit 1
  fi
  read -r -p "¿Continuar con la reconfiguración? [s/N] " answer
  if [[ ! "${answer}" =~ ^[sS]$ ]]; then
    echo "Cancelado por el usuario."
    exit 0
  fi
fi

# -----------------------------------------------------------------------------
# 3. Dependencias. Se instala lo imprescindible si no está presente. En una red
#    sin Internet esto debe hacerse antes; si apt falla, el script se detiene.
# -----------------------------------------------------------------------------
echo "[1/8] Comprobando paquetes..."
missing=()
command -v hostapd >/dev/null 2>&1 || missing+=(hostapd)
command -v dnsmasq >/dev/null 2>&1 || missing+=(dnsmasq)
command -v iw >/dev/null 2>&1 || missing+=(iw)
command -v rfkill >/dev/null 2>&1 || missing+=(rfkill)
if (( ${#missing[@]} > 0 )); then
  echo "       Instalando: ${missing[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}"
fi
command -v ip >/dev/null 2>&1 || { echo "ERROR: falta iproute2." >&2; exit 1; }

# La interfaz debe existir (en una Pi 3B integrada es wlan0).
if ! ip link show "${AP_IFACE}" >/dev/null 2>&1; then
  echo "ERROR: la interfaz ${AP_IFACE} no existe. Compruebe 'ip link'." >&2
  exit 1
fi

# Copia de seguridad de cada fichero que se va a modificar.
mkdir -p "${BACKUP_DIR}"
for file in \
  "${HOSTAPD_CONF}" \
  "${DNSMASQ_CONF}" \
  "${NM_CONF}" \
  "${IP_UNIT}" \
  /etc/default/hostapd \
  /etc/dnsmasq.conf \
  /etc/dhcpcd.conf \
  /etc/systemd/system/hostapd.service.d/microclima.conf \
  /etc/systemd/system/dnsmasq.service.d/microclima.conf \
  "${STATE_FILE}"; do
  if [[ -e "${file}" ]]; then
    cp --parents -a "${file}" "${BACKUP_DIR}/"
  fi
done
echo "       Copia de seguridad: ${BACKUP_DIR}"

# -----------------------------------------------------------------------------
# 4. Neutralizar gestores que intentarían conectar wlan0 como cliente.
#    - NetworkManager: marcar wlan0 como unmanaged.
#    - dhcpcd: denyinterfaces wlan0.
#    - wpa_supplicant@wlan0: detenerlo si existe.
#    La IP la asigna después una unidad systemd propia, común a ambos gestores.
# -----------------------------------------------------------------------------
echo "[2/8] Liberando ${AP_IFACE} de gestores de red de estación..."
install -d -m 755 /etc/NetworkManager/conf.d
cat >"${NM_CONF}" <<EOF
# Generado por Microclima Edge. wlan0 lo controla hostapd, no NetworkManager.
[keyfile]
unmanaged-devices=interface-name:${AP_IFACE}
EOF

if systemctl is-active --quiet NetworkManager; then
  if command -v nmcli >/dev/null 2>&1; then
    nmcli device set "${AP_IFACE}" managed no >/dev/null 2>&1 || true
    nmcli general reload >/dev/null 2>&1 || true
  fi
fi

if [[ -f /etc/dhcpcd.conf ]]; then
  if ! grep -q '^# BEGIN MICROCLIMA AP' /etc/dhcpcd.conf; then
    cat >>/etc/dhcpcd.conf <<EOF

# BEGIN MICROCLIMA AP
# La IP de wlan0 la configura microclima-ap-ip.service; dhcpcd no debe tocarla.
denyinterfaces ${AP_IFACE}
# END MICROCLIMA AP
EOF
  fi
  if systemctl is-active --quiet dhcpcd; then
    systemctl restart dhcpcd || true
  fi
fi

if systemctl list-unit-files "wpa_supplicant@${AP_IFACE}.service" >/dev/null 2>&1; then
  systemctl stop "wpa_supplicant@${AP_IFACE}.service" >/dev/null 2>&1 || true
  systemctl disable "wpa_supplicant@${AP_IFACE}.service" >/dev/null 2>&1 || true
fi

systemctl stop hostapd >/dev/null 2>&1 || true
systemctl stop dnsmasq >/dev/null 2>&1 || true
rfkill unblock wifi >/dev/null 2>&1 || true

# -----------------------------------------------------------------------------
# 5. hostapd: punto de acceso WPA2 en 2,4 GHz. Se usa hw_mode=g e ieee80211n
#    porque la Raspberry Pi 3B no tiene 5 GHz. La red es visible; "cerrada"
#    significa aislada de Internet, no SSID oculto, para facilitar el uso.
# -----------------------------------------------------------------------------
echo "[3/8] Generando configuración de hostapd..."
install -d -m 755 /etc/hostapd
{
  printf '%s\n' \
    '# =====================================================================' \
    '# Generado por Microclima Edge. No editar a mano: ejecute setup_ap.sh.' \
    '# Punto de acceso WPA2 para una red local de conservación sin Internet.' \
    '# =====================================================================' \
    "interface=${AP_IFACE}" \
    'driver=nl80211' \
    "country_code=${AP_COUNTRY}" \
    'ieee80211d=1' \
    'hw_mode=g' \
    "channel=${AP_CHANNEL}" \
    'ieee80211n=1' \
    'wmm_enabled=1' \
    "ssid=${AP_SSID}" \
    'ignore_broadcast_ssid=0' \
    'auth_algs=1' \
    'macaddr_acl=0' \
    'wpa=2' \
    "wpa_passphrase=${AP_PASSPHRASE}" \
    'wpa_key_mgmt=WPA-PSK' \
    'rsn_pairwise=CCMP' \
    'wpa_group_rekey=600' \
    'ap_isolate=0'
} >"${HOSTAPD_CONF}"
chmod 600 "${HOSTAPD_CONF}"

# /etc/default/hostapd es el punto de entrada del servicio Debian.
if [[ ! -f /etc/default/hostapd ]]; then
  touch /etc/default/hostapd
fi
if grep -q '^#*DAEMON_CONF=' /etc/default/hostapd; then
  sed -i 's|^#*DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
else
  echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >>/etc/default/hostapd
fi
systemctl unmask hostapd >/dev/null 2>&1 || true

# -----------------------------------------------------------------------------
# 6. dnsmasq: DHCP y DNS exclusivamente en wlan0. No se declara ningún servidor
#    DNS ascendente, por lo que los clientes no pueden resolver Internet. Se
#    publica microclima.local para comodidad dentro de la red.
# -----------------------------------------------------------------------------
echo "[4/8] Generando configuración de dnsmasq..."
install -d -m 755 /etc/dnsmasq.d
{
  printf '%s\n' \
    '# =====================================================================' \
    '# Generado por Microclima Edge. DHCP/DNS de la red aislada.' \
    '# =====================================================================' \
    "interface=${AP_IFACE}" \
    'bind-dynamic' \
    "listen-address=${AP_IP}" \
    'no-resolv' \
    'no-poll' \
    'domain-needed' \
    'bogus-priv' \
    'local-service' \
    'dhcp-authoritative' \
    'log-dhcp' \
    "dhcp-range=${DHCP_START},${DHCP_END},255.255.255.0,${DHCP_LEASE}" \
    "dhcp-option=3,${AP_IP}" \
    "dhcp-option=6,${AP_IP}" \
    "address=/microclima.local/${AP_IP}" \
    'domain=lan' \
    'local-ttl=60'
} >"${DNSMASQ_CONF}"

# Debian incluye /etc/dnsmasq.d si esta directiva está activa. Si un sistema
# minimalista la ha comentado, se añade para que nuestro drop-in se lea.
if ! grep -Eq '^[[:space:]]*conf-dir=/etc/dnsmasq\.d' /etc/dnsmasq.conf; then
  printf '\n# Microclima Edge: cargar drop-ins de dnsmasq\nconf-dir=/etc/dnsmasq.d/,*.conf\n' \
    >>/etc/dnsmasq.conf
fi

# -----------------------------------------------------------------------------
# 7. IP estática persistente. Se usa una unidad oneshot en lugar de dhcpcd o
#    NetworkManager para que funcione igual en Bullseye y Bookworm. Se ejecuta
#    antes de hostapd/dnsmasq y vuelve a aplicar la dirección en cada arranque.
# -----------------------------------------------------------------------------
echo "[5/8] Creando servicio de IP estática..."
IP_BIN="$(command -v ip)"
IW_BIN="$(command -v iw || true)"
cat >"${IP_UNIT}" <<EOF
[Unit]
Description=Microclima Edge - IP estática de ${AP_IFACE}
Documentation=file:///etc/microclima/ap.env
After=network-pre.target
Before=hostapd.service dnsmasq.service
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
# 'replace' es idempotente: no duplica la dirección si ya existía.
ExecStart=${IP_BIN} link set dev ${AP_IFACE} up
ExecStart=${IP_BIN} addr replace ${AP_IP}/${AP_PREFIX} dev ${AP_IFACE}
EOF
if [[ -n "${IW_BIN}" ]]; then
  # Desactivar el ahorro de energía mejora la estabilidad del AP con clientes
  # que duermen; el guion '-' permite que el servicio no falle si no aplica.
  printf 'ExecStart=-%s dev %s set power_save off\n' "${IW_BIN}" "${AP_IFACE}" >>"${IP_UNIT}"
fi
cat >>"${IP_UNIT}" <<EOF

[Install]
WantedBy=multi-user.target
EOF

# Drop-ins de systemd para ordenar y reiniciar los servicios si fallan.
install -d -m 755 /etc/systemd/system/hostapd.service.d
cat >/etc/systemd/system/hostapd.service.d/microclima.conf <<EOF
[Unit]
Wants=microclima-ap-ip.service
After=microclima-ap-ip.service

[Service]
Restart=on-failure
RestartSec=3
EOF
install -d -m 755 /etc/systemd/system/dnsmasq.service.d
cat >/etc/systemd/system/dnsmasq.service.d/microclima.conf <<EOF
[Unit]
Wants=microclima-ap-ip.service hostapd.service
After=microclima-ap-ip.service hostapd.service

[Service]
Restart=on-failure
RestartSec=3
EOF

# Guardar parámetros para futuras ejecuciones. La contraseña queda solo en root.
install -d -m 700 "${STATE_DIR}"
umask 077
cat >"${STATE_FILE}" <<EOF
# Generado por setup_ap.sh. Contiene la clave WPA2: no compartir.
AP_SSID=${AP_SSID}
AP_PASSPHRASE=${AP_PASSPHRASE}
AP_IFACE=${AP_IFACE}
AP_IP=${AP_IP}
WEB_PORT=${WEB_PORT}
EOF
chmod 600 "${STATE_FILE}"
umask 022

# -----------------------------------------------------------------------------
# 8. Activar y arrancar. No se habilita NAT ni ip_forward: la red queda aislada.
#    Si ufw está activo se abren solo los puertos necesarios.
# -----------------------------------------------------------------------------
echo "[6/8] Activando servicios..."
systemctl daemon-reload
systemctl enable microclima-ap-ip.service >/dev/null
systemctl enable hostapd.service >/dev/null
systemctl enable dnsmasq.service >/dev/null

# Limpiar una posible IP antigua de DHCP y aplicar la estática.
ip addr flush dev "${AP_IFACE}" >/dev/null 2>&1 || true
systemctl restart microclima-ap-ip.service
systemctl restart hostapd.service
systemctl restart dnsmasq.service

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi '^Status: active'; then
  ufw allow 67/udp >/dev/null 2>&1 || true
  ufw allow 53/tcp >/dev/null 2>&1 || true
  ufw allow 53/udp >/dev/null 2>&1 || true
  ufw allow "${WEB_PORT}/tcp" >/dev/null 2>&1 || true
fi

echo "[7/8] Verificando servicios..."
sleep 2
ok=1
if ! systemctl is-active --quiet hostapd; then
  echo "ERROR: hostapd no está activo. Últimas líneas del journal:" >&2
  journalctl -u hostapd --no-pager -n 30 >&2 || true
  ok=0
fi
if ! systemctl is-active --quiet dnsmasq; then
  echo "ERROR: dnsmasq no está activo. Últimas líneas del journal:" >&2
  journalctl -u dnsmasq --no-pager -n 30 >&2 || true
  ok=0
fi
if ! ip -4 addr show "${AP_IFACE}" | grep -q "inet ${AP_IP}/"; then
  echo "ERROR: ${AP_IFACE} no tiene la IP ${AP_IP}. Diagnóstico:" >&2
  ip -4 addr show "${AP_IFACE}" >&2 || true
  ok=0
fi
if (( ok == 0 )); then
  echo "La configuración quedó escrita, pero algún servicio falló." >&2
  echo "Backup: ${BACKUP_DIR}" >&2
  exit 1
fi

# -----------------------------------------------------------------------------
# 9. Resumen final. No se imprime la contraseña en logs persistentes; solo por
#    la terminal del operador. Si se usa qrencode, se muestra un QR opcional.
# -----------------------------------------------------------------------------
echo "[8/8] Red Wi-Fi configurada correctamente."
echo "=============================================================="
echo " SSID:       ${AP_SSID}"
echo " Contraseña: ${AP_PASSPHRASE}"
echo " IP del AP:  ${AP_IP}"
echo " Web:        http://${AP_IP}:${WEB_PORT}"
echo " Fichero de estado: ${STATE_FILE} (solo root)"
echo " Backup:     ${BACKUP_DIR}"
echo "--------------------------------------------------------------"
echo " La red NO tiene salida a Internet: no se ha configurado NAT ni"
echo " reenvío IP, y dnsmasq no usa resolutores externos."
echo ""
echo " Arranque el monitor con:"
echo "   cd $(pwd)"
echo "   sudo ./dist/microclima_rpi"
echo " o, desde el código fuente:"
echo "   sudo .venv/bin/python src/microclima_rpi.py"
echo "=============================================================="

if command -v qrencode >/dev/null 2>&1; then
  echo ""
  echo "QR para conectar el móvil a la red (WPA2):"
  qrencode -t ANSIUTF8 "WIFI:T:WPA;S:${AP_SSID};P:${AP_PASSPHRASE};;" || true
fi
