# Monitor de Microclima Edge para Conservación Preventiva

Sistema autónomo de vigilancia ambiental para archivos, bibliotecas, museos y
depósitos. Mide temperatura, humedad relativa, eCO₂ equivalente y COV totales,
evalúa las medidas contra tres perfiles normativos y avisa mediante una interfaz
web local y un buzzer activo. Todo el software funciona **sin conexión a
Internet**: la interfaz, Socket.IO y los recursos gráficos se sirven desde el
propio equipo.

El proyecto incluye dos ejecutables:

| Ejecutable | Plataforma | Sensores | Buzzer | Panel de simulación |
|---|---|---|---|---|
| `microclima_sim` | Kubuntu / PC x86_64 | Generador interno | Emulación por consola | Sí |
| `microclima_rpi` | Raspberry Pi 3B / ARM | BME280 + SGP30 + DS3231 por I2C1 | GPIO17, pin físico 11 | No |

Ambos comparten el 100 % de la lógica de perfiles, alarmas, persistencia y web.
La única diferencia es la pieza que entrega las medidas y el driver acústico.

---

## 1. Características

- **Tres perfiles normativos** seleccionables y persistidos en `config.json`.
- **Sincronización en tiempo real** entre todos los navegadores mediante
  Flask-SocketIO: perfil, silencio, medidas y estado del simulador.
- **Alarma acústica no bloqueante** con patrones distintos para temperatura
  alta/baja, humedad alta/baja, calidad de aire y múltiples variables.
- **Interfaz 100 % offline**, responsive y sin CDN, con Socket.IO local.
- **Tolerancia a fallos**: si un sensor I2C falla, la web sigue operativa y
  muestra el diagnóstico; si el bus completo no abre, el servidor no entra en
  un bucle de reinicio.
- **Persistencia atómica** de la configuración (fichero temporal + `fsync` +
  `os.replace`), con copia de seguridad si el JSON está corrupto.
- **Modo simulación** con generación automática o inyección manual de las cuatro
  magnitudes desde sliders y campos numéricos.
- **Empaquetado onefile** con PyInstaller, incluyendo `templates/` y `static/`.

---

## 2. Estructura del proyecto

```text
.
├── README.md                     Este documento
├── requirements.txt              Dependencias Python
├── build_executables.sh          Compilación de ambos ejecutables
├── config.json                   Estado persistido (se crea al arrancar)
├── src/
│   ├── microclima_sim.py         Punto de entrada de simulación
│   ├── microclima_rpi.py         Punto de entrada de producción
│   └── microclima/
│       ├── config.py             Perfiles, validación y config.json atómico
│       ├── logging_setup.py      Logging a consola y fichero
│       ├── core/
│       │   ├── alarm.py          Patrones acústicos y evaluación de umbrales
│       │   ├── monitor.py        Hilo periódico de adquisición
│       │   └── runtime.py        Orquestador de estado y eventos
│       ├── hardware/
│       │   └── buzzer.py         Driver GPIO, consola y controlador no bloqueante
│       ├── sensors/
│       │   ├── base.py           Contrato SensorReader / SensorReading
│       │   ├── bme280.py         Driver I2C del BME280 (0x76)
│       │   ├── sgp30.py          Driver I2C del SGP30 (0x58)
│       │   ├── ds3231.py         RTC DS3231 (0x68) y sincronización horaria
│       │   ├── rpi.py            Composición de los tres dispositivos
│       │   └── simulator.py      Generador auto/manual para Kubuntu
│       └── web/
│           └── app.py            Flask, Socket.IO y endpoints HTTP
├── templates/index.html          Interfaz única (SPA)
├── static/
│   ├── css/style.css             Diseño responsive y estados de alarma
│   └── js/
│       ├── app.js                Cliente Socket.IO y renderizado
│       └── socket.io.min.js      Socket.IO 4.8.1 servido localmente
├── scripts/setup_ap.sh           Punto de acceso Wi-Fi aislado
├── build/
│   ├── microclima_sim.spec       Receta PyInstaller para x86_64
│   ├── microclima_rpi.spec       Receta PyInstaller para ARM
│   └── README.md                 Notas de empaquetado
└── dist/                         Ejecutables generados (no versionado)
```

