# Reporte de investigación — Captura de tráfico GPS/LRRP desde repetidora MOTOTRBO

## Objetivo original
Detectar y capturar paquetes UDP con datos GPS (NAI-D/LRRP) que la repetidora MOTOTRBO envía desde handys DGM8550, para eventualmente escribir un parser en Python.

## Diagnóstico de causa raíz (CONFIRMADO)
La falta de paquetes en las capturas iniciales **no se debió a un problema de red, switching o aislamiento WiFi**. La causa real: la repetidora tiene configurado como destino NAI-D la IP de una PC vieja que ya no existe en la red `10.101.1.0/24`. El tráfico UDP se emite hacia un destino que ya no está — nunca iba a aparecer en ninguna captura pasiva, sin importar el punto de la red desde donde se mirara.

**Plan de corrección**: reprogramar la repetidora vía CPS/USB para que el destino NAI-D apunte a esta máquina (`10.101.1.114`). Una vez hecho eso, el tráfico va a llegar **unicast directo** a esta IP — no hace falta mirroring, tap ni acceso privilegiado a ningún equipo de red intermedio.

## Entorno de red (para referencia)
- Máquina de trabajo: Ubuntu, WiFi (`wlx001f05356807`) a la red "Fibra Comando", IP `10.101.1.114/24`.
- Gateway: `10.101.1.10` — Huawei EG8147X6-10.
- Repetidora: `10.101.1.30` — sin lease DHCP (IP estática), puertos 80/443 abiertos (interfaz web propia, con TLS roto/legacy — inaccesible desde curl y Chrome).

## Camino descartado como principal (Opción B, solo como nota)
Se exploró usar la función **"Remote Mirror"** del router Huawei (Maintenance Diagnostics → Packet Capture By Mirroring) para mirrorear el tráfico de `10.101.1.30` directamente desde el router, dado que una red switcheada normalmente no deja ver tráfico unicast entre otros dos hosts desde un tercero (esto en sí no era el problema real, pero en su momento no lo sabíamos). Quedó bloqueado en el campo obligatorio "Destination IP Address" (no acepta vacío/0.0.0.0, y no había forma de saber la IP correcta sin acceso a la PC vieja). **Con el diagnóstico actual, esta vía queda descartada como plan principal** — se mantiene documentada por si en el futuro hiciera falta mirrorear tráfico de otro equipo de la red sin reprogramarlo.

## Herramientas ya instaladas/configuradas
- `nmap`, `tcpdump` con capabilities (`cap_net_raw,cap_net_admin` via `setcap`) — corren sin sudo para *iniciar*, pero **detenerlos con `kill` normal falla por permisos** (el kernel bloquea señales de un proceso sin esas capabilities hacia uno que sí las tiene). Para parar tcpdump si el `kill` normal falla: `sudo kill -TERM <pid>`.
- Pendientes de instalar (no bloqueante): `tshark`, `netdiscover`, `arp-scan`.

## Entregable de esta etapa: listener UDP + backup pcap

Directorio: `~/lrrp_capture/`

- **`lrrp_listener.py`** — Escucha UDP en `0.0.0.0:<puerto>` (default 4001, configurable con `--port`). Por cada paquete recibido imprime timestamp (ISO8601 con timezone), IP:puerto de origen, longitud del payload, y un hexdump completo (offset + hex + ascii). Loguea simultáneamente a stdout y a archivo (`--log`, default `lrrp_capture.log`). Sin dependencias externas (solo stdlib).
- **`start_capture.sh [puerto] [interfaz]`** — Arranca en background:
  1. `tcpdump -U -i <interfaz> -w capture_<timestamp>.pcap "udp port <puerto>"` (el `-U` fuerza flush por paquete, para no perder datos si el proceso no se puede cerrar limpiamente).
  2. `lrrp_listener.py` con log a `lrrp_capture_<timestamp>.log`.
  
  Defaults: puerto `4001`, interfaz `wlx001f05356807`. Guarda los PIDs en `tcpdump.pid` y `listener.pid`.
- **`stop_capture.sh`** — Detiene ambos procesos. Si el `kill` de tcpdump falla por el tema de capabilities, imprime la instrucción exacta (`sudo kill -TERM <pid>`).

### Validado con test end-to-end
Se probó el flujo completo (start → enviar paquete UDP sintético → verificar log → stop) en loopback antes de entregarlo: el listener capturó y logueó correctamente timestamp/origen/longitud/hexdump. Confirmado el problema de `kill` sobre tcpdump (esperado, documentado en `stop_capture.sh`).

### Uso cuando se reprograme la repetidora
```bash
cd ~/lrrp_capture
./start_capture.sh              # puerto 4001, interfaz wlx001f05356807 (defaults)
tail -f lrrp_capture_*.log       # ver paquetes en vivo
# ... esperar el reporte GPS del handy ...
./stop_capture.sh                # si tcpdump no para, usar sudo kill -TERM <pid> que indique
```

Si el NAI-D de la repetidora usa otro puerto, pasarlo como primer argumento: `./start_capture.sh 4005`.

## Pendiente / próximos pasos (NAI-D)
1. Reprogramar la repetidora (CPS/USB) apuntando el destino NAI-D a `10.101.1.114`.
2. Correr `start_capture.sh`, activar GPS en el handy, esperar el reporte.
3. Analizar el `.pcap` resultante y el log de hexdumps para identificar la estructura NAI-D/LRRP (headers, posibles campos de lat/long).
4. Documentar la estructura observada en un markdown separado una vez que haya paquetes reales para analizar.

---

## Sesión 2 — Protocolo Link Establishment (IPSC), puerto UDP 50000

### Contexto y nuevo dato de configuración
Dato confirmado en el CPS de la repetidora SLR5100, distinto y por debajo del protocolo NAI-D/LRRP:
- Link Type: **Master**
- Master IP: `10.101.1.30`
- Master UDP Port: **50000**
- UDP Port (propio): 50000
- Beacon Duration: 4320 ms
- Beacon Interval: 60 seg

Hipótesis a probar: si la repetidora emite beacons periódicos de "Link Establishment" (protocolo IP Site Connect / IPSC) incluso sin un peer conectado, deberían ser visibles con una captura pasiva simple.

### Resultado de la captura pasiva
- Filtro: `udp port 50000 and host 10.101.1.30`, interfaz `wlx001f05356807`.
- Duración: **5+ minutos continuos** (325 segundos), suficiente para 5 ciclos completos de un beacon de 60s si existieran.
- Verificado antes de empezar: `10.101.1.30` respondía normalmente a ping y ARP (equipo activo, no caído).
- **Resultado: 0 paquetes capturados.** Archivo pcap de 24 bytes (solo header global, sin ningún registro de paquete).

**Este resultado NO se interpreta como fallo de la captura.** El filtro, la interfaz y la disponibilidad del equipo estaban confirmados. Dos hipótesis quedan abiertas, sin evidencia para descartar ninguna:
1. El beacon Master→Peer se envía **unicast a un peer específico configurado** (no en broadcast). Si no hay ningún peer registrado en este momento (posible el mismo problema de "IP vieja inexistente" que ya encontramos con el NAI-D), el tráfico nunca sale a la red de forma visible para una captura pasiva externa al propio equipo.
2. El campo "Beacon Duration/Interval" del codeplug **podría no corresponder al handshake UDP de Link Establishment**, sino al beacon de RF que la repetidora transmite por aire para que los radios hagan roaming entre sitios — un mecanismo completamente distinto, que no generaría tráfico UDP en absoluto. Ver investigación de protocolo abajo — esto no está confirmado ni descartado por ninguna fuente pública encontrada.

**Pendiente de confirmar**, no asumido: cuál de las dos hipótesis (o ambas) explica la ausencia de tráfico.

### Investigación de protocolo público (Plan B)
Búsqueda de documentación pública sobre el protocolo Link Establishment / IPSC de Motorola (puerto UDP 50000). Resumen:

**Hallazgo principal**: el protocolo es propietario, no publicado oficialmente por Motorola. Toda la documentación disponible viene de ingeniería inversa de la comunidad DMR/radioaficionados.

**Fuentes con datos concretos de estructura de paquete** (dos proyectos independientes convergen en los mismos valores, lo cual es la evidencia más sólida encontrada):

- **BogdanDIA/IPSC** (disector de Wireshark, C) — `github.com/BogdanDIA/IPSC/blob/master/packet-ipsc.c`. Define tipos hex `0x90`-`0x99` (MASTER_REG_REQ, MASTER_REG_REPLY, PEER_LIST_REQ, PEER_REG_REQ, MASTER_ALIVE_REQ/REPLY, PEER_ALIVE_REQ/REPLY) y una cabecera común: byte 0 = tipo, bytes 1-4 = RPT_ID, byte 5 = flags de "linking" (Peer Op, Peer Mode, IPSC Slot1/2), bytes 6-9 = flags de servicio, bytes 10-13 = versión, bytes 14-23 = "Auth Digest" (10 bytes).
- **HBLink-org/DMRlink**, `ipsc/ipsc_const.py` — `github.com/HBLink-org/DMRlink`. Stack productivo en Python que confirma independientemente los mismos valores hex (`MASTER_REG_REQ=0x90` ... `DE_REG_REPLY=0x9B`).
- **n0mjs710/ipsc2hbp** — `github.com/n0mjs710/ipsc2hbp`. README declara explícitamente: *"IPSC is not an open standard... much is unknown"*. Tiene `ipsc_packet_reference.md` con más detalle — no se pudo transcribir automáticamente, revisar directamente en el repo.
- **pd0mz/go-dmr** — PDF "DMRplus IPSC Protocol for HB repeater (20150726)" en el repo (vía Git LFS, no se pudo extraer automáticamente, descargar manualmente).
- **rick51231/node-dmr-lib** — implementa `IPSCPeer`, sin documentación de formato en el README, revisar código fuente.

**Sobre "Beacon Duration/Interval" específicamente**: en documentación de configuración (TRBOnet, SmartPTT, foros) este término aparece asociado al **beacon RF** para roaming entre sitios, no confirmado como el mismo mecanismo del handshake UDP 50000. El hilo de RadioReference "Decoding MotoTRBO 'beacons'" es especulativo, sin hex dumps ni mención del puerto UDP 50000. **No se encontró ninguna fuente que confirme la relación 1:1 entre el campo del codeplug y alguno de los tipos de paquete IPSC listados arriba.**

**Conclusión de la investigación**: existe base pública real para arrancar un parser de Link Establishment si el handshake efectivamente ocurre (tipos `0x90`-`0x9B`, cabecera de 24 bytes confirmada por dos fuentes independientes), pero el significado fino de varios campos (Auth Digest, flags de servicio, payload de PEER_LIST_REPLY) no está documentado con certeza pública, y la relación entre el campo de configuración "Beacon" y este protocolo específico queda sin confirmar.

