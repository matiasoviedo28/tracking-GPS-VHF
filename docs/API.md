# API — contrato de telemetría

Este documento define el contrato entre `sdr-decoder` (emisor) y `backend`
(receptor), según lo establecido en `ARQUITECTURA.md` secciones 3 y 8. Es el
contrato de referencia: `sdr-decoder` se implementa contra esto, no al revés.

---

## POST /api/telemetry

Recibe un reporte de posición de un equipo. Pensado originalmente para
invocarse una vez por cada paquete LRRP decodificado (protocolo de posición
estándar de la repetidora, ver `sdr-decoder/INVESTIGACION_LRRP.md` — a la
fecha, sin éxito). Desde el hito documentado en ese mismo archivo ("🎯 HITO —
Primera coordenada GPS real capturada"), también puede recibir una posición
originada en un mecanismo distinto y **oportunista**: un Baofeng UV-32 que
manda su GPS como texto plano por UDP, capturado vía el rebote ICMP "Port
Unreachable" del destinatario (ver `sdr-decoder/dmr_texto_plano_parser.py`,
antes `baofeng_gps_parser.py` — renombrado al generalizarse a más de un
mecanismo de captura). El
contrato de este endpoint no cambia por eso — sigue siendo simplemente "una
posición, de algún equipo, con lat/lon" — pero no asumir que todo lo que
llega acá vino de LRRP.

### Request

`Content-Type: application/json`

| Campo         | Tipo               | Requerido   | Descripción                                              |
|---------------|--------------------|-------------|-----------------------------------------------------------|
| `radio_id`    | string             | ver nota¹   | ID DMR del equipo transmisor.                              |
| `radio_ip`    | string             | ver nota¹   | IP del equipo dentro de la red DMR, al momento del envío.  |
| `radio_alias` | string             | sí          | Nombre legible del equipo (ej. "Móvil 3").                 |
| `lat`         | number             | sí          | Latitud, grados decimales (WGS84).                         |
| `lon`         | number             | sí          | Longitud, grados decimales (WGS84).                        |
| `timestamp`   | string (ISO 8601)  | sí          | Momento del reporte GPS, generado por el propio equipo.    |
| `altitud`     | number             | no          | Metros sobre el nivel del mar, si LRRP la provee.          |
| `velocidad`   | number             | no          | Velocidad, en km/h, si LRRP la provee.                     |
| `rumbo`       | number             | no          | Rumbo/heading, en grados (0-360), si LRRP lo provee.       |

¹ **`radio_id` y `radio_ip` no son ambos obligatorios, pero al menos uno de
los dos tiene que venir en cada envío.** Un payload sin ninguno de los dos es
inválido (`422`). Esto permite que `sdr-decoder` envíe lo que tenga
disponible en cada reporte según lo que LRRP le haya dado, sin bloquear el
envío por la ausencia de uno solo de esos dos campos.

`radio_alias` viaja en cada envío (ver ARQUITECTURA.md sección 8.3) — no hace
falta consultar una tabla de mapeo separada para resolverlo. `backend` busca
el equipo primero por `radio_id` (si vino) y, si no lo encuentra, por
`radio_ip` (si vino). Si no hay equipo existente que matchee ninguno de los
dos, se da de alta uno nuevo. Si el envío solo trae uno de los dos campos, el
otro campo ya conocido (de un envío anterior) no se pisa ni se borra — solo
se actualiza el que efectivamente llegó en este reporte, además del alias.

#### Ejemplo de request

```json
{
  "radio_id": "3021045",
  "radio_ip": "10.10.0.15",
  "radio_alias": "Móvil 3",
  "lat": -32.34456,
  "lon": -65.01923,
  "timestamp": "2026-07-29T14:32:07-03:00",
  "altitud": 712.4,
  "velocidad": 18.5,
  "rumbo": 134
}
```

### Response — éxito

**`200 OK`**

```json
{
  "status": "ok",
  "posicion_id": 4831,
  "equipo_id": 7,
  "recibido_en": "2026-07-29T14:32:07.912481-03:00"
}
```

