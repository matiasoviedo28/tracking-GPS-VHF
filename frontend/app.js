// Puerto donde el backend expone /ws/telemetry (debe coincidir con
// BACKEND_PORT en el .env usado por docker-compose).
const BACKEND_WS_PORT = 8000;
const WS_URL = `ws://${window.location.hostname}:${BACKEND_WS_PORT}/ws/telemetry`;

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

function conectar() {
  const ws = new WebSocket(WS_URL);

  ws.onopen = () => setEstado("connected", "Conectado al backend");

  ws.onmessage = (event) => {
    const posicion = JSON.parse(event.data);
    actualizarMarcador(posicion);
  };

  ws.onclose = () => {
    setEstado("disconnected", "Desconectado — reintentando…");
    setTimeout(conectar, 3000);
  };

  ws.onerror = () => ws.close();
}

conectar();
