const EMPTY = { type: "FeatureCollection", features: [] };
const RAMP_STEPS = 10;

const RISK_MAX = 0.08;

const RISK = {
  id: "risk",
  label: "Stranding risk",
  color: "#b91c1c",
  property: "probability",
  domain: RISK_MAX,
  stops: ["#1e3a8a", "#2563eb", "#22d3ee", "#facc15", "#f97316", "#b91c1c"],
};

const SEA = {
  id: "sea",
  label: "Sea state (waves)",
  color: "#0369a1",
  speed: "wave_height_m",
  direction: "wave_direction_deg",
  from: false,
  max: 6,
  stops: ["#bae6fd", "#38bdf8", "#0369a1", "#1e1b4b"],
};

const POINTS = [
  { id: "vessels", label: "Fishing vessels", color: "#0f766e", radius: 4 },
  { id: "strandings", label: "Strandings", color: "#7c3aed", radius: 6 },
];

const LAYERS = [RISK, SEA, ...POINTS];

const FIELD_LABELS = {
  probability: "Probability",
  seasonal: "Seasonal baseline",
  relative_index: "Relative to peak",
  persistence: "Recent nearby strandings",
  drift_index: "Drift arrivals",
  swell_m: "Swell height",
  wave_m: "Wave height",
  onshore_m: "Onshore wave push",
  period_s: "Wave period",
  source: "Computed from",
  segment_id: "Segment",
  length_km: "Segment length",
  mmsi: "MMSI",
  ship_name: "Vessel",
  flag: "Flag",
  gear_type: "Gear",
  vessel_type: "Type",
  effort_hours: "Fishing effort",
  recorded_at: "Recorded",
  species_scientific: "Species",
  species_common: "Nom commun",
  individual_count: "Individuals",
  time_uncertainty_hours: "Time uncertainty",
  coordinate_uncertainty_m: "Position uncertainty",
  location_precision: "Position from",
  external_id: "Source id",
  wave_height_m: "Wave height",
  wave_direction_deg: "Wave direction",
  wave_period_s: "Wave period",
  swell_height_m: "Swell height",
  wind_speed_kmh: "Wind speed",
  wind_direction_deg: "Wind direction",
  current_speed_kmh: "Current speed",
  current_direction_deg: "Current direction",
  forecast: "Forecast",
};

const UNITS = {
  length_km: "km",
  effort_hours: "h",
  time_uncertainty_hours: "h",
  coordinate_uncertainty_m: "m",
  wave_height_m: "m",
  swell_height_m: "m",
  wave_m: "m",
  swell_m: "m",
  onshore_m: "m",
  period_s: "s",
  wave_period_s: "s",
  wind_speed_kmh: "km/h",
  current_speed_kmh: "km/h",
  wave_direction_deg: "°",
  wind_direction_deg: "°",
  current_direction_deg: "°",
  relative_index: "%",
};

const PERCENT = new Set(["probability", "seasonal"]);
const PRECISE = new Set(["drift_index", "persistence"]);

const POPUP_FIELDS = {
  risk: [
    "probability", "relative_index", "seasonal", "persistence", "drift_index",
    "swell_m", "wave_m", "onshore_m", "period_s", "source", "segment_id",
  ],
  sea: [
    "wave_height_m", "wave_direction_deg", "wave_period_s", "swell_height_m",
    "wind_speed_kmh", "wind_direction_deg", "current_speed_kmh",
    "current_direction_deg", "forecast",
  ],
  vessels: [
    "ship_name", "mmsi", "flag", "gear_type", "vessel_type", "effort_hours",
    "recorded_at", "source",
  ],
  strandings: [
    "species_common", "species_scientific", "individual_count", "recorded_at",
    "time_uncertainty_hours", "coordinate_uncertainty_m", "location_precision",
    "source", "external_id",
  ],
};

function lerpColor(a, b, t) {
  const parse = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const [ar, ag, ab] = parse(a);
  const [br, bg, bb] = parse(b);
  const mix = (x, y) => Math.round(x + (y - x) * t);
  return `rgb(${mix(ar, br)},${mix(ag, bg)},${mix(ab, bb)})`;
}