### Próximos pasos (Link Establishment)
1. **Sin asumir causa**: decidir si vale la pena configurar un peer real (otra repetidora, o un segundo IP Site Connect simulado/software) para ver si el Master emite tráfico recién ahí — validaría la hipótesis 1.
2. Revisar manualmente `n0mjs710/ipsc2hbp/ipsc_packet_reference.md` y el PDF de `pd0mz/go-dmr` (no se pudieron leer automáticamente) para más detalle de estructura antes de intentar un parser a ciegas.
3. Considerar si "Beacon Duration/Interval" es en realidad RF (no IP) — de ser así, esta vía de captura de red queda cerrada para ese mecanismo específico, y no habría nada que capturar por UDP relacionado a eso.

**Actualización (Sesión 3): el punto 2 ya se completó** — ver más abajo. `ipsc_packet_reference.md` sí se pudo leer directamente clonando el repo (a diferencia del intento de fetch remoto de la sesión 2), y resultó ser una referencia de protocolo mucho más completa de lo esperado.

---

## Sesión 3 — Preparación de registro como peer IPSC (sin ejecutar contra la repetidora todavía)

### Contexto
Hipótesis de la sesión 2: el Master (repetidora) probablemente no emite tráfico de Link Establishment visible porque no hay ningún peer registrado contra el cual enviarlo. Para probar esto de forma controlada, se evaluó implementar el lado "peer" del protocolo IPSC y registrarse activamente contra el Master (`10.101.1.30:50000`).

**IMPORTANTE**: en esta sesión se preparó y validó todo el tooling, pero **no se transmitió ningún paquete hacia la repetidora real**. Toda validación se hizo contra un master IPSC falso corriendo en loopback (127.0.0.1), nunca contra `10.101.1.30`.

### Evaluación de proyectos de referencia
Directorio: `~/ipsc_peer_test/` (separado de `~/lrrp_capture/`). Se clonaron y leyeron completos:
- `HBLink-org/DMRlink`
- `n0mjs710/ipsc2hbp`

Evaluación completa en `~/ipsc_peer_test/ipsc_peer_evaluacion.md`. Resumen:
- **DMRlink no corre en Python 3 tal cual** (falla `py_compile` con error de sintaxis Python 2) y depende de Twisted/`dmr_utils`/`bitstring`, no instalados. Sirve solo como referencia histórica.
- **ipsc2hbp requiere Python 3.11+** (usa `tomllib` de stdlib) — este sistema tiene Python 3.10.12, `import tomllib` falla. Correr el daemon completo tal cual no es viable sin instalar una versión de Python más nueva.
- **Decisión**: no ejecutar ninguno de los dos proyectos completos. Se extrajo la lógica de la clase `IPSCPeerProtocol` (`ipsc2hbp/ipsc/protocol.py`) — que no depende de `dmr-utils3`/`bitarray` (esas libs solo las usa la parte de traducción de voz, que no necesitamos) — y se reescribió como script standalone de stdlib puro. Esto es más auditable que correr un daemon completo de terceros contra un equipo de producción.
- Bonus: `ipsc2hbp/ipsc_packet_reference.md` resultó ser una referencia de protocolo mucho más detallada que lo encontrado en la sesión 2 — incluye estructura byte-a-byte confirmada de `MASTER_REG_REQ` (0x90), `MASTER_REG_REPLY` (0x91), `PEER_LIST_REQ/REPLY` (0x92/0x93), `MASTER_ALIVE_REQ/REPLY` (0x96/0x97), `DE_REG_REQ/REPLY` (0x9A/0x9B), campos MODE/FLAGS/IPSC_VERSION, y mucho más (incluyendo la estructura de `GROUP_VOICE` para cuando eventualmente haga falta).

### Config mínima identificada (ver evaluación para detalle completo)
| Parámetro | Valor propuesto | Certeza |
|---|---|---|
| Master IP:Puerto | `10.101.1.30:50000` | Confirmado (CPS) |
| Peer Radio ID | `9999999` (arbitrario) | Según `ipsc2hbp.toml.sample`: *"debe ser único en el sistema IPSC, NO es el radio ID de la repetidora"* — es decir, no hace falta que coincida con nada preexistente **según esta implementación de referencia**. **No confirmado para la SLR5100 real**: pendiente que el usuario revise en el CPS si hay una lista de "Sites"/peers esperados que restrinja qué IDs acepta el Master. |
| Auth | Deshabilitado | El campo "Authentication Key" vacío en el CPS sugiere que no espera HMAC-SHA1 |
| MODE byte | `0x40` (software/app, sin radio, ambos slots deshabilitados) | Es el valor que reportan aplicaciones de software según la referencia de protocolo — más honesto que anunciarse como repetidor operativo |
| FLAGS (4 bytes) | `0x00000000` (sin capacidades reclamadas) | Elección conservadora: no reclama ser master, no pide voz, no pide auth |
| IPSC Version | `0x04020401` | Valor estándar documentado (IPSC v2/v1) |

### Entregables preparados (NO ejecutados contra la repetidora)
Directorio `~/ipsc_peer_test/`:
- **`ipsc_peer_probe.py`** — script de prueba de registro, stdlib puro. En una sola pasada (sin loop de keepalive ni reintentos automáticos) envía como máximo 3 paquetes UDP: `MASTER_REG_REQ` (14 bytes) → espera `MASTER_REG_REPLY` → `PEER_LIST_REQ` (5 bytes) → espera `PEER_LIST_REPLY` → `DE_REG_REQ` (5 bytes, de-registro limpio) → espera `DE_REG_REPLY`. Loguea timestamp + hexdump completo de cada paquete enviado y cada respuesta recibida (o timeout si no hay respuesta). Flags `--skip-peer-list` y `--skip-deregister` disponibles si se quiere un envío aún más mínimo.
- **`fake_master_for_testing.py`** — master IPSC falso para pruebas en loopback, implementa 0x90/0x92/0x9A del lado servidor. Solo para testing local, nunca apuntado a la red real.
- **`ipsc_peer_evaluacion.md`** — evaluación completa de los dos proyectos y justificación de la decisión.

### Validación realizada (sin tocar la repetidora)
Se corrió `ipsc_peer_probe.py` contra `fake_master_for_testing.py` en `127.0.0.1`. Resultado: intercambio completo y correcto de los 3 pares de paquetes (`0x90`→`0x91`, `0x92`→`0x93`, `0x9A`→`0x9B`), con el peer ID `9999999` codificado correctamente en hex (`98 96 7f`). El log completo del test quedó registrado (y luego se limpió del directorio de entregables, no forma parte del resultado final).

### Estado: PENDIENTE DE CONFIRMACIÓN EXPLÍCITA antes de correr contra `10.101.1.30`
No se ha enviado ni se enviará ningún paquete a la repetidora real sin que el usuario confirme explícitamente en el chat el plan exacto de transmisión (comando exacto, paquetes exactos, y captura tcpdump en paralelo lista para correr).

### RESULTADO REAL (usuario confirmó, prueba ejecutada 2026-07-26 03:08 -03)

El usuario confirmó el plan y corrió el comando exacto documentado arriba directamente desde su propia terminal (la ejecución automática fue bloqueada por el clasificador de seguridad del entorno de trabajo al intentar correrla nosotros — no se intentó sortear ese bloqueo).

**Resultado**: `MASTER_REG_REQ` enviado (14 bytes, peer ID `9999999`) — **timeout a los 5 segundos, sin ninguna respuesta**.

**Cruzado con captura pasiva en paralelo** (`tcpdump host 10.101.1.30`, corriendo desde antes de ejecutar el probe):
```
03:08:31.004366  10.101.1.114.35908 > 10.101.1.30.50000: UDP, length 14   ← nuestro REG_REQ, confirmado en el cable
03:08:36.169109  ARP who-has 10.101.1.30 tell 10.101.1.114                ← resolución ARP posterior, no relacionada a IPSC
03:08:36.172468  ARP reply 10.101.1.30 is-at e0:92:8f:e0:19:62
```
**Total: 3 paquetes.** Confirmado que nuestro `MASTER_REG_REQ` salió correctamente hacia `10.101.1.30:50000` (14 bytes, coincide exacto con lo esperado). **Cero respuesta UDP de ningún tipo** — ni `MASTER_REG_REPLY` (0x91), ni ningún otro opcode, ni un rechazo explícito. El equipo sigue activo (resolvió ARP normalmente segundos después, misma MAC de siempre).

**Interpretación — sin sobre-concluir:**
- Silencio total (ni siquiera un rechazo) es **consistente con** una implementación que tiene una lista de peers/sites preconfigurada y descarta silenciosamente registros de IDs no reconocidos (así es como se comporta, por ejemplo, la implementación de referencia `IPSCMasterProtocol` de ipsc2hbp cuando `allowed_peer_ids` está poblado: solo loguea WARNING internamente y no responde nada). **No está confirmado que el firmware real de la SLR5100 se comporte igual** — es una hipótesis razonable, no un hecho verificado.
- Coincide con el mismo patrón encontrado dos veces antes en esta investigación (NAI-D apuntando a una PC vieja; posible causa similar en el beacon de Link Establishment de la sesión 2): el sistema parece estar configurado para hablar solo con destinos/peers específicos predefinidos, no aceptar conexiones entrantes arbitrarias.
- Alternativas no descartadas: que haga falta un campo adicional adecuado (más allá de Radio ID) que no estemos enviando, que la autenticación sí esté activa pese al campo vacío en el CPS, o que el "Link Type: Master" de un IP Site Connect comercial simplemente no acepte peers no listados por diseño (a diferencia de los servidores IPSC "abiertos" del mundo radioaficionado que sí lo hacen).

### Próximos pasos (Sesión 3)
1. Revisar en el CPS de la repetidora si existe una sección de "Sites" / lista de peers esperados con IPs o IDs específicos — si existe, ese es casi seguro el motivo del silencio.
2. Si existe esa lista, considerar (con el usuario, sin ejecutar nada sin confirmación) usar un Peer Radio ID que coincida con uno de los esperados, si es seguro hacerlo.
3. Revisar si hay algún log/diagnóstico accesible en el propio equipo (mismo problema de TLS roto de la sesión 1 impide el panel web) que muestre intentos de registro rechazados.
4. `tcpdump` de esta prueba sigue corriendo en background (PID `265473` al momento de escribir esto) — no se pudo detener por el mismo problema de capabilities de siempre; liberar con `sudo kill -TERM` cuando se quiera.

### Segundo intento (reproducibilidad, 2026-07-26 03:13 -03)
El usuario repitió exactamente el mismo comando una segunda vez. Mismo resultado: `MASTER_REG_REQ` (14 bytes, mismo peer ID) confirmado en el cable a las 03:13:48, **cero respuesta**, timeout a los 5s. La captura (que seguía corriendo desde el primer intento) lo confirma de forma independiente — mismo patrón: nuestro paquete sale, no llega nada, y ~5s después hay una resolución ARP normal (probablemente coincidencia con el cierre del socket del probe, no relacionado al protocolo IPSC).

