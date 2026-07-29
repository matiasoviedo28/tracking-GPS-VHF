# sdr-decoder (placeholder)

Servicio pendiente de implementación, a cargo del equipo de decodificación
(ver `ARQUITECTURA.md` secciones 2 y 10). Este directorio solo contiene un
`Dockerfile` mínimo para que `docker-compose.yml` pueda referenciarlo sin
romper el resto del stack.

Responsabilidades futuras de este servicio (fuera del alcance de esta sesión):

- Captura de señal DMR vía SDR y decodificación del protocolo LRRP.
- Envío de telemetría a `backend` vía `POST /api/telemetry`
  (contrato en `docs/API.md`).
- Passthrough de dispositivo USB del SDR y verificación (`check_sdr.sh`),
  usando el identificador fijo por regla `udev` definido en
  `ARQUITECTURA.md` sección 9. Pendiente de resolver en `docker-compose.yml`
  (mapeo `--device`/`devices:`).
