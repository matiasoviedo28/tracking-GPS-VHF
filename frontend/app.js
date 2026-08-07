// Puerto donde el backend expone la API/WebSocket (debe coincidir con
// BACKEND_PORT en el .env usado por docker-compose).
const BACKEND_WS_PORT = 8000;
const WS_URL = `ws://${window.location.hostname}:${BACKEND_WS_PORT}/ws/telemetry`;
const API_EQUIPOS_URL = `http://${window.location.hostname}:${BACKEND_WS_PORT}/api/equipos`;

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

// Centro inicial: Merlo, San Luis, Argentina.
const CENTRO_MERLO = [-32.3436, -65.0128];

const map = L.map("map").setView(CENTRO_MERLO, 13);

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

function construirPopup(posicion) {
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
  return partes.join("<br>");
}

function actualizarMarcador(posicion) {
  const posLatLng = [posicion.lat, posicion.lon];
  let marcador = marcadores.get(posicion.equipo_id);

  if (!marcador) {
    marcador = L.marker(posLatLng).addTo(map);
    marcadores.set(posicion.equipo_id, marcador);
  } else {
    marcador.setLatLng(posLatLng);
  }

  marcador.bindPopup(construirPopup(posicion));
}

function setEstado(clase, texto) {
  const el = document.getElementById("status");
  el.className = `status status--${clase}`;
  el.textContent = texto;
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
        ultimo_visto: equipo.ultimo_visto ? new Date(equipo.ultimo_visto) : null,
        ultimo_evento: equipo.ultimo_evento,
      });
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
  actualizarMarcador(datos);

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

function conectar() {
  const ws = new WebSocket(WS_URL);

  ws.onopen = () => setEstado("connected", "Conectado al backend");

  ws.onmessage = (event) => {
    const datos = JSON.parse(event.data);
    if (datos.type === "presence_update") {
      manejarPresenceUpdate(datos);
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
conectar();

// Recalcula tiempos relativos y estado online/offline aunque no lleguen
// mensajes nuevos (si no, un equipo queda "online" para siempre en pantalla
// una vez visto, hasta el próximo evento).
setInterval(renderPanelEquipos, 30000);
