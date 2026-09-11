const EMPTY = { type: "FeatureCollection", features: [] };
const RAMP_STEPS = 10;

const CONDITION_LAYERS = [
  {
    id: "wind",
    label: "Wind",
    color: "#f1c40f",
    speed: "wind_speed_kmh",
    direction: "wind_direction_deg",
    max: 70,
    from: true,
    stops: ["#2ecc71", "#f1c40f", "#e74c3c"],
    fields: ["wind_speed_kmh", "wind_direction_deg"],
  },
  {
    id: "currents",
    label: "Currents",
    color: "#6c5ce7",
    speed: "current_speed_kmh",
    direction: "current_direction_deg",
    max: 8,
    from: false,
    thick: true,
    stops: ["#74b9ff", "#6c5ce7", "#e84393"],
    fields: ["current_speed_kmh", "current_direction_deg"],
  },
  {
    id: "waves",
    label: "Waves",
    color: "#22a6b3",
    speed: "wave_height_m",
    direction: "wave_direction_deg",
    max: 8,
    from: false,
    stops: ["#dff9fb", "#22a6b3", "#130f40"],
    fields: [
      "wave_height_m",
      "wave_direction_deg",
      "wave_period_s",
      "swell_height_m",
      "swell_direction_deg",
      "swell_period_s",
      "sea_surface_temperature_c",
      "sea_level_m",
    ],
  },
];

const POINT_LAYERS = [
  { id: "vessels", label: "Vessels", color: "#2b6cb0", radius: 4 },
  { id: "strandings", label: "Strandings", color: "#e63946", radius: 7 },
];

const LAYERS = [...CONDITION_LAYERS, ...POINT_LAYERS];

const FIELD_LABELS = {
  mmsi: "MMSI",
  ship_name: "Name",
  flag: "Flag",
  gear_type: "Gear",
  vessel_type: "Type",
  effort_hours: "Fishing hours",
  species_scientific: "Species",
  species_common: "Common name",
  individual_count: "Animals",
  recorded_at: "Recorded at",
  time_uncertainty_hours: "Time uncertainty",
  coordinate_uncertainty_m: "Position uncertainty",
  location_precision: "Position from",
  source: "Source",
  sources: "Sources",
  external_id: "Source id",
  lat: "Latitude",
  lon: "Longitude",
  wind_speed_kmh: "Wind speed",
  wind_direction_deg: "Wind direction",
  current_speed_kmh: "Current speed",
  current_direction_deg: "Current direction",
  wave_height_m: "Wave height",
  wave_direction_deg: "Wave direction",
  wave_period_s: "Wave period",
  swell_height_m: "Swell height",
  swell_direction_deg: "Swell direction",
  swell_period_s: "Swell period",
  sea_surface_temperature_c: "Sea temperature",
  sea_level_m: "Tide height",
};

const UNITS = {
  wind_speed_kmh: " km/h",
  current_speed_kmh: " km/h",
  wave_height_m: " m",
  swell_height_m: " m",
  wave_period_s: " s",
  swell_period_s: " s",
  effort_hours: " h",
  time_uncertainty_hours: " h",
  coordinate_uncertainty_m: " m",
  sea_surface_temperature_c: " °C",
  sea_level_m: " m",
};

const DIRECTION_SENSE = {
  wind_direction_deg: "from",
  current_direction_deg: "toward",
  wave_direction_deg: "toward",
  swell_direction_deg: "toward",
};

function lerpColor(a, b, t) {
  const from = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const to = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const mix = from.map((v, i) => Math.round(v + (to[i] - v) * t));
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
  for (const layer of CONDITION_LAYERS) {
    for (let i = 0; i < RAMP_STEPS; i++) {
      const color = rampColor(layer.stops, i / (RAMP_STEPS - 1));
      map.addImage(`${layer.id}-${i}`, arrowImage(color, layer.thick));
    }
  }
}

function iconImageExpression(layer) {
  const expression = ["step", ["get", layer.speed], `${layer.id}-0`];
  for (let i = 1; i < RAMP_STEPS; i++) {
    expression.push((layer.max * i) / RAMP_STEPS, `${layer.id}-${i}`);
  }
  return expression;
}

function toFeatureCollection(rows) {
  return {
    type: "FeatureCollection",
    features: rows.map((row) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [row.lon, row.lat] },
      properties: row,
    })),
  };
}

function conditionRows(rows, layer) {
  return rows
    .filter((row) => row[layer.speed] !== null && row[layer.direction] !== null)
    .map((row) => {
      const picked = { lat: row.lat, lon: row.lon, sources: row.sources };
      for (const field of layer.fields) picked[field] = row[field];
      return picked;
    });
}