---

## 3. Requisitos

### 3.1. Kubuntu / PC de desarrollo

- Python 3.10 o superior (probado con Python 3.14).
- `python3-venv`, `python3-pip`.
- Navegador con JavaScript y WebSocket.
- No se necesita hardware I2C ni GPIO.

### 3.2. Raspberry Pi 3B

- Raspberry Pi OS Bullseye o Bookworm (32 o 64 bits).
- I2C1 activado (`raspi-config` → Interface Options → I2C).
- Paquetes del sistema: `python3-venv`, `python3-pip`, `i2c-tools`,
  `hostapd`, `dnsmasq`, `iw`, `rfkill`.
- BME280, SGP30, DS3231 y buzzer activo.
- Fuente de alimentación estable de 5 V; los picos del buzzer no deben hundir
  la tensión de la Pi.

---

## 4. Instalación y ejecución en Kubuntu

```bash
cd ~/microclima_edge          # o la carpeta donde esté el proyecto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Arranque desde el código fuente
python3 src/microclima_sim.py

# O con el ejecutable ya compilado
./dist/microclima_sim
```

La interfaz queda en **http://127.0.0.1:5000**. El simulador escucha en
`0.0.0.0` por defecto para poder abrirlo desde otro equipo de la misma red
local; no lo exponga a Internet.

Variables de entorno útiles:

```bash
MICROCLIMA_PORT=5001 \
MICROCLIMA_SAMPLE_INTERVAL=0.5 \
MICROCLIMA_CONFIG=/tmp/microclima_config.json \
python3 src/microclima_sim.py
```

| Variable | Defecto | Descripción |
|---|---|---|
| `MICROCLIMA_HOST` | `0.0.0.0` | Interfaz de escucha. |
| `MICROCLIMA_PORT` | `5000` | Puerto HTTP/WebSocket. |
| `MICROCLIMA_SAMPLE_INTERVAL` | `1.0` | Segundos entre muestras (0,2–60). |
| `MICROCLIMA_CONFIG` | junto al ejecutable | Ruta alternativa de `config.json`. |

### 4.1. Panel de simulación

En modo `microclima_sim` aparece el **Panel de Inyección Manual**:

1. Active **Valores manuales**.
2. Mueva los sliders o escriba en los campos numéricos de temperatura, humedad,
   eCO₂ y TVOC.
3. El servidor valida cada valor y difunde el nuevo estado a todos los clientes.
4. Al desactivar el interruptor, el generador vuelve a modo automático.

El modo automático genera una onda suave alrededor del perfil activo; de vez en
cuando cruza un umbral para poder observar la alarma sin tocar los controles.

---

## 5. Instalación en Raspberry Pi 3B

### 5.1. Conexionado

Todos los dispositivos comparten el bus I2C1 (GPIO2 = SDA, GPIO3 = SCL) y
3,3 V. Las direcciones no entran en conflicto.

| Dispositivo | Dirección | VCC | GND | SDA | SCL | Otros |
|---|---|---|---|---|---|---|
| BME280 | `0x76` | Pin 1 (3V3) | Pin 6 | Pin 3 (GPIO2) | Pin 5 (GPIO3) | SDO a GND para `0x76` |
| SGP30 | `0x58` | Pin 1 o 17 (3V3) | Pin 6 o 9 | Pin 3 (GPIO2) | Pin 5 (GPIO3) | Módulo con regulador |
| DS3231 | `0x68` | Pin 1 (3V3) | Pin 6 | Pin 3 (GPIO2) | Pin 5 (GPIO3) | Batería CR2032 |
| Buzzer activo | GPIO17 | — | Pin 9 o 14 | — | — | Positivo al pin 11 (GPIO17) |

