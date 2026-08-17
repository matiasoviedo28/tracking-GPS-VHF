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

---

## Sesión 9 — Grabación pasiva de 15 minutos: confirmado que el burst de la Base Guardia es ARS, no LRRP

### Contexto
Sesión puramente pasiva — sin ninguna acción coordinada del usuario sobre los handys. Objetivo: usar una ventana larga (15 min) para buscar repeticiones del burst de datos de la Base Guardia (Source=1000, Target 64250, visto en la sesión 7) y estimar si es periódico.

### Grabación
```bash
timeout 900 rtl_sdr -f 159635000 -s 240000 -g 30 iq_159635_20260807_191404.cu8
```
Inicio 19:14:04. Terminación limpia (898,94s, sin colgarse).

**Incidente operativo**: la máquina del usuario se tildó y debió apagarse de forma forzada durante el análisis posterior a la grabación. Se verificó la integridad completa antes de continuar: el archivo de grabación ya estaba cerrado y escrito en disco antes del crash (mismo tamaño, mismo histograma exacto tras el reinicio), el dongle se detectó normalmente, y no quedó ningún proceso colgado. **Sin pérdida de datos.**

### Offset de frecuencia: sigue derivando, ahora -7000 Hz
Perfil de potencia (referencial, no de búsqueda) sobre el archivo completo: solo una ventana con actividad clara, t≈270-274s. Barrido empírico de `freq_corr` sobre esa ventana (método estándar desde la sesión 8): valores probados de -8000 a +8000 Hz, óptimo en **-7000 Hz** (140 syncs vs. 0 en la mayoría de los otros valores). **Tercera sesión consecutiva con un offset distinto** (-6504 → -6700 → -7000, sesiones 7-8-9), tendencia monótona creciente en magnitud — posible deriva térmica del cristal del dongle a lo largo del día. Se reconfirma como estándar: medir con barrido empírico en cada sesión, nunca asumir el valor anterior.

### Escaneo lineal completo (898,9s) — resultado: 142 syncs, todo concentrado en una única ráfaga
| Tipo | Cantidad |
|---|---|
| IDLE (CC=01) | 113 |
| CSBK (CC=01) | 16 |
| DATA (Header, CC=01) | 2 |
| R12U (bloque, CC=01) | 1 |

**Localización temporal** (cortando en tramos de 60s, método estándar de esta sesión por no haber timestamps en vivo): de los 15 tramos, **solo el tramo t=240-300s tiene contenido** (140 de los 142 syncs); el resto del archivo (t=0-240s y t=300-898s, prácticamente los 15 minutos completos) está en silencio real, salvo 2 blips de ruido sin contenido (un `CACH/Burst FEC ERR` aislado en t≈300-360s y otro en t≈480-540s, sin Color Code ni payload — ruido, no señal real). **Posición precisa de la ráfaga: t≈270-274s ≈ 19:18:34-19:18:38 hora real.**

### 🎯 HALLAZGO PRINCIPAL — confirmado por el propio parser de dsd-fme: el burst de la Base Guardia es ARS, no LRRP

La ráfaga completa (16 preámbulos CSBK + 2 headers de datos + 1 bloque) se reensambló correctamente esta vez, y el parser MNIS de `dsd-fme` etiquetó el mensaje explícitamente:
```
Motorola Network Interface Service Header (MNIS);
SRC(MNIS): 00001000;
DST(MNIS): 00064250; MNIS ARS;   ???: 3BB7
UTF8 Text: _-- -1000__SR--
Slot 1 - Multi Block PDU Message
 1F100201333BB70009F02004
 3130303000005352CB9F0000
```

**Verificado contra el código fuente** (`dmr_block.c:1294`): `mnis_type == 0x33` imprime literalmente `"MNIS ARS"` — un código de tipo MNIS **distinto y mutuamente excluyente** de `LRRP` (`0x11`) y `LOCN` (`0x01`, línea 1292-1293). Es decir: **esto no es una hipótesis — el propio parser de dsd-fme identificó el mensaje como ARS (Automatic Registration Service)**, no como reporte de posición. Esto cierra definitivamente la pregunta abierta al final de la sesión 7 ("¿es ARS/registro, o podría ser LRRP?").

**Comparación byte a byte con el burst de la sesión 7** (mismo Source=1000, Target=64250):
- Header SAP09: `02 90 00 FA FA 00 03 E8 82 00 25 2B` — **idéntico byte a byte** al de la sesión 7.
- Bloque de datos: `20 04 31 30 30 30 00 00 [XX][XX][XX][XX]` — **primeros 8 bytes idénticos** (incluye el ASCII "1000"), solo cambian los últimos 4 (consistentes con ruido/CRC variable en un bloque sin checksum real, ver salvedad de la sesión 7).
- **Nuevo esta sesión**: un segundo header de datos, `Slot 1 Data Header - Extended - SAP 01 [Moto NET] - MFID 10 [Moto]`, no visto en la sesión 7 — es la cabecera específica de MNIS (motivo por el cual esta vez el mensaje se reensambló y clasificó completo, mientras que en la sesión 7 solo se había visto la mitad de la secuencia).

**Conclusión**: el mensaje de la Base Guardia es un **registro/keepalive ARS estándar de MOTOTRBO**, reproducible y con contenido casi estático — no es, y estructuralmente no puede ser, el canal por el que viaja LRRP/GPS. La búsqueda de LRRP debe seguir enfocada en otro tipo de mensaje (probablemente `mnis_type=0x11`), no en este.

### Periodicidad — no se repitió dentro de esta sesión, pero se puede acotar el intervalo combinando todas las sesiones

Dentro de los 898s de esta grabación, el burst apareció **una sola vez** — no se pudo medir un intervalo directamente. Combinando con las sesiones anteriores (todas en la misma repetidora/sistema):

| Sesión | Ventana observada | Duración | Ocurrencias |
|---|---|---|---|
| 7 | 18:19:59-18:23:55 | 236s | 1 |
| 8 (1er intento) | 17:45:55-17:51:57 (*) | 362s | 0 |
| 8 (2do intento) | 19:00:02-19:05:28 | 326s | 0 |
| 9 | 19:14:04-19:29:04 | 899s | 1 |

*(el horario exacto de la sesión 8/1er intento está tomado del nombre de archivo `iq_159635_20260807_184555.cu8`; ver sesión 8 para el detalle.)*

**Cero ocurrencias en dos ventanas separadas de ~5-6 minutos cada una (sesión 8)** sugiere que el intervalo, si es periódico, es **mayor a ese orden de magnitud** (no se ve un registro cada pocos minutos). La sesión 9 (una sola ocurrencia en 15 minutos, con silencio total antes y después) es consistente con un intervalo bastante más largo — **posiblemente del orden de 20-60+ minutos, o bien un evento no estrictamente periódico** (podría dispararse por otra condición: reinicio del equipo, cambio de canal, roaming, o un intervalo largo de ARS configurado en el CPS de la Base Guardia). **No se puede fijar un valor exacto con los datos disponibles** — se necesitarían 2+ ocurrencias dentro de una misma grabación continua para medir el intervalo real, cosa que no ocurrió en ninguna sesión hasta ahora.

### Otros Source ID con tráfico de datos — ninguno encontrado
En los 16 CSBK + 2 DATA + 1 R12U de esta sesión, **el único Source presente es 1000 (Base Guardia)**. Ningún otro equipo del sistema (Matías/1001, 1002, u otros no identificados) generó tráfico de datos durante esta ventana pasiva de 15 minutos — consistente con que nadie estaba operando activamente los handys.

### Próximos pasos (Sesión 9)
1. **La pregunta original del proyecto (captar LRRP) sigue abierta** — el hallazgo de esta sesión resuelve una rama secundaria (qué es el burst de la Base Guardia) pero no avanza directamente el objetivo principal.
2. Dado que ARS queda confirmado como un mensaje distinto de LRRP, y que activar emergencia/rastreo en Matías (sesión 8) no disparó ningún burst de datos, **reconsiderar si existe algún trigger real para que un handy individual transmita LRRP** — podría no ser algo que el propio HT decida enviar espontáneamente, sino algo que se dispare por una solicitud activa desde el lado NAI-D/aplicación (volviendo, en cierto sentido, a la línea de investigación de la sesión 1, ahora con más contexto de protocolo).
3. Si se quiere seguir la pista de periodicidad del ARS, haría falta una grabación continua mucho más larga (order de 1+ hora) para capturar 2+ repeticiones y medir el intervalo real — no es prioritario para el objetivo de LRRP, pero quedaría como dato de caracterización del sistema.
4. Sigue pendiente el script puente hacia el backend — sigue sin haber ninguna decodificación LRRP confirmada de ningún radio todavía.

---

## Sesión 10 — Prueba real extendida en el cuartel: fallo del monitoreo autónomo y cero eventos capturados

### Contexto y objetivo
Objetivo: dejar corriendo el circuito completo (SDR en vivo → `live_presence_bridge.py` → backend → frontend) de forma autónoma por 45-60 minutos en el cuartel (buena señal, condición ya validada), capturando tráfico real y espontáneo sin necesitar coordinación de timing continua con el usuario. **Resultado: no se logró — cero eventos detectados en toda la sesión (~4 horas reales transcurridas), pese a al menos una transmisión real confirmada por el usuario.** Esta sesión documenta las fallas encontradas, no un éxito — es información igual de valiosa para no repetir los mismos errores.

### Fallo 1 — crash real de USB del dongle, no relacionado a software propio
Tras cortar el bridge (`live_presence_bridge.py`, que usa el manejo interno de RTL de `dsd-fme` en modo vivo) e intentar varias grabaciones de calibración con `rtl_sdr` en sucesión rápida, se encontraron **2 grabaciones consecutivas de 0 bytes** pese a que el log de `rtl_sdr` mostraba arranque normal (`Tuned to...`, `Tuner gain set to...`, `Allocating 15 zero-copy buffers`) y, en un caso, corrió la duración completa esperada sin escribir nada. Diagnóstico: `rtl_test -p` **generó un core dump real** (`timeout: la orden monitorizada ha volcado un 'core'`), confirmando una falla genuina a nivel USB/librtlsdr — no un bug de scripting. **Se resolvió con un replug físico del dongle** (desconectar/reconectar), tras lo cual `rtl_test` volvió a reportar pérdida de muestras normal (~1 ppm) sin crashear. **Hallazgo operativo nuevo**: alternar rápidamente entre el modo RTL interno de `dsd-fme` (usa oversampling propio a 1.008 MS/s) y `rtl_sdr` plano (240 kS/s) varias veces seguidas puede dejar el dongle en un estado inestable — conviene dar más margen entre ambos modos, o replugear preventivamente al alternar.

### Fallo 2 — el monitoreo autónomo programado no se ejecutó
Se arrancó el bridge a las 20:39:23 con `ppm=-49` (última calibración conocida, de la sesión de QA end-to-end) y se programó un chequeo periódico vía `ScheduleWakeup` a los 8 minutos. **El siguiente chequeo real del estado del bridge no ocurrió hasta las 22:50** — más de 2 horas después, sin ningún monitoreo ni recalibración intermedia. No se pudo determinar con certeza si el wakeup programado falló en dispararse o si hubo un salto de tiempo real no visible entre turnos de la conversación. **En todo ese lapso, el bridge nunca logró sync con Color Code=01** (el aviso de recalibración a los 75s se disparó una sola vez, como está diseñado, y no se repitió pese a seguir sin sync por horas). Esto es un fallo operativo real de esta sesión, no una limitación de la investigación de RF en sí — queda documentado para revisar el mecanismo de monitoreo periódico en sesiones futuras (considerar chequeos activos por bloques dentro del mismo turno en vez de depender exclusivamente de un wakeup entre turnos, para ventanas cortas de 45-60 min).

