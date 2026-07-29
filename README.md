# tracking-GPS-VHF

Sistema de localización en tiempo real para la flota de radios DMR del Cuartel de
Bomberos Voluntarios de Merlo (San Luis, Argentina).

## Descripción

El sistema recibe telemetría GPS transmitida por los handys Motorola DGP8550 del
cuartel (protocolo LRRP sobre DMR), la persiste, y la expone en un mapa web
interactivo con posición en tiempo real e historial de recorrido por equipo.

Reemplaza la funcionalidad de localización que antes brindaba TRBOnet, sin depender
de licencias comerciales de Motorola: la señal se captura por RF con un SDR y se
decodifica de forma independiente.

## Componentes

El proyecto está dividido en tres servicios independientes, orquestados con Docker
Compose:

| Servicio | Función |
|---|---|
| `sdr-decoder` | Captura la señal DMR vía SDR, decodifica el protocolo LRRP y envía la telemetría al backend. |
| `backend` | Expone la API que recibe la telemetría, la persiste, y sirve los datos al frontend en tiempo real. |
| `frontend` | Interfaz web con mapa interactivo, posición en vivo e historial por equipo. |

El detalle técnico de cada componente, las decisiones de diseño tomadas, y el
esquema de datos están documentados en [`ARQUITECTURA.md`](./ARQUITECTURA.md).

## Estado del proyecto

En etapa de diseño e implementación inicial. La decodificación de la señal (lado
`sdr-decoder`) y el desarrollo de `backend`/`frontend` avanzan en paralelo, contra
la API real desde el inicio (sin mocks intermedios).

## Repositorio

```
tracking-GPS-VHF/
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
    ├── API.md              (contrato del endpoint de telemetría)
    └── protocolo-lrrp.md   (documentación del trabajo de decodificación del protocolo)
```