Al persistir la posición, `backend` la emite también por
`ws://backend/ws/telemetry` (ver más abajo) a todos los clientes conectados.

### Response — error de validación

**`422 Unprocessable Entity`** — payload con campos faltantes o de tipo
incorrecto (validación estándar de Pydantic/FastAPI).

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "lat"],
      "msg": "Field required"
    }
  ]
}
```

También responde `422` si el payload no trae ni `radio_id` ni `radio_ip`:

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body"],
      "msg": "Value error, Debe venir al menos uno de radio_id o radio_ip"
    }
  ]
}
```

**`400 Bad Request`** — payload sintácticamente válido pero con valores fuera
de rango (ej. `lat`/`lon` fuera de rango físico válido).

```json
{
  "detail": "lat fuera de rango válido (-90 a 90)"
}
```

### Códigos de estado

| Código | Cuándo |
|---|---|
| `200` | Telemetría válida, persistida y emitida por WebSocket. |
| `400` | Payload bien formado pero con valores inválidos (ej. lat/lon fuera de rango). |
| `422` | Payload mal formado o con campos faltantes/tipo incorrecto. |

`sdr-decoder` reintenta hasta 5 veces ante falta de respuesta `200` (ver
ARQUITECTURA.md sección 3); agotados los reintentos, descarta el dato.

Además de persistir la posición, `backend` actualiza `ultimo_visto` del
equipo (ver `POST /api/presence` abajo) — un reporte de posición también
cuenta como evidencia de que el equipo está activo, aunque no sea en sí un
evento de voz/emergencia/ARS.

---

## POST /api/presence

Registra que se decodificó un burst DMR válido de un equipo — voz,
emergencia o un mensaje de registro ARS — **sin posición asociada**. Es el
mecanismo de "presencia" mientras la decodificación de LRRP (posición) sigue
en investigación por separado (ver `sdr-decoder/INVESTIGACION_LRRP.md`): con
esto ya se puede saber qué equipos están activos y cuándo fue la última vez
que se los escuchó, sin depender de tener su ubicación.

### Request

`Content-Type: application/json`

| Campo         | Tipo               | Requerido | Descripción                                                                 |
|---------------|--------------------|-----------|------------------------------------------------------------------------------|
| `radio_id`    | string             | sí        | ID DMR del equipo transmisor.                                                |
| `radio_alias` | string             | no        | Nombre legible del equipo. Si el equipo ya existe, no pisa el alias conocido si no viene. |
| `timestamp`   | string (ISO 8601)  | sí        | Momento de la decodificación del burst.                                      |
| `evento`      | string             | no        | Uno de `"voz"`, `"emergencia"`, `"ars"`, `"gps"`. Si no viene, se conserva el último evento conocido. |

A diferencia de `/api/telemetry`, acá `radio_id` es siempre obligatorio (no
hay alternativa por `radio_ip`) — un burst de voz/emergencia/ARS/GPS
decodificado siempre trae el ID DMR de origen.

`"gps"` es distinto de una posición real: se postea cuando `sdr-decoder`
reconoce un token de protocolo LRRP/LOCN (request o response) en el burst
decodificado, no cuando ya tiene coordenadas — la posición decodificada de
verdad sigue yendo por `POST /api/telemetry` (ver sección anterior), que
todavía no se logró con datos reales (ver `sdr-decoder/INVESTIGACION_LRRP.md`).

#### Ejemplo de request

```json
{
  "radio_id": "1001",
  "radio_alias": "Matías",
  "timestamp": "2026-08-07T19:02:35-03:00",
  "evento": "emergencia"
}
```

### Response — éxito

**`200 OK`**

```json
{
  "status": "ok",
  "equipo_id": 3,
  "ultimo_visto": "2026-08-07T19:02:35-03:00",
  "ultimo_evento": "emergencia"
}
```

Al persistir, `backend` emite por `ws://backend/ws/telemetry` un mensaje
`"type": "presence_update"` (ver más abajo) a todos los clientes conectados.

### Response — error de validación

