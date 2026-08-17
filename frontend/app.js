// Puerto donde el backend expone la API/WebSocket (debe coincidir con
// BACKEND_PORT en el .env usado por docker-compose).
const BACKEND_WS_PORT = 8000;
const WS_URL = `ws://${window.location.hostname}:${BACKEND_WS_PORT}/ws/telemetry`;
const API_EQUIPOS_URL = `http://${window.location.hostname}:${BACKEND_WS_PORT}/api/equipos`;
const API_AUDIO_URL = `http://${window.location.hostname}:${BACKEND_WS_PORT}/api/audio-eventos`;
const API_SDR_STATUS_URL = `http://${window.location.hostname}:${BACKEND_WS_PORT}/api/sdr-status`;

// Debe coincidir con PRESENCE_ONLINE_THRESHOLD_SECONDS del backend (ver
// .env.example) — no hay forma de inyectar env vars a este frontend
// estático, así que se mantiene como constante duplicada a mano, igual que
// BACKEND_WS_PORT arriba.
const ONLINE_THRESHOLD_MS = 5 * 60 * 1000;

const NOMBRES_EVENTO = {
  voz: "Voz",
  emergencia: "Emergencia",
  ars: "ARS (registro)",
};

// Íconos disponibles para el mapa (ver IconoEquipo en backend/app/schemas.py)
// — elección puramente visual del operador vía el popup del marcador.
const ICONOS = {
  base_vhf: { emoji: "📡", etiqueta: "Base VHF" },
  camion_bomberos: { emoji: "🚒", etiqueta: "Camión de bomberos" },
  ambulancia: { emoji: "🚑", etiqueta: "Ambulancia" },
  fuego: { emoji: "🔥", etiqueta: "Incendio activo" },
  handy: { emoji: "📻", etiqueta: "Handy" },
  bombero: { emoji: "🧑‍🚒", etiqueta: "Bombero a pie" },
};