Notas de montaje:

- Un **buzzer activo** solo necesita nivel alto/bajo; no se genera PWM. Si el
  módulo consume más corriente de la que puede entregar GPIO17, use un
  transistor NPN/MOSFET y una resistencia de base, alimentando el buzzer desde
  3,3 V o 5 V según su tensión nominal.
- Si el buzzer es **activo-bajo**, arranque con
  `MICROCLIMA_BUZZER_ACTIVE_LOW=1` o construya el driver con `active_high=False`.
- Mantenga los cables I2C cortos y con masa común. Un bus mal conectado produce
  errores intermitentes que la interfaz mostrará como incidencias.
- El DS3231 debe llevar una CR2032 en buen estado. Si el bit OSF está activo, la
  hora no se considera fiable y el sistema lo avisa.

### 5.2. Preparar Raspberry Pi OS

```bash
sudo raspi-config
# Interface Options -> I2C -> Yes
# Interface Options -> SPI -> No (no se usa)
sudo reboot

sudo apt update
sudo apt install -y python3-venv python3-pip i2c-tools hostapd dnsmasq iw rfkill

sudo usermod -aG i2c,gpio "$USER"
# Cierre sesión y vuelva a entrar para aplicar los grupos
```

Compruebe que los tres dispositivos responden:

```bash
i2cdetect -y 1
```

Debe mostrar `58`, `68` y `76`. Si aparece `77`, el SDO del BME280 está a 3V3;
cambie el driver a `0x77` o reconecte SDO a GND. Si no aparece `76`, revise el
cableado y que el sensor sea un BME280 (chip ID `0x60`), no un BMP280.

### 5.3. Instalar el proyecto

```bash
cd ~
git clone <URL-del-repositorio> microclima_edge   # o copie la carpeta
cd microclima_edge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5.4. Ejecutar

```bash
# Desde el código fuente
sudo .venv/bin/python src/microclima_rpi.py

# O con el ejecutable compilado en la propia Pi
sudo ./dist/microclima_rpi
```

La interfaz queda en **http://192.168.4.1:5000** cuando el punto de acceso está
activo, o en la IP de la Pi si se conecta por Ethernet/LAN.

### 5.5. Permisos del RTC

El DS3231 se lee siempre, pero **ajustar el reloj del sistema** requiere
privilegios. El programa intenta `time.clock_settime(CLOCK_REALTIME, ...)` y,
si falla, `date -s @epoch`. Sin permisos, registra el aviso y continúa
monitorizando; la hora de los registros puede no ser fiable.

Opciones:

1. **Ejecutar con `sudo`** (sencillo para un equipo dedicado).
2. **Dar la capacidad `CAP_SYS_TIME` al servicio** (recomendado):

```ini
# /etc/systemd/system/microclima.service
[Unit]
Description=Monitor de Microclima Edge
After=network.target microclima-ap-ip.service
Wants=microclima-ap-ip.service

[Service]
Type=simple
User=pi
Group=pi
SupplementaryGroups=i2c gpio
WorkingDirectory=/home/pi/microclima_edge
ExecStart=/home/pi/microclima_edge/dist/microclima_rpi
Restart=on-failure
RestartSec=5
AmbientCapabilities=CAP_SYS_TIME
CapabilityBoundingSet=CAP_SYS_TIME
Environment=MICROCLIMA_CONFIG=/home/pi/microclima_edge/config.json

[Install]
WantedBy=multi-user.target
```

Ajuste `User`, `Group`, rutas y grupos a su instalación. Si el grupo `gpio` no
existe, elimine `gpio` de `SupplementaryGroups`. Después:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now microclima.service
journalctl -u microclima.service -f
```

> `setup_ap.sh` no instala este servicio: solo configura hostapd, dnsmasq y la
> IP estática. El servicio del monitor se crea aparte para que el operador
> decida cómo arrancarlo.

---

## 6. Punto de acceso Wi-Fi aislado

