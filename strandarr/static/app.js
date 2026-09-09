const EMPTY = { type: "FeatureCollection", features: [] };
const RAMP_STEPS = 10;

const FIELDS = {
  wind: { max: 50, stops: ["#2ecc71", "#f1c40f", "#e74c3c"], flip: true },
  currents: { max: 3, stops: ["#74b9ff", "#6c5ce7", "#e84393"], flip: false },
};

const LAYERS = [
  { id: "wind", label: "Wind", color: "#f1c40f" },
  { id: "currents", label: "Currents", color: "#6c5ce7" },
  { id: "vessels", label: "Vessels", color: "#2b6cb0" },
  { id: "strandings", label: "Strandings", color: "#e63946" },
];

const FIELD_LABELS = {
  mmsi: "MMSI",
  ship_name: "Name",
  flag: "Flag",
  gear_type: "Gear",
  vessel_type: "Type",
  effort_hours: "Fishing hours",
  speed: "Speed",
  species_scientific: "Species",
  species_common: "Common name",
  individual_count: "Animals",
  recorded_at: "Recorded at",
  source: "Source",
  external_id: "Source id",
  lat: "Latitude",
  lon: "Longitude",
};

const UNITS = { speed: " km/h", effort_hours: " h" };

let gridStep = 1.0;