### Fallo 3 — no se logró recalibrar pese a múltiples intentos, incluso con una transmisión real confirmada
Tras detectar la falta de sync de 2+ horas, se hicieron **8 grabaciones de calibración dedicadas en total** (antes y después del replug del dongle), barriendo `freq_corr` en rangos de hasta ±12.000 Hz — muy por encima de cualquier deriva vista en sesiones anteriores (máximo histórico: -7800 Hz, sesión de QA end-to-end). **Ninguna produjo un solo sync real.** El usuario confirmó explícitamente una transmisión real con la Base a las 23:12:10 — pero esa transmisión ocurrió durante una corrida del bridge (no de una grabación `rtl_sdr` dedicada), y **tampoco generó ningún POST** pese a ser una transmisión real confirmada. Los intentos posteriores de grabar una ventana `rtl_sdr` dedicada para volver a capturar una transmisión y recalibrar **no lograron coincidir en tiempo con ninguna transmisión real** (ventanas de 45-60s y hasta un pedido de PTT inmediato de 25s, todos con 0 syncs en barridos amplios). **No se pudo determinar si el problema es un offset de frecuencia fuera del rango barrido, un problema de timing de coordinación, u otra causa** — queda abierto, no se inventa una explicación.

### Estado final
- `GET /api/equipos` → `[]` (cero equipos, cero eventos, en toda la sesión).
- `docker compose down -v` + `up -d backend frontend database` funcionó sin problemas — la infraestructura en sí sigue sana (ya validado en sesiones de QA anteriores).
- `live_presence_bridge.py` sigue con `FREQ_CORR_PPM = "-49"` en el código — **este valor queda sin confirmar/desconfirmar por esta sesión**, no se debe asumir que sigue siendo válido ni que está descartado.
- **Esta NO fue la sesión en la que el circuito completo (SDR real → panel frontend) quedó confirmado funcionando de punta a punta con datos reales.** Sigue sin lograrse — lo más cerca que se llegó hasta ahora fue la validación offline de la lógica de parsing del script (sesión de QA end-to-end, 61 detecciones correctas sobre un archivo grabado) y las pruebas con POSTs manuales/simulados (sesión de prueba visual del panel), pero nunca una detección en vivo real confirmada de punta a punta.

### Próximos pasos (Sesión 10)
1. **Revisar el mecanismo de monitoreo autónomo** antes de repetir una ventana larga sin supervisión — el `ScheduleWakeup` de 8 minutos no se ejecutó como se esperaba; considerar chequeos activos dentro del mismo turno (bloques de espera acotados) para ventanas de duración conocida y relativamente corta (script un poco más simple es preferible a una dependencia entre turnos que falló sin poder diagnosticarse).
2. **Repetir la calibración con coordinación más ajustada todavía** — quizás grabar una ventana bien larga (3-5 min) con el usuario avisando CADA transmisión en tiempo real (protocolo de la sesión 8, que sí funcionó bien), en vez de intentar ventanas cortas de 25-60s a la espera de una única transmisión puntual.
3. Investigar si vale la pena ampliar el rango de barrido de `freq_corr` más allá de ±12.000 Hz en la próxima sesión, dado que no se pudo confirmar que -49 ppm (ni ningún valor cercano) siga sirviendo.
4. Documentar como lección permanente: alternar rápido entre modo RTL interno de `dsd-fme` y `rtl_sdr` plano puede crashear el dongle (ver Fallo 1) — dejar margen o replugear entre ambos modos.
5. Sigue pendiente el objetivo original de LRRP, y ahora también sigue pendiente confirmar el circuito de presencia funcionando de punta a punta con datos reales (no solo simulados/offline).

---

## Sesión 11 — Retomar con coordinación activa: causa raíz encontrada (antena desconectada), y un hallazgo nuevo más profundo (el modo SDR en vivo de dsd-fme no decodifica pese a calibración offline confirmada)

### Contexto
Sesión explícitamente coordinada paso a paso (a diferencia de la sesión 10, que falló por depender de un mecanismo de espera automático poco confiable) — el usuario en el cuartel, con atención completa, confirmando cada acción con timestamp en tiempo real (protocolo de la sesión 8).

### Arranque limpio
`docker compose down -v` + `up -d backend frontend database`: los 3 contenedores arriba sin problemas, `GET /health` → 200, `GET /api/equipos` → `[]`. Dongle verificado sano con `rtl_test -p` (pérdida de muestras normal, sin core dump — a diferencia del fallo de hardware real de la sesión 10).

### Primer intento de recalibración — 0 actividad en 179s, causa raíz: sin antena
Primera grabación de 3 minutos, con dos eventos reales confirmados por el usuario en tiempo real (un PTT ~23:20:23 y una activación/desactivación de modo emergencia ~23:21:17). **Resultado: ninguna ventana del archivo completo (179s) mostró actividad de potencia por encima del piso de ruido — ni una sola**, y el barrido de `freq_corr` en un rango de ±12.000 Hz dio 0 syncs en absoluto.

Antes de asumir un problema de calibración, se preguntó directamente al usuario por el estado físico de la antena — **la antena del SDR estaba desconectada**. El usuario está en el cuartel (ubicación con buena señal ya validada), pero sin antena conectada equivale, en la práctica, a estar sin ninguna recepción real (mismo patrón ya documentado en la sesión 5 y en la sesión de QA end-to-end con antena mala en otra ubicación). **Causa raíz identificada y resuelta con una simple pregunta antes de seguir grabando a ciegas** — evita repetir el patrón de la sesión 10 de gastar múltiples grabaciones sin verificar lo físico primero.

### Segunda recalibración, con antena conectada — éxito claro
Nueva grabación de 3 minutos, misma antena reconectada. Perfil de potencia: picos claros de hasta **+11 dB** sobre el piso de ruido, coincidiendo con precisión con los timestamps reportados en tiempo real (PTT de ~20s → t≈15-44s del archivo; activación de emergencia → t≈63-79s). Barrido empírico de `freq_corr` sobre esa ventana: óptimo en **-7500 Hz (-47 ppm)**, con 1345 syncs reales (1184 con Color Code=01) — la validación offline más sólida de toda la investigación hasta ahora. Cuarta sesión consecutiva con un offset distinto (-6504 → -6700 → -7000 → -7800 → **-7500**, sesiones 7-8-9-QA-11) — la deriva no es estrictamente monótona (bajó en esta sesión respecto a la anterior), reforzando que hay que remedir siempre, sin asumir tendencia.

`live_presence_bridge.py` actualizado con `FREQ_CORR_PPM = "-47"`.

### 🔎 HALLAZGO PRINCIPAL DE LA SESIÓN — el modo SDR en vivo de dsd-fme no decodifica, pese a calibración offline fuertemente confirmada

Con -47 ppm recién validado (1345 syncs offline), se arrancó `live_presence_bridge.py` en modo vivo y se pidieron **3 transmisiones reales de prueba sucesivas** (un PTT de 10s a través del bridge completo, y dos pruebas más de `dsd-fme` en modo vivo directo sin el bridge — un PTT corto de 3-4s y uno largo de 15-20s). **Ninguna de las tres generó un solo sync con Color Code=01** — ni siquiera vía `dsd-fme` corriendo solo, sin la capa de parsing de Python de por medio, descartando de plano cualquier bug en el script puente.

**Esto descarta las hipótesis previas** (calibración vieja, duración de PTT insuficiente) y aísla el problema a una diferencia real entre dos pipelines:
- **Pipeline offline (validado una y otra vez desde la sesión 7)**: `rtl_sdr` graba a 240 kS/s crudo → `iq_to_wav.py` demodula y decima a 48 kHz → `dsd-fme -i archivo.wav`.
- **Pipeline en vivo (`dsd-fme -i rtl:...`)**: maneja el RTL-SDR internamente con un esquema completamente distinto — muestrea a **1.008 MS/s**, oversamplea **84x**, y entrega audio demodulado a **12 kHz** (confirmado en el propio log de arranque de `dsd-fme`: "Oversampling input by: 84x... Sampling at 1008000 S/s... Output at 12000 Hz").

**No se pudo determinar la causa exacta** de por qué el mismo offset de frecuencia (convertido correctamente de Hz a ppm, verificado con la fórmula estándar `ppm = offset_hz / freq_hz × 1e6`) funciona perfecto en un pipeline y no sincroniza en absoluto en el otro — queda como pregunta abierta, no se inventa una explicación. Hipótesis razonables sin confirmar: el ancho de banda interno (`BW 12`) o el esquema de oversampling del modo RTL en vivo de `dsd-fme` podrían tener una tolerancia distinta a la esperada frente a offsets de frecuencia, o podría haber una diferencia de implementación entre cómo esa build de `dsd-fme` aplica la corrección PPM en su frontend RTL propio vs. cómo se aplica la rotación de fase en `iq_to_wav.py`.

**Implicación práctica**: el diseño actual de `live_presence_bridge.py` (que depende del modo SDR en vivo de `dsd-fme`) puede no ser viable tal como está, independientemente de qué tan bien calibrado esté el ppm. La alternativa más prometedora, sugerida pero **no implementada en esta sesión** (a pedido explícito del usuario, que prefirió documentar y parar): rearmar el bridge para que use `rtl_sdr` grabando en bloques cortos continuos + `dsd-fme` en modo archivo sobre cada bloque (el pipeline que sí está validado repetidamente), en vez de `dsd-fme -i rtl:...`.

### Estado final
- `GET /api/equipos` → `[]` — **cero eventos reales posteados en esta sesión tampoco**, pese a lograr la calibración offline más sólida hasta ahora.
- **Esta tampoco fue la sesión en la que el circuito completo (SDR real → panel frontend) quedó confirmado funcionando de punta a punta con datos reales.** El objetivo de la sesión no se logró, pero se avanzó significativamente: se descartó definitivamente la hipótesis de "falta de calibración" como causa del problema del circuito en vivo, y se aisló un problema arquitectónico concreto y accionable.
- Dongle, contenedores y antena quedaron todos en buen estado al cierre — no hay ningún problema de hardware pendiente, solo el hallazgo de arquitectura de software documentado arriba.

### Próximos pasos (Sesión 11)
1. **Prioridad alta**: rearmar `live_presence_bridge.py` para usar `rtl_sdr` (grabación continua por bloques cortos, ej. 10-15s) + `dsd-fme` en modo archivo sobre cada bloque en loop, en vez de `dsd-fme -i rtl:...` — es un cambio de arquitectura, no un ajuste de parámetros.
2. Antes de esa reescritura, sería valioso confirmar si el problema es específico de esta build de `dsd-fme` (`AW 2026-34-g69d3115`) probando (si es fácil) alguna otra build/fork, aunque no es bloqueante para la solución del punto 1.
3. **Lección de proceso reforzada**: preguntar por el estado físico (antena conectada, ubicación) ANTES de grabar repetidamente ante resultados en cero — la sesión 10 gastó 8 grabaciones sin preguntar esto; esta sesión lo resolvió con una sola pregunta a tiempo.
4. Sigue pendiente el objetivo original de LRRP y ahora, más concretamente que nunca, confirmar el circuito de presencia en vivo de punta a punta — con una arquitectura de bridge distinta a la actual.

---

## Sesión 12 — Reescritura del bridge a pipeline offline por bloques: 🎯 PRIMERA CONFIRMACIÓN REAL DE PUNTA A PUNTA DEL PROYECTO

### Contexto
Directamente motivada por el hallazgo de la sesión 11: el modo SDR en vivo de `dsd-fme` (`-i rtl:...`) usa un pipeline interno de muestreo (1.008 MS/s, oversampling 84x) que nunca logró decodificar nada, pese a calibración offline sólidamente confirmada. Objetivo: reescribir `sdr-decoder/live_presence_bridge.py` para que use exclusivamente el pipeline offline validado sesión tras sesión desde la sesión 7 (`rtl_sdr` → `iq_to_wav.py` → `dsd-fme` en modo archivo), a costa de latencia por bloque en vez de detección instantánea.

Antena confirmada con el usuario antes de arrancar: la misma que dio los 1345 syncs de la sesión 11, sin cambios.