function floorHour(iso) {
  const at = new Date(iso);
  at.setUTCMinutes(0, 0, 0);
  return at;
}

const fmtHour = (at) => at.toISOString();
const fmtDay = (at) => at.toISOString().slice(0, 10);
const fmtLabel = (at) => at.toUTCString().slice(0, 22) + " UTC";

function fmtValue(key, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.join(", ");
  if (key in DIRECTION_SENSE) {
    return `${DIRECTION_SENSE[key]} ${Math.round(value)}°`;
  }
  if (typeof value === "number") {
    return Math.round(value * 100) / 100 + (UNITS[key] || "");
  }
  return String(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function popupHtml(layerId, props) {
  const title = LAYERS.find((layer) => layer.id === layerId).label;
  const rows = Object.entries(props)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(
      ([key, value]) =>
        `<tr><th>${escapeHtml(FIELD_LABELS[key] || key)}</th>` +
        `<td>${escapeHtml(fmtValue(key, value))}</td></tr>`
    )
    .join("");
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
let pendingHour = 0;
const hidden = new Set();

function bindPopup(id) {
  map.on("click", id, (event) => {
    new maplibregl.Popup({ maxWidth: "320px" })
      .setLngLat(event.lngLat)
      .setHTML(popupHtml(id, event.features[0].properties))
      .addTo(map);
  });
  map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
}

function addArrowLayer(layer) {
  map.addSource(layer.id, { type: "geojson", data: EMPTY });
  map.addLayer({
    id: layer.id,
    type: "symbol",
    source: layer.id,
    layout: {
      "icon-image": iconImageExpression(layer),
      "icon-rotate": layer.from
        ? ["+", ["get", layer.direction], 180]
        : ["get", layer.direction],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "icon-size": [
        "interpolate",
        ["linear"],
        ["get", layer.speed],
        0,
        0.45,
        layer.max,
        1.0,
      ],
    },
  });
  bindPopup(layer.id);
}

function addCircleLayer(layer) {
  map.addSource(layer.id, { type: "geojson", data: EMPTY });
  map.addLayer({
    id: layer.id,
    type: "circle",
    source: layer.id,
    paint: {
      "circle-radius": layer.radius,
      "circle-color": layer.color,
      "circle-stroke-width": 1,
      "circle-stroke-color": "#fff",
      "circle-opacity": 0.85,
    },
  });
  bindPopup(layer.id);
}

function renderLegend(counts) {
  legend.innerHTML = LAYERS.map(
    (layer) =>
      `<label class="legend-row"><input type="checkbox" data-layer="${layer.id}"` +
      `${hidden.has(layer.id) ? "" : " checked"}>` +
      `<span class="dot" style="background:${layer.color}"></span>${layer.label}` +
      `<span class="count">${counts[layer.id] ?? "–"}</span></label>`
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
  CONDITION_LAYERS.forEach(addArrowLayer);
  POINT_LAYERS.forEach(addCircleLayer);
  renderLegend({});

  const range = await fetch("/api/range").then((response) => response.json());
  if (!range.min || !range.max) {
    label.textContent = "No data ingested yet";
    return;
  }
  buildTimeline(floorHour(range.min), floorHour(range.max));
});

function buildTimeline(start, end) {
  hours = [];
  for (let at = new Date(start); at <= end; at.setUTCHours(at.getUTCHours() + 1)) {
    hours.push(new Date(at));
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
    const target = hours.find((hour) => fmtDay(hour) === jump.value);
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

  const at = encodeURIComponent(fmtHour(hour));
  const request = ++pendingHour;
  const [conditions, vessels, strandings] = await Promise.all([
    fetch(`/api/conditions?at=${at}`).then((response) => response.json()),
    fetch(`/api/vessels?at=${at}`).then((response) => response.json()),
    fetch(`/api/strandings?day=${fmtDay(hour)}`).then((response) => response.json()),
  ]);
  if (request !== pendingHour) return;

  const counts = { vessels: vessels.length, strandings: strandings.length };
  for (const layer of CONDITION_LAYERS) {
    const rows = conditionRows(conditions, layer);
    map.getSource(layer.id).setData(toFeatureCollection(rows));
    counts[layer.id] = rows.length;
  }
  map.getSource("vessels").setData(toFeatureCollection(vessels));
  map.getSource("strandings").setData(toFeatureCollection(strandings));
  renderLegend(counts);
}
