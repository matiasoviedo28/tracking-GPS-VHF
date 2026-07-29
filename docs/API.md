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

`radio_id` puede llegar como `null` si ese equipo nunca fue reportado con
`radio_id` (solo con `radio_ip`) hasta el momento.

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