function lerpColor(a, b, t) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const mix = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${mix.join(",")})`;
}

function rampColor(stops, t) {
  const scaled = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(scaled));
  return lerpColor(stops[i], stops[i + 1], scaled - i);
}

function arrowImage(color, thick) {
  const size = 40;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.translate(size / 2, size / 2);
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  ctx.lineWidth = thick ? 7 : 4;
  ctx.lineCap = "round";
  const tail = thick ? 8 : 13;
  const head = thick ? 6 : 8;
  ctx.beginPath();
  ctx.moveTo(0, tail);
  ctx.lineTo(0, -tail + head);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0, -tail);
  ctx.lineTo(-head, -tail + head);
  ctx.lineTo(head, -tail + head);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.55)";
  ctx.lineWidth = 1;
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

function registerArrows(map) {
  for (const [id, field] of Object.entries(FIELDS)) {
    for (let i = 0; i < RAMP_STEPS; i++) {
      const color = rampColor(field.stops, i / (RAMP_STEPS - 1));
      map.addImage(`${id}-${i}`, arrowImage(color, id === "currents"));
    }
  }
}

function iconImageExpression(id, field) {
  const expr = ["step", ["get", "speed"], `${id}-0`];
  for (let i = 1; i < RAMP_STEPS; i++) {
    expr.push((field.max * i) / RAMP_STEPS, `${id}-${i}`);
  }
  return expr;
}

function toFeatureCollection(rows, offset = 0) {
  return {
    type: "FeatureCollection",
    features: rows.map((row) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [row.lon + offset, row.lat + offset] },
      properties: row,
    })),
  };
}

function floorHour(iso) {
  const d = new Date(iso);
  d.setUTCMinutes(0, 0, 0);
  return d;
}

const fmtHour = (d) => d.toISOString();
const fmtDay = (d) => d.toISOString().slice(0, 10);
const fmtLabel = (d) => d.toUTCString().slice(0, 22) + " UTC";

function fmtValue(key, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return Math.round(value * 100) / 100 + (UNITS[key] || "");
  return String(value);
}

function popupHtml(layerId, props) {
  const title = LAYERS.find((l) => l.id === layerId).label;
  const entries = Object.entries(props).filter(
    ([k, v]) => v !== null && v !== undefined && v !== "" && k !== "direction"
  );
  let rows = entries
    .map(([k, v]) => `<tr><th>${FIELD_LABELS[k] || k}</th><td>${fmtValue(k, v)}</td></tr>`)
    .join("");
  if (props.direction !== undefined && props.direction !== null) {
    const deg = Math.round(props.direction);
    const wording = layerId === "wind" ? `from ${deg}°` : `toward ${deg}°`;
    rows = `<tr><th>Direction</th><td>${wording}</td></tr>` + rows;
  }
  return `<div class="popup"><h4>${title}</h4><table>${rows}</table></div>`;
}

const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/positron",
  center: [1.75, 46.75],
  zoom: 5,
});

const label = document.getElementById("label");
const legend = document.getElementById("legend");
const track = document.getElementById("track");
const jump = document.getElementById("jump");

let selectedTick = null;
let hours = [];
const hidden = new Set();

function bindPopup(id) {
  map.on("click", id, (e) => {
    new maplibregl.Popup({ maxWidth: "320px" })
      .setLngLat(e.lngLat)
      .setHTML(popupHtml(id, e.features[0].properties))
      .addTo(map);
  });
  map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
}

function addFieldLayer(id) {
  const field = FIELDS[id];
  map.addSource(id, { type: "geojson", data: EMPTY });
  map.addLayer({
    id,
    type: "symbol",
    source: id,
    layout: {
      "icon-image": iconImageExpression(id, field),
      "icon-rotate": field.flip ? ["+", ["get", "direction"], 180] : ["get", "direction"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "icon-size": [
        "interpolate",
        ["linear"],
        ["get", "speed"],
        0,
        0.45,
        field.max,
        1.0,
      ],
    },
  });
  bindPopup(id);
}

function addCircleLayer(id, color, radius) {
  map.addSource(id, { type: "geojson", data: EMPTY });
  map.addLayer({
    id,
    type: "circle",
    source: id,
    paint: {
      "circle-radius": radius,
      "circle-color": color,
      "circle-stroke-width": 1,
      "circle-stroke-color": "#fff",
      "circle-opacity": 0.85,
    },
  });
  bindPopup(id);
}

function renderLegend(counts) {
  legend.innerHTML = LAYERS.map(
    (l) =>
      `<label class="legend-row"><input type="checkbox" data-layer="${l.id}"` +
      `${hidden.has(l.id) ? "" : " checked"}>` +
      `<span class="dot" style="background:${l.color}"></span>${l.label}` +
      `<span class="count">${counts[l.id] ?? "–"}</span></label>`
  ).join("");
  legend.querySelectorAll("input[data-layer]").forEach((input) => {
    input.addEventListener("change", () => {
      const id = input.dataset.layer;
      if (input.checked) hidden.delete(id);
      else hidden.add(id);
      map.setLayoutProperty(id, "visibility", input.checked ? "visible" : "none");
    });
  });
}

map.on("load", async () => {
  registerArrows(map);
  addFieldLayer("wind");
  addFieldLayer("currents");
  addCircleLayer("vessels", "#2b6cb0", 4);
  addCircleLayer("strandings", "#e63946", 7);
  renderLegend({});

  const range = await fetch("/api/range").then((r) => r.json());
  if (range.grid_step) gridStep = range.grid_step;
  if (!range.min || !range.max) {
    label.textContent = "No data ingested yet";
    return;
  }
  buildTimeline(floorHour(range.min), floorHour(range.max));
});

function buildTimeline(start, end) {
  hours = [];
  for (let d = new Date(start); d <= end; d.setUTCHours(d.getUTCHours() + 1)) {
    hours.push(new Date(d));
  }

  let hoursEl = null;
  let lastDay = null;
  for (const hour of hours) {
    const day = fmtDay(hour);
    if (day !== lastDay) {
      const dayEl = document.createElement("div");
      dayEl.className = "day";
      const dayLabel = document.createElement("div");
      dayLabel.className = "day-label";
      dayLabel.textContent = day;
      hoursEl = document.createElement("div");
      hoursEl.className = "hours";
      dayEl.appendChild(dayLabel);
      dayEl.appendChild(hoursEl);
      track.appendChild(dayEl);
      lastDay = day;
    }
    const tick = document.createElement("div");
    tick.className = "tick";
    tick.textContent = hour.getUTCHours();
    tick.dataset.hour = fmtHour(hour);
    tick.addEventListener("click", () => selectHour(hour, tick));
    hoursEl.appendChild(tick);
  }

  jump.min = fmtDay(hours[0]);
  jump.max = fmtDay(hours[hours.length - 1]);
  jump.addEventListener("change", () => {
    const target = hours.find((h) => fmtDay(h) === jump.value);
    if (target) {
      const tick = track.querySelector(`[data-hour="${fmtHour(target)}"]`);
      selectHour(target, tick);
      tick.scrollIntoView({ inline: "center", block: "nearest" });
    }
  });

  const last = hours[hours.length - 1];
  const lastTick = track.querySelector(`[data-hour="${fmtHour(last)}"]`);
  selectHour(last, lastTick);
  lastTick.scrollIntoView({ inline: "end", block: "nearest" });
}

async function selectHour(hour, tick) {
  if (selectedTick) selectedTick.classList.remove("selected");
  tick.classList.add("selected");
  selectedTick = tick;
  label.textContent = fmtLabel(hour);
  jump.value = fmtDay(hour);

  const hourParam = encodeURIComponent(fmtHour(hour));
  const day = fmtDay(hour);
  const [vessels, wind, currents, strandings] = await Promise.all([
    fetch(`/api/vessels?hour=${hourParam}`).then((r) => r.json()),
    fetch(`/api/wind?hour=${hourParam}`).then((r) => r.json()),
    fetch(`/api/currents?hour=${hourParam}`).then((r) => r.json()),
    fetch(`/api/strandings?day=${day}`).then((r) => r.json()),
  ]);

  map.getSource("vessels").setData(toFeatureCollection(vessels));
  map.getSource("wind").setData(toFeatureCollection(wind));
  map.getSource("currents").setData(toFeatureCollection(currents, gridStep / 2));
  map.getSource("strandings").setData(toFeatureCollection(strandings));
  renderLegend({
    vessels: vessels.length,
    wind: wind.length,
    currents: currents.length,
    strandings: strandings.length,
  });
}