function rampColor(stops, t) {
  const span = 1 / (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(t / span));
  return lerpColor(stops[index], stops[index + 1], (t - index * span) / span);
}

const floorHour = (iso) => {
  const at = new Date(iso);
  at.setUTCMinutes(0, 0, 0);
  return at;
};

const fmtHour = (at) => at.toISOString();
const fmtDay = (at) => at.toISOString().slice(0, 10);
const fmtLabel = (at) => at.toUTCString().slice(0, 22) + " UTC";

function fmtValue(key, value) {
  if (value === null || value === undefined || value === "") return "–";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value !== "number") return String(value);
  if (PERCENT.has(key)) return (value * 100).toFixed(3) + " %";
  const unit = UNITS[key] ? ` ${UNITS[key]}` : "";
  if (PRECISE.has(key)) return value.toPrecision(3) + unit;
  return (Math.abs(value) >= 100 ? value.toFixed(0) : value.toFixed(2)) + unit;
}

function escapeHtml(value) {
  return String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function popupHtml(layerId, props) {
  const fields = POPUP_FIELDS[layerId] ?? Object.keys(props);
  const rows = fields
    .filter((key) => props[key] !== undefined)
    .map(
      (key) =>
        `<tr><th>${escapeHtml(FIELD_LABELS[key] ?? key)}</th>` +
        `<td>${escapeHtml(fmtValue(key, props[key]))}</td></tr>`
    )
    .join("");
  return `<table class="popup">${rows}</table>`;
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

function arrowImage(color) {
  const size = 40;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.translate(size / 2, size / 2);
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(0, 13);
  ctx.lineTo(0, -5);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0, -13);
  ctx.lineTo(-8, -5);
  ctx.lineTo(8, -5);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.55)";
  ctx.lineWidth = 1;
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

function iconImageExpression(layer) {
  const expression = ["step", ["get", layer.speed], `${layer.id}-0`];
  for (let i = 1; i < RAMP_STEPS; i++) {
    expression.push((layer.max * i) / RAMP_STEPS, `${layer.id}-${i}`);
  }
  return expression;
}

function rampExpression(layer) {
  const expression = ["interpolate", ["linear"], ["get", layer.property]];
  for (let i = 0; i < layer.stops.length; i++) {
    expression.push((layer.domain * i) / (layer.stops.length - 1), layer.stops[i]);
  }
  return expression;
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
const hidden = new Set(["sea"]);

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

function addLayer(layer, spec) {
  map.addSource(layer.id, { type: "geojson", data: EMPTY });
  map.addLayer({
    id: layer.id,
    source: layer.id,
    ...spec,
    layout: {
      ...(spec.layout ?? {}),
      visibility: hidden.has(layer.id) ? "none" : "visible",
    },
  });
  bindPopup(layer.id);
}

function addRiskLayer() {
  addLayer(RISK, {
    type: "line",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": rampExpression(RISK),
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 3, 11, 9],
      "line-opacity": 0.9,
    },
  });
}

function addSeaLayer() {
  addLayer(SEA, {
    type: "symbol",
    layout: {
      "icon-image": iconImageExpression(SEA),
      "icon-rotate": ["get", SEA.direction],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "icon-size": [
        "interpolate", ["linear"], ["get", SEA.speed], 0, 0.45, SEA.max, 1.0,
      ],
    },
  });
}

function addPointLayer(layer) {
  addLayer(layer, {
    type: "circle",
    paint: {
      "circle-radius": layer.radius,
      "circle-color": layer.color,
      "circle-stroke-width": 1,
      "circle-stroke-color": "#fff",
      "circle-opacity": 0.85,
    },
  });
}

