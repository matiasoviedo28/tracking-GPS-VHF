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
  gps: "GPS (dato)",
  aprs: "GPS automático (APRS)",
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
// maxZoom: 19 explícito porque L.markerClusterGroup.addTo(map) se llama
// antes de que inicializarSelectorCapas() agregue la capa de tiles (más
// abajo en este archivo) — sin esto, markercluster no tiene de dónde
// leer el maxZoom del mapa todavía y tira "Map has no maxZoom specified".
// Coincide con el maxZoom: 19 que ya tienen las dos capas en CAPAS_BASE.
const map = L.map("map", { zoomControl: false, maxZoom: 19 }).setView(CENTRO_MERLO, 13);
L.control.zoom({ position: "bottomleft" }).addTo(map);

// Agrupa marcadores cercanos (o exactamente superpuestos, ej. dos equipos
// reportando la misma posición) en una burbuja con el número de equipos —
// se abre en click/zoom. maxClusterRadius chico porque acá lo que importa
// es separar coincidencias exactas, no agrupar por zona.
const grupoMarcadores = L.markerClusterGroup({
  maxClusterRadius: 40,
  spiderfyOnMaxZoom: true,
  showCoverageOnHover: false,
}).addTo(map);

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
    marcador = L.marker(posLatLng, { icon: icono });
    grupoMarcadores.addLayer(marcador);
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

// ---- Trazabilidad histórica (trazados) ----
// Ver docs/API.md, GET /api/equipos/{equipo_id}/posiciones, y ARQUITECTURA.md
// sección 6. Decisiones de alcance ya tomadas (no reabrir):
//   - Click en equipo sin trazado -> abre el modal de filtros.
//   - Click en equipo con trazado -> lo saca del mapa (toggle), sin reabrir
//     el modal.
//   - Varios equipos pueden estar trazados a la vez, cada uno con color fijo
//     propio; el modo "por velocidad" solo se habilita con exactamente 1
//     trazado activo (con 2+, se fuerza color fijo por equipo).

const API_HISTORICO_URL = (equipoId) => `${API_EQUIPOS_URL}/${equipoId}/posiciones`;

// Paleta de colores fija por equipo (10 tonos distintos y legibles, con
// buen contraste tanto sobre el mapa de calles como el satelital) — se
// asigna por equipo_id % length, así que es estable entre sesiones para el
// mismo equipo mientras no cambie de id.
const PALETA_TRAZADOS = [
  "#e6194b", // rojo
  "#3cb44b", // verde
  "#4363d8", // azul
  "#f58231", // naranja
  "#911eb4", // violeta
  "#42d4d4", // cian
  "#f032e6", // magenta
  "#9a6324", // marrón
  "#000075", // azul marino
  "#808000", // oliva
];

function colorPorEquipo(equipoId) {
  return PALETA_TRAZADOS[equipoId % PALETA_TRAZADOS.length];
}

// Gradiente del modo "por velocidad" — celeste (lento) a rojo (rápido).
// Coincide a mano con el gradiente CSS de .trazado-leyenda-barra en
// style.css (no hay una única fuente de verdad entre CSS e íconos Leaflet,
// así que si se cambia acá hay que cambiar también ahí).
const COLOR_VELOCIDAD_MIN = "#46c9f0";
const COLOR_VELOCIDAD_MAX = "#e6194b";

function hexARgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function interpolarColor(colorMin, colorMax, t) {
  const c1 = hexARgb(colorMin);
  const c2 = hexARgb(colorMax);
  const r = Math.round(c1.r + (c2.r - c1.r) * t);
  const g = Math.round(c1.g + (c2.g - c1.g) * t);
  const b = Math.round(c1.b + (c2.b - c1.b) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

// equipo_id -> respuesta cruda del backend (posiciones + metadata). Su sola
// presencia en este Map es lo que define "este equipo tiene trazado activo"
// — se guarda el crudo (no solo el layer) para poder redibujar en otro modo
// de color sin volver a pedirle nada al backend.
const trazadosDatos = new Map();
// equipo_id -> L.LayerGroup ya agregado al mapa (se recrea entero en cada
// redibujo — más simple que mutar polylines existentes, y el volumen de
// puntos por trazado es chico, ver max_puntos en el backend).
const trazadosCapas = new Map();
// equipo_id -> { mostrados, total } — persiste mientras el trazado esté
// activo, para el aviso "Mostrando datos limitados (X de Y puntos)".
const avisosMuestreo = new Map();

let modoColorTrazado = "equipo"; // "equipo" | "velocidad"
let filtroEquipoActivo = null; // equipo_id para el que está abierto el modal de filtros

function puedeUsarModoVelocidad(datos) {
  return (
    datos != null &&
    datos.velocidad_min != null &&
    datos.velocidad_max != null &&
    datos.velocidad_max > datos.velocidad_min
  );
}

function mostrarToast(mensaje) {
  const el = document.createElement("div");
  el.className = "toast-trazado";
  el.textContent = mensaje;
  document.body.appendChild(el);
  requestAnimationFrame(() => el.classList.add("toast-trazado--visible"));
  setTimeout(() => {
    el.classList.remove("toast-trazado--visible");
    setTimeout(() => el.remove(), 250);
  }, 3800);
}

function limpiarCapaTrazado(equipoId) {
  const capa = trazadosCapas.get(equipoId);
  if (capa) {
    map.removeLayer(capa);
    trazadosCapas.delete(equipoId);
  }
}

// Redibuja un único trazado a partir de los datos ya cacheados en
// trazadosDatos — no pega al backend. El modo velocidad solo se aplica
// realmente si hay exactamente un trazado activo Y ese trazado tiene rango
// de velocidad utilizable; si no, cae a color fijo por equipo sin romper
// nada (chequeo local, no depende de que actualizarSelectorModo haya
// corrido antes).
function redibujarTrazado(equipoId) {
  limpiarCapaTrazado(equipoId);
  const datos = trazadosDatos.get(equipoId);
  if (!datos || !datos.posiciones || datos.posiciones.length === 0) return;

  const puntos = datos.posiciones;
  const capa = L.layerGroup();
  const usaVelocidad =
    modoColorTrazado === "velocidad" &&
    trazadosDatos.size === 1 &&
    puedeUsarModoVelocidad(datos);

  if (usaVelocidad && puntos.length > 1) {
    for (let i = 0; i < puntos.length - 1; i++) {
      const a = puntos[i];
      const b = puntos[i + 1];
      const vel = a.velocidad != null ? a.velocidad : b.velocidad;
      const t =
        vel == null
          ? 0.5
          : (vel - datos.velocidad_min) / (datos.velocidad_max - datos.velocidad_min);
      const color = interpolarColor(COLOR_VELOCIDAD_MIN, COLOR_VELOCIDAD_MAX, Math.min(1, Math.max(0, t)));
      L.polyline(
        [[a.lat, a.lon], [b.lat, b.lon]],
        { color, weight: 4, opacity: 0.9 }
      ).addTo(capa);
    }
  } else {
    const color = colorPorEquipo(equipoId);
    L.polyline(puntos.map((p) => [p.lat, p.lon]), { color, weight: 4, opacity: 0.85 }).addTo(capa);
  }

  const inicio = puntos[0];
  const fin = puntos[puntos.length - 1];
  L.circleMarker([inicio.lat, inicio.lon], {
    radius: 7,
    color: "#1b5e20",
    weight: 2,
    fillColor: "#66bb6a",
    fillOpacity: 1,
  })
    .bindTooltip("Inicio del rango")
    .addTo(capa);
  L.circleMarker([fin.lat, fin.lon], {
    radius: 7,
    color: "#7a1414",
    weight: 2,
    fillColor: "#e53935",
    fillOpacity: 1,
  })
    .bindTooltip("Fin del rango")
    .addTo(capa);

  capa.addTo(map);
  trazadosCapas.set(equipoId, capa);
}

function redibujarTodosLosTrazados() {
  for (const equipoId of trazadosDatos.keys()) {
    redibujarTrazado(equipoId);
  }
}

// Sincroniza el panel "Trazados" (visibilidad, selector de modo habilitado/
// deshabilitado, leyenda de velocidad, avisos) con el estado actual, y
// redibuja todos los trazados según corresponda. Es el único punto que
// decide si el modo velocidad puede seguir activo — se llama después de
// cualquier cambio (nuevo trazado, trazado quitado, click en el selector).
function actualizarSelectorModo() {
  const panel = document.getElementById("panel-trazados");
  const activos = trazadosDatos.size;
  if (panel) panel.hidden = activos === 0;

  const multiplesActivos = activos >= 2;
  if (multiplesActivos && modoColorTrazado === "velocidad") {
    // Con 2+ equipos trazados, el modo velocidad no aplica (¿de cuál
    // equipo sería la leyenda?) — se fuerza color fijo por equipo.
    modoColorTrazado = "equipo";
  }

  const btnVelocidad = document.querySelector('.modo-btn[data-modo="velocidad"]');
  if (btnVelocidad) btnVelocidad.disabled = multiplesActivos;

  document.querySelectorAll(".modo-btn").forEach((btn) => {
    btn.classList.toggle("modo-btn--activo", btn.dataset.modo === modoColorTrazado);
  });

  const datosUnico = activos === 1 ? trazadosDatos.values().next().value : null;
  const nota = document.getElementById("trazados-modo-nota");
  if (nota) {
    if (multiplesActivos) {
      nota.hidden = false;
      nota.textContent = "Modo \"por velocidad\" deshabilitado: solo puede usarse con un único equipo trazado a la vez.";
    } else if (activos === 1 && modoColorTrazado === "velocidad" && !puedeUsarModoVelocidad(datosUnico)) {
      nota.hidden = false;
      nota.textContent = "Este equipo no tiene datos de velocidad en el rango — se usa color fijo por equipo.";
    } else {
      nota.hidden = true;
    }
  }

  const leyenda = document.getElementById("trazados-leyenda-velocidad");
  if (leyenda) {
    const mostrarLeyenda = activos === 1 && modoColorTrazado === "velocidad" && puedeUsarModoVelocidad(datosUnico);
    leyenda.hidden = !mostrarLeyenda;
    if (mostrarLeyenda) {
      document.getElementById("leyenda-vel-min").textContent = `${datosUnico.velocidad_min.toFixed(1)} km/h`;
      document.getElementById("leyenda-vel-max").textContent = `${datosUnico.velocidad_max.toFixed(1)} km/h`;
    }
  }

  redibujarTodosLosTrazados();
}

function renderAvisosTrazado() {
  const lista = document.getElementById("trazados-avisos");
  if (!lista) return;
  lista.innerHTML = Array.from(avisosMuestreo.entries())
    .map(([equipoId, info]) => {
      const alias = (equiposEstado.get(equipoId) || {}).alias || `equipo ${equipoId}`;
      return `<li class="trazado-aviso-item">⚠ ${alias}: mostrando datos limitados (${info.mostrados} de ${info.total} puntos)</li>`;
    })
    .join("");
}

function quitarTrazado(equipoId) {
  limpiarCapaTrazado(equipoId);
  trazadosDatos.delete(equipoId);
  avisosMuestreo.delete(equipoId);
  renderAvisosTrazado();
  renderPanelEquipos(); // refresca el swatch de color del equipo en la lista
  actualizarSelectorModo();
}

function dibujarTrazado(equipoId, datos) {
  trazadosDatos.set(equipoId, datos);

  if (datos.muestreado) {
    avisosMuestreo.set(equipoId, { mostrados: datos.posiciones.length, total: datos.total_real });
  } else {
    avisosMuestreo.delete(equipoId);
  }
  renderAvisosTrazado();

  renderPanelEquipos();
  actualizarSelectorModo();
}

async function cargarYDibujarTrazado(equipoId, desdeIso, hastaIso) {
  const url = `${API_HISTORICO_URL(equipoId)}?desde=${encodeURIComponent(desdeIso)}&hasta=${encodeURIComponent(hastaIso)}`;
  let datos;
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      const detalle = resp.status === 400 ? " (rango de fechas inválido)" : "";
      throw new Error(`GET posiciones -> ${resp.status}${detalle}`);
    }
    datos = await resp.json();
  } catch (err) {
    console.error("No se pudo cargar el histórico de posiciones:", err);
    mostrarToast("No se pudo cargar el histórico de posiciones. Reintentá más tarde.");
    return;
  }

  if (!datos.posiciones || datos.posiciones.length === 0) {
    mostrarToast("Sin datos de posición en este rango");
    return;
  }

  dibujarTrazado(equipoId, datos);
}