**Dos intentos independientes, mismo resultado exacto** — descarta que haya sido un problema de timing puntual la primera vez. El silencio es un comportamiento consistente y reproducible, lo cual refuerza (sin todavía confirmar) la hipótesis de la lista de peers preconfigurada.

---

## Sesión 4 — Revisión de código fuente: ¿paquete mal formado?

### Contexto
Se descartó la hipótesis de "lista de Sites" del CPS: esa sección (General > Sites) es para vincular repetidoras físicas en sistemas multi-sitio (Site ID, Reserved Wide Area Channels, Neighbors) — no aplica a un sistema standalone de un solo sitio como el nuestro. Nueva hipótesis: nuestro paquete `MASTER_REG_REQ` puede estar mal formado, específicamente en los bytes de MODE/FLAGS, no en su longitud o estructura general.

**Importante: en esta sesión no se transmitió nada a `10.101.1.30`.** Solo análisis de código fuente y un test contra el master falso local (loopback).

### Hallazgo 1 — CAI Network / CAI Group Network: no aparecen en el protocolo de registro
Búsqueda exhaustiva de "CAI" en el código fuente completo de DMRlink e ipsc2hbp: **cero resultados relevantes** en la lógica de construcción de paquetes IPSC. Estos campos del CPS (CAI Network, CAI Group Network) parecen pertenecer a la capa de direccionamiento *over-the-air* (cómo el repetidor arma los DMR ID completos a partir de IDs cortos para la interfaz de aire), un dominio de configuración distinto y no relacionado al handshake Master-Peer por IP que estamos probando. **No se necesita codificar estos campos en el paquete de registro** — hipótesis descartada por ausencia total de evidencia en dos implementaciones independientes.

### Hallazgo 2 — Longitud, checksum, padding: nuestro paquete es correcto
`dmrlink.py` línea 318: `self.MASTER_REG_REQ_PKT = (MASTER_REG_REQ + self._local_id + self.TS_FLAGS + IPSC_VER)` — exactamente `opcode(1) + peer_id(4) + MODE(1)+FLAGS(4) + IPSC_VER(4) = 14 bytes`. Idéntico a la construcción de `ipsc2hbp` y a lo que enviamos. **No hay checksum, longitud adicional, ni padding faltante** — la estructura de 14 bytes está confirmada correcta por dos fuentes independientes.

### Hallazgo 3 (el más importante) — MODE byte: usamos el valor equivocado

Nuestro paquete de la sesión 3 usaba `MODE = 0x40` (ENABLED + NO_RADIO + TS1/TS2 DISABLED), basado en un comentario de `ipsc_packet_reference.md` que decía que "aplicaciones de software... típicamente reportan 0x40". **Este valor no es el que usan implementaciones reales y probadas para registrarse como peer activo.**

Evidencia directa del código de `dmrlink_SAMPLE.cfg` (la configuración de ejemplo real, usada en despliegues de producción de DMRlink contra repetidoras MOTOTRBO físicas, vigentes desde 2013):
```
PEER_OPER: True
IPSC_MODE: DIGITAL
TS1_LINK: True
TS2_LINK: True
```
Que `ipsc/dmrlink_config.py` (líneas 172-187) traduce a `MODE_BYTE = 0x6A` (operational + digital + TS1 linked + TS2 linked) — **el mismo valor, además, que `ipsc2hbp/config.py` trae hardcodeado como "safe default"**, con el comentario explícito en el código: *"Proven working values — do not change without a wire capture to verify."* Es decir, **ambas fuentes independientes coinciden en 0x6A como el valor que efectivamente funciona contra repetidoras MOTOTRBO reales**, para *cualquier* peer que quiera efectivamente enlazarse (no solo repetidoras — DMRlink mismo es software, no una radio física, y usa 0x6A).

El `0x40` que usamos aparentemente corresponde a un perfil distinto (posiblemente CPS/herramientas de solo consulta que no buscan enlazar timeslots), no al de un peer que busca registrarse activamente en el sistema — que es nuestro caso.

### Hallazgo 4 — FLAGS: nos identificamos con cero capacidades, cuando deberíamos anunciar CON_APP + RCM

Usamos `FLAGS = 0x00000000` (sin ninguna capacidad reclamada). El mismo `dmrlink_SAMPLE.cfg` real, sección `[LOCAL]` del peer, usa:
```
CSBK_CALL: False
RCM: True          # "Repeater Call Monitoring"
CON_APP: True      # "Third Party Console App - exactly what DMRlink is"
XNL_CALL: False
DATA_CALL: True
VOICE_CALL: True
AUTH_ENABLED: True (en su caso; el nuestro debe ser False — ver más abajo)
```
El comentario del propio archivo de configuración (línea 124) dice textualmente sobre `CON_APP`: *"Third Party Console App - exactly what DMRlink is"* — es decir, **el propio autor documenta que una aplicación de software como la nuestra debe anunciarse con el flag CON_APP activo**, no con FLAGS en cero. `ipsc/dmrlink_config.py` (líneas 189-210) traduce esto a `FLAGS = 0x00 0x00 0x60 0x1C` (con auth) — sin el bit de AUTH (que no corresponde en nuestro caso, porque el campo "Authentication Key" de la repetidora está vacío) sería `0x00 0x00 0x60 0x0C`.

### Comparación byte a byte: paquete enviado (sesión 3) vs. paquete propuesto (sesión 4)

| Byte(s) | Campo | Enviado (sesión 3) | Propuesto (sesión 4) | Fuente del cambio |
|---|---|---|---|---|
| 0 | Opcode | `90` | `90` | Sin cambio |
| 1-4 | Peer ID (9999999) | `00 98 96 7f` | `00 98 96 7f` | Sin cambio |
| 5 | MODE | `40` | `6a` | DMRlink real config + safe default de ipsc2hbp |
| 6-9 | FLAGS | `00 00 00 00` | `00 00 60 0c` | `dmrlink_SAMPLE.cfg`: RCM+CON_APP (byte 3), DATA+VOICE sin AUTH (byte 4) |
| 10-13 | IPSC Version | `04 02 04 01` | `04 02 04 01` | Sin cambio — valor estándar, coincide en ambas fuentes |

**Paquete completo propuesto**: `90 00 98 96 7f 6a 00 00 60 0c 04 02 04 01` (14 bytes, misma longitud).

### Hallazgo 5 (lead nuevo, no relacionado al bug actual) — posible pista sobre dónde viaja el GPS dentro de IPSC
Revisando `DMRlink/ipsc/ipsc_const.py` en detalle (no buscado en sesiones anteriores), el diccionario `TYPE` de call types de `CALL_MON_STATUS` (0x61) incluye:
```python
'\x84': 'ARS/GPS?' # Not yet clear, seen by a user running ARS & GPS
```
Es decir, el propio autor de DMRlink observó (sin confirmar del todo) que el call type `0x84` dentro de paquetes `CALL_MON_STATUS` (0x61) está asociado a tráfico de ARS (Automatic Registration Service) y GPS. Esto es una **pista no confirmada, no una conclusión** — pero sugiere una hipótesis alternativa: si logramos registrarnos como peer con la capacidad RCM (Repeater Call Monitoring) activa, podríamos eventualmente ver datos de GPS viajando dentro del stream de `CALL_MON_STATUS`, como un canal separado del NAI-D/LRRP investigado en la sesión 1. Esto seria un hallazgo para investigar en el futuro, no ahora.

### Big picture: ¿aplica "Link Establishment" a un sistema standalone de un solo sitio?
**Sí, aparentemente aplica igual.** Evidencia: `ipsc_packet_reference.md` documenta que el propio CPS (Customer Programming Software) de Motorola usa este mismo mecanismo de registro Master-Peer (opcodes `0xE0`/`0xE1`, "REMOTE_PROGRAMMING_REQ/REPLY") para la programación remota de la repetidora vía IP — y la programación remota via IP funciona en sistemas standalone de un solo sitio sin ningún problema (es el modo normal de operación). Esto indica que el puerto 50000 / protocolo Master-Peer **no es exclusivo de la vinculación multi-sitio (IP Site Connect real)** — es el mecanismo general de conexión de *cualquier* aplicación externa autorizada (CPS, RDAC, consolas de terceros, y presumiblemente también NAI-D/GPS) contra el repetidor, sea standalone o parte de un sistema multi-sitio.

**Relación con la investigación de NAI-D (sesión 1)**: son, aparentemente, **dos mecanismos separados**. NAI-D parece ser un push UDP directo y sin registro previo hacia una IP de destino configurada (lo que motivó el plan de reprogramar el destino a `10.101.1.114` en la sesión 1) — no requiere pasar por este handshake Master-Peer. El plan de la sesión 1 sigue siendo válido de forma independiente a lo que resulte de esta línea de investigación del Link Establishment.

### Hipótesis de próximo experimento (PENDIENTE DE REVISIÓN — no ejecutar sin confirmar)
Repetir exactamente el mismo procedimiento de la sesión 3 (captura tcpdump en paralelo + `ipsc_peer_probe.py`), pero con el script ya actualizado (`~/ipsc_peer_test/ipsc_peer_probe.py`, ahora acepta `--mode-byte` y `--flags-bytes` configurables) y estos parámetros:

```bash
python3 ~/ipsc_peer_test/ipsc_peer_probe.py --master-ip 10.101.1.30 --master-port 50000 \
  --peer-id 9999999 --mode-byte 6a --flags-bytes 0000600c --timeout 5 \
  --log ~/ipsc_peer_test/probe_real_v2.log
```

Paquete exacto que se transmitiría: `90 00 98 96 7f 6a 00 00 60 0c 04 02 04 01` (14 bytes) — validado contra el master falso en loopback, mismo comportamiento que la versión anterior salvo los bytes de MODE/FLAGS.

**Riesgo**: igual o menor al intento anterior — seguimos anunciando capacidades estándar de una aplicación de terceros conectándose a un sistema IPSC (exactamente el perfil que usa DMRlink en producción desde hace más de una década), no reclamamos ser master ni pedimos autenticación que no vamos a poder cumplir.

### RESULTADO REAL (usuario confirmó, prueba ejecutada 2026-07-26 03:30 -03)

El usuario confirmó y esta vez la ejecución no fue bloqueada por el clasificador de seguridad del entorno (a diferencia de la sesión 3).

**Resultado: de nuevo timeout a los 5 segundos, sin ninguna respuesta.**