function crearIcono(iconoKey) {
  const emoji = (ICONOS[iconoKey] && ICONOS[iconoKey].emoji) || "";
  return L.divIcon({
    className: "marcador-equipo",
    html: `<div class="marcador-punto">${emoji}</div>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -15],
  });
}

// Centro inicial: Merlo, San Luis, Argentina.
const CENTRO_MERLO = [-32.3436, -65.0128];

// zoomControl: false porque el control de zoom por defecto va arriba a la
// izquierda, donde ahora vive el panel de audio (ver index.html) — se
// vuelve a agregar abajo a la izquierda, lejos de los dos paneles.
const map = L.map("map", { zoomControl: false }).setView(CENTRO_MERLO, 13);
L.control.zoom({ position: "bottomleft" }).addTo(map);

// Capas base seleccionables (ver control-capas en index.html) — ninguna se
// agrega al mapa acá, eso lo hace inicializarSelectorCapas() más abajo.
const CAPAS_BASE = {
  calle: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }),
  satelite: L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 19,
      attribution: "Tiles &copy; Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    }
  ),
};

const CAPA_MAPA_STORAGE_KEY = "tracking-gps-vhf:capa-mapa";
let capaMapaActual = null;

function setCapaMapa(nombre) {
  if (!CAPAS_BASE[nombre] || nombre === capaMapaActual) return;
  if (capaMapaActual) map.removeLayer(CAPAS_BASE[capaMapaActual]);
  CAPAS_BASE[nombre].addTo(map);
  capaMapaActual = nombre;
  document.querySelectorAll(".capa-btn").forEach((btn) => {
    btn.classList.toggle("capa-btn--activo", btn.dataset.capa === nombre);
  });
  try {
    localStorage.setItem(CAPA_MAPA_STORAGE_KEY, nombre);
  } catch (err) {
    console.error(`No se pudo guardar ${CAPA_MAPA_STORAGE_KEY} en localStorage:`, err);
  }
}

function inicializarSelectorCapas() {
  let inicial = "calle";
  try {
    const guardada = localStorage.getItem(CAPA_MAPA_STORAGE_KEY);
    if (guardada && CAPAS_BASE[guardada]) inicial = guardada;
  } catch (err) {
    console.error(`No se pudo leer ${CAPA_MAPA_STORAGE_KEY} de localStorage:`, err);
  }
  setCapaMapa(inicial);
  document.querySelectorAll(".capa-btn").forEach((btn) => {
    btn.addEventListener("click", () => setCapaMapa(btn.dataset.capa));
  });
}

const marcadores = new Map(); // equipo_id -> L.Marker

function formatearHora(isoString) {
  return new Date(isoString).toLocaleString("es-AR", {
    dateStyle: "short",
    timeStyle: "medium",
  });
}

function construirSelectorIconos(equipoId, iconoActual) {
  const botones = Object.entries(ICONOS)
    .map(([key, { emoji, etiqueta }]) => {
      const activo = key === iconoActual ? " icono-btn--activo" : "";
      return `<button type="button" class="icono-btn${activo}" data-equipo-id="${equipoId}" data-icono="${key}" title="${etiqueta}">${emoji}</button>`;
    })
    .join("");

  return `
    <div class="icono-selector">
      <div class="icono-selector-titulo">Cambiar ícono:</div>
      <div class="icono-selector-botones">${botones}</div>
    </div>
  `;
}

function construirPopup(equipoId, posicion, iconoActual) {
  const partes = [
    `<strong>${posicion.radio_alias}</strong>`,
    `Última posición: ${posicion.lat.toFixed(5)}, ${posicion.lon.toFixed(5)}`,
    `Hora: ${formatearHora(posicion.timestamp)}`,
  ];
  if (posicion.velocidad != null) {
    partes.push(`Velocidad: ${posicion.velocidad} km/h`);
  }
  if (posicion.rumbo != null) {
    partes.push(`Rumbo: ${posicion.rumbo}°`);
  }
  return partes.join("<br>") + construirSelectorIconos(equipoId, iconoActual);
}

// equipo_id -> última posición conocida (necesario para poder redibujar el
// marcador/popup cuando cambia el ícono, sin depender de un nuevo
// position_update).
const ultimasPosiciones = new Map();

function renderMarcador(equipoId) {
  const posicion = ultimasPosiciones.get(equipoId);
  if (!posicion) return; // todavía no llegó ninguna posición para este equipo

  const estado = equiposEstado.get(equipoId) || {};
  const posLatLng = [posicion.lat, posicion.lon];
  let marcador = marcadores.get(equipoId);
  const icono = crearIcono(estado.icono);

  if (!marcador) {
    marcador = L.marker(posLatLng, { icon: icono }).addTo(map);
    marcadores.set(equipoId, marcador);
  } else {
    marcador.setLatLng(posLatLng);
    marcador.setIcon(icono);
  }

  marcador.bindPopup(construirPopup(equipoId, posicion, estado.icono));
}

async function cambiarIcono(equipoId, icono) {
  try {
    const resp = await fetch(`${API_EQUIPOS_URL}/${equipoId}/icono`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ icono }),
    });
    if (!resp.ok) throw new Error(`PATCH icono -> ${resp.status}`);
    // La actualización visual llega por WS (icono_update), no hace falta
    // tocar el marcador acá directamente.
  } catch (err) {
    console.error("No se pudo actualizar el ícono:", err);
  }
}

document.addEventListener("click", (event) => {
  const boton = event.target.closest(".icono-btn");
  if (!boton) return;
  cambiarIcono(Number(boton.dataset.equipoId), boton.dataset.icono);
});

function setEstado(clase, texto) {
  const el = document.getElementById("status");
  el.className = `status status--${clase}`;
  el.textContent = texto;
}

// ---- Indicador de estado del SDR (ver docs/operacion-sdr.md) ----

const NOMBRES_ESTADO_SDR = {
  ok: "SDR: OK",
  sin_datos: "SDR: sin datos",
  mala_antena: "SDR: mala antena",
  desconectado: "SDR: desconectado",
};

function actualizarIndicadorSdr(status, timestamp, detalle) {
  const el = document.getElementById("sdr-status-indicator");
  if (!el) return;

  el.className = `sdr-status-indicator sdr-status--${status}`;
  const texto = el.querySelector(".sdr-status-texto");
  if (texto) texto.textContent = NOMBRES_ESTADO_SDR[status] || `SDR: ${status}`;

  const hora = timestamp ? formatearHora(timestamp) : null;
  const partes = [NOMBRES_ESTADO_SDR[status] || status];
  if (hora) partes.push(`— ${hora}`);
  if (detalle) partes.push(`\n${detalle}`);
  el.title = partes.join(" ");
}

async function cargarEstadoSdrInicial() {
  try {
    const resp = await fetch(API_SDR_STATUS_URL);
    if (!resp.ok) throw new Error(`GET /api/sdr-status -> ${resp.status}`);
    const datos = await resp.json();
    actualizarIndicadorSdr(datos.status, datos.timestamp, datos.detalle);
  } catch (err) {
    console.error("No se pudo cargar el estado inicial del SDR:", err);
  }
}

function manejarSdrStatusUpdate(datos) {
  actualizarIndicadorSdr(datos.status, datos.timestamp, datos.detalle);
}

// ---- Panel de audio (bitácora) ----

const audioEventos = new Map(); // id -> { id, radio_id, radio_alias, timestamp_inicio, duracion_seg, escuchado }

function formatearDuracion(seg) {
  if (seg < 60) return `${Math.round(seg)}s`;
  const minutos = Math.floor(seg / 60);
  const segundos = Math.round(seg % 60);
  return `${minutos}m ${segundos}s`;
}

function construirItemAudioHTML(evento) {
  const estadoClase = evento.escuchado ? "audio-item--escuchado" : "audio-item--no-escuchado";
  const alias = evento.radio_alias || evento.radio_id || "Desconocido";
  const radioId = evento.radio_id ? `<span class="audio-radio-id">${evento.radio_id}</span>` : "";
  const duracion = formatearDuracion(evento.duracion_seg);
  const tiempoInicial = formatearTiempo(evento.duracion_seg);
  return `
    <li class="audio-item ${estadoClase}" data-id="${evento.id}">
      <div class="audio-card-header">
        <span class="audio-alias">${alias}</span>
        <span class="audio-timestamp">${formatearHora(evento.timestamp_inicio)}</span>
      </div>
      <div class="audio-card-meta">
        ${radioId}
        <span class="audio-duracion">${duracion}</span>
      </div>
      <div class="audio-player-custom" data-id="${evento.id}">
        <button type="button" class="audio-play-btn" aria-label="Reproducir">▶</button>
        <div class="audio-progress-track">
          <div class="audio-progress-fill"></div>
        </div>
        <span class="audio-tiempo">${tiempoInicial}</span>
      </div>
      <audio class="audio-player-oculto" preload="none" data-id="${evento.id}" src="${API_AUDIO_URL}/${evento.id}/file"></audio>
    </li>
  `;
}

// Único <audio> con reproducción exclusiva: al arrancar uno, se pausan
// todos los demás (Parte 3 del pedido de rediseño).
let audioSonandoActual = null;

function formatearTiempo(seg) {
  if (!Number.isFinite(seg) || seg < 0) seg = 0;
  const m = Math.floor(seg / 60);
  const s = Math.floor(seg % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
}

// Conecta los controles propios (botón + barra) de una card con su <audio>
// oculto — se llama tanto en la carga inicial como al insertar un clip
// nuevo en vivo, para no depender de un solo render global.
function adjuntarControlesAudio(li) {
  const audioEl = li.querySelector(".audio-player-oculto");
  const boton = li.querySelector(".audio-play-btn");
  const track = li.querySelector(".audio-progress-track");
  const fill = li.querySelector(".audio-progress-fill");
  const tiempoEl = li.querySelector(".audio-tiempo");
  const id = Number(audioEl.dataset.id);
  const duracionMeta = (audioEventos.get(id) || {}).duracion_seg || 0;

  const actualizarProgreso = () => {
    const duracion = audioEl.duration || duracionMeta;
    const pct = duracion ? (audioEl.currentTime / duracion) * 100 : 0;
    fill.style.width = `${Math.min(100, pct)}%`;
    tiempoEl.textContent = audioEl.paused && audioEl.currentTime === 0
      ? formatearTiempo(duracion)
      : formatearTiempo(audioEl.currentTime);
  };

  boton.addEventListener("click", () => {
    if (audioEl.paused) {
      audioEl.play();
    } else {
      audioEl.pause();
    }
  });

  track.addEventListener("click", (event) => {
    const duracion = audioEl.duration || duracionMeta;
    if (!duracion) return;
    const rect = track.getBoundingClientRect();
    const pct = (event.clientX - rect.left) / rect.width;
    audioEl.currentTime = Math.max(0, Math.min(1, pct)) * duracion;
  });

  audioEl.addEventListener("play", () => {
    if (audioSonandoActual && audioSonandoActual !== audioEl) {
      audioSonandoActual.pause();
    }
    audioSonandoActual = audioEl;
    boton.textContent = "⏸";
    marcarEscuchado(id);
  });

  audioEl.addEventListener("pause", () => {
    boton.textContent = "▶";
  });

  audioEl.addEventListener("timeupdate", actualizarProgreso);
  audioEl.addEventListener("loadedmetadata", actualizarProgreso);

  audioEl.addEventListener("ended", () => {
    boton.textContent = "▶";
    fill.style.width = "0%";
    audioEl.currentTime = 0;
    tiempoEl.textContent = formatearTiempo(audioEl.duration || duracionMeta);
    if (audioSonandoActual === audioEl) audioSonandoActual = null;

    // Parte 4 — reproducción en secuencia (switch, default OFF): si está
    // activado, avanza hacia el presente (el clip más reciente que el que
    // acaba de terminar) — la lista muestra el más nuevo arriba, así que
    // eso es el hermano ANTERIOR en el DOM, no el siguiente.
    const toggle = document.getElementById("toggle-secuencial");
    if (toggle && toggle.checked) {
      const siguiente = li.previousElementSibling;
      const siguienteAudio = siguiente && siguiente.querySelector
        ? siguiente.querySelector(".audio-player-oculto")
        : null;
      if (siguienteAudio) siguienteAudio.play();
    }
  });
}

function actualizarContadorAudio() {
  const el = document.getElementById("contador-audio");
  if (el) el.textContent = String(audioEventos.size);
}

// Solo para la carga inicial — reconstruye toda la lista de una vez. Los
// clips que llegan en vivo por WS NO pasan por acá (ver
// agregarClipAudioEnVivo): un innerHTML completo destruiría y recrearía
// todos los <audio> existentes, cortando la reproducción de un clip que ya
// estuviera sonando en el momento.
function renderPanelAudio() {
  const lista = document.getElementById("lista-audio");
  const eventos = Array.from(audioEventos.values()).sort(
    (a, b) => new Date(b.timestamp_inicio) - new Date(a.timestamp_inicio)
  );

  actualizarContadorAudio();

  if (eventos.length === 0) {
    lista.innerHTML = '<li class="lista-audio-vacio">Sin clips todavía</li>';
    return;
  }

  lista.innerHTML = eventos.map(construirItemAudioHTML).join("");
  lista.querySelectorAll(".audio-item").forEach(adjuntarControlesAudio);
}

function agregarClipAudioEnVivo(datos) {
  const evento = {
    id: datos.id,
    radio_id: datos.radio_id,
    radio_alias: datos.radio_alias,
    timestamp_inicio: datos.timestamp_inicio,
    duracion_seg: datos.duracion_seg,
    escuchado: datos.escuchado,
  };
  audioEventos.set(evento.id, evento);
  actualizarContadorAudio();

  const lista = document.getElementById("lista-audio");
  const vacio = lista.querySelector(".lista-audio-vacio");
  if (vacio) vacio.remove();

  lista.insertAdjacentHTML("afterbegin", construirItemAudioHTML(evento));
  const li = lista.querySelector(`.audio-item[data-id="${evento.id}"]`);
  if (li) {
    adjuntarControlesAudio(li);
    li.classList.add("audio-item--nuevo");
    li.addEventListener("animationend", () => li.classList.remove("audio-item--nuevo"), { once: true });

    // "Escuchar en vivo" (switch, default OFF): reproduce el clip apenas
    // llega, sin esperar un click. La reproducción exclusiva ya existente
    // (ver adjuntarControlesAudio, evento 'play') se encarga sola de
    // pausar cualquier otro clip que estuviera sonando — no hace falta
    // duplicar esa lógica acá.
    const toggleEscucharVivo = document.getElementById("toggle-escuchar-vivo");
    if (toggleEscucharVivo && toggleEscucharVivo.checked) {
      const audioEl = li.querySelector(".audio-player-oculto");
      if (audioEl) {
        audioEl.play().catch((err) => {
          // El navegador puede bloquear el autoplay si todavía no hubo
          // ninguna interacción del usuario con la página — no hay forma
          // de forzarlo desde JS, solo degradar sin romper nada más.
          console.error("No se pudo reproducir en vivo (posible bloqueo de autoplay del navegador):", err);
        });
      }
    }
  }
}

// Actualiza solo la clase visual del <li> (no reconstruye el <audio>) —
// tanto para el marcado optimista local como para un audio_event_escuchado
// que llegue de otro cliente con el panel abierto.
function actualizarClaseEscuchado(id) {
  const li = document.querySelector(`.audio-item[data-id="${id}"]`);
  if (li) {
    li.classList.remove("audio-item--no-escuchado");
    li.classList.add("audio-item--escuchado");
  }
}

function manejarAudioEventoEscuchado(datos) {
  const evento = audioEventos.get(datos.id);
  if (evento) {
    evento.escuchado = datos.escuchado;
    audioEventos.set(datos.id, evento);
  }
  actualizarClaseEscuchado(datos.id);
}

async function marcarEscuchado(id) {
  const evento = audioEventos.get(id);
  if (!evento || evento.escuchado) return; // ya marcado, no repetir el PATCH

  manejarAudioEventoEscuchado({ id, escuchado: true }); // optimista, sin esperar la respuesta
  try {
    const resp = await fetch(`${API_AUDIO_URL}/${id}/escuchado`, { method: "PATCH" });
    if (!resp.ok) throw new Error(`PATCH escuchado -> ${resp.status}`);
  } catch (err) {
    console.error("No se pudo marcar el clip como escuchado:", err);
  }
}

async function cargarAudioEventosIniciales() {
  try {
    const resp = await fetch(API_AUDIO_URL);
    if (!resp.ok) throw new Error(`GET /api/audio-eventos -> ${resp.status}`);
    const eventos = await resp.json();
    for (const evento of eventos) {
      audioEventos.set(evento.id, evento);
    }
    renderPanelAudio();
  } catch (err) {
    // No bloquea el resto de la app si falla la carga inicial de este panel.
    console.error("No se pudo cargar el estado inicial de la bitácora de audio:", err);
  }
}

// Persistencia de los switches del panel en localStorage — localStorage
// SÍ es válido acá (a diferencia de un Artifact de claude.ai): esto es
// una app real, propia, fuera de ese contexto. Es una mejora de UX, no
// algo obligatorio — si falla (modo privado estricto, cuota, etc.) el
// switch simplemente arranca en su default (false) sin romper nada más.
function restaurarSwitch(elementId, storageKey) {
  const el = document.getElementById(elementId);
  if (!el) return;
  try {
    el.checked = localStorage.getItem(storageKey) === "true";
  } catch (err) {
    console.error(`No se pudo leer ${storageKey} de localStorage:`, err);
  }
  el.addEventListener("change", () => {
    try {
      localStorage.setItem(storageKey, String(el.checked));
    } catch (err) {
      console.error(`No se pudo guardar ${storageKey} en localStorage:`, err);
    }
  });
}

// ---- Panel de equipos (presencia) ----

const equiposEstado = new Map(); // equipo_id -> { alias, radio_id, tipo, ultimo_visto: Date|null, ultimo_evento }

function esOnline(ultimoVisto) {
  return ultimoVisto != null && Date.now() - ultimoVisto.getTime() <= ONLINE_THRESHOLD_MS;
}

function tiempoRelativo(fecha) {
  if (fecha == null) return "sin datos";
  const segundos = Math.floor((Date.now() - fecha.getTime()) / 1000);
  if (segundos < 5) return "recién";
  if (segundos < 60) return `hace ${segundos} seg`;
  const minutos = Math.floor(segundos / 60);
  if (minutos < 60) return `hace ${minutos} min`;
  const horas = Math.floor(minutos / 60);
  if (horas < 24) return `hace ${horas} h`;
  const dias = Math.floor(horas / 24);
  return `hace ${dias} d`;
}

function upsertEquipoEstado(datos) {
  const existente = equiposEstado.get(datos.id) || {};
  equiposEstado.set(datos.id, { ...existente, ...datos });
}

function renderPanelEquipos() {
  const lista = document.getElementById("lista-equipos");
  // Más reciente primero (no alfabético) — un equipo sin ultimo_visto
  // todavía (nunca visto) se va al final, no al principio.
  const equipos = Array.from(equiposEstado.values()).sort((a, b) => {
    const tA = a.ultimo_visto ? a.ultimo_visto.getTime() : 0;
    const tB = b.ultimo_visto ? b.ultimo_visto.getTime() : 0;
    return tB - tA;
  });

  const contador = document.getElementById("contador-equipos");
  if (contador) contador.textContent = String(equipos.length);

  if (equipos.length === 0) {
    lista.innerHTML = '<li class="lista-equipos-vacio">Sin equipos todavía</li>';
    return;
  }

  lista.innerHTML = equipos
    .map((equipo) => {
      const online = esOnline(equipo.ultimo_visto);
      const evento = equipo.ultimo_evento ? NOMBRES_EVENTO[equipo.ultimo_evento] || equipo.ultimo_evento : null;
      return `
        <li class="equipo-item">
          <span class="equipo-dot ${online ? "equipo-dot--online" : ""}"></span>
          <span class="equipo-info">
            <div class="equipo-alias">${equipo.alias}</div>
            <div class="equipo-radio-id">${equipo.radio_id || "—"}</div>
            <div class="equipo-detalle">${tiempoRelativo(equipo.ultimo_visto)}${evento ? ` · ${evento}` : ""}</div>
          </span>
        </li>
      `;
    })
    .join("");
}

async function cargarEquiposIniciales() {
  try {
    const resp = await fetch(API_EQUIPOS_URL);
    if (!resp.ok) throw new Error(`GET /api/equipos -> ${resp.status}`);
    const equipos = await resp.json();
    for (const equipo of equipos) {
      upsertEquipoEstado({
        id: equipo.id,
        alias: equipo.alias,
        radio_id: equipo.radio_id,
        tipo: equipo.tipo,
        icono: equipo.icono,
        ultimo_visto: equipo.ultimo_visto ? new Date(equipo.ultimo_visto) : null,
        ultimo_evento: equipo.ultimo_evento,
      });
      if (equipo.ultima_posicion) {
        ultimasPosiciones.set(equipo.id, {
          equipo_id: equipo.id,
          radio_id: equipo.radio_id,
          radio_alias: equipo.alias,
          lat: equipo.ultima_posicion.lat,
          lon: equipo.ultima_posicion.lon,
          altitud: equipo.ultima_posicion.altitud,
          velocidad: equipo.ultima_posicion.velocidad,
          rumbo: equipo.ultima_posicion.rumbo,
          timestamp: equipo.ultima_posicion.timestamp,
        });
        renderMarcador(equipo.id);
      }
    }
    renderPanelEquipos();
  } catch (err) {
    // No bloquea el resto de la app (mapa/WS) si falla la carga inicial del
    // panel — se reintentará implícitamente con los próximos presence_update.
    console.error("No se pudo cargar el estado inicial de equipos:", err);
  }
}

function manejarPresenceUpdate(datos) {
  upsertEquipoEstado({
    id: datos.equipo_id,
    alias: datos.radio_alias,
    radio_id: datos.radio_id,
    ultimo_visto: datos.ultimo_visto ? new Date(datos.ultimo_visto) : null,
    ultimo_evento: datos.ultimo_evento,
  });
  renderPanelEquipos();
}

function manejarPositionUpdate(datos) {
  ultimasPosiciones.set(datos.equipo_id, datos);
  renderMarcador(datos.equipo_id);

  // Una posición también es evidencia de presencia (ver docs/API.md) — se
  // refleja en el panel aunque no traiga un "evento" propio de
  // voz/emergencia/ars.
  const existente = equiposEstado.get(datos.equipo_id);
  upsertEquipoEstado({
    id: datos.equipo_id,
    alias: datos.radio_alias,
    radio_id: datos.radio_id,
    ultimo_visto: datos.timestamp ? new Date(datos.timestamp) : null,
    ultimo_evento: existente ? existente.ultimo_evento : null,
  });
  renderPanelEquipos();
}

function manejarIconoUpdate(datos) {
  upsertEquipoEstado({ id: datos.equipo_id, icono: datos.icono });
  renderMarcador(datos.equipo_id);
}

function conectar() {
  const ws = new WebSocket(WS_URL);

  ws.onopen = () => setEstado("connected", "Conectado al backend");

  ws.onmessage = (event) => {
    const datos = JSON.parse(event.data);
    if (datos.type === "presence_update") {
      manejarPresenceUpdate(datos);
    } else if (datos.type === "icono_update") {
      manejarIconoUpdate(datos);
    } else if (datos.type === "audio_event") {
      agregarClipAudioEnVivo(datos);
    } else if (datos.type === "audio_event_escuchado") {
      manejarAudioEventoEscuchado(datos);
    } else if (datos.type === "sdr_status_update") {
      manejarSdrStatusUpdate(datos);
    } else {
      // "position_update", o mensaje sin "type" (compatibilidad hacia atrás).
      manejarPositionUpdate(datos);
    }
  };

  ws.onclose = () => {
    setEstado("disconnected", "Desconectado — reintentando…");
    setTimeout(conectar, 3000);
  };

  ws.onerror = () => ws.close();
}

// ---- Wake Lock: evita que la pantalla/PC se suspenda mientras el panel
// esté abierto (mismo mecanismo que usa un video de YouTube reproduciendo,
// pero explícito — acá no hay audio/video sonando todo el tiempo, así que
// no podemos depender de eso). Requiere secure context (localhost o
// HTTPS) — si no está disponible, se degrada en silencio sin romper nada.
let wakeLock = null;

async function pedirWakeLock() {
  if (!("wakeLock" in navigator)) {
    console.warn("Wake Lock API no disponible (¿navegador viejo, o ni localhost ni HTTPS?).");
    return;
  }
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    console.log("Wake Lock activo — la pantalla no debería suspenderse mientras esta pestaña esté visible.");
  } catch (err) {
    console.error("No se pudo activar Wake Lock:", err);
  }
}

// El wake lock se libera solo cuando la pestaña deja de estar visible —
// hay que volver a pedirlo cuando vuelve a estar en primer plano.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    pedirWakeLock();
  }
});

pedirWakeLock();

restaurarSwitch("toggle-secuencial", "tracking-gps-vhf:reproducir-en-secuencia");
restaurarSwitch("toggle-escuchar-vivo", "tracking-gps-vhf:escuchar-en-vivo");
inicializarSelectorCapas();

cargarEquiposIniciales();
cargarAudioEventosIniciales();
cargarEstadoSdrInicial();
conectar();

// Recalcula tiempos relativos y estado online/offline aunque no lleguen
// mensajes nuevos (si no, un equipo queda "online" para siempre en pantalla
// una vez visto, hasta el próximo evento).
setInterval(renderPanelEquipos, 30000);