### Reescritura
Nuevo diseño de `live_presence_bridge.py` (loop indefinido):
1. `rtl_sdr -f 159635000 -s 240000 -g 30 -n <BLOCK_SECONDS*240000> bloque.cu8` — grabación de bloque corto. **Cambio de diseño importante**: se usa `-n <muestras>` (conteo fijo de muestras) en vez de `timeout N` + señal — `rtl_sdr` termina solo al completar la cuenta, sin depender de manejo de señales. Esto evita de raíz la clase de bug ya vista varias veces en sesiones anteriores (`rtl_sdr`/`rtl_test` quedando colgados o con core dump al recibir SIGTERM tras alternar modos).
2. `python3 iq_to_wav.py bloque.cu8 bloque.wav 240000 -7500` — conversión reusando el script ya validado (no reimplementado, mismo archivo de `~/sdr_dmr_test/`), con el offset de -7500 Hz confirmado en la sesión 11.
3. `dsd-fme -fs -i bloque.wav -s 48000 -Z -o null` — modo archivo, el pipeline probado.
4. Parseo de la salida completa del bloque con la misma lógica de estado secuencial (Color Code=01 como gate) y las mismas regex ya validadas contra logs reales.
5. POST a `/api/presence` por cada evento nuevo (con el mismo rate-limit de 5s por radio_id).
6. Borrado de los archivos temporales del bloque, pase lo que pase (con `finally`).
7. Bloques que no encuentran nada se loguean como "sin actividad" — explícitamente normal, no error — y el loop sigue solo.

`BLOCK_SECONDS = 12`. Validado con un test unitario de la función de parsing contra líneas reales conocidas (incluyendo un caso negativo: Color Code≠01 debe ignorarse) antes de probar en vivo.

### 🎯 HALLAZGO PRINCIPAL — primera confirmación real de punta a punta de todo el proyecto

**El primerísimo bloque de prueba mecánica (12s, sin ninguna transmisión coordinada, solo tráfico ambiente) detectó un evento real: Base Guardia con ARS, posteado con éxito (`200 OK`) y confirmado en `GET /api/equipos`.** Esto ya es, en sí mismo, la primera vez que el circuito completo (SDR real → parsing → `POST /api/presence` → backend → base de datos → `GET /api/equipos`) funciona de punta a punta con datos 100% reales, sin simular nada.

A continuación se corrió el loop completo con una prueba coordinada: el usuario avisó un PTT en tiempo real (~23:53:12). **El bloque inmediatamente siguiente lo detectó y posteó correctamente** (`[Matías] evento=voz -> POST 200`), confirmado en el momento, no al final. Un tercer bloque agarró otro ARS espontáneo de Base Guardia. **3-4 iteraciones corridas, cero errores del pipeline, dos equipos distintos detectados y confirmados con datos reales** (uno coordinado, uno espontáneo).

**Esta sí es la primera vez que se confirma el circuito de presencia funcionando de punta a punta con datos reales — no solo la lógica de parsing validada offline (QA end-to-end), no solo POSTs manuales/simulados (prueba visual del panel), sino el pipeline SDR real completo, sin intervención manual en el POST.**

### Latencia real medida — honesta, no instantánea
Cada bloque tarda **~14.3-14.6s de punta a punta** (12s de grabación, inevitables porque es captura en tiempo real, + ~2.3-2.6s de conversión y decodificación). Esto define la latencia real del sistema: en el mejor caso (transmisión al inicio de un bloque que recién arranca), la detección puede llegar en poco más que ese bloque; en el peor caso (transmisión que arranca justo después de que un bloque ya empezó a grabar), puede tardar **hasta ~2 ciclos de bloque, del orden de 29 segundos**, antes de aparecer en el backend. **No es tiempo real instantáneo — es "casi tiempo real" con una demora de 15-30 segundos**, inherente al diseño por bloques. Esto es un trade-off consciente y documentado, no una limitación oculta.

### Ventana pasiva de 5 minutos — sin errores, y un patrón de periodicidad de ARS nuevo y no explicado
A pedido del usuario, se corrió el loop 5 minutos sin ninguna transmisión manual, para ver si otros handys encendidos (1002, 1003, 1006, sin nadie operándolos) generaban algo. **22 bloques procesados, cero errores del pipeline.**

- **Ningún otro radio ID además de 1000 apareció** — consistente con la explicación dada al usuario en el momento: un DMR no transmite nada por estar solo encendido, sólo con PTT activo o un evento automático (como el ARS de Base Guardia). Confirmado también con una captura pasiva de control de 5 segundos (antes de la ventana de 5 minutos, por un malentendido de la consigna) que dio cero sync y cero eventos — silencio total sin transmisión, tal como se esperaba.
- **Base Guardia mandó ARS 3 veces en los 5 minutos** (bloques 10, 14 y 18), con un intervalo muy consistente de **~58 segundos** entre cada una. **Esto contradice, o al menos no encaja, con la estimación de la sesión 9** (que, con datos mucho más limitados, especulaba un intervalo de 20-60+ minutos, o un evento no periódico). **No se investiga ni se inventa la causa de la discrepancia acá** — puede ser que el intervalo real siempre fue de este orden y las sesiones anteriores simplemente no tuvieron ventanas continuas lo bastante centradas para verlo repetirse, o puede haber cambiado algo de la configuración de la Base Guardia entre sesiones. Queda como dato nuevo a confirmar en una sesión futura con una ventana pasiva más larga.
- 2 bloques adicionales (12 y 22) mostraron sync real con Color Code=01 pero sin ningún evento reconocido por las regex actuales — contenido real del sistema (posiblemente IDLE u otro tipo de CSBK) que no es voz/emergencia/ARS. No es un error, solo tráfico de un tipo que el script no clasifica todavía.
- **Consecuencia observada del umbral de 5 minutos**: Matías, que había sido "visto" a las 23:53:12, pasó a `online: false` en el `GET /api/equipos` final (más de 5 minutos después) — primera confirmación en un escenario real (no simulado) de que el campo `online` calculado por el backend se comporta como se diseñó.

#### Precisión del intervalo de ~58s — regular en la medida en que se puede medir, pero con un límite de resolución real

A pedido del usuario, se recalculó el intervalo entre los 3 eventos ARS usando los tiempos exactos de fin de procesamiento de cada bloque (no la cuenta aproximada de "4 bloques" de la sección anterior): **57.9s entre bloque 10→14, y 57.5s entre bloque 14→18** — solo 0.4s de diferencia entre ambos intervalos.

**Salvedad honesta sobre qué tanto dice esto**: el método por bloques tiene un límite de resolución real de aproximadamente **el ancho de un bloque (~12-14s)** para ubicar el instante exacto de cada burst — solo se sabe en qué bloque cayó cada ARS, no el segundo exacto dentro de esos 12s de grabación. Con bloques de ~14.4s corridos pegados uno a otro, un intervalo real de 58.0s "clavado" produce exactamente el patrón observado (avanzar 4 bloques cada vez, 4×14.4≈57.6s) — pero esa misma observación **también sería compatible con un intervalo real con unos segundos de jitter** (por ejemplo, entre 52s y 64s), no necesariamente un timer fijo del equipo. Con solo 2 intervalos medidos y esa resolución, **no se puede confirmar si es un timer exacto o algo aproximado** — los datos son consistentes con ~58s regular, pero no alcanzan para descartar variación real de varios segundos entre repeticiones.

**Para responder esto con precisión** haría falta una grabación continua (sin los huecos de procesamiento entre bloques) de varios minutos, ubicando el instante exacto de cada burst directamente en el archivo crudo — mismo método de barrido/bisección de sesiones anteriores, esta vez apuntado a medir el intervalo real entre 3+ repeticiones consecutivas de ARS. **No se hizo en esta sesión** — queda como pendiente concreto si se quiere zanjar la pregunta.

### Limitación menor encontrada, no bloqueante
Al cortar el loop con `kill -TERM` sobre el proceso Python, en más de una ocasión un `rtl_sdr` hijo (ya lanzado por la iteración en curso) quedó huérfano un momento, sin terminar junto con el padre — se resolvió matándolo aparte (o, en algunos casos, terminó solo gracias al límite de `-n` muestras). No causó pérdida de datos ni archivos residuales (la limpieza de temporales sigue funcionando), pero es un detalle de robustez a mejorar: idealmente manejar el grupo de procesos para que un corte del script mate también a cualquier subproceso en curso.

### Estado final
- `GET /api/equipos` → 2 equipos: **Base Guardia** (`ultimo_evento: ars`, `online: true`) y **Matías** (`ultimo_evento: voz`, `online: false` — ya venció el umbral).
- `live_presence_bridge.py` reescrito por completo, con la calibración de la sesión 11 (`FREQ_CORR_HZ = -7500`) incorporada directamente (ya no como parámetro PPM de un modo en vivo que no servía).
- Contenedores y dongle en buen estado al cierre.

### Próximos pasos (Sesión 12)
1. Investigar el patrón de ~58s de ARS de Base Guardia con una ventana pasiva más larga (15+ min) para confirmar si es realmente periódico a ese intervalo, y reconciliar con la estimación mucho más larga de la sesión 9.
2. Arreglar la limitación menor de procesos huérfanos al cortar el loop (manejo de grupo de procesos o señal explícita al subproceso activo).
3. Considerar si vale la pena reducir `BLOCK_SECONDS` (ej. a 8-10s) para bajar la latencia, evaluando si sigue siendo suficiente para que `dsd-fme` logre sync de forma confiable con bloques más cortos.
4. Clasificar el tipo de burst real que aparece con Color Code=01 pero sin matchear ninguna regex actual (bloques 12 y 22) — podría ser información adicional del sistema, no solo ruido.
5. Sigue pendiente el objetivo original de LRRP — el circuito de presencia ya está confirmado de punta a punta, LRRP/posición sigue siendo la pieza faltante.

---

## Sesión 13 — Escuchar directo el uplink del handy (153.335 MHz): sin evidencia de que la repetidora "se coma" el LRRP

### Contexto e hipótesis
Hasta esta sesión, **todas** las grabaciones fueron en 159.635 MHz (downlink, retransmisión de la repetidora) — nunca se había escuchado directo el uplink (153.335 MHz, handy→repetidora). Hipótesis a probar: ¿la repetidora podría estar recibiendo un LRRP del handy pero no retransmitiéndolo? Si al escuchar directo al handy aparecía algo nuevo que nunca se vio del lado del downlink, sería evidencia a favor.

Sesión nocturna, con posible tráfico real de terceros (QAP) en el sistema — por eso se hizo **solo con modo rastreo GPS activado, sin PTT de voz ni modo emergencia**, para no meter ruido innecesario a esa hora. Ganancia moderada (22, redondea a ~22.9 dB) en vez de 30, por la limitación de clipping ya documentada en sesiones tempranas por proximidad al handy — sin clipping esta vez (histograma sin valores en los extremos 0-3/252-255).

Limitación técnica conocida de antemano (sesión 5): en uplink, `dmr_ms.c` fuerza el timeslot a 0 siempre — no se puede confirmar con certeza si un burst viene de TS1 o TS2 como sí se puede en downlink. No fue bloqueante para esta prueba (el objetivo era detectar SI aparecía algo nuevo, no clasificarlo perfecto).

### Grabación
```bash
timeout 300 rtl_sdr -f 153335000 -s 240000 -g 22 s13_uplink_20260814_003035.cu8
```
Inicio 00:30:35, terminación limpia a los 300s (299,3s reales). Sin clipping. El usuario activó el rastreo GPS del handy en algún momento de la ventana, pero **no llegó a confirmar el timestamp exacto** — limitación de esta sesión, no se pudo correlacionar con precisión.

### Recalibración — offset consistente con sesiones recientes, meseta amplia
Perfil de potencia: dos ráfagas muy fuertes y breves, **t≈8-9s y t≈76-77s, +23 a +25 dB sobre el piso de ruido** — mucho más fuertes que cualquier pico visto en downlink (ahí el máximo histórico rondaba +11dB), consistente con estar mucho más cerca de la fuente real (el propio handy transmisor, no su retransmisión). Barrido de `freq_corr`: meseta amplia y estable entre -8000 y -7400 Hz (40 syncs / 32 Color Code=01 en todos los valores de esa meseta) — se usó **-7500 Hz**, igual que la última calibración de downlink de la sesión 11/12 (dentro del margen de derivas ya vistas).