**`422 Unprocessable Entity`** — payload con campos faltantes, de tipo
incorrecto, o `evento` con un valor fuera de
`"voz"`/`"emergencia"`/`"ars"`/`"gps"`.

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "radio_id"],
      "msg": "Field required"
    }
  ]
}
```

### Códigos de estado

| Código | Cuándo |
|---|---|
| `200` | Presencia válida, persistida y emitida por WebSocket. |
| `422` | Payload mal formado, campos faltantes/tipo incorrecto, o `evento` inválido. |

---

## GET /api/equipos

Devuelve el estado completo de todos los equipos conocidos — pensado para
que el frontend cargue el estado inicial del panel de presencia al abrir la
página, antes de que empiecen a llegar updates por WebSocket.

### Response

**`200 OK`**

```json
[
  {
    "id": 3,
    "radio_id": "1001",
    "radio_ip": null,
    "alias": "Matías",
    "tipo": "handy",
    "modelo": "DGP8550",
    "activo": true,
    "ultimo_visto": "2026-08-07T19:02:35-03:00",
    "ultimo_evento": "emergencia",
    "icono": "base_vhf",
    "online": true,
    "ultima_posicion": null
  },
  {
    "id": 7,
    "radio_id": "3021045",
    "radio_ip": "10.10.0.15",
    "alias": "Móvil 3",
    "tipo": "movil",
    "modelo": null,
    "activo": true,
    "ultimo_visto": "2026-07-29T14:32:07-03:00",
    "ultimo_evento": null,
    "online": false,
    "ultima_posicion": {
      "lat": -32.34456,
      "lon": -65.01923,
      "altitud": 712.4,
      "velocidad": 18.5,
      "rumbo": 134,
      "timestamp": "2026-07-29T14:32:07-03:00",
      "recibido_en": "2026-07-29T14:32:07.912481-03:00"
    }
  }
]
```

`online` es un campo calculado: `true` si `ultimo_visto` está dentro de los
últimos `PRESENCE_ONLINE_THRESHOLD_SECONDS` (300s / 5 minutos por defecto,
configurable — ver `.env.example`). `ultimo_evento` es `null` si el equipo
nunca reportó presencia con un tipo de evento (por ejemplo, si solo se lo
conoce por telemetría de posición). `icono` es `null` hasta que se elige uno
manualmente (ver `PATCH /api/equipos/{id}/icono` abajo) — es una
representación puramente visual en el mapa, no viene de ningún dato
decodificado por RF. `ultima_posicion` es `null` si el equipo nunca envió una
posición (esperable mientras LRRP siga sin funcionar).

No requiere query params ni paginación — volumen esperado de 6-20 equipos
(ver ARQUITECTURA.md sección 4).

---

## PATCH /api/equipos/{id}/icono

Asigna manualmente un ícono a un equipo, para representarlo en el mapa del
frontend (pensado para el contexto de bomberos — base VHF, camión, handy,
etc.). Es una elección del operador, sin relación con ningún dato
decodificado por RF ni con el campo `tipo` (que categoriza al equipo en sí:
handy/móvil/base).

### Request

`Content-Type: application/json`

| Campo    | Tipo   | Requerido | Descripción                                                                 |
|----------|--------|-----------|------------------------------------------------------------------------------|
| `icono`  | string | sí        | Uno de `"base_vhf"`, `"camion_bomberos"`, `"ambulancia"`, `"fuego"`, `"handy"`, `"bombero"`. |

#### Ejemplo de request

```json
{
  "icono": "camion_bomberos"
}
```

### Response — éxito

**`200 OK`** — devuelve el `EquipoOut` completo y actualizado (mismo formato
que cada elemento de `GET /api/equipos`).

Al persistir, `backend` emite por `ws://backend/ws/telemetry` un mensaje
`"type": "icono_update"` (ver más abajo) a todos los clientes conectados, para
que el mapa se actualice en el momento sin depender de un refresh.

### Response — error

**`404 Not Found`** — no existe ningún equipo con ese `id`.

**`422 Unprocessable Entity`** — `icono` ausente o con un valor fuera de la
lista permitida.

### Códigos de estado

| Código | Cuándo |
|---|---|
| `200` | Ícono actualizado y emitido por WebSocket. |
| `404` | No existe un equipo con ese `id`. |
| `422` | Payload mal formado o `icono` inválido. |