document.getElementById("trazados-modo-selector").addEventListener("click", (event) => {
  const btn = event.target.closest(".modo-btn");
  if (!btn || btn.disabled) return;
  modoColorTrazado = btn.dataset.modo;
  actualizarSelectorModo();
});

// ---- Modal de filtros de rango de fechas ----

const RANGO_ATAJO_DEFAULT = "24h";

function calcularRangoAtajo(atajo) {
  const hasta = new Date();
  const MS_HORA = 60 * 60 * 1000;
  switch (atajo) {
    case "1h":
      return { desde: new Date(hasta.getTime() - MS_HORA), hasta };
    case "24h":
      return { desde: new Date(hasta.getTime() - 24 * MS_HORA), hasta };
    case "7d":
      return { desde: new Date(hasta.getTime() - 7 * 24 * MS_HORA), hasta };
    case "30d":
      return { desde: new Date(hasta.getTime() - 30 * 24 * MS_HORA), hasta };
    default:
      return null; // "personalizado": no se tocan los inputs
  }
}

function formatearParaInputLocal(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function marcarAtajoActivo(atajo) {
  document.querySelectorAll(".atajo-btn").forEach((btn) => {
    btn.classList.toggle("atajo-btn--activo", btn.dataset.atajo === atajo);
  });
}

function aplicarAtajo(atajo) {
  marcarAtajoActivo(atajo);
  const rango = calcularRangoAtajo(atajo);
  if (!rango) return; // "personalizado": el usuario edita los campos a mano
  document.getElementById("filtro-desde").value = formatearParaInputLocal(rango.desde);
  document.getElementById("filtro-hasta").value = formatearParaInputLocal(rango.hasta);
}

function abrirFiltroTrazado(equipoId) {
  filtroEquipoActivo = equipoId;
  const estado = equiposEstado.get(equipoId) || {};
  document.getElementById("filtro-trazado-titulo").textContent = `Trazado histórico — ${estado.alias || `equipo ${equipoId}`}`;
  aplicarAtajo(RANGO_ATAJO_DEFAULT);
  document.getElementById("modal-filtro-trazado").hidden = false;
}

async function cerrarFiltroTrazadoYDibujar() {
  const equipoId = filtroEquipoActivo;
  document.getElementById("modal-filtro-trazado").hidden = true;
  filtroEquipoActivo = null;
  if (equipoId == null) return;

  const desdeVal = document.getElementById("filtro-desde").value;
  const hastaVal = document.getElementById("filtro-hasta").value;
  if (!desdeVal || !hastaVal) return; // rango incompleto: no hay nada para pedir al backend

  const desde = new Date(desdeVal);
  const hasta = new Date(hastaVal);
  if (desde > hasta) {
    mostrarToast("El rango de fechas es inválido: 'desde' es posterior a 'hasta'.");
    return;
  }

  await cargarYDibujarTrazado(equipoId, desde.toISOString(), hasta.toISOString());
}

document.querySelector(".filtro-atajos").addEventListener("click", (event) => {
  const btn = event.target.closest(".atajo-btn");
  if (!btn) return;
  aplicarAtajo(btn.dataset.atajo);
});

["filtro-desde", "filtro-hasta"].forEach((id) => {
  document.getElementById(id).addEventListener("input", () => marcarAtajoActivo("personalizado"));
});

document.getElementById("filtro-limpiar").addEventListener("click", () => aplicarAtajo(RANGO_ATAJO_DEFAULT));
document.getElementById("filtro-trazado-cerrar").addEventListener("click", cerrarFiltroTrazadoYDibujar);

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
      // Trazabilidad histórica (ver sección "Trazados" más arriba): un
      // equipo con trazado activo muestra un swatch con su color fijo, y el
      // click sobre el <li> lo quita en vez de reabrir el modal de filtros.
      const tieneTrazado = trazadosDatos.has(equipo.id);
      const swatch = tieneTrazado
        ? `<span class="equipo-trazado-swatch" style="background:${colorPorEquipo(equipo.id)}"></span>`
        : "";
      const tituloClick = tieneTrazado ? "Click para quitar el trazado del mapa" : "Click para ver el trazado histórico";
      return `
        <li class="equipo-item${tieneTrazado ? " equipo-item--trazado" : ""}" data-equipo-id="${equipo.id}" title="${tituloClick}">
          <span class="equipo-dot ${online ? "equipo-dot--online" : ""}"></span>
          <span class="equipo-info">
            <div class="equipo-alias">${equipo.alias}</div>
            <div class="equipo-radio-id">${equipo.radio_id || "—"}</div>
            <div class="equipo-detalle">${tiempoRelativo(equipo.ultimo_visto)}${evento ? ` · ${evento}` : ""}</div>
          </span>
          ${swatch}
        </li>
      `;
    })
    .join("");
}

document.getElementById("lista-equipos").addEventListener("click", (event) => {
  const li = event.target.closest(".equipo-item");
  if (!li || !li.dataset.equipoId) return;
  const equipoId = Number(li.dataset.equipoId);
  if (trazadosDatos.has(equipoId)) {
    quitarTrazado(equipoId);
  } else {
    abrirFiltroTrazado(equipoId);
  }
});

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
