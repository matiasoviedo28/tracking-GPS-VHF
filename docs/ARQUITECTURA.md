# Arquitectura — bomberos-tracking

## 1. Objetivo del sistema

Recibir telemetría GPS de handys DMR (vía SDR y decodificación del protocolo LRRP),
persistirla, y mostrarla en un mapa web en tiempo real, con historial por equipo.

---

## 2. Arquitectura general (contenedores)

```
┌─────────────────────────────────────────────────────────┐
│                        Docker host                       │
│                                                           │
│  ┌──────────────┐    ┌──────────────┐    ┌────────────┐ │
│  │  sdr-decoder │───▶│   backend    │───▶│  frontend  │ │
│  └──────────────┘    └──────┬───────┘    └────────────┘ │
│                              │                            │
│                       ┌──────▼───────┐                    │
│                       │   database   │                    │
│                       └──────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

`sdr-decoder` no se conecta nunca directo a `database`. Toda persistencia pasa
obligatoriamente por `backend`, que es el único contenedor con acceso a la base.

### Contenedores

| Contenedor | Función |
|---|---|
| `sdr-decoder` | Acceso al hardware SDR (USB passthrough), decodificación DMR/LRRP, envío de telemetría al backend. |
| `backend` | API REST + WebSocket, lógica de negocio, persistencia, autenticación (a futuro). |
| `database` | Persistencia de equipos, posiciones e histórico. |
| `frontend` | Interfaz web con mapa interactivo, consumo de WebSocket + REST. |

### SDR y Docker — **RESUELTO**, containerizado y validado contra hardware real

Dado que el pipeline de decodificación era la pieza de mayor incertidumbre técnica
del proyecto, se prototipó primero en modo standalone (sin Docker, ver
`sdr-decoder/INVESTIGACION_LRRP.md`) y se containerizó una vez que la
decodificación fue estable — evitó mezclar dos fuentes de problemas nuevas al
mismo tiempo (protocolo + containerización), tal como se había planeado acá.

**Passthrough USB implementado**: se investigó (no se asumió) el mecanismo
correcto para pasar el dongle al contenedor `sdr-decoder`. `--privileged` se
descartó por exponer todo el host sin necesidad; en cambio, `docker-compose.yml`
hace bind-mount de `/dev/bus/usb` completo (no de un nodo puntual, que se rompe
con un replug al cambiar de bus/device) + `device_cgroup_rules: ["c 189:* rmw"]`
(wildcard sobre el major USB) — esto permite que un replug del dongle (nuevo
bus/device asignado por el kernel) siga siendo visible dentro del contenedor sin
reiniciarlo. El target único de despliegue es Ubuntu (decisión de proyecto, no
se contempla Windows).

El `Dockerfile` de `sdr-decoder` compila `mbelib` + `dsd-fme` desde código fuente
en un stage de build (misma receta validada manualmente en el host, ver
`sdr-decoder/INVESTIGACION_LRRP.md`), y copia solo el binario final + las
librerías compartidas imprescindibles (confirmadas con `ldd` sobre el binario
real) a un stage de runtime más liviano.

### Verificación de conexión del SDR — **RESUELTO**

El SDR queda conectado de forma permanente al servidor físico compartido (Ubuntu).
`sdr-decoder/check_sdr.sh` (implementado y probado contra el hardware real):

- Confirma que el driver DVB del kernel (`dvb_usb_rtl28xxu`) no esté cargado, y
  que el blacklist persistente exista en `/etc/modprobe.d/`.
- Confirma que el symlink estable de la regla `udev` (sección 9) exista y
  apunte a un nodo con los permisos esperados (`plugdev`, `0660`).
- Devuelve un código de salida claro (`0` = OK, distinto de `0` = problema).

**Corre siempre en el HOST, nunca dentro de un contenedor** — el driver DVB y las
reglas `udev` son configuración del kernel del host; ningún namespace ni flag de
Docker puede resolver esto desde adentro de un contenedor (confirmado
explícitamente investigando cómo lo maneja la comunidad de proyectos SDR en
Docker). Es un prerrequisito manual antes de `docker compose up`, documentado en
`docs/operacion-sdr.md`, no parte del `Dockerfile` ni de `docker-compose.yml`.

---

## 3. Comunicación entre contenedores

**Decisión:** `sdr-decoder` envía los datos a `backend` vía **HTTP POST simple**.
Cada vez que se decodifica un paquete LRRP, se realiza un POST a
`backend/api/telemetry`. Es el único mecanismo de conexión entre ambos servicios;
no se contempla cola de mensajes u otro mecanismo en esta etapa.

Justificación: simplicidad de implementación y debugging entre equipos, y el
volumen de datos esperado (6-20 equipos, actualizaciones cada varios minutos) no
justifica una cola de mensajes (Redis pub/sub o similar). Se reevaluará si el
volumen crece o si aparecen consumidores adicionales del mismo dato (por ejemplo,
un módulo de bitácora automática de eventos, evaluado como posible desarrollo
futuro).

**Política de reintentos:** `sdr-decoder` reintenta el POST hasta **5 veces**
buscando una respuesta `200`. Agotados los 5 intentos sin éxito, el dato se
descarta intencionalmente — no hay cola de reintento posterior ni persistencia
local de fallback. Se acepta la pérdida puntual a cambio de simplicidad,
considerando que un nuevo reporte de posición llega en pocos minutos.

**Contrato del endpoint:** definido por `backend` y documentado en `docs/API.md`
con ejemplo real de request/response (ver sección 8). Como mínimo incluye
`radio_id`, `lat`, `lon`, `timestamp` (del propio equipo transmisor), y
opcionalmente `altitud`, `velocidad`, `rumbo` cuando el protocolo LRRP los provea
de forma confiable.

---

## 4. Modelo de datos

### Tabla `equipos`

```
id              PK
radio_id        -- ID DMR del equipo
alias           -- Nombre legible (ej. "Móvil 3")
tipo            -- handy | movil | base
modelo          -- ej. DGP8550
activo          -- boolean
```

### Tabla `posiciones` (histórico)

```
id              PK
equipo_id       FK -> equipos
lat
lon
altitud
velocidad
rumbo           -- si LRRP lo provee
timestamp       -- momento del reporte GPS, generado por el equipo
recibido_en     -- momento en que sdr-decoder capturó el dato
```

### Consideraciones de diseño

- Se persiste el histórico completo de posiciones, no solo la última — habilita
  reproducción temporal (timelapse) del recorrido de cada equipo sin requerir
  cambios de esquema posteriores.
- Se distingue el timestamp de origen (del equipo) del timestamp de recepción —
  permite detectar demoras atribuibles al enlace de radio, diferenciándolas de
  problemas de la aplicación.
- El esquema deja abierta la incorporación futura de una tabla `eventos` hermana,
  para el eventual desarrollo de bitácora automática (fuera del alcance de esta
  versión).

### Motor de base de datos

**PostgreSQL.** Adecuado para el volumen esperado (6-20 equipos, actualizaciones
cada varios minutos), con soporte geoespacial nativo disponible (extensión
PostGIS) si en el futuro se requieren consultas espaciales — no necesario en esta
versión.

---

## 5. Backend

- **Stack:** **FastAPI (Python)**, confirmado. Comparte lenguaje con `sdr-decoder`
  y ofrece soporte nativo de WebSockets junto con REST.
- **Tiempo real:** WebSocket (`/ws/telemetry`) que emite cada posición nueva
  recibida desde `sdr-decoder`. El frontend se suscribe y actualiza sin polling.
- **Autenticación:** no requerida en esta versión (uso interno, red local). Queda
  pendiente si el sistema se expone fuera de la red del cuartel.

---

## 6. Frontend

- **Librería de mapa:** **Leaflet**, con tiles de OpenStreetMap.
- **Marcadores por equipo:** ícono distintivo por tipo (handy/móvil/base), con
  popup al hacer clic que muestra:
  - Alias e identificador del equipo
  - Última posición (lat/lon, hora)
  - Velocidad y rumbo, cuando estén disponibles
  - Acceso al histórico de recorrido (línea de tiempo/timelapse)
- **Actualización:** en tiempo real vía WebSocket, sin necesidad de recargar la
  página.

---

## 7. Estructura del repositorio

```
bomberos-tracking/
├── docker-compose.yml
├── sdr-decoder/
│   ├── Dockerfile
│   └── ...
├── backend/
│   ├── Dockerfile
│   └── ...
├── frontend/
│   ├── Dockerfile
│   └── ...
└── docs/
    ├── ARQUITECTURA.md
    ├── API.md
    └── protocolo-lrrp.md