---

## Bitácora de audio

Registra el audio decodificado de cada evento de voz (incluye emergencia), sin
excepción y sin filtrar por equipo — pensado para poder reescuchar después
cualquier transmisión, no solo saber que ocurrió (eso ya lo cubre
`POST /api/presence`). Si una transmisión larga queda partida en dos o más
bloques del lado de `sdr-decoder`, hoy queda como dos o más clips separados
— no se intenta unirlos en esta versión (ver `sdr-decoder/live_presence_bridge.py`).

El audio se persiste en disco (no en la base) — ver `AUDIO_STORAGE_DIR` en
`.env.example` y el volumen `audio_data` en `docker-compose.yml`.

### POST /api/audio-eventos

Sube un clip de audio decodificado junto con su metadata. Lo invoca
`sdr-decoder` una vez por cada bloque que contuvo al menos un evento de voz
o emergencia.

#### Request

`Content-Type: multipart/form-data`

| Campo              | Tipo               | Requerido | Descripción                                                        |
|--------------------|--------------------|-----------|----------------------------------------------------------------------|
| `archivo`          | archivo (binario)  | sí        | El audio decodificado (WAV).                                        |
| `timestamp_inicio` | string (ISO 8601)  | sí        | Momento aproximado de inicio de la transmisión (inicio del bloque).  |
| `duracion_seg`     | number             | sí        | Duración aproximada del clip, en segundos (duración del bloque).     |
| `radio_id`         | string             | no        | ID DMR del equipo transmisor, si se pudo identificar.                |
| `radio_alias`      | string             | no        | Nombre legible del equipo, si se conoce.                             |

`radio_id`/`radio_alias` son opcionales a propósito: un bloque puede
contener voz de un `radio_id` sin alias conocido — igual se guarda el clip
(bitácora de "lo que se escuchó", no un directorio de equipos).

#### Ejemplo (curl, para probar sin depender del SDR)

```bash
curl -X POST http://localhost:8000/api/audio-eventos \
  -F "archivo=@clip-de-prueba.wav" \
  -F "timestamp_inicio=2026-08-16T14:32:07-03:00" \
  -F "duracion_seg=12" \
  -F "radio_id=1001" \
  -F "radio_alias=Matías"
```

### Response — éxito

**`200 OK`**

```json
{
  "id": 5,
  "radio_id": "1001",
  "radio_alias": "Matías",
  "timestamp_inicio": "2026-08-16T14:32:07-03:00",
  "duracion_seg": 12,
  "escuchado": false,
  "ubicacion": null
}
```

Al persistir, `backend` emite por `ws://backend/ws/telemetry` un mensaje
`"type": "audio_event"` (ver más abajo) con la metadata — **no** el archivo
en sí, el frontend lo pide aparte con `GET /api/audio-eventos/{id}/file`
recién cuando el usuario le da play.

### Response — error de validación

**`422 Unprocessable Entity`** — falta `archivo`, `timestamp_inicio` o
`duracion_seg`, o vienen con un tipo incorrecto.

### Códigos de estado

| Código | Cuándo |
|---|---|
| `200` | Clip guardado en disco, persistido en base y emitido por WebSocket. |
| `422` | Payload mal formado o campos requeridos faltantes/de tipo incorrecto. |

---

### GET /api/audio-eventos

Lista todos los clips de audio, ordenados por `timestamp_inicio` descendente
(más recientes primero) — pensado para que el frontend cargue el estado
inicial del panel de audio al abrir la página.

#### Response

**`200 OK`**

```json
[
  {
    "id": 6,
    "radio_id": "1000",
    "radio_alias": "Base Guardia",
    "timestamp_inicio": "2026-08-16T14:35:10-03:00",
    "duracion_seg": 12,
    "escuchado": false,
    "ubicacion": null
  },
  {
    "id": 5,
    "radio_id": "1001",
    "radio_alias": "Matías",
    "timestamp_inicio": "2026-08-16T14:32:07-03:00",
    "duracion_seg": 12,
    "escuchado": true,
    "ubicacion": null
  }
]
```