### Catálogo completo de bursts — escaneo lineal del archivo completo (299,3s), no solo la ventana de actividad
El escaneo del archivo completo dio **exactamente el mismo resultado** que la ventana de 100s analizada primero — confirma que **todo el contenido real está en los primeros 100 segundos; los ~200s restantes están en silencio total**, sin ninguna otra actividad.

| Tipo | Cantidad |
|---|---|
| CSBK (CC=01) | 20 |
| DATA (Header, CC=01) | 4 |
| R12U (bloque, CC=01) | 2 |
| CSBK (CC=XX, marginal) | 1 |

**El único Source ID presente en todo el archivo, sin excepción, es 1000 (Base Guardia).**

### Resultado — es el mismo ARS de Base Guardia ya conocido, byte por byte, ahora visto también en uplink

Las dos ráfagas capturadas (t≈8-9s y t≈76-77s) son, cada una, la secuencia completa de **"Individual Data" CSBK preamble (×9-10 repeticiones) + Data Header SAP09 + Data Header Extended SAP01 [Moto NET] + bloque R12U con MNIS ARS"** — **estructuralmente idéntica**, byte a byte en los campos fijos, al burst de ARS de Base Guardia ya documentado extensamente en las sesiones 7, 9 y 12 (mismo Target `64250`/`0xFAFA`, mismo SAP09, mismo "MNIS ARS" tipo `0x33`, mismo ASCII "1000" en el bloque de datos). Solo cambia el campo `???` de 2 bytes dentro del mensaje MNIS ARS (`AE30` en la primera ráfaga, `AE31` en la segunda) — parece un contador de secuencia que incrementa en 1 entre transmisiones, dato nuevo no visto antes con esta claridad.

**Hallazgo nuevo confirmado (aunque no es el buscado)**: Base Guardia transmite **su propio burst directo en el uplink** (153.335 MHz) — hasta ahora solo se había visto este mensaje del lado del downlink (retransmisión de la repetidora). Esto confirma que Base Guardia opera como una unidad más del sistema DMR (como un handy), transmitiendo y siendo repetida, no como una conexión de backend directa a la repetidora.

**No apareció ningún burst, tipo de mensaje, o Source ID que no se hubiera visto ya en downlink.** Nada nuevo, nada sin clasificar, ningún CSBK o header de datos con una estructura distinta a lo ya conocido.

### 🎯 Conclusión sobre la hipótesis — sin evidencia a favor, pero tampoco descartada del todo

**Escuchar directo el uplink no mostró ningún burst nuevo o no reconocido que sugiriera que la repetidora esté descartando un LRRP real del handy.** Lo único capturado fue tráfico ya conocido (el ARS de Base Guardia), ahora confirmado también visible directamente en uplink.

**Importante, para no sobre-concluir**: esto **no descarta** la hipótesis de raíz, porque **tampoco se capturó ningún burst atribuible al propio handy del usuario** (ningún Source ID distinto de 1000 en todo el archivo), pese a que se activó el rastreo GPS durante la ventana. Esto es exactamente consistente con el patrón ya establecido en las sesiones 8 y 9: **activar el modo rastreo por sí solo no dispara ninguna transmisión detectable** — ni en downlink, ni ahora tampoco en uplink. No se pudo probar la hipótesis "la repetidora se come el LRRP" porque no se logró que el handy transmitiera nada en absoluto durante esta ventana — el experimento necesitaría, en una próxima sesión, algún evento que si dispare una transmisión real del handy (una emergencia, un PTT, o lo que sea que dispare el reporte de posición) mientras se escucha en uplink, para recién ahí poder comparar de igual a igual contra lo que se ve (o no se ve) en downlink en el mismo momento.

### Dato adicional — intervalo entre las dos ráfagas de Base Guardia: 68s, distinto de los ~58s de la sesión 12
t≈8-9s y t≈76-77s da un intervalo de **~68 segundos** entre las dos transmisiones de Base Guardia — distinto de los ~57.5-57.9s medidos en la sesión 12 (aunque esa medición ya tenía una salvedad de resolución de ±12-14s documentada). **No se investiga ni se inventa la causa de la diferencia acá** — otro dato a favor de que el intervalo de ARS de Base Guardia amerita una medición dedicada y más larga en una sesión futura, como ya se había dejado pendiente.

### Próximos pasos (Sesión 13)
1. **Repetir el experimento del uplink, pero esta vez con un evento que sí dispare tráfico real del handy** (emergencia, PTT, o lo que confirme una transmisión) — coordinado con timestamp en tiempo real, para poder comparar de igual a igual contra qué se ve (o no) en downlink en el mismo instante exacto. Esta sesión no pudo probar la hipótesis original por falta de esa transmisión.
2. Aprovechar cualquier futura grabación de uplink más larga para seguir midiendo el intervalo de ARS de Base Guardia (ya van dos mediciones distintas: ~58s y ~68s) — confirmar con una grabación continua dedicada.
3. Sigue pendiente el objetivo original de LRRP — esta sesión no lo acercó ni lo alejó, solo confirmó que el ARS de Base Guardia es visible en ambos lados del enlace, sin aportar contenido nuevo.

---

## Sesión 14 — Marcar el instante exacto del fix GPS (no la activación del modo): sin transmisión detectable, y periodicidad del ARS de Base Guardia confirmada con mucha más precisión

### Contexto e hipótesis
Continuación directa de la sesión 13. Nueva hipótesis, más específica: quizás el LRRP se transmite en el instante en que el handy **pasa de "sin fix" a "fix obtenido"**, no al activar el modo rastreo en sí (que fue lo que se marcó, sin éxito, en sesiones anteriores). El usuario ya estaba en una ventana con buena señal GPS confirmada.

### Ajuste de protocolo en el momento (vale la pena documentarlo)
El primer ciclo apagar/prender el modo rastreo (~00:50:50-00:51:00) **no fue válido para la hipótesis**: el usuario reportó que el HT sigue mostrando coordenadas en ambos modos — es decir, mantiene el último fix en pantalla independientemente del estado del modo, sin una pérdida real de señal GPS. Se corrigió el protocolo en tiempo real: en vez de tocar el modo rastreo, se le pidió al usuario **alejarse físicamente de la ventana** (para perder la vista al cielo y forzar una pérdida real de señal GPS) y volver, marcando el instante en que la pantalla muestra coordenadas *nuevas*, no las guardadas. Esto dio dos timestamps más:
- Alejamiento + reactivación del modo: **~00:54:50-00:54:55**
- **Fix obtenido (el timestamp clave): ~00:55:44-00:55:54**

### Grabación
```bash
timeout 420 rtl_sdr -f 153335000 -s 240000 -g 22 s14_uplink_20260814_004931.cu8
```
Inicio 00:49:31, terminación limpia a los 420s (418,9s reales). Sin clipping (min=122/max=133, std=0,50 — normal).

### Recalibración — mismo offset de sesiones recientes
Meseta amplia y estable entre -8000 y -7000 Hz (20 syncs en todos los valores de esa meseta, sobre una ventana con la primera transmisión de Base Guardia). Se usó **-7500 Hz**, igual que las sesiones 11-13.

### Escaneo lineal completo — el único tráfico real, otra vez, es Base Guardia
El escaneo del archivo completo (418,9s) dio: 41 CSBK, 8 DATA (header), 4 R12U — consistente con **4 repeticiones completas** de la misma secuencia de ARS de Base Guardia (Source 1000, Target 64250) ya documentada en sesiones 7, 9, 12 y 13. **Ningún otro Source ID apareció en todo el archivo.**

### 📊 Periodicidad del ARS de Base Guardia — medida con mucha más precisión esta vez: 68s exactos, tres veces seguidas

Las 4 ocurrencias de Base Guardia cayeron en t≈16-17s, t≈84-85s, t≈152-153s y t≈220-221s — es decir, **tres intervalos consecutivos, los tres de exactamente 68 segundos**. A diferencia de la medición de la sesión 12 (que tenía una salvedad de resolución de ±12-14s por ser un método basado en bloques discretos), esta vez la medición viene de detección directa por potencia sobre una grabación continua sin huecos — mucho más confiable. **Conclusión actualizada**: el intervalo de ARS de Base Guardia parece ser un timer razonablemente regular de ~68 segundos (coincide con el valor ya visto, también de 68s, entre las 2 ocurrencias de la sesión 13 — ahora confirmado con 3 repeticiones consecutivas en la misma sesión, no solo 2 sesiones distintas). Esto reemplaza la estimación de ~58s de la sesión 12 como la medición más confiable disponible.

### 🎯 Resultado sobre la hipótesis del fix GPS — negativo, y ya van dos sesiones consecutivas con el mismo patrón

Se revisó con lupa, con un barrido de `freq_corr` exhaustivo (±9.000 Hz) y contexto amplio, cada uno de los 3 timestamps reportados por el usuario:

| Timestamp reportado | Ventana revisada (t desde inicio) | Resultado |
|---|---|---|
| Ciclo 1 apagar/prender (~00:50:50-00:51:00, luego invalidado) | t≈79-89s (dentro de t=50-120s analizado) | 20 syncs encontrados, pero **confirmado que son la transmisión periódica de Base Guardia** (coincide con el pico de potencia de t=84-85s), no algo del handy del usuario |
| Alejamiento + reactivación (~00:54:50-00:54:55) | t≈319-324s (dentro de t=290-340s analizado) | **Cero sync en todo el rango de `freq_corr` barrido** |
| **Fix obtenido (~00:55:44-00:55:54)** | t≈373-383s (ventana t=340-418s, con contexto amplio) | **Cero sync — ni siquiera un intento marginal registrado por dsd-fme** (ninguna línea de "no sync", el log está completamente vacío de intentos de decodificación en esa ventana) |

**No apareció absolutamente nada atribuible al handy del usuario en ningún momento reportado — ni en el toggle, ni en el instante preciso del fix GPS.** Combinado con el resultado igual de negativo de la sesión 13 (modo rastreo activado, sin timestamp de fix), esto ya es un patrón replicado dos veces: **ni activar el modo de rastreo, ni el instante exacto de obtener un fix GPS real, disparan una transmisión detectable del handy — ni en downlink ni en uplink.**

### Interpretación — sin sobre-concluir, pero replanteando la búsqueda
Esto **no prueba que el handy nunca transmita LRRP** — solo que, en las condiciones probadas hasta ahora (dos sesiones, cuatro eventos de rastreo/fix distintos, cero transmisiones detectadas), **ni activar el modo ni obtener un fix nuevo son, por sí solos, el disparador**. Las hipótesis que quedan en pie, sin confirmar:
1. El reporte de posición podría requerir un intervalo periódico propio mucho más largo (como el ARS, pero con su propio timer independiente, posiblemente de varios minutos u horas) — no ligado al evento de "fix obtenido" en sí.
2. Podría requerir una solicitud activa desde el lado de la aplicación/NAI-D (retomando la línea original de la sesión 1) — el handy no lo transmite espontáneamente, hay que "pedirlo".
3. Podría estar deshabilitado en el codeplug de este HT específico para el canal GPS-R2, o requerir una condición adicional no identificada (ej. combinarse con emergencia, no solo rastreo).

### Próximos pasos (Sesión 14)
1. **Dejar de asumir que "activar rastreo" o "conseguir fix" son el disparador** — ya se probó dos veces sin éxito. Enfocar la próxima sesión en probar **emergencia + rastreo simultáneo** en uplink (la sesión 8 solo lo probó en downlink, sin éxito en datos, pero nunca se probó en uplink directo) o en investigar si existe un botón/función de "enviar posición ahora" en el HT.
2. Si se consigue acceso al CPS del HT (no solo de la repetidora), revisar la configuración del canal GPS-R2 para ver si hay un intervalo de reporte configurado explícitamente — dato que resolvería la pregunta de raíz en vez de seguir probando a ciegas por RF.
3. La periodicidad de ~68s del ARS de Base Guardia ya está razonablemente bien confirmada (3 repeticiones consecutivas exactas) — no hace falta seguir dedicando sesiones enteras a remedirla, salvo que aparezca un valor distinto que amerite revisar.
4. Sigue pendiente el objetivo original de LRRP — con dos hipótesis de disparador ya descartadas (activación de modo, obtención de fix), la búsqueda debe reorientarse hacia una solicitud activa o un intervalo propio más largo.

