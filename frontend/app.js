// Puerto donde el backend expone la API/WebSocket (debe coincidir con
// BACKEND_PORT en el .env usado por docker-compose).
const BACKEND_WS_PORT = 8000;
const WS_URL = `ws://${window.location.hostname}:${BACKEND_WS_PORT}/ws/telemetry`;
const API_EQUIPOS_URL = `http://${window.location.hostname}:${BACKEND_WS_PORT}/api/equipos`;
const API_AUDIO_URL = `http://${window.location.hostname}:${BACKEND_WS_PORT}/api/audio-eventos`;

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

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

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
  return `
    <li class="audio-item ${estadoClase}" data-id="${evento.id}">
      <div class="audio-info">
        <span class="audio-alias">${alias} ${radioId}</span>
        <span class="audio-detalle">${formatearHora(evento.timestamp_inicio)} · ${formatearDuracion(evento.duracion_seg)}</span>
      </div>
      <audio class="audio-player" controls preload="none" data-id="${evento.id}" src="${API_AUDIO_URL}/${evento.id}/file"></audio>
    </li>
  `;
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

  if (eventos.length === 0) {
    lista.innerHTML = '<li class="lista-audio-vacio">Sin clips todavía</li>';
    return;
  }

  lista.innerHTML = eventos.map(construirItemAudioHTML).join("");
  document.querySelectorAll(".audio-player").forEach((audioEl) => {
    audioEl.addEventListener("play", () => marcarEscuchado(Number(audioEl.dataset.id)), { once: true });
  });
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

  const lista = document.getElementById("lista-audio");
  const vacio = lista.querySelector(".lista-audio-vacio");
  if (vacio) vacio.remove();

  lista.insertAdjacentHTML("afterbegin", construirItemAudioHTML(evento));
  const audioEl = lista.querySelector(`.audio-player[data-id="${evento.id}"]`);
  if (audioEl) {
    audioEl.addEventListener("play", () => marcarEscuchado(evento.id), { once: true });
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
  const equipos = Array.from(equiposEstado.values()).sort((a, b) =>
    (a.alias || "").localeCompare(b.alias || "")
  );

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

cargarEquiposIniciales();
cargarAudioEventosIniciales();
conectar();

// Recalcula tiempos relativos y estado online/offline aunque no lleguen
// mensajes nuevos (si no, un equipo queda "online" para siempre en pantalla
// una vez visto, hasta el próximo evento).
setInterval(renderPanelEquipos, 30000);
