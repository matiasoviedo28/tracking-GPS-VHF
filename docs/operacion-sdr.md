# Operación del SDR — estados, qué hacer, y recalibración de PPM

Este documento es para quien esté de guardia/operando el sistema y vea el
indicador de estado del SDR en el frontend (panel "Equipos", esquina del
header) — qué significa cada estado y qué hacer en cada caso. También
documenta el procedimiento manual de recalibración de frecuencia (PPM),
que **sigue siendo manual** — no hay forma automática de detectar el valor
correcto, solo de sospechar que hace falta revisarlo.

---

## Los 4 estados

El indicador combina dos mediciones automáticas que hace `sdr-decoder` en
cada bloque de 12 segundos (ver `sdr-decoder/live_presence_bridge.py`):

- **std de las muestras IQ crudas** (desvío estándar de los bytes, escala
  0-255): mide si hay una antena físicamente conectada recibiendo algo,
  independiente de si se logra decodificar nada. Referencia empírica
  (sesiones de investigación anteriores, ver `sdr-decoder/INVESTIGACION_LRRP.md`):
  ~0.47-0.60 en recepción normal, ~3+ con antena mal conectada o
  desconectada.
- **Total de syncs DMR** (líneas `Sync: +DMR` de `dsd-fme`, cualquier
  Color Code, no solo el `01` válido): mide si hay estructura de señal DMR
  real en el aire, más allá de si el burst llega a decodificarse como un
  evento reconocido.

### 🔴 Desconectado

`rtl_sdr` no pudo siquiera **abrir** el dispositivo (no es que no reciba
nada — no lo encuentra o no puede tomarlo).

**Qué hacer**: revisar que el dongle esté físicamente conectado por USB.
Si el contenedor `sdr-decoder` viene reiniciándose o el problema persiste,
correr `sdr-decoder/check_sdr.sh` **en el host** (no dentro del
contenedor) para descartar un problema de driver DVB o de la regla `udev`
(ver secciones 2 y 9 de `docs/ARQUITECTURA.md`).

### 🟠 Mala antena

El dongle responde y graba, pero el std de las muestras IQ está por
encima del umbral configurado (`MALA_ANTENA_STD_UMBRAL`, default `1.5` —
variable de entorno en `docker-compose.yml`/`.env`). Esto históricamente
correlacionó con una antena improvisada, mal conectada, o directamente
ausente (ver sesiones de investigación).

**Qué hacer**: revisar físicamente la conexión de la antena al dongle.
Si el std vuelve a bajar del umbral, el estado se corrige solo en el
próximo bloque (~12-14s) — no hace falta reiniciar nada.

### 🟡 Sin datos

La antena está bien (std normal), pero no hubo **ningún** sync DMR
(cualquier Color Code) durante una ventana sostenida (`VENTANA_SIN_DATOS_BLOQUES`,
default 10 bloques ≈ 2-3 minutos).

**Importante — esto NO distingue dos causas distintas**:
1. Silencio normal — nadie transmitió en ese rato. Es esperable y no
   requiere ninguna acción si el sistema viene funcionando bien.
2. Un problema real (calibración de frecuencia desviada, antena floja de
   una forma que no eleva el std lo suficiente, etc.) que hace que
   transmisiones reales no se estén captando.

**Qué hacer**: si este estado persiste más de lo esperable para el tráfico
de radio habitual del cuartel, o si se sabe que hubo una transmisión real
reciente que debería haberse visto, sospechar de la calibración de
frecuencia — ver el procedimiento de recalibración manual más abajo. Antes
de tocar la calibración, confirmar que la antena esté bien conectada (no
basta con que el estado no diga "mala_antena": un std normal no garantiza
una antena óptima, solo que no está claramente mal).

### 🟢 OK

Hubo al menos un sync DMR reciente (cualquier Color Code). Todo funcionando.

---

## Recalibración manual de PPM (`SDR_FREQ_CORR_HZ`)

**Esto sigue siendo un procedimiento manual** — no hay forma automática de
saber cuál es el valor correcto, solo de sospechar (vía el estado
"sin_datos" sostenido) que puede hacer falta revisarlo. La corrección de
frecuencia deriva con el tiempo (temperatura del dongle, tiempo desde el
último ajuste) — valores históricos documentados en
`sdr-decoder/INVESTIGACION_LRRP.md`: -6504, -6700, -7000, -7800, -7500,
-7600 Hz, en distintos momentos.

### Procedimiento

1. **Confirmar la antena conectada primero.** No asumir que es la
   calibración sin haber descartado esto — es la causa más común de
   confusión en sesiones anteriores.

2. **Grabar una ventana de IQ crudo durante una transmisión real** (en el
   host, no dentro del contenedor — necesita el dongle libre):
   ```bash
   rtl_sdr -f 159635000 -s 240000 -g 30 -n <muestras> captura.cu8
   ```
   Coordinar con quien vaya a transmitir para saber en qué instante
   aproximado del archivo está la transmisión real.

3. **Barrer valores de `freq_corr`** con `iq_to_wav.py` (versionado en
   `sdr-decoder/iq_to_wav.py`) + `dsd-fme` en modo archivo, contando
   syncs reales para cada valor:
   ```bash
   for corr in -9000 -8000 -7000 -6000 ... ; do
     python3 sdr-decoder/iq_to_wav.py captura.cu8 "test_${corr}.wav" 240000 "$corr"
     echo "=== $corr ==="
     dsd-fme -fs -i "test_${corr}.wav" -s 48000 -Z -o null 2>&1 | grep -c "Color Code=01"
   done
   ```
   Empezar con un barrido amplio (pasos de ~1000 Hz) para encontrar la
   zona correcta, después un barrido fino (pasos de ~200 Hz) alrededor del
   mejor valor encontrado — mismo método usado en todas las sesiones de
   calibración anteriores (ver `sdr-decoder/INVESTIGACION_LRRP.md`).

4. **Actualizar `SDR_FREQ_CORR_HZ`** en `.env` (o como variable de entorno
   al levantar `docker-compose.yml`) con el valor que maximizó los syncs
   con Color Code=01, y reiniciar el servicio `sdr-decoder`:
   ```bash
   docker compose up -d --build sdr-decoder
   ```

5. **Documentar el nuevo valor y la fecha** en
   `sdr-decoder/INVESTIGACION_LRRP.md`, siguiendo el formato ya usado en
   sesiones anteriores — ayuda a ver la tendencia de deriva en el tiempo.

---

## Referencia rápida

| Estado | Color | Causa | Acción |
|---|---|---|---|
| Desconectado | 🔴 | `rtl_sdr` no pudo abrir el dispositivo | Revisar cable/USB, correr `check_sdr.sh` en el host |
| Mala antena | 🟠 | std de IQ por encima del umbral | Revisar conexión física de la antena |
| Sin datos | 🟡 | Sin sync DMR sostenido, antena OK | Puede ser silencio normal — si persiste sin explicación, sospechar calibración de PPM |
| OK | 🟢 | Sync DMR reciente | Todo normal |