---

## Investigación de código — ¿dsd-fme heredó capacidad de ARMAR/ENVIAR solicitudes LRRP de OK-DMRlib?

**Nota**: esto NO es una sesión de captura (sin SDR, sin transmisión real) — es una revisión de código y documentación existente, de solo lectura, motivada por la hipótesis de que el LRRP requiere una solicitud activa desde una aplicación de red (Location Server) y no se transmite espontáneamente. Repo revisado: `~/sdr_dmr_test/dsd-fme` (clon de `lwvmobile/dsd-fme`, build actual `69d3115`, confirmado idéntico al HEAD del repo vía `git log`).

### 1-2. Documentación de ejemplo y README — sin mención de funcionalidad de envío
`examples/Example_Usage.md` y `examples/Install_Notes.md` no mencionan LRRP, GPS, "location request" ni "polling" relacionado a esto — la única coincidencia de "poll" es sobre polling de VFO/frecuencia para trunking, sin relación. El README acredita a **OK-DMRlib** como fuente general de "código e ideas" (junto a otros ~10 proyectos), y lista "LRRP/GPS Mapping" como una funcionalidad **original** agregada por DSD-FME — no hay ninguna mención explícita de que la parte de ENVÍO/codificación de OK-DMRlib haya sido portada.

### 3. Confirmado: dsd-fme es RX-only, sin ninguna capacidad de transmisión
Búsqueda dirigida de términos de transmisión (`transmit`, `tx_mode`, `hackrf_start_tx`, `rtlsdr_set_tx`, etc.) en todo `src/` e `include/`: **cero resultados relevantes** — los únicos matches son comentarios sobre campos de plan de canales RF ("Transmit Offset") o texto describiendo la señal recibida ("most transmitter + scanner..."), nada de código real de TX. Las opciones de entrada (`-i`) solo contemplan dispositivos de **recepción** (rtl, hackrf en modo RX, audio, pulse, archivo) — no existe un equivalente de "-o" para salida de RF (el `-o` que usamos nosotros es de audio decodificado, no RF). **Confirmado explícitamente: dsd-fme asume siempre RX-only**, consistente con el hardware que usamos (RTL-SDR, que físicamente tampoco puede transmitir).

### Hallazgo no buscado, pero el más importante — dsd-fme SÍ reconoce (pasivamente) los tokens de Request/Response de LRRP, y nunca los vimos porque nunca capturamos ese tipo de mensaje

Búsqueda de `"location request"` en el código encontró la función `dmr_lrrp()` en `src/dmr_pdu.c` (línea 562) — un parser completo de tokens LRRP que reconoce, entre otros:
- **Requests**: `0x05` Immediate Location Request, `0x09` Triggered Location Start Request, `0x0F` Triggered Location Stop Request, `0x14` Protocol Version Request.
- **Responses**: `0x07` Immediate Location Response, `0x0B` Triggered Location Start Response, `0x0D` Triggered Location, `0x11` Triggered Location Stop Response, `0x15` Protocol Version Response.
- Si detecta lat/lon reales, imprime `Time`/`Lat`/`Lon`/`Radius`/`Altitude`/`Speed`/`Track` a consola y escribe al archivo `-L` — esto es, literalmente, el resultado final que venimos buscando en las 14 sesiones anteriores.
- Si NO hay lat/lon pero sí reconoce un token de Request o Response, imprime una línea distinta: **`"LRRP SRC: <id>; Request from TGT: <id>;"`** o **`"LRRP SRC: <id>; Response to TGT: <id>;"`** — un patrón de texto que **nunca buscamos en nuestro parsing** (nuestras regex en `live_presence_bridge.py` solo verifican `"Group Emergency Call"`, `"Group Call"` y `"MNIS ARS"`).

**Pero, importante para no sobre-interpretar**: `dmr_lrrp()` solo se invoca (confirmado en `src/dmr_block.c` línea ~1305) cuando el campo `mnis_type` del mensaje decodificado es específicamente **`0x11`** (LRRP puro). Existe también una función hermana, `dmr_locn()` (línea 883), para `mnis_type == 0x01` (LOCN, una codificación alternativa de lat/lon en formato grados-minutos-segundos). **En las 14 sesiones de captura hasta ahora, el único `mnis_type` visto fue `0x33` (ARS, de Base Guardia)** — un tipo completamente distinto que toma una rama de código diferente (solo intenta extraer texto ASCII, nunca llama a `dmr_lrrp()` ni a `dmr_locn()`). Es decir: **no es que hayamos pasado por alto esta salida en logs pasados — nunca capturamos el tipo de mensaje (`0x11` o `0x01`) que la dispararía.**

**No se encontró ninguna función de codificación/armado de LRRP** (`grep` de `encode`, `build_lrrp`, `construct.*location`, etc. — cero resultados) — confirma que, aunque dsd-fme reconoce pasivamente los tokens de Request/Response si aparecen en el aire, **no tiene forma de generar/enviar una solicitud propia**, consistente con ser RX-only.

### 4. Git log — sin evidencia de funcionalidad de envío agregada en el historial
El repo está actualizado (`git log` confirma que el build compilado, `69d3115`, es el HEAD actual). Los últimos ~20 commits relacionados a GPS/LRRP/location son todos del lado de **decodificación/parsing** (arreglos de "GPS LCW" en P25, ensamblado de datos DMR, registro de eventos GPS en el historial) — ninguno menciona agregar una función de solicitud/envío.

### 🎯 Conclusión — confirma la hipótesis de trabajo, y da una pista concreta y accionable para la próxima sesión de captura

1. **Confirmado sin ambigüedad**: dsd-fme no tiene, y nunca tuvo, capacidad de armar o enviar una solicitud de ubicación — haría falta una herramienta aparte (posiblemente basada en OK-DMRlib directamente, o escrita a medida) si se quisiera probar la hipótesis de "solicitud activa" generando el pedido nosotros mismos.
2. **Pista accionable inmediata, sin necesitar ninguna herramienta nueva**: agregar a las regex de parsing (tanto en futuras sesiones de captura manual con `-Z`, como en `live_presence_bridge.py`) la detección de `"MNIS LRRP"`, `"MNIS LOCN"`, y el patrón `"LRRP SRC:"` — si el sistema alguna vez transmite un mensaje con `mnis_type` `0x11` o `0x01` (en vez del `0x33` de ARS que venimos viendo siempre), **hoy no lo detectaríamos** porque no está en las regex actuales. Esto no garantiza encontrar el LRRP, pero cierra un punto ciego real y confirmado en la herramienta de detección actual.

### Próximos pasos
1. **Actualizar `live_presence_bridge.py`** para reconocer `"MNIS LRRP"`, `"MNIS LOCN"`, y `"LRRP SRC:"` como eventos nuevos (ej. `evento="lrrp"` o `evento="locn"`) — cambio de código pendiente, no hecho en esta investigación de solo lectura.
2. Revisar si alguna sesión de captura futura, con estas nuevas regex, finalmente muestra un `mnis_type` distinto de `0x33` — sería la primera confirmación de que el canal SÍ transporta LRRP/LOCN alguna vez, aunque sea raro.
3. Si se quiere probar activamente la hipótesis de "solicitud desde Location Server", haría falta una herramienta de envío separada (no dsd-fme) — evaluar si OK-DMRlib (Python, según lo ya investigado en sesiones de código de la sesión 1) sirve para esto, en una sesión aparte y con la autorización correspondiente antes de transmitir nada hacia el sistema real.

---

## Re-análisis retroactivo de logs guardados — confirmación definitiva (con una salvedad honesta): nunca se capturó LRRP/LOCN en ninguna sesión

**Nota**: tampoco es una sesión de captura — re-escaneo de solo lectura sobre logs de texto ya generados en sesiones anteriores, motivado directamente por el punto ciego de regex encontrado en la investigación de código previa.

### Alcance del re-análisis
Se encontraron **140 archivos `.log`/`.txt`** en `~/sdr_dmr_test/` (11.393 líneas en total), cubriendo las sesiones 5 a 15: todos los `dsdout_*.log` de análisis manual con `-Z` (sesiones 7, 8, 9, 13, 14 y sus barridos de `freq_corr`), logs de `rtl_sdr`/`rtl_test`, y logs de recalibración. No se encontró ningún `.log`/`.txt` adicional en `~/Escritorio/GIT/tracking-GPS-VHF/sdr-decoder/` ni en `~/lrrp_capture/`. Tampoco existe `~/lrrp.txt` (el archivo de salida LRRP por defecto de `dsd-fme`) — coherente con nunca haber decodificado un LRRP real, en ningún momento, con ninguna invocación.

### Búsqueda — cero coincidencias, verificado en tres capas
Se buscaron, case-insensitive, los 7 patrones exactos identificados en la investigación de código anterior: `"LRRP SRC:"`, `"MNIS LRRP"`, `"MNIS LOCN"`, `"Immediate Location Request"`, `"Triggered Location"`, `"Protocol Version Request"`, `"Protocol Version Response"`.

- **Búsqueda combinada** (los 7 patrones a la vez) sobre los 140 archivos: **0 coincidencias**.
- **Búsqueda individual** (cada patrón por separado, para descartar un problema con la regex combinada): **0 coincidencias en los 7 casos**, 0 archivos con matches en cualquiera de ellos.
- **Chequeo cruzado independiente**: se contaron todas las apariciones de `"MNIS [A-Z]*"` en los 140 archivos — el resultado fue **17 apariciones, todas `MNIS ARS`, ninguna de otro tipo**. Esto confirma, desde un ángulo distinto al de la búsqueda de patrones específicos, que ningún `mnis_type` distinto de `0x33` (ARS) fue jamás decodificado en ninguna de las sesiones analizadas.

### ⚠️ Salvedad honesta e importante — no es 100% del historial completo

Al inventariar dónde vivían los logs, se encontró que **las corridas del bridge en vivo (`live_presence_bridge.py`, sesión 12 en adelante) NO tienen el texto crudo de `dsd-fme` guardado en disco** — el script captura la salida de cada bloque en una variable de Python, la parsea en memoria, y la descarta; solo se imprimen/guardan los resúmenes (`"[bloque N] evento=... -> POST 200"` o `"sin actividad"`), nunca el log completo por bloque. Se verificó este comportamiento revisando 6 archivos de salida de corridas del bridge que sobrevivían en `/tmp` — en los 6, solo aparecen los resúmenes, nunca el texto de `dsd-fme` en sí.

**Esto significa que las corridas del bridge en vivo (que sí detectaron eventos reales — Matías voz, Base Guardia ARS repetidas veces) no se pueden re-auditar retroactivamente por este método.** No es evidencia de que ahí SÍ hubiera un LRRP no detectado — es, literalmente, una zona sin datos para volver a mirar. Queda como una limitación real del diseño actual del script, no como un hallazgo de LRRP oculto.

### 🎯 Conclusión

**Para todo lo que efectivamente quedó registrado en disco (140 archivos, 11.393 líneas, sesiones 5-15 de análisis manual con `-Z`): confirmado con tres verificaciones independientes que nunca se capturó un mensaje LRRP o LOCN real** — ni una Request, ni una Response, ni ningún token de esos protocolos. Todo el tráfico de datos jamás visto fue `MNIS ARS` de Base Guardia.

**Para las corridas del bridge en vivo (sesión 12 en adelante): la pregunta queda sin poder confirmarse ni descartarse retroactivamente**, por la limitación de diseño recién descubierta (no se guarda el texto crudo por bloque).

### Próximos pasos
1. **Corregir la limitación del bridge encontrada en este re-análisis**: modificar `live_presence_bridge.py` para que guarde (aunque sea en un log rotativo, no need de guardar todo para siempre) el texto crudo de `dsd-fme` por bloque — al menos hasta que se implementen las nuevas regex de LRRP/LOCN, para no repetir este mismo punto ciego hacia adelante.
2. Sigue pendiente de la investigación de código anterior: agregar las regex de `"MNIS LRRP"`, `"MNIS LOCN"`, y `"LRRP SRC:"` a `live_presence_bridge.py` y a cualquier análisis manual futuro con `-Z`.
3. Con esta confirmación, la pregunta original del proyecto ("¿el sistema transmite LRRP alguna vez?") sigue sin respuesta definitiva — solo se descartó que haya pasado desapercibido en el 100% del historial escaneable; las sesiones del bridge en vivo son la única zona ciega real que queda.

