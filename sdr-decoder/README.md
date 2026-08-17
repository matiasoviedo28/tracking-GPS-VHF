# sdr-decoder

Captura y decodificación DMR/LRRP real vía SDR, containerizado (ver
`Dockerfile`, `docker-compose.yml`). Ya no es un placeholder — `docker
compose up` levanta `sdr-decoder` completo (rtl_sdr + dsd-fme +
`live_presence_bridge.py`), sin pasos manuales dentro del contenedor.

**Prerrequisito de host, siempre manual, antes de `docker compose up`**:
correr `./check_sdr.sh` para confirmar que el driver DVB del kernel esté
blacklisteado y que la regla `udev` (symlink `/dev/sdr_bomberos`) haya
aplicado — ver `docs/ARQUITECTURA.md` secciones 2 y 9, y
`docs/operacion-sdr.md`.

## Archivos

- `Dockerfile`: build multi-stage (compila `mbelib` + `dsd-fme` desde
  código fuente en el stage de build, copia solo binario + librerías
  imprescindibles al runtime).
- `live_presence_bridge.py`: el bridge real — graba bloques con
  `rtl_sdr`, decodifica con `dsd-fme`, postea presencia/audio/estado del
  SDR al backend. Ver el docstring del archivo para el detalle completo y
  las variables de entorno de configuración.
- `iq_to_wav.py`: conversión de IQ crudo a WAV demodulado (usado tanto por
  el bridge como para el procedimiento manual de recalibración de PPM).
- `check_sdr.sh`: chequeo de prerrequisitos de host — corre en el host,
  nunca dentro de un contenedor (ver comentario en el propio script).
- `INVESTIGACION_LRRP.md`: historial completo de la investigación de
  captura GPS/LRRP (sesiones de RF con SDR) que llevó al diseño actual —
  referencia obligada antes de tocar la calibración de frecuencia o el
  parsing de `dsd-fme`.

## Documentación relacionada

- `docs/ARQUITECTURA.md` secciones 2 y 9: diseño de contenedores y
  passthrough USB.
- `docs/operacion-sdr.md`: qué significa cada estado del SDR mostrado en
  el frontend, y el procedimiento manual de recalibración de PPM.
- `docs/API.md`: contrato de `POST /api/presence`, `POST
  /api/audio-eventos`, y `POST/GET /api/sdr-status`.
