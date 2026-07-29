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

| Campo         | Tipo               | Requerido | Descripción                                              |
|---------------|--------------------|-----------|-----------------------------------------------------------|
| `radio_id`    | string             | sí        | ID DMR del equipo transmisor.                              |
| `radio_ip`    | string             | sí        | IP del equipo dentro de la red DMR, al momento del envío.  |
| `radio_alias` | string             | sí        | Nombre legible del equipo (ej. "Móvil 3").                 |
| `lat`         | number             | sí        | Latitud, grados decimales (WGS84).                         |
| `lon`         | number             | sí        | Longitud, grados decimales (WGS84).                        |
| `timestamp`   | string (ISO 8601)  | sí        | Momento del reporte GPS, generado por el propio equipo.    |
| `altitud`     | number             | no        | Metros sobre el nivel del mar, si LRRP la provee.          |
| `velocidad`   | number             | no        | Velocidad, en km/h, si LRRP la provee.                     |
| `rumbo`       | number             | no        | Rumbo/heading, en grados (0-360), si LRRP lo provee.       |

`radio_alias` viaja en cada envío (ver ARQUITECTURA.md sección 8.3) — no hace
falta consultar una tabla de mapeo separada para resolverlo. Si el `radio_id`
es nuevo para `backend`, se da de alta el equipo automáticamente con ese
alias; si ya existe y el alias cambió, se actualiza.

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

---

## WS /ws/telemetry

WebSocket de solo lectura para el frontend. No requiere mensaje inicial del
cliente: al conectarse, empieza a recibir cada posición nueva que `backend`
persiste, en el momento en que se persiste.

### Mensaje emitido (por cada posición nueva)

```json
{
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

No emite histórico al conectarse — solo posiciones nuevas a partir de la
conexión. Un endpoint de histórico (para timelapse, sección 6 de
ARQUITECTURA.md) queda fuera del alcance de esta versión.

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