---

## Sesión 16 — Corregir el punto ciego del bridge (detección real de LRRP/GPS + logs crudos) y repetir captura coordinada

**Objetivo doble**: (1) cerrar de una vez el punto ciego identificado en el re-análisis anterior — el bridge en vivo nunca guardó el texto crudo de `dsd-fme` ni reconocía los tokens de LRRP/LOCN — y (2) repetir una captura coordinada con la herramienta ya corregida, por si esta vez aparece algo.

### Parte 1 — Cambios de código en `live_presence_bridge.py`

Antes de tocar el SDR, se modificó el script (diff mostrado y confirmado con el usuario antes de ejecutar nada):

1. **Nueva regex de detección** (línea 133): `LRRP_GPS_RE` reconoce `"LRRP SRC:"`, `"MNIS LRRP"`, `"MNIS LOCN"`, `"Immediate Location Request"` y `"Triggered Location"` — los mismos patrones identificados en la investigación de código de la sesión anterior, ahora sí incorporados a la herramienta que corre en vivo (antes solo se buscaban en análisis manuales puntuales).
2. **`parsear_bloque()` devuelve una 3ra variable** (`hallazgos_lrrp`, línea 170-226): se revisa **cada línea** de la salida de `dsd-fme` contra `LRRP_GPS_RE`, sin condicionarlo a que haya sync con Color Code=01 (deliberado — un hallazgo de LRRP real es demasiado importante como para arriesgarse a perderlo por el mismo gate que se usa para descartar ruido de voz/ARS).
3. **`guardar_log_crudo()`** (línea 280): nueva función que escribe el texto completo devuelto por `dsd-fme` de **cada bloque procesado** (no solo el resumen) a `sdr-decoder/logs_sesion16/bloque_NNNN_<timestamp>.log` — cierra exactamente el punto ciego de auditoría encontrado en el re-análisis retroactivo.
4. **`procesar_un_bloque()`** (línea 295 en adelante): si `hallazgos_lrrp` no está vacío, imprime un banner muy visible (`### 🚨 POSIBLE LRRP/GPS DETECTADO — BLOQUE {indice} 🚨`) y postea `evento="gps"` para cada hallazgo con `radio_id` conocido.
5. Se corrió un test unitario simulando texto con `"MNIS LRRP"` y `"LRRP SRC: 1001; ..."` — detectado y asociado correctamente al `radio_id="1001"` vía el tracking de `mnis_src_pendiente` ya existente.

**Antes de arrancar cualquier grabación**, y a pedido explícito del usuario, se actualizó también el contrato del backend para aceptar el nuevo evento:
- `backend/app/schemas.py`: `EventoPresencia = Literal["voz", "emergencia", "ars", "gps"]` (antes sin `"gps"`), con comentario aclarando que `"gps"` significa que se reconoció un *token* de protocolo LRRP/LOCN, no una posición real (la posición real seguiría yendo por `POST /api/telemetry`).
- `backend/app/models.py`: comentario de `ultimo_evento` actualizado.
- `docs/API.md`: documentado el nuevo valor de `evento`, con la aclaración explícita de que `"gps"` ≠ posición real.
- Backend reconstruido (`docker compose up -d --build backend` — el Dockerfile copia `app/` sin volume mount, así que un rebuild es obligatorio) y probado con un POST real de `evento="gps"` (`200 OK`, confirmado en `GET /api/equipos`), luego limpiado (`docker compose down -v` + `up -d` fresco) antes de la captura real.

### Parte 2 — Captura coordinada de 6 minutos

**Recalibración necesaria antes de arrancar**: el valor de la sesión 11 (-7500 Hz) ya no daba sync — 8 bloques (~95s) sin ningún Color Code=01 al arrancar el bridge corregido. Siguiendo la instrucción explícita de no asumir y recalibrar empíricamente:

- **Dos intentos de grabación de recalibración con cero actividad de RF** (ni siquiera ruido con forma de transmisión), pese a que el usuario confirmó haber hecho PTT en ambos. El patrón (std≈0.47, indistinguible del piso de ruido normal, pero cero actividad real) es **exactamente el mismo síntoma diagnosticado en la sesión 11**: antena mal conectada. Se le preguntó directamente al usuario por la conexión de la antena; confirmó **"estaba mal conectada"** tras reconectarla — mismo root cause, segunda vez.
- Un tercer intento con cero actividad resultó ser, simplemente, que el usuario todavía no había transmitido (no un problema nuevo).
- El **cuarto intento tuvo éxito**: actividad real detectada en t≈21-27s (pico de +7.14dB), confirmada por la forma de la señal y la elevación de std (0.57 vs 0.47 de piso).
- **Barrido empírico de `freq_corr`** sobre esa grabación real: barrido amplio (`-9000` a `+3000` Hz) encontró `-8000` Hz como mejor candidato (170 syncs); barrido fino (`-8500` a `-7600` Hz) encontró **`-7600` Hz como óptimo: 174 syncs, 165 con Color Code=01** — mejor que `-8000` (170/160), `-8200` (168/160), `-7800` (154/144) y claramente mejor que `-8500` (89/83). Se actualizó `FREQ_CORR_HZ` en el script (y el comentario/docstring de calibración) a este nuevo valor.

**Arranque del bridge corregido** (159.635 MHz, -7600 Hz, gain=30): tras el reinicio, **9 bloques (~4 minutos) sin ningún sync ambiente con Color Code=01** — más allá de la ventana de 60-90s pedida. En vez de asumir que la recalibración (hecha minutos antes, sobre una transmisión real, con un sweep riguroso) estaba mal, se planteó la hipótesis alternativa: **el bridge tiene un hueco ciego de ~14s de procesamiento por cada ciclo de ~26s** (12s grabando + ~14s decodificando, sin grabar) durante el cual no puede capturar nada — con el ARS de Base Guardia teniendo un período documentado de ~68s (sesión 14), es plausible que varias ocurrencias hayan caído justo en ese hueco, sin que eso implique mala calibración. Se consultó al usuario, que optó por hacer un PTT corto para confirmar de forma directa en vez de seguir esperando tráfico ambiente.

**Sync real confirmado de forma directa**: el PTT del usuario (~10s, confirmado en el momento) fue detectado en el **bloque 14**: `[Matías] evento=voz -> POST 200`. **Ningún string nuevo de LRRP/GPS matcheó en ese bloque** (no se disparó el banner). Recién en este punto se le avisó explícitamente al usuario que podía transmitir con confianza (aunque, en la práctica, el propio PTT de confirmación ya cumplió ese rol).

**Ventana de 6 minutos** corrida desde el sync real confirmado (02:21:42 a 02:27:42, no desde el arranque del proceso): pasiva de ahí en más, sin más coordinación activa pedida al usuario. Durante la ventana, el **bloque 38 detectó un ARS espontáneo de Base Guardia** (`evento=ars -> POST 200`) — confirmación adicional de que el sync seguía funcionando, y tampoco matcheó ningún string de LRRP/GPS.

### Segunda pasada — grep de los 5 strings nuevos sobre TODOS los logs crudos de la sesión

Con `guardar_log_crudo()` ya en funcionamiento, esta fue la **primera sesión con capacidad real de auditar el texto crudo de una corrida en vivo del bridge** (el punto ciego identificado en el re-análisis anterior). Se generaron **51 archivos** en `sdr-decoder/logs_sesion16/` (1.761 líneas en total, cubriendo ambas corridas del bridge de esta sesión — la fallida con -7500 Hz y la exitosa con -7600 Hz). Grep combinado de los 5 patrones (`LRRP SRC:`, `MNIS LRRP`, `MNIS LOCN`, `Immediate Location Request`, `Triggered Location`) sobre los 51 archivos: **0 coincidencias** (verificado también con `grep -q` y código de salida). Chequeo de sanidad: los logs sí contienen contenido real y no están vacíos — 378 líneas con `Sync:`/`Color Code`, y los 2 eventos reales (Matías voz, Base Guardia ARS) quedaron efectivamente grabados en el texto crudo correspondiente.

### `GET /api/equipos` final

```json
[
  {
    "radio_id": "1000", "alias": "Base Guardia",
    "ultimo_evento": "ars", "online": true,
    "ultima_posicion": null
  },
  {
    "radio_id": "1001", "alias": "Matías",
    "ultimo_evento": "voz", "online": false,
    "ultima_posicion": null
  }
]
```

Ningún equipo con `ultimo_evento: "gps"` — consistente con el grep de segunda pasada.

### 🎯 Conclusión — capacidad de detección real confirmada, resultado de esta sesión sigue siendo negativo (y hay que decirlo así, sin adornarlo)

**Lo que SÍ cambió y es el hito de esta sesión**: `live_presence_bridge.py` **ahora tiene capacidad real de detectar y clasificar un token de LRRP/LOCN si alguna vez aparece uno en el aire** — antes de esta sesión, ni siquiera lo hubiéramos reconocido en el bridge en vivo (el punto ciego confirmado en el re-análisis anterior). Esto se verificó en tres niveles: (a) test unitario con texto simulado, (b) la nueva capacidad de guardar el texto crudo por bloque, que por primera vez permite auditar retroactivamente una corrida en vivo, y (c) esta misma sesión, donde el grep de segunda pasada corrió exitosamente sobre datos reales recién capturados.

**Lo que NO cambió**: **no apareció ningún LRRP/LOCN real en esta sesión** — ni durante el PTT de voz coordinado, ni durante el ARS espontáneo de Base Guardia, ni en el resto de la ventana pasiva. El objetivo original del proyecto (capturar un LRRP real) sigue sin resolverse. Si en una futura sesión aparece un hallazgo real, **eso sí sería el hito principal del proyecto** — esta sesión solo garantiza que, si aparece, esta vez sí lo vamos a ver.

### Próximos pasos
1. El punto ciego de auditoría del bridge en vivo (identificado en el re-análisis anterior) está cerrado — pero solo hacia adelante; las corridas de sesiones 12-15 siguen sin poder auditarse retroactivamente.
2. Sigue abierta la pregunta de fondo: ¿qué dispara una transmisión LRRP/LOCN real en este sistema, si es que ocurre alguna vez? Ninguna sesión hasta ahora (activación de rastreo, fix GPS, emergencia, ni tráfico ambiente prolongado) lo disparó.
3. Considerar, en una futura sesión, una ventana pasiva mucho más larga (15-30+ min) ahora que el bridge sí guarda todo el texto crudo y detecta los tokens correctos — maximizaría las chances de capturar algo si el intervalo real de un eventual reporte propio es largo (hipótesis 1 de la sesión 14, todavía sin descartar).
4. ~~El hueco ciego de ~14s por ciclo del bridge (mientras decodifica, no graba) sigue existiendo~~ — **corregido más abajo**: esta cifra de ~14s nunca se midió con precisión y estaba mal. Ver la investigación de "pérdida de audio en el hueco entre bloques", que la mide con exactitud (~2.2-2.9s) a raíz de la bitácora de audio.

---

## Investigación — pérdida de audio en el hueco entre bloques del bridge (bitácora de audio)

**Nota**: no es una sesión de captura de LRRP — es un análisis de solo lectura sobre logs ya generados por la bitácora de audio (feature nueva, ver `docs/API.md`), motivado por un reporte concreto del usuario: hizo un PTT único y continuo contando "del 1 al 20"; en el clip guardado se escuchaba claramente del 1 al 15, pero el tramo final nunca apareció, ni en ese clip ni en ningún clip posterior.

### Corrección de un dato ya documentado: el hueco NO es de ~14s