function renderLegend(counts, notes = {}) {
  legend.innerHTML = LAYERS.map(
    (layer) =>
      `<label class="legend-row"${notes[layer.id] ? ` title="${escapeHtml(notes[layer.id])}"` : ""}>` +
      `<input type="checkbox" data-layer="${layer.id}"` +
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

const fetchJson = (url) => fetch(url).then((response) => response.json());

async function snapshot(hour) {
  const at = encodeURIComponent(fmtHour(hour));
  const day = fmtDay(hour);
  const [conditions, vessels, strandings, risk] = await Promise.all([
    fetchJson(`/api/conditions?at=${at}`),
    fetchJson(`/api/vessels?at=${at}`),
    fetchJson(`/api/strandings?day=${day}`),
    fetchJson(`/api/risk?day=${day}`),
  ]);
  return { conditions, vessels, strandings, risk };
}

function riskNote(risk) {
  const forecast = (risk.features ?? []).some(
    (feature) => feature.properties.source === "forecast"
  );
  return forecast ? { risk: "Computed from forecast conditions" } : {};
}

function toggleDayExpanded(dayEl) {
  const willExpand = !dayEl.classList.contains("expanded");
  track.querySelectorAll(".day.expanded").forEach((d) => d.classList.remove("expanded"));
  if (willExpand) dayEl.classList.add("expanded");
}

function buildTimeline(start, end) {
  hours = [];
  for (let at = new Date(start); at <= end; at.setUTCHours(at.getUTCHours() + 1)) {
    hours.push(new Date(at));
  }

  const now = new Date();
  const today = fmtDay(now);
  let hoursEl = null;
  let lastDay = null;
  for (const hour of hours) {
    const day = fmtDay(hour);
    if (day !== lastDay) {
      const dayEl = document.createElement("div");
      dayEl.className = "day";
      if (day > today) dayEl.classList.add("future");
      const dayLabel = document.createElement("div");
      dayLabel.className = "day-label";
      dayLabel.textContent = day;
      dayEl.addEventListener("click", (event) => {
        if (event.target.closest(".tick")) return;
        toggleDayExpanded(dayEl);
      });
      hoursEl = document.createElement("div");
      hoursEl.className = "hours";
      dayEl.appendChild(dayLabel);
      dayEl.appendChild(hoursEl);
      track.appendChild(dayEl);
      lastDay = day;
    }
    const tick = document.createElement("div");
    tick.className = "tick";
    if (hour > now) tick.classList.add("future");
    tick.textContent = hour.getUTCHours();
    tick.dataset.hour = fmtHour(hour);
    tick.addEventListener("click", () => selectHour(hour, tick));
    hoursEl.appendChild(tick);
  }

  jump.min = fmtDay(hours[0]);
  jump.max = fmtDay(hours[hours.length - 1]);
  jump.addEventListener("change", () => {
    const target = hours.find((hour) => fmtDay(hour) === jump.value);
    if (!target) return;
    const tick = track.querySelector(`[data-hour="${fmtHour(target)}"]`);
    selectHour(target, tick);
    tick.scrollIntoView({ inline: "center", block: "nearest" });
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

  const dayEl = tick.closest(".day");
  for (const name of ["selected", "expanded"]) {
    track.querySelectorAll(`.day.${name}`).forEach((d) => {
      if (d !== dayEl) d.classList.remove(name);
    });
  }
  dayEl.classList.add("selected", "expanded");

  label.textContent = fmtLabel(hour);
  jump.value = fmtDay(hour);

  const request = ++pendingHour;
  const data = await snapshot(hour);
  if (request !== pendingHour) return;

  map.getSource(RISK.id).setData(data.risk);
  const sea = data.conditions.filter(
    (row) => row[SEA.speed] !== null && row[SEA.direction] !== null
  );
  map.getSource(SEA.id).setData(toFeatureCollection(sea));
  for (const layer of POINTS) {
    map.getSource(layer.id).setData(toFeatureCollection(data[layer.id]));
  }

  renderLegend(
    {
      risk: data.risk.features.length,
      sea: sea.length,
      vessels: data.vessels.length,
      strandings: data.strandings.length,
    },
    riskNote(data.risk)
  );
}

map.on("load", async () => {
  for (let i = 0; i < RAMP_STEPS; i++) {
    map.addImage(`${SEA.id}-${i}`, arrowImage(rampColor(SEA.stops, i / (RAMP_STEPS - 1))));
  }
  addRiskLayer();
  addSeaLayer();
  POINTS.forEach(addPointLayer);
  renderLegend({});

  const range = await fetchJson("/api/range");
  if (!range.min || !range.max) {
    label.textContent = "No data ingested yet";
    return;
  }
  buildTimeline(floorHour(range.min), floorHour(range.max));
});