`ubicacion` queda `null` en toda esta versión (campo preparado para cuando
LRRP dé una posición real — ver `sdr-decoder/INVESTIGACION_LRRP.md` — sin
usar todavía). No requiere query params ni paginación por ahora.

---

### GET /api/audio-eventos/{id}/file

Sirve el archivo de audio del clip, para reproducción o descarga directa
(pensado para usarse como `src` de un `<audio>` en el frontend).

#### Response

**`200 OK`** — el archivo de audio (`Content-Type` según el tipo real,
`audio/wav` por defecto).

**`404 Not Found`** — no existe ningún clip con ese `id`, o el registro
existe pero el archivo ya no está en disco.

---

### PATCH /api/audio-eventos/{id}/escuchado

Marca un clip como escuchado. Lo llama el frontend apenas el usuario le da
play, no antes.

#### Response — éxito

**`200 OK`** — devuelve el `AudioEventoOut` completo y actualizado (mismo
formato que cada elemento de `GET /api/audio-eventos`).

Al persistir, `backend` emite por `ws://backend/ws/telemetry` un mensaje
`"type": "audio_event_escuchado"` (ver más abajo), para que otros clientes
con el panel abierto vean el cambio sin depender de un refresh.

#### Response — error

**`404 Not Found`** — no existe ningún clip con ese `id`.

### Códigos de estado (GET /file y PATCH /escuchado)

| Código | Cuándo |
|---|---|
| `200` | Archivo servido / clip marcado como escuchado. |
| `404` | No existe un clip con ese `id` (o, en `/file`, el archivo no está en disco). |

---

## Estado del SDR

Reporta el estado de salud del hardware SDR (`sdr-decoder`), para poder
distinguir en el frontend "no hay tráfico de radio ahora mismo" (normal)
de "algo anda mal con la captura" (requiere revisión física). Ver
`docs/operacion-sdr.md` para qué significa cada estado y qué hacer en cada
caso.

### POST /api/sdr-status

`sdr-decoder` postea esto en **cada bloque procesado** (~cada 12-14s, ver
`sdr-decoder/live_presence_bridge.py`) — es un heartbeat, no solo un aviso
de cambio. `backend` decide si eso implica un cambio real de estado y solo
en ese caso lo emite por WebSocket (ver más abajo) — evita mandarle a cada
cliente conectado un mensaje idéntico cada pocos segundos.

#### Request

`Content-Type: application/json`

| Campo       | Tipo               | Requerido | Descripción                                                                 |
|-------------|--------------------|-----------|------------------------------------------------------------------------------|
| `status`    | string             | sí        | Uno de `"desconectado"`, `"sin_datos"`, `"mala_antena"`, `"ok"`.             |
| `timestamp` | string (ISO 8601)  | sí        | Momento en que se generó este reporte.                                       |
| `detalle`   | string             | no        | Texto libre con las métricas que llevaron a esta clasificación (ej. `"std=3.07, 0 syncs en 12 bloques"`). |

#### Ejemplo de request

```json
{
  "status": "mala_antena",
  "timestamp": "2026-08-16T21:05:12-03:00",
  "detalle": "std=3.07 (umbral=1.5), posible antena mal conectada o desconectada"
}
```

#### Response — éxito

**`200 OK`** — devuelve el estado persistido (mismo formato que
`GET /api/sdr-status`).

```json
{
  "status": "mala_antena",
  "timestamp": "2026-08-16T21:05:12-03:00",
  "detalle": "std=3.07 (umbral=1.5), posible antena mal conectada o desconectada"
}
```

#### Response — error de validación

**`422 Unprocessable Entity`** — `status` con un valor fuera de la lista
permitida, o campos requeridos faltantes.

### GET /api/sdr-status

Devuelve el último estado conocido — para que el frontend cargue el
estado inicial del indicador al abrir la página, antes de que lleguen
actualizaciones por WebSocket.

#### Response

**`200 OK`**

```json
{
  "status": "ok",
  "timestamp": "2026-08-16T21:06:02-03:00",
  "detalle": "std=0.52 normal, 4 sync(s) DMR en este bloque"
}
```