La entrada anterior de esta misma sección (punto 4 de arriba) afirmaba un hueco de "~14s por ciclo" — ese número **nunca se midió**, era una estimación de sentido común (correspondiéndolo, incorrectamente, con el tiempo de "procesamiento" que imprime el script). Revisando el código: `duracion = time.monotonic() - t0` en `procesar_un_bloque()` mide desde el **inicio de la grabación**, no desde su fin — es decir, el "14.x s de procesamiento" que se imprime por bloque **ya incluye los 12s de grabación**. El tiempo de conversión + decodificación real es solo la diferencia.

### Medición exacta, con datos reales de una transmisión continua

Se coordinó un PTT en tiempo real (usuario contando del 1 al 20), avisándole el instante exacto en que arrancaba a grabarse un bloque nuevo. Con los timestamps de nombre de archivo de los bloques crudos guardados en `logs_sesion16/` (recordar: `BLOCK_SECONDS = 12` exactos, vía `rtl_sdr -n <muestras>`, así que el fin de grabación de cada bloque se puede calcular con precisión sin ambigüedad):

| Bloque | Inicio de grabación | Fin calculado (+12s) | Bloque siguiente arranca | Hueco real |
|---|---|---|---|---|
| 54 | 15:56:06.901381 | 15:56:18.901381 | 15:56:21.178937 | **2.278s** |
| 55 | 15:56:21.178937 | 15:56:33.178937 | 15:56:35.449481 | **2.271s** |
| 56 | 15:56:35.449481 | 15:56:47.449481 | 15:56:50.288209 | **2.839s** |
| 57 | 15:56:50.288209 | 15:57:02.288209 | 15:57:04.945025 | **2.657s** |

**El hueco real medido es de ~2.2 a ~2.9 segundos por ciclo** (no ~14s) — consistente en las 4 transiciones medidas, con el bloque 56→57 (el que efectivamente contiene el corte del conteo del usuario) en el extremo superior de ese rango (2.84s).

### El audio guardado confirma el corte exacto en el límite del bloque, no antes

El bloque 56 (`bloque_0056_20260816_155635_449481.log`) contiene voz real y activa (465 líneas `AMBE`, con hex variado y errores bajos — no un decoder trabado) **hasta la última línea del archivo, justo antes de "End of ... .wav"** — es decir, la persona seguía hablando en el instante exacto en que `rtl_sdr` cortó la grabación a los 12.000s (por diseño, `-n <muestras>` fijas). El clip resultante mide 9.3s reales de audio (medido con el módulo `wave`) sobre un bloque de 12s — el resto del bloque fue silencio antes de que arrancara a hablar.

El bloque 57 (`bloque_0057_20260816_155650_288209.log`), que arranca 2.839s después, **también empieza con voz activa desde su primera línea decodificada** (`VC1` inmediatamente después de "Audio In Device") — confirmando que la persona siguió hablando de forma continua durante todo el hueco de 2.839s. Este bloque solo capturó 1.92s reales de audio (87 líneas `AMBE`, el resto del bloque ya en silencio — coincide con que el usuario terminó de contar poco después).

### 🎯 Conclusión — hipótesis confirmada con evidencia directa, con una salvedad honesta sobre la magnitud exacta

**Confirmado, no asumido**: existe un hueco real de **~2.2 a ~2.9 segundos** entre el fin de grabación de un bloque y el inicio del siguiente, durante el cual `rtl_sdr` no está corriendo (el bridge está ocupado convirtiendo y decodificando el bloque anterior). Cualquier audio que caiga en esa ventana se pierde de forma **irrecuperable** — no es un bug de corte de archivo, es una ventana ciega real e inherente al diseño secuencial elegido en la Sesión 12 (grabar → convertir → decodificar → repetir, en vez de grabación continua).

**Salvedad honesta**: el usuario reportó perder aproximadamente el tramo "16 al 20" (~5 segundos a un ritmo de conteo natural). El hueco medido con precisión (2.839s en la transición relevante) explica una parte real y confirmada de esa pérdida, pero no necesariamente el 100% — el bloque 57 posterior al hueco solo decodificó 1.92s de sus 12s grabados (el resto quedó en silencio o en errores de decodificación no listados como voz), lo que sugiere que además del hueco entre bloques, hay pérdida adicional dentro del propio bloque por calidad marginal de señal/demodulación (mismo problema de recepción documentado en sesiones anteriores). **No se puede afirmar que el hueco por sí solo explique exactamente 5 segundos** — sí se puede afirmar, con evidencia directa, que el hueco es real, mide ~2.2-2.9s, y es una causa confirmada (aunque quizás no la única) de la pérdida reportada.

### Cómo se resolvería (propuesta, sin implementar)

El diseño actual es **secuencial**: grabar 12s → convertir → decodificar → repetir. El hueco es el tiempo de conversión+decodificación, durante el cual nadie está escuchando el aire. La solución de fondo es hacer la grabación **continua e independiente** del procesamiento:

- Correr `rtl_sdr` en un proceso de background **persistente**, escribiendo un stream continuo (o archivos consecutivos sin huecos, ej. con `rtl_sdr` en modo de captura continua a un pipe, o encadenando grabaciones de forma que la siguiente arranque antes de que termine de procesarse la anterior).
- Procesar cada bloque (conversión + `dsd-fme`) en un **hilo o proceso separado**, en paralelo con la grabación del bloque siguiente, en vez de bloquear el loop principal.
- Esto requiere manejar la sincronización entre "grabación en curso" y "bloques ya grabados pendientes de procesar" (una cola simple alcanzaría, dado el volumen bajo de bloques por minuto).

No se implementa en esta investigación (era de solo lectura/análisis) — queda como mejora concreta y ya justificada con datos reales para una futura sesión, si se decide que vale la pena la complejidad adicional frente al enfoque secuencial actual (que ya viene priorizando simplicidad sobre cobertura completa, ver Sesión 12).

---

## Hito — primera captura real sin coordinación: la containerización cumplió su objetivo

**Contexto**: hasta acá, cada transmisión analizada en este documento fue **coordinada en tiempo real** — el usuario avisaba "voy a transmitir ahora", alguien miraba la consola en el momento, y recién ahí se confirmaba sync/detección. Esta vez fue distinto a propósito: el usuario transmitió con un equipo nuevo (radio_id `1012`, una base genérica compatible DMR/GPS de un fabricante distinto a Motorola) sin coordinar nada con quien estuviera del lado del sistema — el mismo `sdr-decoder` corriendo solo en Docker desde la sesión de containerización, sin que nadie estuviera mirando la consola en ese momento.

### Lo que se encontró (investigación de solo lectura, ver más arriba en este mismo documento)

- El bloque real (`bloque_0060_20260817_002310_570212.log`, guardado por el propio contenedor sin intervención) contiene tráfico genuino: `SRC=1012`, `Group Call`, Color Code=01 real. Re-procesado con la función de parsing actual (`parsear_bloque`, sin ejecutar nada nuevo): 50 líneas de burst, clasificadas como `voz`.
- `GET /api/equipos` ya tenía el equipo persistido, con `ultimo_visto: "2026-08-17T00:23:24.876462Z"` — **coincide al milisegundo** con el timestamp del header del bloque crudo (`2026-08-17T00:23:24.875588`). Circuito completo (SDR → parsing → `POST /api/presence` → backend → `GET /api/equipos`) funcionando de punta a punta, sin ningún humano coordinando el momento exacto.

### 🎯 Por qué esto es el hito principal de toda la etapa de containerización

Todas las sesiones anteriores (12 en adelante) que probaron el bridge —incluida la propia sesión de containerización— dependieron de coordinación en tiempo real: alguien avisaba el PTT, alguien miraba la consola al instante, se confirmaba sync antes de seguir. Esta es la **primera vez que el sistema captura, clasifica y persiste correctamente una transmisión real sin que nadie supiera que iba a pasar ni estuviera mirando en el momento**. Es exactamente el objetivo declarado al pasar de "correr `live_presence_bridge.py` a mano en el host, con alguien pendiente" a "`docker compose up` levanta todo y queda corriendo solo, de forma desatendida" — y quedó confirmado con un caso real, no con una prueba dirigida.

### Sobre el GPS/LRRP: mismo patrón, ahora con un fabricante distinto — refuerza la hipótesis, no abre una duda nueva

El equipo `1012` es, según indicó el usuario, una base que **sí incluye GPS** — y sin embargo transmitió voz normal, sin un solo token de LRRP/LOCN (búsqueda de los 5 strings conocidos sobre el bloque completo y sobre los 208 archivos de toda la corrida: cero coincidencias, igual que con todos los equipos Motorola DGP8550 vistos hasta ahora).

Esto es un dato a favor de la hipótesis de fondo, no en contra: hasta ahora, la ausencia de LRRP se podía explicar (sin poder descartar del todo) como algo específico de los DGP8550 analizados — configuración de codeplug, limitación del modelo, etc. Que un **equipo de otro fabricante, que el usuario confirma que tiene GPS habilitado**, muestre exactamente el mismo comportamiento (voz sí, LRRP nunca) hace más difícil sostener que es un problema puntual de una marca o modelo — y es consistente con la hipótesis ya documentada en la Sesión 5 y reforzada en la investigación de código (sección "Investigación de código" más arriba): que el reporte de posición LRRP depende de una **solicitud activa desde el lado de la red** (Location Server / NAI-D), bloqueada en este sistema por la autenticación TLS-PSK de la repetidora (sin la clave disponible) — independientemente de qué radio esté transmitiendo o si ese radio tiene GPS físicamente habilitado. Un handy o base con GPS y LRRP soportado no lo va a transmitir espontáneamente si nunca llega el pedido que lo dispara.

**No se sobre-concluye**: esto es evidencia adicional a favor de una hipótesis ya en pie, no una confirmación definitiva — seguimos sin acceso directo a la capa de red protegida para probarlo de forma concluyente. Pero cada equipo nuevo que muestra el mismo patrón (voz sí, LRRP no) hace más lógico enfocar los próximos esfuerzos en el lado del pedido de red que en seguir sospechando de hardware específico.

### Próximos pasos
1. Sigue en pie la pregunta de fondo: conseguir algún mecanismo para generar (o interceptar) la solicitud activa de Location Server, dado que ya hay dos fabricantes distintos mostrando el mismo comportamiento pasivo.
2. Si en el futuro aparece un tercer equipo (de otro fabricante o modelo) con el mismo patrón, vale la pena empezar a tratar esto casi como confirmado en vez de "hipótesis reforzada".
3. El hito de containerización en sí no requiere más validación — quedó demostrado con un caso real no coordinado. Cualquier ajuste futuro al bridge (ver sección de "pérdida de audio en el hueco entre bloques" más arriba) parte de esta base ya confirmada como funcional de forma autónoma.

---

## 🎯 HITO — Primera coordenada GPS real capturada

**Contexto**: hasta este punto, toda la investigación de posición se había concentrado en LRRP/LOCN — el protocolo de Location Server que, según la hipótesis de las sesiones anteriores, está bloqueado por la autenticación TLS-PSK de la repetidora (ver "Investigación de código" y el hito anterior). El 2026-08-17 el usuario probó un handy prestado, un **Baofeng UV-32** (`radio_id 1`, DMR + GPS), usando su función **"Send → Contacts" (Short Contact)** hacia un contacto con `radio_id 1007`. El propio handy mostró "Msg Received" en pantalla. Minutos después hizo también un PTT de voz normal ("hola probando").

Investigación de solo lectura sobre los bloques ya capturados por el `sdr-decoder` corriendo en Docker — sin coordinar nada en tiempo real, mismo patrón desatendido que el hito anterior.

### El mecanismo real: esto NO es LRRP

El UV-32 **no usa LRRP/LOCN** para mandar su posición. La manda como **texto plano UTF-16LE dentro de un paquete UDP** (puerto 4007↔4007), como un mensaje de datos DMR común (`Individual Data`) — no como un protocolo de localización dedicado.

### Por qué se pudo ver esto en el aire (y por qué normalmente no se vería)

El contacto `1007` **no tenía nada escuchando en el puerto UDP 4007**. Su stack de red respondió automáticamente con un **ICMP "Destination Unreachable — Port Unreachable"** — y ese tipo de mensaje de error, por especificación estándar de ICMP, **incluye una copia del paquete original que lo provocó**. Esa copia (con el texto de la posición adentro) volvió a viajar por el aire de `1007` hacia `1`, y eso fue lo que capturó el sistema.