El script `scripts/setup_ap.sh` convierte `wlan0` en un punto de acceso WPA2
para una red de conservación **sin salida a Internet**:

- SSID por defecto: `Microclima_Archivo_01`.
- IP del AP: `192.168.4.1/24`.
- DHCP: `192.168.4.10`–`192.168.4.100`, máscara `255.255.255.0`.
- DNS local: dnsmasq, sin resolutores ascendentes.
- Web: `http://192.168.4.1:5000`.
- Sin NAT, sin `ip_forward` y sin DNS externo: los clientes solo alcanzan el
  monitor y los servicios locales.

### 6.1. Ejecución

```bash
sudo ./scripts/setup_ap.sh

# Personalizar SSID y contraseña
sudo AP_SSID='Microclima_Sala_2' AP_PASSPHRASE='UnaClaveLarga2026' \
  ./scripts/setup_ap.sh

# Automatizar sin preguntas (solo laboratorio)
sudo ASSUME_YES=1 ./scripts/setup_ap.sh
```

La primera ejecución genera una contraseña aleatoria de 24 caracteres
hexadecimales y la guarda en `/etc/microclima/ap.env` con permisos `600`. El
script la imprime una sola vez al final; si la pierde, consulte ese fichero como
root o vuelva a ejecutar con `AP_PASSPHRASE`.

> **Advertencia:** el script reconfigura `wlan0`. Si está conectado por SSH a
> través de Wi-Fi, la sesión se cortará. Ejecútelo desde consola local o por
> Ethernet. Antes de modificar nada crea una copia de seguridad en
> `/var/backups/microclima-ap-<fecha>/`.

### 6.2. Comprobar el AP

```bash
systemctl status hostapd dnsmasq microclima-ap-ip
ip -4 addr show wlan0
journalctl -u hostapd -n 30 --no-pager
journalctl -u dnsmasq -n 30 --no-pager
```

Desde un móvil o portátil, conéctese al SSID y abra
`http://192.168.4.1:5000`. No habrá Internet: es intencionado.

### 6.3. Revertir la configuración

El script no elimina los ficheros originales: los guarda en el directorio de
backup que imprime al terminar. Para volver a la configuración anterior:

```bash
sudo systemctl disable --now hostapd dnsmasq microclima-ap-ip.service
sudo systemctl daemon-reload
# Restaure desde /var/backups/microclima-ap-*/ los ficheros que desee recuperar
# y reinicie los servicios de red.
```

---

## 7. Perfiles normativos

Los límites viven en `src/microclima/config.py` y se persisten en
`config.json`. La web **no duplica** los umbrales: recibe el catálogo por
Socket.IO y dibuja exactamente lo que evalúa el servidor.

| Perfil | Temperatura | Humedad relativa | eCO₂ máx. | TVOC máx. |
|---|---|---|---|---|
| `ISO_11799` — Archivos y Bibliotecas | 14,0–18,0 °C | 35,0–50,0 % | 800 ppm | 200 ppb |
| `EN_15757` — Orgánicos e Higroscópicos | 18,0–22,0 °C | 45,0–55,0 % | 1000 ppm | 300 ppb |
| `METALES` — Metales Arqueológicos | 15,0–24,0 °C | 0,0–35,0 % | 1200 ppm | 500 ppb |

El perfil inicial es `ISO_11799`. Al cambiarlo desde cualquier navegador:

1. El servidor valida el identificador.
2. Persiste la selección en `config.json` de forma atómica.
3. Reajusta la alarma con los nuevos límites.
4. Emite `profile_changed` y `state_update` a **todos** los clientes.

### 7.1. `config.json`

Se crea en la raíz del proyecto cuando se ejecuta desde el código fuente, o
junto al ejecutable cuando `sys.frozen` es verdadero. En PyInstaller onefile
**no** se guarda dentro de `_MEIPASS`, porque ese directorio se destruye al
cerrar el programa.