Confirmado en la captura pasiva paralela (`tcpdump host 10.101.1.30`, con dump hexadecimal completo):
```
03:30:15.024232  10.101.1.114.55897 > 10.101.1.30.50000: UDP, length 14
  payload: 90 00 98 96 7f 6a 00 00 60 0c 04 02 04 01   ← exacto, confirmado byte a byte en el cable
03:30:20.232152  ARP who-has 10.101.1.30 tell 10.101.1.114   ← mismo patrón de siempre, no relacionado a IPSC
03:30:20.647220  ARP reply 10.101.1.30 is-at e0:92:8f:e0:19:62
```
El paquete con MODE=`0x6a` y FLAGS=`0x0000600c` salió exactamente como se planeó. **Cero respuesta, igual que con MODE=`0x40`/FLAGS=`0x00000000` en la sesión 3.**

### Conclusión de la Sesión 4: la hipótesis de MODE/FLAGS incorrectos queda debilitada
**Tres intentos independientes, dos configuraciones distintas de MODE/FLAGS, mismo resultado exacto: silencio total.** Esto reduce bastante la probabilidad de que el problema sea el valor de esos bytes específicos — si el Master estuviera simplemente descartando por un MODE/FLAGS "raro" pero seguiría respondiendo a un peer con el perfil "correcto" (0x6A, el valor usado en producción real por DMRlink), deberíamos haber visto al menos algún tipo de respuesta esta vez. No la hubo.

**Hipótesis que ganan peso, sin confirmar:**
1. Existe algún mecanismo de autorización (allowlist de IP o de Radio ID, o autenticación real) en algún otro lugar del CPS que no hemos revisado todavía — no en "Sites" (ya descartado), posiblemente bajo alguna sección de "Network Applications", "RDAC", o un toggle general de habilitación que no hemos identificado.
2. El campo "Authentication Key" vacío podría no significar "sin autenticación" — podría haber un checkbox separado de "Enable Authentication" que esté activo independientemente del contenido de la clave, y el Master esté descartando silenciosamente paquetes no autenticados.
3. El "Link Type: Master" en este modelo/firmware específico (SLR5100) podría no aceptar registro dinámico de peers de terceros en absoluto, incluso con el formato de paquete perfectamente correcto — a diferencia de los sistemas IPSC "abiertos" del mundo radioaficionado (donde corre DMRlink) que sí lo permiten.

### Próximos pasos (Sesión 4, actualizados)
1. Revisar en el CPS si existe un toggle de "Enable Authentication" separado del campo de la clave, y cualquier sección de "Network Applications" / "RDAC" / autorización que no hayamos visto todavía.
2. Considerar contactar o revisar documentación oficial de Motorola sobre requisitos de "Network Application" para la familia SLR5100 (más allá de lo que la comunidad de radioaficionados ha reverse-engineered, que es para IPSC "abierto").
3. No seguir variando bytes del paquete a ciegas sin nueva evidencia concreta — ya se probaron las dos configuraciones documentadas por fuentes independientes (DMRlink real y "safe default" de ipsc2hbp) sin éxito.
4. Ambos `tcpdump` de esta sesión (PID de sesión 3 y PID `270665` de esta prueba) siguen corriendo en background — liberar con `sudo kill -TERM` cuando se quiera.

---

## Sesión 5 — Pivote a captura por RF (SDR), bypaseando la capa IP protegida

### Contexto y motivación del pivote
Se descubrió que el camino por IP (Link Establishment / NAI-D) está protegido por TLS-PSK Authentication en la repetidora, sin la clave disponible — bloqueando el avance por esa vía. Se decidió pivotar a un enfoque completamente distinto: capturar la ráfaga DMR directamente por RF con un SDR, en la frecuencia de subida (handy→repetidora), **antes** de que el tráfico llegue a cualquier capa de red protegida. Esto es una capa físicamente distinta (aire, no Ethernet/IP) y no debería estar afectada por la protección TLS-PSK del lado de red.

**Frecuencia confirmada con el usuario**: RX 153.335 MHz — convención estándar de repetidoras, donde "RX" es la frecuencia en la que **el repetidor recibe** (o sea, la que los handys transmiten), que es exactamente la dirección handy→repetidora que buscamos.

### Hardware detectado
- **SDR**: dongle genérico RTL2832U (USB ID `0bda:2832`), con tuner **Rafael Micro R820T** — confirmado con `rtl_test` (rango típico ~24 MHz–1766 MHz, cubre VHF sin problema).
- Bloqueado inicialmente por el driver de kernel `dvb_usb_rtl28xxu` (el conflicto clásico de estos dongles: el kernel los reclama como sintonizador de TV digital). Se resolvió con `sudo modprobe -r` + blacklist persistente en `/etc/modprobe.d/blacklist-rtlsdr.conf` (`blacklist dvb_usb_rtl28xxu`), y reglas udev de `librtlsdr` (`60-librtlsdr0.rules`) que asignan el dispositivo al grupo `plugdev` una vez liberado del driver DVB.

### Configuración de sudo NOPASSWD (nueva, a pedido del usuario)
Para agilizar esta y futuras sesiones, se configuró `/etc/sudoers.d/claude-code` con una lista acotada de comandos sin contraseña: `apt install`, `modprobe`, `udevadm`, `setcap`, `kill`, `make install`, `ldconfig`. **No incluye `tee` ni shell genérica** — cualquier escritura directa de archivos root (como el blacklist de modprobe) se le sigue pidiendo al usuario explícitamente. Esto resuelve, entre otras cosas, el problema recurrente de no poder detener procesos `tcpdump` con capabilities (ahora `sudo kill` funciona sin pedir contraseña).

### Elección de herramienta: DSD-FME (no SDR++)
Investigación con fuentes externas (no solo memoria/documentación):
- **SDR++**: confirmado que **no tiene decodificador DMR nativo en Linux**. Existe un feature request abierto sin resolver en el repo oficial. Descartado.
- **DSD-FME** (`lwvmobile/dsd-fme`, fork activo "Florida Man Edition" de DSD): tiene soporte LRRP **dedicado y maduro**, confirmado leyendo el código fuente real (no solo la documentación):
  - Archivo `src/dsd_gps.c` completo dedicado a decodificar múltiples formatos de GPS embebido en DMR (LRRP y variantes), con salida final en grados decimales + N/S/E/W + error de posición + velocidad + rumbo.
  - Flag de línea de comandos `-L <archivo>` escribe/appendea los datos LRRP decodificados directamente a un archivo — dos formatos disponibles vía menú NCurses: `~/lrrp.txt` (compatible con QGis) o `./DSDPlus.LRRP` (compatible con la herramienta LRRP.exe de DSDPlus).
  - Flag `-Z` loguea a consola el payload crudo de cada frame/PDU decodificado (útil para ver datos DMR sin parsear, más allá de lo que el parser LRRP reconozca).
  - Soporte de entrada RTL-SDR directo: `-i rtl:dev:freq:gain:ppm:bw:sq:vol`.
  - **Importante**: esto significa que probablemente no haga falta escribir un parser de LRRP propio (el objetivo original de la sesión 1) — DSD-FME ya lo hace, con salida en formato utilizable.