```

`docs/protocolo-lrrp.md` documenta el trabajo de decodificación e ingeniería
inversa del protocolo — valioso como referencia del proyecto y, potencialmente,
para publicación posterior siguiendo el mismo criterio aplicado en otros proyectos
de radio del cuartel (documentación pública, código de producción privado).

---

## 8. Decisiones y responsabilidades definidas

1. **Contrato del endpoint de telemetría:** diseñado y documentado por el equipo
   de backend en `docs/API.md`, con ejemplo real (request/response, campos, tipos,
   códigos de estado). El equipo de decodificación consume ese contrato ya
   definido.
2. **Estabilidad de la decodificación:** responsabilidad del equipo de
   `sdr-decoder`. La calidad del dato (señal, protocolo, decodificación) es
   independiente del desarrollo de `backend`/`frontend`, que se construyen para
   recibir lo que llegue por el endpoint definido, sin acoplarse a los detalles
   internos de captura.
3. **Identificación de equipos:** `sdr-decoder` incluye en cada envío al menos
   `radio_id`, `radio_ip` y `radio_alias` — el alias humano viaja junto con la
   telemetría, sin requerir tabla de mapeo separada en esta versión. Una tabla de
   override en `backend` (para editar el alias desde la interfaz) queda como
   posible extensión futura.
4. **Hardware SDR:** un único dongle. No se contempla en esta versión escucha
   simultánea de uplink y downlink, ni redundancia con múltiples unidades.

---

## 9. Identificación estable del SDR (udev) — **RESUELTO, con la limitación ya cerrada**

Regla `udev` implementada en `/etc/udev/rules.d/99-rtlsdr-tracking.rules`,
independiente del bus/puerto USB de conexión:

- Matcheo por `idVendor`/`idProduct` del dispositivo (RTL2832U: `0bda:2832`) **Y**
  `ATTRS{serial}` (número de serie real que expone este dongle físico:
  `77771111153705700`, confirmado con `lsusb -v`).
- Symlink fijo: `/dev/sdr_bomberos`.
- Permisos de grupo `plugdev`, modo `0660`.

Esto garantiza que el dispositivo sea identificable de forma estable entre
reinicios y reconexiones (verificado con `udevadm trigger` re-evaluando la
regla sin necesitar recompilar nada), y lo verifica `check_sdr.sh` (sección 2).

**Limitación conocida cerrada**: la versión anterior de esta regla (genérica de
`librtlsdr`, matcheando solo por `idVendor`/`idProduct`) no distinguía entre dos
dongles idénticos en la misma máquina — quedaba anotado acá como limitación
futura, condicionada a "si el chip expone número de serie". Se confirmó que
**sí lo expone**, así que la regla nueva ya matchea también por
`ATTRS{serial}`, identificando la unidad física exacta, no solo el modelo de
chip. Si en el futuro se reemplaza el dongle físico, hay que actualizar el
serial en la regla.

**Nota de contenedores**: dentro de Docker, `sdr-decoder` NO monta este symlink
puntual (`/dev/sdr_bomberos`) — monta `/dev/bus/usb` completo (ver sección 2,
por resiliencia ante replugs) y selecciona el dispositivo por índice/serie de
`librtlsdr` (`SDR_DEVICE_INDEX`). El symlink del host sigue siendo útil para
`check_sdr.sh` y para cualquier uso manual del dongle fuera de Docker (ej. el
procedimiento de recalibración de PPM, ver `docs/operacion-sdr.md`).

---

## 10. Plan de trabajo

Desarrollo directo contra los componentes reales, sin mocks intermedios.

1. **`sdr-decoder` y `backend` en paralelo:** el equipo de decodificación avanza
   sobre el pipeline SDR → LRRP mientras el equipo de backend implementa el modelo
   de datos (sección 4) y el endpoint definido en `docs/API.md`. Objetivo de esta
   etapa: que `sdr-decoder` pueda enviar telemetría real al endpoint real, y que
   `backend` la persista en PostgreSQL.
2. **`frontend`:** se inicia una vez que `backend` recibe y persiste datos reales
   (aunque de forma intermitente mientras `sdr-decoder` continúa en ajuste), ya
   conectado a datos reales en base — no a datos de prueba.
3. Integración completa vía `docker-compose up`, con el passthrough del USB del
   SDR resuelto según sección 2.
4. Prueba de punta a punta con equipos reales del cuartel.