```json
{
  "schema_version": 1,
  "selected_profile": "ISO_11799",
  "profiles": {
    "ISO_11799": {
      "id": "ISO_11799",
      "name": "ISO 11799 - Archivos y Bibliotecas",
      "description": "...",
      "limits": {
        "temperature": { "min": 14.0, "max": 18.0, "unit": "°C" },
        "humidity": { "min": 35.0, "max": 50.0, "unit": "%" },
        "eco2": { "min": null, "max": 800.0, "unit": "ppm" },
        "tvoc": { "min": null, "max": 200.0, "unit": "ppb" }
      }
    }
  }
}
```

- Si el archivo no existe, se crea con los valores de fábrica.
- Si el JSON es inválido, se conserva una copia
  `config.json.corrupt-<fecha>` y se restauran los valores de fábrica.
- Si falta uno de los tres perfiles, se recupera su definición por defecto.
- La escritura usa un fichero temporal en el mismo directorio, `fsync` y
  `os.replace`, protegida por un `RLock`.
- Para usar otra ruta: `MICROCLIMA_CONFIG=/var/lib/microclima/config.json`.

---

## 8. Patrones acústicos

El motor de alarmas es puro y determinista. Cuenta cada magnitud individual
fuera de rango; si hay dos o más, prevalece `MULTI`. Por tanto, eCO₂ y TVOC
altos a la vez también producen alarma múltiple.

| Patrón | Condición | Pitidos | Pausa entre pitidos | Repetición | Código |
|---|---|---|---|---|---|
| `temperature_high` | Temperatura > máximo | 600, 200 ms | 180 ms | 1,8 s | `— ·` |
| `temperature_low` | Temperatura < mínimo | 600, 200, 200 ms | 180 ms | 1,8 s | `— · ·` |
| `humidity_high` | Humedad > máximo | 600, 600, 200 ms | 180 ms | 1,8 s | `— — ·` |
| `humidity_low` | Humedad < mínimo | 600, 600, 200, 200 ms | 180 ms | 1,8 s | `— — · ·` |
| `air_quality_high` | eCO₂ o TVOC fuera de límite | 200, 200, 200 ms | 100 ms | 1,5 s | `· · ·` |
| `multiple_alerts` | ≥ 2 magnitudes fuera de rango | 100 ms × 5 | 100 ms | 1,0 s | `· · · · ·` |
| `silent` | Todo dentro de norma | — | — | — | — |

El **botón de silencio** es global: un cliente lo pulsa y el servidor emite
`mute_changed` a todos. El silencio es de ejecución, no se persiste: al
reiniciar, el sistema vuelve a sonar. El patrón sigue retenido y se reanuda al
reactivar el sonido.

El hilo del buzzer es independiente del de sensores y del servidor. Los cambios
de patrón o de silencio se aplican con una `Condition`, sin `sleep()` en el hilo
de Flask, de modo que la web nunca se bloquea durante un pitido.

---

## 9. Interfaz web y eventos

### 9.1. Endpoints HTTP

| Ruta | Método | Descripción |
|---|---|---|
| `/` | GET | Interfaz SPA con el modo inyectado. |
| `/health` | GET | Diagnóstico; `200` si hay lectura sana, `503` si degradado. |
| `/api/state` | GET | Estado JSON completo, solo lectura. |
| `/favicon.ico` | GET | Respuesta `204` para evitar ruido en logs. |

### 9.2. Socket.IO

Servidor → cliente:

| Evento | Contenido |
|---|---|
| `initial_state` | Estado completo al conectar (solo al nuevo cliente). |
| `state_update` | Estado completo tras cada muestra o cambio. |
| `profile_changed` | Perfil seleccionado, perfil completo y catálogo. |
| `mute_changed` | `{ muted: bool }` para todos los clientes. |
| `sim_changed` | Estado del panel de simulación (solo modo sim). |
| `server_error` | Error de validación dirigido al cliente que lo provocó. |

Cliente → servidor:

| Evento | Payload | Respuesta |
|---|---|---|
| `select_profile` | `{ "profile_id": "EN_15757" }` | ACK `{ ok, selected_profile }`. |
| `set_mute` | `{ "muted": true }` | ACK `{ ok, muted }`. |
| `sim_control` | `{ mode?, temperature?, humidity?, eco2?, tvoc? }` | ACK `{ ok, mode, values }`. |
| `request_state` | `{}` | ACK `{ ok }` y `initial_state`. |

El cliente usa `textContent`, nunca `innerHTML` con datos del servidor. La
política CSP solo permite recursos locales (`default-src 'self'`), y
`connect-src 'self' ws: wss:` habilita el WebSocket.

### 9.3. Tarjetas y diagnóstico

Cada tarjeta muestra valor, unidad, rango permitido y estado
**Normal / Alerta / Sin datos**. El panel de sistema añade presión barométrica,
última lectura, fuente de datos, salida acústica, errores de sensores, RTC y
plataforma. Los errores I2C no se ocultan: en un equipo de archivo es preferible
un aviso visible a una medida silenciosamente obsoleta.

---

## 10. Compilación de ejecutables

### 10.1. Kubuntu x86_64

```bash
source .venv/bin/activate
./build_executables.sh --sim-only
```

El script comprueba dependencias y recursos, y ejecuta PyInstaller con
`--onefile`, `--add-data "templates:templates"` y
`--add-data "static:static"`. El resultado es `dist/microclima_sim`.

También puede usar la receta reproducible:

```bash
.venv/bin/python -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller/sim \
  build/microclima_sim.spec
```

### 10.2. Raspberry Pi 3B

PyInstaller **no es un compilador cruzado**: debe ejecutarse en la propia Pi
(o en otra máquina ARM de la misma arquitectura).

```bash
# En la Raspberry Pi
cd ~/microclima_edge
source .venv/bin/activate
./build_executables.sh --rpi-only

# Alternativa con spec
.venv/bin/python -m PyInstaller --noconfirm --clean \
  --distpath dist --workpath build/pyinstaller/rpi \
  build/microclima_rpi.spec
```

El resultado es `dist/microclima_rpi`. La receta incluye `smbus2`, `gpiozero`,
`lgpio`, el driver threading de Engine.IO y `simple-websocket`.

### 10.3. Notas de empaquetado

- `config.json` **no** se empaqueta: se crea junto al ejecutable para que sea
  persistente y editable.
- Los recursos se extraen en `sys._MEIPASS`; `web/app.py::_resource_base`
  localiza `templates/` y `static/` tanto en fuente como en onefile.
- `build_executables.sh` genera un spec en `build/generated/` y deja los
  artefactos intermedios en `build/pyinstaller/`.
- El ejecutable de simulación pesa aproximadamente 15 MB.

---

## 11. Verificación rápida

```bash
# 1. Sintaxis Python
.venv/bin/python -m compileall -q src

# 2. Sintaxis del script de red
bash -n scripts/setup_ap.sh

# 3. Arranque del simulador
MICROCLIMA_PORT=5000 .venv/bin/python src/microclima_sim.py &
curl -fsS http://127.0.0.1:5000/health

# 4. Recursos offline
for p in / /static/css/style.css /static/js/app.js /static/js/socket.io.min.js; do
  curl -s -o /dev/null -w "%{http_code} $p\n" "http://127.0.0.1:5000$p"
done

# 5. Estado y perfiles
curl -fsS http://127.0.0.1:5000/api/state | python3 -m json.tool | head -40

# 6. Detener
kill %1
```

Para probar la sincronización entre dos navegadores, abra dos pestañas en
`http://127.0.0.1:5000`, cambie el perfil en una y compruebe que la otra
actualiza el desplegable, las tarjetas y los límites sin recargar. Pulse el
botón de silencio en una y verifique que la otra muestra el mismo estado.

---

## 12. Solución de problemas

### No aparecen los sensores en `i2cdetect`

