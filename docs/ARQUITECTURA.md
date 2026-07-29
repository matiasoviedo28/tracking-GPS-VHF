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

### SDR y Docker

El USB del SDR requiere passthrough directo al contenedor `sdr-decoder`
(`--device=...`). Para evitar depender de un path de bus/puerto USB inestable entre
reinicios o reconexiones, se define un identificador fijo por regla `udev` (ver
sección 9).

Dado que el pipeline de decodificación es la pieza de mayor incertidumbre técnica
del proyecto, se prototipa primero en modo standalone (sin Docker) y se
containeriza una vez que la decodificación sea estable — evita mezclar dos fuentes
de problemas nuevas al mismo tiempo (protocolo + containerización).

### Verificación de conexión del SDR

El SDR queda conectado de forma permanente al servidor físico compartido (Ubuntu).
Se define un script `check_sdr.sh` que:

- Confirma que el dongle esté detectado, y falla con un mensaje claro si no aparece.
- Verifica que no esté tomado por el driver de TV del kernel
  (`dvb_usb_rtl28xxu`) y lo libera si hace falta.
- Usa el identificador fijo por regla `udev` (sección 9) en vez de depender del
  bus/device USB, que puede variar entre reinicios.
- Devuelve un código de salida claro (`0` = OK, distinto de `0` = problema),
  utilizable como chequeo previo en `docker-compose up` o como healthcheck.

Pendiente de implementación y validación contra el hardware real.

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

## 9. Identificación estable del SDR (udev)

Dado que el dongle se utiliza en más de una máquina (estaciones de trabajo de cada
integrante del equipo, y eventualmente el servidor), se define un identificador
fijo por regla `udev`, independiente del bus/puerto USB de conexión:

- Matcheo por `idVendor`/`idProduct` del dispositivo (RTL2832U: `0bda:2832`).
- Symlink fijo (ej. `/dev/sdr_bomberos`).
- Permisos de grupo `plugdev`, modo `0660`.

Esto garantiza que el path pasado a Docker (`--device=/dev/sdr_bomberos`) sea
estable entre reinicios y reconexiones, y simplifica la verificación en
`check_sdr.sh` (sección 2).

Limitación conocida: la regla identifica el modelo de chip, no una unidad física
individual. Si en el futuro se utiliza más de un dongle idéntico en la misma
máquina, se requiere un criterio adicional (por ejemplo, número de serie USB, si
el chip lo expone). No aplica a esta versión, que define un único SDR.

Pendiente de implementación y documentación en `docs/setup-sdr.md`, replicable en
cada estación de trabajo del equipo.

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