Fuentes: [dsd-fme (GitHub)](https://github.com/lwvmobile/dsd-fme), [dsd-neo cli.md](https://github.com/arancormonk/dsd-neo/blob/main/docs/cli.md) (fork más nuevo con documentación de CLI más detallada, mismo flag `-L`), [SDRPlusPlus issue #959](https://github.com/AlexandreRouma/SDRPlusPlus/issues/959) (feature request de decodificación digital sin resolver).

### Instalación
Compilado desde código fuente en `~/sdr_dmr_test/`:
1. `mbelib` (fork `lwvmobile/mbelib`, rama `ambe_tones`) — requisito para decodificación de voz AMBE. Trae aviso de patentes del codec (práctica estándar en la comunidad, no bloqueante).
2. `dsd-fme` — compiló limpio, detectó automáticamente `librtlsdr` (v0.6.0), PulseAudio, ncurses, codec2, libsndfile, fftw3, lapack.

Binario final: `/usr/local/bin/dsd-fme`.

### Resultado de la prueba de recepción básica: NO CONFIRMADA todavía

Se corrieron 3 pruebas con `dsd-fme -fs -i rtl:0:153.335M:22:0:12:0:2` (modo `-fs` = DMR TDMA BS/MS Simplex, el correcto para un sistema convencional de un solo repetidor, no trunking):

| Intento | Duración | Condición | Resultado |
|---|---|---|---|
| 1 | 30s | Sin transmisión coordinada | Sin sync DMR (esperable, sin tráfico) |
| 2 | 30s | Usuario presionó PTT en un handy | Sin sync DMR |
| 3 | 60s | Usuario presionó PTT | Sin sync DMR + **evento de desconexión USB a mitad de captura** (`cb transfer status: 5` repetido = LIBUSB_TRANSFER_NO_DEVICE, `Reattaching kernel driver failed!` al final) |

**Dos problemas identificados, no confundir uno con otro:**
1. **Sin antena conectada** — el usuario confirmó que no hay ninguna antena en el RTL-SDR, solo cercanía física (~10cm) a un handy transmisor. Un conector SMA/MCX pelado es una vía de recepción muy pobre e inconsistente; esto por sí solo explica plausiblemente la falta de sync en los 3 intentos.
2. **Desconexión USB transitoria** en el intento 3 — el dispositivo se re-enumeró (pasó de "Bus 003 Device 004" a "Device 006") durante la captura. Verificado que el dongle sigue funcional después (`rtl_test` OK). Causa no confirmada — podría ser alimentación USB insuficiente, conexión floja, o un golpe físico accidental al manipular el handy tan cerca del laptop. **No se investigó más a fondo esta sesión.**

**No se validó la recepción básica.** Esto queda pendiente, no se avanzó a la parte de análisis de data bursts/CSBK en vivo (no tiene sentido sin señal real entrando).

### Próximos pasos (Sesión 5)
1. Conseguir y conectar una antena apropiada para VHF ~150MHz (aunque sea un cable rígido de ~49cm de largo, cuarto de onda) antes de repetir la prueba de recepción.
2. Verificar la conexión física USB del dongle (puerto, cable, alimentación) para descartar que se repita la desconexión transitoria.
3. Una vez validada la recepción básica (sync DMR real, con o sin voz decodificada), repetir con `-L lrrp.log` activado y generar un evento GPS real (activar tracking en un handy) para ver si aparece algo en el archivo LRRP de salida — esto podría directamente reemplazar la necesidad de un parser propio.
4. Si DSD-FME logra decodificar tráfico de este sistema, evaluar correr con `-Z` en paralelo para inspeccionar cualquier CSBK/data burst crudo no reconocido por el parser LRRP estándar.

### Continuación (misma sesión) — antena improvisada conectada + usuario alejado del laptop

Cambios: se conectó una antena improvisada al SDR, y el usuario se alejó varios metros del laptop antes de transmitir, específicamente para evitar la desconexión USB del intento 3 anterior (hipótesis: la RF de un handy transmitiendo a ~10cm del dongle interfería con el bus USB).

**Comando base validado**: `dsd-fme -fs -i rtl:0:153.335M:22:0:12:0:2 -o null` (+ `-Z` para payload crudo, + `-L <archivo>` en las pruebas de LRRP). Nota técnica constante en todas las pruebas: el tuner se posiciona realmente en 153.587 MHz (offset autom. de +252kHz para evitar el spike de DC) y desplaza digitalmente la señal de interés a 153.335 MHz.

#### Prueba A — 120 segundos, con `-Z`, voz normal (PTT) coordinada
Verificado `rtl_test` sano antes de arrancar.

| Métrica | Resultado |
|---|---|
| Eventos "Sync: +DMR" | **2** — `04:20:25` (Color Code=XX, no decodificado limpio) y `04:20:29` (Color Code=00, decodificado limpio) |
| Desconexiones USB | **0** — confirma que alejarse del laptop resolvió el problema del intento 3 anterior |
| Frames de voz (AMBE) | **Sí**, en ambos syncs, con errores FEC variables (1 a 5 por trama) |
| Total audio errors (contador acumulado de la sesión) | 98 |

Detalle del segundo sync (el limpio): además de voz, se capturó un PDU crudo de 9 bytes vía `-Z`: `C3 C6 CC D9 36 4D 93 64 D4`, marcado con `SLOT 1 FLCO FEC ERR` — es decir, tiene error de FEC en el Link Control, por lo que su contenido no es confiable todavía, pero confirma que sí se están recibiendo PDUs de datos, no solo voz. El primer sync produjo un PDU de puros ceros (`00 00 00...`) con `SRC=0 TGT=0`, consistente con ruido/decodificación fallida, no datos reales.

**Conclusión de la Prueba A**: recepción real confirmada (voz + al menos un PDU parcialmente decodificado), pero señal marginal/débil — consistente con antena improvisada, no con un problema de configuración de software.

#### Prueba B — 30 segundos, con `-L lrrp_test.log`, coordinando activación de modo rastreo GPS
**Resultado: 0 sync, 0 errores de audio, archivo LRRP nunca se creó** (dsd-fme aparentemente solo genera ese archivo cuando decodifica al menos un registro LRRP real, no de antemano).

El usuario confirmó que el modo rastreo GPS del handy (alias "Matías", ID `1005`) ya estaba activo y mostrando coordenadas localmente en el propio HT — pero se desconoce si el HT transmite ese dato de forma continua, bajo demanda, o en un intervalo periódico no conocido. Dato nuevo relevante para futuras sesiones: **HT identificado como alias "Matías" / ID `1005`**.

#### Prueba C — 3 minutos, con `-L lrrp_test.log`, ventana más amplia para un reporte periódico
**Resultado: 1 sync marginal** (`04:26:37`, Color Code=XX, `CACH/Burst FEC ERR` — error de FEC a nivel de ráfaga TDMA), sin frames de voz después, **sin archivo LRRP generado**, **0 desconexiones USB**.

### Síntesis de las 3 pruebas de esta continuación
- **0 desconexiones USB en ninguna de las 3 pruebas** (350 segundos totales de captura) — confirma que la proximidad RF era la causa del problema del intento 3 anterior, resuelto alejándose.
- **4 eventos de sync en total**, la mayoría con errores de FEC (Color Code=XX o CACH/Burst FEC ERR); solo 1 de los 4 decodificó limpio (Color Code=00, con voz).
- **Ningún registro LRRP capturado todavía**, pese a que el modo rastreo del handy estaba confirmado activo — no se sabe si es por timing (el HT no transmitió el reporte dentro de las ventanas probadas) o por señal insuficiente para decodificar ese burst específico en caso de haber ocurrido.
- El patrón de errores es consistente con **antena improvisada como principal factor limitante actual**, no con un problema de configuración de dsd-fme, frecuencia, o protocolo.

### Próximos pasos (actualizados)
1. **Antena real apropiada para ~150MHz** sigue siendo la mejora de mayor impacto esperado — la improvisada alcanza para sync ocasional pero no para decodificación confiable.
2. Si se consigue mejor antena, repetir con ventanas largas (3-5 min) y `-L` activo, apuntando específicamente a capturar un reporte LRRP limpio (sin errores de FEC) en vez de solo voz.
3. Investigar (para una próxima sesión) si existe forma de forzar/solicitar un reporte GPS bajo demanda desde el HT en vez de esperar un intervalo periódico desconocido — reduciría la incertidumbre de timing.
4. Guardar el alias/ID del HT de prueba ("Matías" / `1005`) para poder identificarlo en futuros PDUs decodificados por SRC ID, una vez que la decodificación sea confiable.

### Antena "corregida" + prueba en frecuencia de bajada (159.635 MHz)

Se ajustó/corrigió la antena improvisada, y se corrieron 2 pruebas adicionales en 153.335 MHz, más 1 prueba pasiva en la frecuencia de bajada del repetidor (159.635 MHz, la que el repetidor transmite hacia los handys — dirección opuesta a la que veníamos escuchando). **Ninguna de las 3 involucró PTT de voz confirmado.**

| # | Frecuencia | Antena | Duración | Acción del usuario | Sync | Voz | USB | LRRP |
|---|---|---|---|---|---|---|---|---|
| C1 | 153.335 | Corregida | 180s | Ninguna (ajustando antena) | 0 | No | No | No |
| C2 | 153.335 | Corregida | 120s | Apagó/prendió rastreo GPS del HT "Matías"/1005, **sin PTT** | 0 | No | No | No |
| D1 | **159.635** (bajada) | Corregida | 180s | Ninguna, pasivo | 0 | No | No | No |

**Conclusión importante de estas 3 pruebas**: en ninguna de las dos frecuencias (subida 153.335 ni bajada 159.635) se detectó actividad alguna en ventanas sin PTT de voz — ni siquiera con el toggle de rastreo GPS del HT. Esto es consistente con que **el sistema no emite ninguna portadora/baliza continua en RF durante inactividad** (comportamiento normal de una repetidora convencional, no trunking: solo transmite cuando hay una llamada real en curso). El toggle de encendido/apagado del modo rastreo por sí solo aparentemente no genera transmisión RF inmediata.

**Sigue sin resolverse** si la antena "corregida" es mejor, peor o igual que la improvisada original — el único dato comparable que tenemos (B1, con la antena vieja) tuvo PTT de voz confirmado y sí mostró actividad; ninguna prueba con la antena nueva incluyó todavía PTT confirmado. **Pendiente**: repetir con PTT de voz real usando la antena corregida, para tener la comparación de igual a igual.

### Balance completo de la Sesión 5 hasta ahora (8 pruebas)
| Antena | Con PTT confirmado | Sin PTT | Resultado |
|---|---|---|---|
| Ninguna | 2 pruebas (A2, A3) | 1 prueba (A1) | 0 sync en las 3; A3 tuvo desconexión USB por proximidad |
| Improvisada + distancia | 1 prueba (B1) | 2 pruebas (B2, B3) | **Único caso con recepción real**: B1 con PTT → 2 syncs, 1 limpio con voz. B2/B3 sin PTT → 0-1 sync marginal |
| Corregida | 0 pruebas todavía | 3 pruebas (C1, C2, D1) | 0 actividad en las 3 |

**Patrón que se sostiene en las 8 pruebas**: la única condición que generó recepción confiable fue PTT de voz activo. Ningún toggle de GPS, ni escucha pasiva en ninguna de las 2 frecuencias, produjo actividad detectable.

---

### HALLAZGO CLAVE — voz y GPS viajan en timeslots DMR distintos, y dsd-fme no los distingue en el modo que venimos usando

**Dato nuevo confirmado por el usuario en el codeplug del HT DGP8550** (no en la repetidora): el canal de voz normal usa **Timeslot 1**. Existe un canal separado, **"GPS-R2"**, configurado en **Timeslot 2**, misma frecuencia (RX 159.635 / TX 153.335 desde la perspectiva del HT — coincide con lo que ya sabíamos), mismo Color Code (1). Es decir, en el mismo canal de RF hay dos timeslots DMR (estructura TDMA estándar), y voz/GPS usan slots separados dentro de ese mismo par de frecuencias.

**Investigación en el código fuente de dsd-fme — confirmado, no es una limitación de configuración sino del código:**

- Todos nuestros syncs hasta ahora mostraron `MS/DM MODE/MONO` en la línea de "Sync:". Esto es el formato específico del archivo `dmr_ms.c` (decodificador de bursts con patrón de sync tipo "MS" — Mobile Station — el que usa la transmisión directa de un handy hacia el repetidor, en el enlace de **subida**, 153.335 MHz).
- En `dmr_ms.c`, líneas 65 y 419: **`state->currentslot = 0; //force to slot 0`** (comentario textual: *"Hardset variables for MS/Mono"*) — el timeslot se fuerza a 0 (slot 1) sin importar en qué slot real venía el burst.
- Contraste con `dmr_bs.c` (decodifica bursts con patrón de sync tipo "BS" — Base Station — la retransmisión del propio repetidor, en el enlace de **bajada**, 159.635 MHz): líneas 133/805, **`internalslot = state->currentslot = tact_bits[1];`** — aquí el slot SÍ se deriva de bits de timing reales del burst, alternando correctamente entre TS1/TS2 (confirmado también visualmente en el código: imprime `[SLOT1] slot2` o `slot1 [SLOT2]` según corresponda).

**Conclusión**: escuchando en la frecuencia de subida (153.335 MHz, lo que hicimos en todas las pruebas hasta ahora), dsd-fme recibe bursts con patrón "MS" — que por diseño del protocolo DMR es el patrón correcto para una transmisión de subscriptor, independientemente de si usa TS1 o TS2 — pero el código de dsd-fme que procesa ese patrón **no distingue el timeslot real, lo fuerza a 0 siempre**. Esto significa que en TODAS nuestras capturas anteriores (incluidas B1 y B3, las únicas con sync), **no hay forma de saber retroactivamente si el burst decodificado era TS1 (voz) o TS2 (GPS)** — la información de slot simplemente no se registró de forma diferenciada. Revisamos los logs de B1/B3 específicamente: ambos muestran `MS/DM MODE/MONO`, confirmando que pasaron por este mismo camino de código sin distinción de slot.

**Esto no significa necesariamente que el GPS nunca haya sido recibido** — el parser de LRRP (`dsd_gps.c`) actúa sobre el contenido decodificado del burst de datos, no sobre la etiqueta de slot; es posible que un burst de GPS haya llegado y no lo reconociéramos como tal en el log, aunque no hay evidencia de esto todavía (ningún archivo LRRP se generó en ninguna prueba).

**Implicación práctica para la próxima prueba**: escuchar en la frecuencia de **bajada (159.635 MHz)** durante una transmisión activa debería hacer que dsd-fme reciba el patrón "BS" (la retransmisión del propio repetidor), que sí pasa por el código de `dmr_bs.c` con tracking de slot real y correcto — ahí sí deberíamos poder diferenciar visualmente TS1 (voz) de TS2 (GPS) en la salida de consola.

---

## Sesión 6 — Grabación real desde el cuartel (cerca de la repetidora), pipeline offline IQ→WAV→dsd-fme

### Contexto y condición nueva
Usuario físicamente en el cuartel, cerca de la repetidora — mejor ubicación que las pruebas de la sesión 5 (ni saturación por cercanía extrema al handy, ni los ~800m de debilidad de señal anteriores). Objetivo: usar por primera vez de punta a punta la metodología "grabar IQ crudo con `rtl_sdr` en 159.635 MHz (downlink) + iterar offline con `dsd-fme` en modo lectura de archivo", coordinando en tiempo real una transmisión de voz normal (control, TS1) y una activación de modo emergencia (con rastreo GPS ya activado de antes) en un HT (TS2).

### Verificación de hardware (antes de grabar)
- `lsusb` + `rtl_test -t`: dongle detectado (`0bda:2832`, tuner Rafael Micro R820T), tabla de ganancias completa, sin errores de reclamo USB.
- Test de estabilidad con `timeout 8 rtl_test -p`: pérdida de muestras despreciable (4 ppm, "lost at least 144 bytes" sobre ~33MB) — dongle sano.
- **Hallazgo operativo (no de RF)**: un intento anterior de test con `timeout 10 rtl_test -p | tail -30` dejó el proceso `rtl_test` colgado en segundo plano (no terminó pese al `timeout`, aparentemente por un problema conocido de libusb al cancelar streaming bajo un pipe), reteniendo el dongle (`usb_claim_interface error -6` en el siguiente intento). Se identificó vía `ps aux` y se mató con `kill -9`. **Mismo patrón se repitió con la grabación real** (ver abajo) — queda como lección operativa: después de cualquier `rtl_sdr`/`rtl_test` cortado con `timeout`, verificar con `ps aux` que el proceso realmente murió antes de reutilizar el dongle.

### Grabación real
Comando ejecutado, confirmado con el usuario antes de correr:
```bash
timeout 240 rtl_sdr -f 159635000 -s 240000 -g 30 iq_159635_20260807_175446.cu8
```
- Inicio: 17:54:46. Ganancia solicitada 30 dB → redondeada por hardware a 29.7 dB (paso soportado más cercano).
- **El proceso `rtl_sdr` volvió a colgarse al recibir la señal de corte del `timeout`** (mismo bug de libusb que en el punto anterior): el archivo dejó de crecer a los ~239.2s (coincide exacto con el objetivo de 240s), pero el proceso siguió vivo hasta que se lo mató manualmente con `kill -9`. **El archivo grabado quedó íntegro y completo** — el bug es solo de terminación del proceso, no afecta los datos capturados.
- Archivo final: `iq_159635_20260807_175446.cu8`, 114.819.072 bytes = 239,2 s a 240 kS/s.

Cronología reportada por el usuario (aproximada, ver discrepancia de timing más abajo): PTT de voz de control (TS1) ~minuto 1; activación de modo emergencia (TS2) ~minuto 2-3, con el modo rastreo GPS ya activado de antes (no recién en esta ventana).

### Análisis de potencia — primer hallazgo importante: la detección simple por energía no sirve para ubicar los bursts DMR
Se calculó el perfil de potencia RMS del IQ crudo completo en ventanas de 1s (`numpy`, sin filtrado). Resultado: picos de +3 a +6 dB sobre el piso de ruido solo en t≈5-14s, t≈38-54s y t≈74-75s (todos dentro del primer minuto y cuarto); el resto del archivo (t=80s a t=239s) se ve como piso de ruido plano.

**Se verificó con `dsd-fme` que estas ventanas de "alta energía" NO contienen ningún sync DMR real** (0 syncs en los 3 clips aislados). Los 2 únicos eventos de sync real de todo el archivo (confirmados por bisección binaria, ver abajo) están en **t≈110-125s** y **t≈227-239s** — zonas que el detector de energía por RMS había marcado como "silencio". Conclusión: **la potencia total de banda no es un proxy confiable para presencia de tráfico DMR** en esta configuración (la modulación 4FSK de DMR no necesariamente eleva la energía total de forma visible sobre el ruido de fondo cuando la señal es débil/marginal; los picos de energía detectados son de origen no identificado — posible RF ambiente del cuartel, no relacionado al repetidor).

Adicionalmente, sin relación al bug de detección: el histograma de bytes del IQ crudo mostró rango dinámico muy angosto (min=125, max=130 sobre una escala de 0-255, centro 127.5, `std=0.53`) — **sin clipping/saturación** (cero muestras en los extremos 0-3 o 252-255), pero consistente con señal recibida débil en términos absolutos pese a la cercanía a la repetidora. No se investigó a fondo la causa (antena, ganancia aún insuficiente, o el "[R82XX] PLL not locked!" que sigue apareciendo en cada arranque desde la sesión 5, nunca confirmado como benigno o no).

### Metodología de bisección binaria para ubicar los bursts reales
Dado que la energía no servía como guía, se localizaron los 2 eventos de sync reales por bisección binaria del archivo completo (cortar por la mitad con `numpy`, convertir a WAV, correr `dsd-fme -fs -Z`, ver si el conteo de "Sync: +DMR" es 0 o 1, repetir sobre la mitad que dio resultado). 4 niveles de bisección ubicaron los eventos en ventanas de ~10-15s.

**Hallazgo de método, importante para sesiones futuras**: recortar el clip demasiado ajustado alrededor del burst (sin varios segundos de contexto previo) hace que `dsd-fme` **pierda el sync incluso cuando la señal está objetivamente ahí** — se confirmó con clips de 6-12s centrados exactamente en la ventana que en un recorte más ancho (24-30s) sí sincronizaba. Esto es consistente con que el decodificador necesita tiempo de estabilización (AGC / recuperación de timing) antes de poder enganchar un burst. **Recomendación para el pipeline offline**: nunca recortar a menos de ~20-30s de ventana alrededor de un evento de interés.

### Evento A — voz TS1 confirmada (t≈110-125s de la grabación ≈ 17:56:36-17:56:51 real)
```
Sync: +DMR MS/DM MODE/MONO | Color Code=XX | VC*
18 tramas AMBE decodificadas con error FEC variable (0 a 5 por trama) — consistente con voz real, no ruido
SLOT 1 FLCO FEC ERR (el Full Link Control del slot tuvo error de FEC, contenido de voz en sí válido)
DMR PDU Payload: 46 60 00 00 00 37 D0 03 D6
```
**Slot 1 confirmado explícitamente** (no forzado a 0 como en las capturas de subida de la sesión 5) — la retransmisión de bajada sí permite diferenciar el timeslot real, como se había predicho al cierre de la sesión 5. Casi con certeza corresponde al PTT de voz de control coordinado por el usuario, ~50-80s más tarde de lo estimado ("~minuto 1").

### Evento B — burst de datos en TS2, candidato a LRRP pero sin decodificar (t≈227-239s ≈ 17:58:33-17:58:45 real, literalmente los últimos segundos del archivo)
```
Sync: +DMR MS/DM MODE/MONO | Color Code=XX | PI (CRC ERR)
DMR PDU Payload: 88 18 E0 06 A8 28 91 30 F8 E0 23 92
```
Este es un **burst de datos (no voz)** — "PI" (Privacy Indicator) es un tipo de cabecera de PDU DMR, distinto del "VC" (voice call) del Evento A. Es la primera vez en todo el proyecto que se captura un burst de **datos** (no de voz) desde la bajada. Se probó relajar el chequeo de CRC (`-F`) y deshabilitar el filtrado de entrada (`-l`) para intentar rescatar más contenido — ninguno de los dos agregó datos nuevos (`-l` incluso empeoró el resultado, perdiendo el header PI). El archivo `-L` (LRRP) **nunca se generó** en ningún intento — `dsd-fme` solo tiene la cabecera de este PDU, no los bloques de datos subsiguientes donde iría la posición GPS.

**Dato operativo importante**: este burst aparece a **3-15 segundos del corte de la grabación** (el archivo termina en t=239,2s). Es muy probable que la transmisión real haya continuado más allá de nuestra ventana de 240s y que los bloques de datos siguientes (los que contendrían el payload LRRP real) hayan quedado fuera de la grabación.

### Balance de la sesión — no hay decodificación LRRP todavía, pero hay progreso concreto
- **No se logró contenido LRRP real** — no corresponde el hito de la tarea 6, no se destaca como tal.
- Progreso real: primera vez que se captura y aísla un **burst de datos** distinguible de voz en la bajada (Evento B), con cabecera de PDU reconocida por `dsd-fme` (PI), aunque con CRC fallido y sin cuerpo.
- Confirmado el tracking correcto de timeslot en bajada (Evento A en SLOT 1 explícito), validando la predicción de cierre de la sesión 5.
- Confirmado que el pipeline offline completo (`rtl_sdr` → `iq_to_wav.py` → `dsd-fme -i archivo.wav`) funciona de punta a punta y es utilizable para iterar sin repetir transmisiones.
- Descubierta y documentada una limitación real del pipeline: ventanas de recorte angostas rompen el sync aunque la señal esté presente.

### Próximos pasos (Sesión 6)
1. **Repetir la prueba con grabación más larga** (recomendado 6-8 min en vez de 4) para no cortar el burst de datos del final — el Evento B sugiere que el timing real de las transmisiones coordinadas por radio en el cuartel corre más tarde de lo estimado a ojo, y conviene dejar más margen de cola.
2. Si se repite, extraer el clip alrededor del nuevo burst de datos con ventanas de **al menos 20-30s** de contexto (lección de esta sesión), no recortes ajustados.
3. Investigar la causa del rango dinámico angosto del IQ (std=0,53 sin clipping) — probar una ganancia más alta que 30 (ya que no hubo saturación, hay margen) o revisar la antena/acople físico, dado que seguimos cerca de la repetidora y sería esperable más señal.
4. Sigue sin resolverse si el "[R82XX] PLL not locked!" que aparece en cada arranque del dongle es benigno o síntoma de un problema real — no bloqueó la recepción esta sesión, pero tampoco se descartó.
5. Recordatorio operativo para toda futura sesión: verificar con `ps aux` después de cualquier `rtl_sdr`/`rtl_test` cortado por `timeout` — el proceso puede quedar colgado reteniendo el dongle pese a que el archivo ya esté completo.
6. No se avanzó todavía al script puente hacia el backend (`POST /api/telemetry` del repo `tracking-GPS-VHF`) — sigue correctamente pendiente para una sesión posterior, una vez que haya una decodificación LRRP real y repetible.

---

## Sesión 7 — Corrección de frecuencia: primer burst de datos con CRC válido confirmado por código fuente

### Contexto
Continuación directa de la sesión 6. Nueva grabación real coordinada: el usuario generó 2-3 ciclos de "modo emergencia activado (~30-90s, con corte automático a los ~30s observado por el usuario, no confirmado como regla general) / silencio (~30-40s)" sobre el mismo HT, con rastreo GPS y PTT de control también presentes, dentro de una única grabación de downlink (159.635 MHz).

### Grabación
```bash
timeout 600 rtl_sdr -f 159635000 -s 240000 -g 30 iq_159635_20260807_181959.cu8
```
- Inicio 18:19:59. Cortada manualmente con `kill -TERM` a pedido del usuario tras confirmar que terminó los ciclos (a los ~228s de los 600s de timeout).
- **A diferencia de la sesión 6, el proceso `rtl_sdr` terminó limpiamente esta vez** (SIGTERM funcionó sin colgarse) — no se repitió el bug de libusb, o no aplicó en este corte puntual.
- Archivo final: `iq_159635_20260807_181959.cu8`, 113.246.208 bytes = 235,9 s a 240 kS/s. Sin clipping (histograma min=125/max=130 sobre 0-255), rango dinámico similar a la sesión anterior (std=0,60).

### Primer intento: escaneo lineal completo con dsd-fme → 0 syncs (aplicando la lección de la sesión 6)
Se abandonó la detección por picos de energía (lección de sesión 6: no correlaciona con bursts DMR reales) y se corrió `dsd-fme -fs -Z -L` directamente sobre el archivo completo convertido a WAV. **Resultado: 0 syncs reales en todo el archivo**, pese a que el perfil de potencia (calculado igual que antes, solo como referencia, no como método de búsqueda) mostraba varias ventanas de actividad real a lo largo de todo el archivo. Se descartó también un problema de recorte (se probó archivo completo, ambas mitades, y ventanas de 60-70s centradas en picos de potencia — todo dio 0 syncs).

### Diagnóstico y causa raíz encontrada: offset de frecuencia sin compensar
Se comparó el espectro (FFT, ventana Hanning, promediado) de una ventana con actividad real (t=119-123s) contra una ventana de silencio (t=140-144s):
- Ventana de silencio: pico exactamente en +0 Hz, ancho ~0 Hz — el clásico spike de DC del RTL2832U, no señal real.
- **Ventana de actividad: pico en -6504 Hz, ~27,6 dB sobre el piso de ruido, ancho aprox. 3,9 kHz — señal real, no el spike de DC.**

El offset de -6,5 kHz es del orden de lo esperado por el error de PPM del dongle ya medido en la sesión 5 (26-45 ppm ≈ 4,2-7,2 kHz a 159,635 MHz) — **nunca se había compensado este offset en el pipeline offline** (las sesiones 5 y 6 usaron `freq_corr=0` en `iq_to_wav.py`, asumiendo sintonía directa suficiente). Se aplicó `freq_corr=-6504` en `iq_to_wav.py` sobre la ventana de prueba: **pasó de 0 a 152 syncs reales**, con `Color Code=01` (el valor correcto y confirmado del sistema) apareciendo de forma limpia y repetida, y el patrón `[slot1] slot2` / `slot1 [slot2]` mostrando **tracking real y alternante de timeslot** (no forzado a un valor fijo).

**Esta es la causa raíz de por qué las sesiones 5 y 6 solo lograban syncs marginales/esporádicos**: no era (solo) señal débil, sino una corrección de frecuencia faltante en el pipeline offline. Aplicada al archivo completo (`freq_corr=-6504`), el escaneo completo pasó de 0 a **1.425 syncs reales** sobre los 235,9s.

### Inventario de bursts sobre el archivo completo corregido
| Tipo | Cantidad | Nota |
|---|---|---|
| IDLE (CC=01) | 865 | Bursts de relleno normales del canal |
| TLC (Terminator LC, CC=01) | 147 | Fin de llamadas de voz |
| VC1-VC6 (tramas de voz, CC=01) | ~177 | Voz de las distintas transmisiones/ciclos |
| CSBK (CC=01) | 11 | Ver detalle abajo — 2 grupos distintos |
| VLC (Voice LC Header, CC=01) | 7 | Inicio de llamadas de voz |
| VC con CC≠01 (00/02/03/08) | ~22 | Decodificación marginal puntual, ruido — no indica sistemas con otro Color Code real |
| **DATA (Data Header, CC=01)** | **1** | **Ver detalle — el hallazgo principal de la sesión** |
| **R12U (bloque de datos, CC=01)** | **1** | Bloque de payload asociado al DATA header de arriba |

### 🏆 HALLAZGO PRINCIPAL — primer burst de datos con CRC confirmado válido por código fuente (no LRRP, pero el mayor avance técnico del proyecto hasta ahora)

De los 11 CSBK, **8 son preámbulos repetidos** de una misma transmisión: `Preamble CSBK - Individual Data - Source: 1000 - Target: 64250`, seguidos de:
```
Slot 1 Data Header - Indiv - Unconfirmed Delivery - Source: 1000 Target: 64250
 SAP 09 [EXTD HDR] - FMF 1 - BLOCKS 02 - PAD 00 - FSN: [0] - Unconfirmed data single fragment 1
 DMR PDU Payload [02][90][00][FA][FA][00][03][E8][82][00][25][2B]
```
seguido de un único bloque de datos:
```
Color Code=01 | R12U
 DMR PDU Payload [20][04][31][30][30][30][00][00][BD][45][8D][46]
```

**Se verificó contra el código fuente de `dsd-fme` (no se asumió) que este header pasó CRC real:**
- `dmr_dburst.c:627` solo imprime `(CRC ERR)` cuando `CRCCorrect == 0` — y esa marca **no aparece** en este burst.
- El caso `0x06` (Data Header) usa un CRC de 16 bits real (`crclen=16`, `crcmask=0xCCCC`) — no es un burst exento de chequeo.
- El CSBK preámbulo (`dmr_csbk.c`) solo imprime el contenido `Preamble CSBK - ...` dentro de un `if(IrrecoverableErrors == 0 && CRCCorrect == 1)` — o sea que **el hecho mismo de que se haya impreso ya confirma CRC válido** en el CSBK.

**Es la primera vez en todo el proyecto que se confirma, con respaldo en el código fuente (no por inferencia visual del log), un burst con CRC genuinamente válido.**

**Salvedad honesta, para no sobre-interpretar**: el bloque de datos `R12U` (tipo `0x07`, *unconfirmed*) **no tiene CRC propio** — el código de `dsd-fme` fija `CRCCorrect=1` incondicionalmente para bloques 1/2-rate no confirmados ("no CRC available on these", comentario textual del código), precisamente porque el formato "unconfirmed" de DMR no lleva checksum por bloque. Es decir: **el header sí está criptográficamente confirmado; el contenido del bloque de payload en sí depende únicamente de la corrección FEC (que no marcó error), sin un CRC que lo confirme de forma independiente.**

**¿Es esto LRRP?** Se revisó `dmr_block.c`: el parser de LRRP de `dsd-fme` exige un campo interno "tipo MNIS" específico (`0x11`=LRRP, `0x01`=LOCN) dentro del mensaje reensamblado para activar el parser LRRP — campo que **no está presente/no matcheó** en este mensaje (por eso `-L` no generó archivo). Es decir: **es un dato real, con header CRC válido, pero según la propia lógica de reconocimiento de `dsd-fme` no es un mensaje LRRP** — es más probablemente un mensaje corto de otro tipo (ARS/registro, status, o similar). Dato interesante: el payload del bloque contiene los bytes `31 30 30 30`, que en ASCII es literalmente **"1000"** — coincide exactamente con el Radio ID de origen (`Source: 1000`), sugiriendo que el mensaje incluye el ID del radio como texto.

**Target 64250 (`0xFAFA` en hex, visible en el payload del header)** — no se pudo confirmar contra ninguna fuente qué aplicación fija corresponde a este ID; queda como pregunta abierta, no se inventa una respuesta.

### Hallazgo secundario — señalización de "Group Emergency Call" confirmada extensamente
Se encontró, en gran cantidad de bursts `TLC`/`VLC` a lo largo de gran parte del archivo (primera aparición ~línea 172 ≈ t≈13s, última ~línea 2551 ≈ t≈199s — **posiciones aproximadas por proporción de línea sobre el total, no timestamps exactos**, ver limitación metodológica abajo), el patrón:
```
SLOT 1 TGT=1 SRC=1001 FLCO=0x00 FID=0x00 SVC=0x80 Group Emergency Call
```
El bit de servicio `SVC=0x80` marcado explícitamente como "Group Emergency Call" confirma, a nivel de protocolo, que el modo emergencia del HT (`SRC=1001` — nota: **ID distinto** al `Source: 1000` del burst de datos, y también distinto del HT "Matías"/1005 de la sesión 5; no se pudo determinar todavía si son radios distintos o roles distintos del mismo sistema) efectivamente marca los Link Control de las llamadas de voz subsiguientes con el flag de emergencia. Voz asociada con errores FEC bajos (`err=[0][0]`, `[0][1]` en varias tramas AMBE) — señal de buena calidad en esos tramos.

También se encontró un segundo CSBK, distinto del de datos: `Preamble CSBK - Group CSBK - Source: 1001 - Target: 1` — parte del establecimiento de la llamada de grupo de emergencia, no un mensaje de datos independiente.

### Comparación entre bursts (tarea pedida) — limitada a un solo burst de datos real
**No fue posible comparar estructura entre múltiples bursts de datos independientes**, como se esperaba con los 2-3 ciclos del usuario: de las 8 repeticiones de CSBK + 1 header + 1 bloque encontrados, **todo corresponde a una única transmisión de datos** (mismo Source/Target, mismo contenido de payload en el único bloque). Los "Group Emergency Call" repetidos (147 TLC) son señalización de la MISMA llamada de voz sostenida repitiéndose en cada superframe, no transmisiones de datos independientes. **Hipótesis, no confirmada**: el reporte de datos (Individual Data → 64250) puede dispararse una sola vez por activación de rastreo GPS (no por cada ciclo de emergencia), lo que explicaría por qué 2-3 ciclos de emergencia produjeron solo 1 transmisión de datos.

### Limitación metodológica encontrada: no hay forma de obtener timestamp exacto dentro del archivo
`dsd-fme` en modo lectura de archivo no imprime un offset de tiempo/muestra dentro del WAV — solo la hora de reloj de pared del momento en que se procesa (irrelevante, ya que el procesamiento corre ~47x más rápido que tiempo real). Las posiciones "t≈Xs" reportadas en esta sesión son una **estimación aproximada por proporción de número de línea sobre el total de líneas del log**, no una medición real — un burst rodeado de muchos IDLE/errores ocupa más líneas por segundo real que uno en zona limpia, así que esta proporción puede estar sesgada. **Pendiente para una próxima sesión**: si hace falta correlación temporal precisa, cortar el archivo en tramos de duración conocida (p. ej. cada 20s) y correr cada uno por separado, usando el índice de tramo como referencia de tiempo real.

### Próximos pasos (Sesión 7)
1. **Adoptar la corrección de frecuencia como paso estándar del pipeline offline** — medir el offset real (vía FFT rápida sobre cualquier ventana con actividad, comparado con una ventana de silencio para descartar el spike de DC) antes de descartar una grabación por "sin sync".
2. Investigar qué es el Target `64250` (`0xFAFA`) y si el mensaje de datos (Source 1000) es ARS, un status, u otra cosa — no es LRRP según la lógica propia de `dsd-fme`, pero el contenido ASCII "1000" sugiere que vale la pena decodearlo a mano si aparece de nuevo.
3. Probar activar el rastreo GPS "desde cero" (apagado → encendido) en una nueva grabación corta, en vez de encender/apagar el modo emergencia repetidamente — para probar la hipótesis de que el burst de datos se dispara por activación de tracking, no por el ciclo de emergencia en sí.
4. Aclarar (con el usuario, no asumido) si los Radio ID 1000 y 1001 corresponden a radios/roles distintos, para poder interpretar mejor de dónde viene cada tipo de burst.
5. Sigue pendiente el script puente hacia el backend (`POST /api/telemetry`) — todavía no hay una decodificación LRRP confirmada (el burst de esta sesión es un dato real pero no reconocido como LRRP por la herramienta), así que sigue correctamente para una sesión posterior.

---

## Sesión 8 — IDs de radio confirmados, protocolo de timestamps en vivo, y primer resultado negativo limpio sobre Source=1001

### Corrección importante heredada de la sesión 7
El usuario confirmó los IDs reales del sistema (dato de configuración, no inferido):
- **1001 = "Matías"** — el HT de prueba con el que se viene activando rastreo GPS/emergencia.
- **1000 = "Base Guardia"** — equipo fijo del cuartel, **no** el handy de prueba.
- **1 = "BBVV_Merlo"** — probablemente el Target ID de grupo/sistema de la propia repetidora.

Esto **debilita (sin descartar)** la hipótesis de cierre de la sesión 7 ("el burst de datos se dispara al activar rastreo GPS"): el burst con CRC válido de la sesión 7 vino de la Base Guardia (equipo fijo), no de Matías — es decir, no hay evidencia de que haya estado relacionado con la activación de GPS del handy de prueba. Podría ser tráfico independiente y periódico de la Base Guardia (ARS/registro), sin relación con el rastreo.

### Primer intento de esta sesión: grabación de 8 min con protocolo antiguo (contar todo al final)
```bash
timeout 480 rtl_sdr -f 159635000 -s 240000 -g 30 iq_159635_20260807_184555.cu8
```
- Grabación de 362,6s (se cortó manualmente, terminación limpia sin colgarse).
- Offset de frecuencia medido por FFT: **-6504 Hz, igual que la sesión 7** (mismo dongle, condiciones similares).
- El usuario hizo más pasos de los planeados (Matías → Base Guardia → HT nuevo "1002" → HT Hytera, sin telemetría) pero **contó todo al final**, sin timestamps intermedios.
- **Resultado: ningún contenido de voz decodificado en todo el archivo** (0 AMBE, 0 TLC/VLC), solo un cluster breve (~t=180-200s) de intentos de CSBK **todos con CRC fallido**, con bytes sugestivos pero no confirmables (posible Source 1000 u 1001 — un solo bit de diferencia entre `0x03E8` y `0x03E9` en un burst con CRC fallido no permite distinguirlos).
- **Se descartó la hipótesis de desconexión USB** (propuesta por el usuario) con evidencia directa: el log de `rtl_sdr` no mostró ningún mensaje de desconexión (a diferencia de los de la sesión 5, que sí eran explícitos), y las estadísticas del IQ crudo (std, min/max) fueron idénticas antes/durante/después del cluster de actividad — el dongle grabó de forma continua y sana. La ausencia de contenido es silencio real de RF, no una falla de hardware.
- **Decisión**: repetir con un protocolo distinto — el usuario avisa por chat, en tiempo real, antes de cada acción — para poder anclar cada burst a un timestamp real en vez de depender de bisección a ciegas (limitación ya señalada al cierre de la sesión 7).

### Segunda grabación: protocolo de timestamps en tiempo real (exitoso)
```bash
timeout 480 rtl_sdr -f 159635000 -s 240000 -g 30 iq_159635_20260807_190002.cu8
```
Inicio real: **19:00:02**. El usuario avisó cada acción en el momento; línea de tiempo reconstruida (offset ≈ hora real − 19:00:02):

| Hora real | t≈ (s) | Acción |
|---|---|---|
| 19:00:02 | 0 | Inicio de grabación. Ya encendidos (idle): Base Guardia, Matías (1001), HT 1002/BVM1002 |
| 19:01:06 | 64 | PTT HT 1002, ~5s |
| 19:02:14 | 132 | PTT Base Guardia ~5s + toque accidental de Matías al final |
| 19:02:35 | 153 | Modo emergencia activado en Matías (1001) |
| 19:03:00 | 178 | Emergencia se desactiva sola (~25s de duración — consistente con el corte automático a los ~30s ya notado en la sesión 7) |
| 19:03:09 | 187 | Emergencia reactivada en Matías |
| 19:04:25 | 263 | Se apagan todos los HT Motorola (solo queda Base Guardia); se prepara HT Hytera |
| 19:05:19 | 317 | PTT Hytera, ~5s |
| 19:05:28 | 326 | Fin de grabación (corte manual) |

**El protocolo funcionó bien** — permitió correlacionar bursts encontrados con acciones específicas sin depender de bisección a ciegas, resolviendo la limitación señalada al cierre de la sesión 7.

### Offset de frecuencia: confirmado que varía sesión a sesión, y que la FFT simple no siempre alcanza
El offset de esta grabación **NO fue -6504 Hz** (el de la sesión 7) — la comparación FFT actividad-vs-silencio esta vez dio el pico en **+0 Hz en ambas ventanas** (ambiguo, no permitió leer el offset directamente a simple vista). Se resolvió de forma empírica: se corrió `dsd-fme` sobre la ventana del PTT de 1002 con varios valores de `freq_corr` (0, ±6504, ±6700) y se comparó el conteo de syncs reales — **-6700 Hz** dio 141 syncs (vs. 0 en el resto de los valores probados), confirmado y afinado con un barrido fino (`-6600` a `-6800`) que confirmó `-6700` como óptimo (141 syncs, mayoría Color Code=01).

**Lección de método, agregada al estándar de la sesión 7**: la comparación FFT actividad-vs-silencio no siempre distingue el offset a simple vista (esta vez ambos picos cayeron en +0 Hz). El método robusto es: barrer varios valores de `freq_corr` sobre una ventana con actividad conocida y quedarse con el que maximice syncs reales de `dsd-fme` — la FFT sirve como primera aproximación rápida, pero el criterio final es empírico (conteo de syncs), no visual.

### Resultados — filtrado específico por Source=1001

Con `freq_corr=-6700` aplicado al archivo completo (326s): **627 syncs reales**, con contenido de voz esta vez sí presente.

**SRC=1001 (Matías) — 33 líneas, todas `TGT=1 SRC=1001 FLCO=0x00 FID=0x00 SVC=0x80 Group Emergency Call`** (Color Code=01, válido) + 1 variante con `TGT=33` (probable bit corrupto de `TGT=1`, un solo bit de diferencia). **Coincide con la activación de modo emergencia reportada en tiempo real** (t≈153-178s y t≈187-263s).

**SRC=1002 — 13 líneas, `TGT=1 SRC=1002 FLCO=0x00 FID=0x00 SVC=0x00 Group Call`** (Color Code=01, válido, sin flag de emergencia — coincide con el PTT normal reportado a t≈64s).

**SRC=1000 (Base Guardia) — NO apareció ninguna vez** en este archivo, ni en voz ni en datos, pese al PTT reportado a t≈132s. No se pudo confirmar por qué (¿señal más débil en ese momento puntual? ¿PTT muy corto para dar tiempo a sync?) — no se investigó más a fondo, queda abierto.

**Ningún burst tipo CSBK "Individual Data" / "DATA Header" (como el de la Base Guardia en la sesión 7) apareció en todo este archivo.** Los únicos bursts no-voz encontrados (`R_1U` ×3, `PI` ×2, `MBCC` ×1) tienen **Color Code=00 u 11 (no 01)** — el mismo patrón de "ruido/decodificación marginal" ya identificado en sesiones anteriores, no tráfico real del sistema.

### 🎯 Resultado principal de la sesión — negativo, limpio y documentado (tarea 7 de la consigna)

**Ningún burst de datos con Source=1001 apareció, pese a dos activaciones de modo emergencia con voz limpiamente confirmada (Color Code=01, SVC=0x80) durante las mismas.** Esto es evidencia nueva (no solo hipótesis) a favor de: **activar rastreo GPS + modo emergencia en el handy no dispara, por sí solo, una transmisión de datos/LRRP independiente** — refuerza la corrección de esta sesión (el burst de la sesión 7 vino de la Base Guardia, no de un handy con GPS activado) en vez de debilitarla más.

**No fue posible la comparación byte a byte pedida** (tarea 6) entre un burst de Source=1001 y el de Source=1000 de la sesión 7, porque no apareció ningún burst de datos de ninguna fuente en esta grabación.

### Resultado adicional — prueba cruzada con HT Hytera: sin decodificación
El PTT del Hytera (t≈317s, cola del archivo) no produjo ningún contenido de voz reconocible — solo unos pocos bursts marginales `PI`/`MBCC` con Color Code=00 (ruido) cerca de la transición de apagar/prender radios, y después vuelta a `IDLE` con Color Code=01 normal. **No es una confirmación de que el Hytera no transmita nada decodificable** — con una sola prueba de 5s no alcanza para concluir eso; solo se puede decir que esta prueba puntual no arrojó nada.

### Próximos pasos (Sesión 8)
1. **Adoptar el protocolo de timestamps en tiempo real como estándar** para todas las sesiones futuras — funcionó y resuelve la limitación de correlación temporal de la sesión 7.
2. **Adoptar el barrido empírico de `freq_corr`** (no solo la FFT visual) como paso estándar al inicio de cada sesión — la FFT puede no distinguir el offset a simple vista.
3. Investigar por qué el PTT de Base Guardia (t≈132s) no dejó ningún rastro esta vez, a diferencia de la sesión 7 — variable de señal puntual, o el PTT fue demasiado corto.
4. Repetir el intento de captar un burst de datos de Source=1001, ahora que confirmamos que la activación de emergencia por sí sola no lo dispara — considerar otros triggers (ej. un "check-in" manual de posición si el HT lo permite, o simplemente más ventanas de tiempo de espera sin ninguna acción, para ver si la Base Guardia repite su burst periódicamente y así confirmar/descartar que sea un evento automático recurrente).
5. Sigue pendiente el script puente hacia el backend — sigue sin haber ninguna decodificación LRRP confirmada de ningún radio todavía.