- Compruebe que I2C está activo: `ls /dev/i2c-*`.
- Revise VCC, GND, SDA y SCL; use masa común.
- BME280 en `0x76` (SDO a GND) o `0x77` (SDO a 3V3).
- SGP30 en `0x58`; DS3231 en `0x68`.
- Si el BME280 no responde, verifique que no sea un BMP280 (sin humedad).

### `Permission denied: /dev/i2c-1`

```bash
sudo usermod -aG i2c "$USER"
# Cierre sesión y vuelva a entrar, o reinicie
```

### El RTC no ajusta la hora

- Compruebe la batería CR2032 y el bit OSF.
- Ejecute con `sudo` o use `AmbientCapabilities=CAP_SYS_TIME` en systemd.
- Sin permisos, el monitor sigue funcionando pero la hora puede ser incorrecta.

### El buzzer no suena

- Verifique GPIO17 = pin físico 11 y la masa.
- Un buzzer activo-bajo necesita `MICROCLIMA_BUZZER_ACTIVE_LOW=1`.
- Si GPIO17 no puede con la corriente, use un transistor.
- Compruebe `gpiozero`/`lgpio` y los permisos del grupo `gpio`.
- En simulación no hay sonido real: solo se dibuja en la consola.

### El puerto 5000 está ocupado

```bash
sudo ss -ltnp | grep 5000
MICROCLIMA_PORT=5001 ./dist/microclima_sim
```

### El AP no aparece o no asigna IP

```bash
rfkill list
systemctl status hostapd dnsmasq microclima-ap-ip
journalctl -u hostapd -n 50 --no-pager
journalctl -u dnsmasq -n 50 --no-pager
ip -4 addr show wlan0
```

Compruebe el país (`AP_COUNTRY`), el canal (`AP_CHANNEL`, 1–13) y que la
interfaz sea `wlan0`. Si NetworkManager sigue gestionando la interfaz, revise
`/etc/NetworkManager/conf.d/99-microclima-unmanaged.conf`.

### La web carga pero no hay datos

- Abra `/health`; si devuelve `503`, revise `sensor_errors`.
- Consulte `microclima.log` y la consola.
- En la Pi, compruebe `i2cdetect -y 1` y los errores I2C.
- En simulación, compruebe que el panel no esté en un valor fuera de rango
  mientras espera una lectura.

### `config.json` corrupto

El sistema crea `config.json.corrupt-<fecha>` y restaura los valores de fábrica.
Revise la copia si necesita recuperar un perfil personalizado.

### El silencio desaparece al reiniciar

Es el comportamiento previsto: el mute es de ejecución y no se persiste. El
perfil normativo sí se persiste.

---

## 13. Seguridad y alcance

- La interfaz **no tiene autenticación**. La protección es la red WPA2 aislada;
  no exponga el puerto 5000 a Internet ni a una red no confiable.
- El AP no ofrece NAT, forwarding ni DNS ascendente: los clientes no tienen
  salida a Internet.
- La contraseña WPA2 se guarda solo en `/etc/microclima/ap.env` (root, `600`) y
  en `/etc/hostapd/hostapd.conf` (root, `600`).
- El servidor añade cabeceras `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy` y CSP restrictiva.
- El proyecto es material académico de un TFG de Mantenimiento Electrónico; no
  sustituye una validación metrológica ni un plan de conservación profesional.

---

## 14. Créditos y normativa

- **ISO 11799**: información y documentación — requisitos de almacenamiento de
  archivos y bibliotecas.
- **EN 15757**: conservación del patrimonio cultural — especificaciones para
  temperatura y humedad relativa en materiales orgánicos higroscópicos.
- Perfil **METALES**: criterio de baja humedad para limitar la corrosión de
  metales arqueológicos y piezas con sales higroscópicas.

Los límites concretos usados por el monitor están en `config.py` y son los
definidos en el enunciado del proyecto. Verifique siempre la normativa vigente
antes de aplicar el equipo en un archivo real.