**Es un hallazgo casi accidental**: si `1007` hubiera tenido una aplicación real escuchando ese puerto (lo esperable en un sistema de despacho funcionando), el paquete se habría consumido silenciosamente del lado de la red y esto **nunca se habría visto por RF**. No es una confirmación de que el GPS del UV-32 sea "visible" en general — es la confirmación de que, cuando el destino rechaza el paquete, el contenido rebota y se puede capturar.

### Los bloques de captura

- `bloque_3425_20260817_170256_496195.log` y `bloque_3426_20260817_170310_881933.log` (17:02:56–17:03:24 UTC).
- Ambos bloques decodificaron el mismo paquete IP (mismo ID de fragmentación `0xB243`) con flag **`Multi Block PDU Message CRC32 ERR`** — cada captura individual falló su propio chequeo de integridad.
- La corrupción de bits cayó en tramos **distintos** de cada copia — cruzando ambas se pudo reconstruir el texto completo sin ambigüedad, incluyendo el tramo "Speed:" que en una sola copia era ilegible.

### Contenido reconstruido (UTF-16LE)

```
Lat:
32°20'26.
Long:
65°1'28.9"
Speed:
0KM/H
```

Convertido a grados decimales: **≈ -32.3406, -65.0247** — coherente con la ubicación real del usuario en Merlo, San Luis.

### Actividad posterior

Entre las 17:03 y las 17:09 UTC se repitieron ~25 paquetes UDP cortos (13 bytes, contenido todo en cero) cada ~14 segundos, con el mismo patrón de rebote ICMP — interpretados como intentos del handy de mantener viva la "conexión" en el puerto 4007, sin más datos de posición adentro (tráfico de mantenimiento de sesión, no una segunda coordenada).

### Estado actual: hallazgo forense, no automatizado

Este hallazgo es **100% manual**, reconstruido a mano sobre los logs crudos de `dsd-fme`. El `live_presence_bridge.py` **no reconoce este patrón todavía** — no hay ningún parser que busque paquetes UDP con este contenido, así que la coordenada **no llegó a `POST /api/telemetry`, no está en la base de datos, y no se vio en el mapa**. Es un dato encontrado después de los hechos, no una captura en vivo del sistema productivo.

### Por qué es el hallazgo más importante del proyecto hasta ahora

En contraste con las 17+ sesiones invertidas en LRRP/LOCN sobre los Motorola DGP8550 y la base 1012 — sin un solo resultado, bloqueadas (según la hipótesis vigente) por TLS-PSK del lado de la red — **este es un camino de GPS completamente distinto**, específico de este Baofeng UV-32, que **no depende en absoluto de resolver el TLS-PSK de la repetidora**: es tráfico de datos DMR común, visible con el mismo `sdr-decoder` que ya está corriendo, sin necesitar acceso a la capa de red protegida. Es la primera vez en todo el proyecto que se ve una coordenada GPS real, de un equipo real, en el aire.

### Próximos pasos
1. ~~Evaluar si vale la pena implementar en el bridge una detección automática de este patrón~~ — **hecho, ver actualización más abajo.**
2. Confirmar si `radio_id 1` (visto en esta sesión) y `radio_id 529385` (detección anterior, con error de CRC, sospechada de ser un Baofeng) son el mismo equipo físico o dos unidades distintas — ver `docs/Equipos.md`.
3. Definir si el mecanismo de "provocar" el rebote ICMP es reproducible a demanda (¿alcanza con que el destino no tenga el puerto abierto?) o si fue una coincidencia de esta prueba puntual — solo se puede confirmar con una transmisión nueva, coordinada, en una sesión aparte.

---

### 🔧 Actualización — detección automatizada en el bridge

Se implementó `sdr-decoder/baofeng_gps_parser.py`, que replica en código la
reconstrucción manual descripta arriba: reconoce bursts "Individual Data"
que decodifican como un paquete ICMP "Destination Unreachable — Port
Unreachable" (tipo 3), extrae el paquete UDP original embebido, decodifica
el payload como texto UTF-16LE, y parsea sus campos de forma genérica (no
asume que son solo `Lat`/`Long`/`Speed` — cualquier etiqueta nueva que
aparezca en una captura futura queda igual disponible en el resultado). Si
la misma captura (mismo ID de paquete IP exterior) aparece corrupta en un
bloque y limpia (o menos corrupta) en el bloque siguiente, el detector las
cruza automáticamente — el mismo mecanismo que se hizo a mano para este
hito, ahora en código.

**Test de regresión obligatorio (antes de tocar el bridge en vivo)**:
`sdr-decoder/test_baofeng_gps_parser.py` corre el parser contra los dos
bloques ya guardados de este hito (`bloque_3425_...` y `bloque_3426_...`),
sin generar ninguna transmisión nueva, y confirma que reproduce exactamente
lo ya reconstruido a mano:

```
radio_id == '1':                 True (obtenido: 1)
radio_id_contacto == '1007':     True (obtenido: 1007)
lat ≈ -32.3406 (±0.001):      True (obtenido: -32.340556)
lon ≈ -65.0247 (±0.001):      True (obtenido: -65.024694)
reconstruido cruzando bloques:   True

PASS: el parser reproduce la coordenada del hito sin necesitar una transmisión nueva.
```

De paso, el parser también clasificó correctamente como **incompleto** (sin
inventar lat/lon) uno de los paquetes UDP "keepalive" de 13 bytes vacíos
que aparecen en el mismo bloque 3426 — confirma que no genera falsos
positivos con el tráfico de mantenimiento de sesión ya documentado más
arriba ("Actividad posterior").

El parser quedó integrado a `live_presence_bridge.py`: por cada bloque
procesado, además de voz/emergencia/ARS/LRRP, también corre este detector;
si encuentra una coordenada completa, hace `POST /api/telemetry` con
`radio_id` = el Source real del paquete UDP original (el equipo con GPS,
no el contacto que rebotó el ICMP), y loguea el hallazgo de forma visible
(🎯), igual que ya se hacía con los hallazgos de LRRP.

**⚠️ Sigue siendo oportunista, no una solución robusta ni permanente.**
Esto no cambia por estar automatizado: sigue dependiendo por completo de
que el destinatario del mensaje no tenga el puerto UDP 4007 escuchando. Si
eso deja de pasar, el detector simplemente no encuentra nada — no hay
ningún error ni aviso que lo distinga de "no hubo transmisión". No se debe
comunicar ni documentar esto como un reemplazo confiable de LRRP en ningún
contexto (interno o de cara al cuartel).

No se hizo ninguna prueba en vivo con el Baofeng para validar esta
integración — el test de regresión contra los bloques ya guardados fue
suficiente para confirmar que el parser funciona. Una prueba en vivo
(nueva transmisión, bridge corriendo, verificar que llega a `GET
/api/equipos` y al mapa) queda pendiente para una sesión aparte, cuando
haya oportunidad de coordinarla.

---

## 🎯 HALLAZGO — El mismo canal expone mensajes de texto, no solo GPS

**Contexto**: durante la prueba en vivo del detector automatizado (ver
actualización arriba), el usuario mandó una posición GPS desde el Baofeng
UV-32 (`radio_id 1`) hacia el contacto `radio_id 1001` (Matías) en vez de
`1007`. El detector automatizado no encontró nada — investigando por qué
(de solo lectura, sobre los bloques ya guardados) se encontró algo más
importante que el motivo puntual del fallo: **`1001` sí tiene algo
escuchando en el puerto UDP 4007** (a diferencia de `1007`), y respondió
con un mensaje de texto propio, capturado igual de expuesto que el GPS.

Para confirmarlo sin ambigüedad, en una prueba de seguimiento el usuario
mandó un mensaje de texto literal ("Test123") **desde su HT `1001`
(Matías) hacia el Baofeng UV-32 (`radio_id 1`)** usando la función de
mensajería del handy — sin nada de GPS de por medio.

### Lo que se encontró

`bloque_0028_20260817_214606_903898.log`, 21:46:21 UTC — burst "Individual
Data", `Source: 1001 Target: 1`, `Confirmed Delivery - Response
Requested`, marcado `Multi Block PDU Message CRC32 ERR` por `dsd-fme`.

Decodificando el paquete IP/UDP embebido:
- Origen: `12.0.3.233` = `0x03E9` = **1001** (Matías).
- Destino: `12.0.0.1` = `0x0001` = **1** (Baofeng UV-32).
- **UDP directo** (puerto 4007 → 2155) — a diferencia del hallazgo de GPS,
  esta vez **no** hubo rebote ICMP: el paquete viajó directo entre los dos
  equipos y se capturó tal cual, sin necesitar ningún error de red de por
  medio.
- Payload UTF-16LE reconstruido: `\n` + `T` + `e` + `s` + `t` + `1` + `2` +
  `3` = **"Test123"** — coincide exactamente con lo que el usuario
  confirmó haber mandado. De los 8 caracteres, 6 decodificaron perfectos y
  2 tenían corrupción de **un solo bit cada uno** (la 'e' con el byte alto
  corrido, la 's' con el byte bajo invertido en un bit: `0x73 XOR 0x33 =
  0x40`) — consistente con señal marginal, no con ruido aleatorio.

### Por qué es un hallazgo aparte del GPS, no una repetición

El mecanismo de captura del GPS (`baofeng_gps_parser.py`) está construido
específicamente para reconocer el **rebote ICMP** cuando el destinatario
rechaza el paquete. Este mensaje de texto **no pasó por ese camino en
absoluto** — viajó como un UDP directo, aceptado y respondido por `1001`,
sin ningún rechazo de red. Es decir: **la exposición no depende de que el
mensaje rebote** — cualquier transmisión de datos de este tipo (texto o
GPS) que el sistema logre capturar y decodificar, con suficiente calidad
de señal, revela su contenido en texto plano. El rebote ICMP del hito
anterior fue *una* forma de verlo, no la única.

### Alcance real: no es "GPS expuesto", es "cualquier mensaje corto expuesto"

Esto generaliza el hallazgo del hito anterior más allá de la posición:
**cualquier mensaje de texto corto mandado con la función "Send" de un
handy compatible con este esquema (Baofeng UV-32 confirmado; sin
confirmar todavía en otros modelos) viaja en texto plano UTF-16LE dentro
de DMR, sin cifrar** — se trate de una coordenada GPS o de un mensaje
escrito a mano como este. No hace falta un error de red para verlo: alcanza
con que `dsd-fme` decodifique el burst con suficiente calidad.

### Estado: hallazgo forense manual, no automatizado

`baofeng_gps_parser.py` **no reconoce este caso** — su lógica de
extracción exige que el paquete exterior sea ICMP (protocolo 1); un UDP
directo como este (protocolo 0x11 sin envoltorio ICMP) no pasa ese filtro
y el detector no lo ve. Este mensaje se decodificó completamente a mano,
igual que el GPS del hito original antes de automatizarlo.

### Próximos pasos
1. Evaluar si vale la pena extender `baofeng_gps_parser.py` para reconocer
   también paquetes UDP directos (sin envoltorio ICMP) con el mismo
   prefijo de aplicación (`SAP 04 [IP Based]`, puerto 4007) — ampliaría la
   detección de "solo coordenadas rebotadas" a "cualquier mensaje de texto
   capturado con suficiente calidad", sea GPS o no.
2. Si se implementa, definir qué hacer con el contenido de un mensaje de
   texto arbitrario (no es una posición, no encaja en `/api/telemetry`) —
   probablemente un evento nuevo o un campo separado, a decidir antes de
   automatizar nada.
3. Tener en cuenta la implicancia de privacidad/seguridad: cualquier
   mensaje de texto mandado por este canal entre handies compatibles es,
   en los hechos, texto plano interceptable — no es exclusivo de este
   sistema de investigación, es una característica (o falla) del propio
   protocolo/firmware del handy. No comunicar esto como una vulnerabilidad
   de `tracking-GPS-VHF` — es una propiedad del hardware/firmware de
   terceros, que este proyecto simplemente puede observar por estar
   escuchando el aire.