Si todavía no llegó ningún reporte de `sdr-decoder` (por ejemplo, backend
recién levantado), devuelve un estado neutro `"sin_datos"` en vez de
`404`, para que el frontend no tenga que tratar esto como un caso de error
aparte.

### Códigos de estado

| Código | Cuándo |
|---|---|
| `200` | Estado persistido/devuelto correctamente. |
| `422` | (Solo POST) Payload mal formado o `status` inválido. |

---

## WS /ws/telemetry

WebSocket de solo lectura para el frontend. No requiere mensaje inicial del
cliente: al conectarse, empieza a recibir cada evento nuevo (posición o
presencia) que `backend` persiste, en el momento en que se persiste. Cada
mensaje trae un campo `"type"` que distingue cuál de los dos es.

### Mensaje `"type": "position_update"` (por cada posición nueva de `/api/telemetry`)

```json
{
  "type": "position_update",
  "equipo_id": 7,
  "radio_id": "3021045",
  "radio_alias": "Móvil 3",
  "lat": -32.34456,
  "lon": -65.01923,
  "altitud": 712.4,
  "velocidad": 18.5,
  "rumbo": 134,
  "timestamp": "2026-07-29T14:32:07-03:00",
  "recibido_en": "2026-07-29T14:32:07.912481-03:00"
}
```

`radio_id` puede llegar como `null` si ese equipo nunca fue reportado con
`radio_id` (solo con `radio_ip`) hasta el momento.

### Mensaje `"type": "presence_update"` (por cada evento nuevo de `/api/presence`)

```json
{
  "type": "presence_update",
  "equipo_id": 3,
  "radio_id": "1001",
  "radio_alias": "Matías",
  "ultimo_visto": "2026-08-07T19:02:35-03:00",
  "ultimo_evento": "emergencia"
}
```

### Mensaje `"type": "icono_update"` (por cada cambio de ícono vía `PATCH /api/equipos/{id}/icono`)

```json
{
  "type": "icono_update",
  "equipo_id": 3,
  "icono": "camion_bomberos"
}
```

### Mensaje `"type": "audio_event"` (por cada clip nuevo de `POST /api/audio-eventos`)

```json
{
  "type": "audio_event",
  "id": 5,
  "radio_id": "1001",
  "radio_alias": "Matías",
  "timestamp_inicio": "2026-08-16T14:32:07-03:00",
  "duracion_seg": 12,
  "escuchado": false
}
```

Trae la metadata, no el archivo — el frontend agrega el clip arriba de la
lista del panel de audio y lo pide con `GET /api/audio-eventos/{id}/file`
solo cuando el usuario le da play.

### Mensaje `"type": "audio_event_escuchado"` (por cada `PATCH /api/audio-eventos/{id}/escuchado`)

```json
{
  "type": "audio_event_escuchado",
  "id": 5,
  "escuchado": true
}
```

### Mensaje `"type": "sdr_status_update"` (solo cuando el estado del SDR cambia)

```json
{
  "type": "sdr_status_update",
  "status": "mala_antena",
  "timestamp": "2026-08-16T21:05:12-03:00",
  "detalle": "std=3.07 (umbral=1.5), posible antena mal conectada o desconectada"
}
```

A diferencia de los demás mensajes, este **no** se emite en cada POST —
`sdr-decoder` postea a `/api/sdr-status` en cada bloque (heartbeat), pero
`backend` solo emite por WS cuando el `status` efectivamente cambió (ver
`POST /api/sdr-status` más arriba).

No emite histórico al conectarse — solo eventos nuevos a partir de la
conexión (para el estado inicial completo, tanto de posición como de
presencia, usar `GET /api/equipos` al cargar la página, `GET
/api/audio-eventos` para la bitácora de audio, y `GET /api/sdr-status` para
el estado del SDR). Un endpoint de histórico de posiciones (para
timelapse, sección 6 de ARQUITECTURA.md) queda fuera del alcance de esta
versión.

---

## GET /health

Chequeo simple de que el backend levantó. Sin dependencias externas en la
respuesta (no valida conexión a la base).

### Response

**`200 OK`**

```json
{
  "status": "ok"
}
```
