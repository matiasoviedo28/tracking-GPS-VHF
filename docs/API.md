# API — contrato de telemetría

Este documento define el contrato entre `sdr-decoder` (emisor) y `backend`
(receptor), según lo establecido en `ARQUITECTURA.md` secciones 3 y 8. Es el
contrato de referencia: `sdr-decoder` se implementa contra esto, no al revés.

---

## POST /api/telemetry

Recibe un reporte de posición de un equipo. Se invoca una vez por cada paquete
LRRP decodificado.

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
conoce por telemetría de posición). `ultima_posicion` es `null` si el equipo
nunca envió una posición (esperable mientras LRRP siga sin funcionar).

No requiere query params ni paginación — volumen esperado de 6-20 equipos
(ver ARQUITECTURA.md sección 4).

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

No emite histórico al conectarse — solo eventos nuevos a partir de la
conexión (para el estado inicial completo, tanto de posición como de
presencia, usar `GET /api/equipos` al cargar la página). Un endpoint de
histórico de posiciones (para timelapse, sección 6 de ARQUITECTURA.md) queda
fuera del alcance de esta versión.

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
