const EMPTY = { type: "FeatureCollection", features: [] };
const RAMP_STEPS = 10;

const RISK = {
  id: "risk",
  label: "Stranding index",
  color: "#b91c1c",
  property: "index",
  domain: 100,
  stops: ["#f2f3f0", "#fcd9c4", "#f4976c", "#d8482f", "#b91c1c"],
};

const CONDITIONS = [
  {
    id: "wind",
    label: "Wind",
    color: "#f1c40f",
    speed: "wind_speed_kmh",
    direction: "wind_direction_deg",
    from: true,
    max: 70,
    stops: ["#2ecc71", "#f1c40f", "#e74c3c"],
    fields: ["wind_speed_kmh", "wind_direction_deg"],
  },
  {
    id: "currents",
    label: "Currents",
    color: "#6c5ce7",
    speed: "current_speed_kmh",
    direction: "current_direction_deg",
    from: false,
    thick: true,
    max: 8,
    stops: ["#74b9ff", "#6c5ce7", "#e84393"],
    fields: ["current_speed_kmh", "current_direction_deg"],
  },
  {
    id: "sea",
    label: "Sea state (waves)",
    color: "#0369a1",
    speed: "wave_height_m",
    direction: "wave_direction_deg",
    from: false,
    max: 6,
    stops: ["#bae6fd", "#38bdf8", "#0369a1", "#1e1b4b"],
    fields: [
      "wave_height_m",
      "wave_direction_deg",
      "wave_period_s",
      "swell_height_m",
      "swell_direction_deg",
      "swell_period_s",
      "sea_surface_temperature_c",
      "sea_level_m",
      "forecast",
    ],
  },
];

const POINTS = [
  { id: "vessels", label: "Fishing vessels", color: "#0058aa", radius: 4 },
  { id: "strandings", label: "Strandings", color: "#bd0000", radius: 6 },
];

const LAYERS = [RISK, ...CONDITIONS, ...POINTS];

const FIELD_LABELS = {
  index: "Stranding index",
  probability: "Probability",
  seasonal: "Seasonal baseline",
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
  swell_direction_deg: "Swell direction",
  swell_period_s: "Swell period",
  wind_speed_kmh: "Wind speed",
  wind_direction_deg: "Wind direction",
  current_speed_kmh: "Current speed",
  current_direction_deg: "Current direction",
  sea_surface_temperature_c: "Sea temperature",
  sea_level_m: "Tide height",
  forecast: "Forecast",
};

const UNITS = {
  index: "/ 100",
  length_km: "km",
  effort_hours: "h",
  time_uncertainty_hours: "h",
  coordinate_uncertainty_m: "m",
  wave_height_m: "m",
  swell_height_m: "m",
  sea_level_m: "m",
  wave_m: "m",
  swell_m: "m",
  onshore_m: "m",
  period_s: "s",
  wave_period_s: "s",
  swell_period_s: "s",
  wind_speed_kmh: "km/h",
  current_speed_kmh: "km/h",
  sea_surface_temperature_c: "°C",
};

const DIRECTION_SENSE = {
  wind_direction_deg: "from",
  current_direction_deg: "toward",
  wave_direction_deg: "toward",
  swell_direction_deg: "toward",
};

const PERCENT = new Set(["probability", "seasonal"]);
const PRECISE = new Set(["drift_index", "persistence"]);
const WHOLE = new Set(["index"]);

const POPUP_FIELDS = {
  risk: [
    "index", "probability", "seasonal", "persistence", "drift_index",
    "swell_m", "wave_m", "onshore_m", "period_s", "source", "segment_id",
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
for (const layer of CONDITIONS) POPUP_FIELDS[layer.id] = layer.fields;

function lerpColor(a, b, t) {
  const parse = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const [ar, ag, ab] = parse(a);
  const [br, bg, bb] = parse(b);
  const mix = (x, y) => Math.round(x + (y - x) * t);
  return `rgb(${mix(ar, br)},${mix(ag, bg)},${mix(ab, bb)})`;
}

function rampColor(stops, t) {
  const scaled = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  return lerpColor(stops[index], stops[index + 1], scaled - index);
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
  if (key in DIRECTION_SENSE) return `${DIRECTION_SENSE[key]} ${Math.round(value)}°`;
  const unit = UNITS[key] ? ` ${UNITS[key]}` : "";
  if (WHOLE.has(key)) return Math.round(value) + unit;
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
  let fields = POPUP_FIELDS[layerId] ?? Object.keys(props);
  if (layerId === RISK.id && !riskFitted) {
    // Unfitted, the probability IS the seasonal baseline. Showing the same
    // number twice under two names reads like a coincidence rather than a state.
    fields = fields.filter((key) => key !== "probability");
  }
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

function conditionRows(rows, layer) {
  return rows
    .filter((row) => row[layer.speed] !== null && row[layer.direction] !== null)
    .map((row) => {
      const picked = { lat: row.lat, lon: row.lon };
      for (const field of layer.fields) picked[field] = row[field];
      return picked;
    });
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

const DATA_SOURCES = [
  {
    text: "Strandings - Observatoire Pelagis (CNRS / La Rochelle Université), " +
      "national stranding network.",
    href: "https://www.observatoire-pelagis.cnrs.fr/",
  },
  {
    text: "Fishing vessels - Global Fishing Watch (historical) and live AIS via " +
      "aisstream.io.",
    href: "https://globalfishingwatch.org/",
  },
  {
    text: "Sea and weather conditions - Open-Meteo (ERA5 reanalysis and marine " +
      "forecast).",
    href: "https://open-meteo.com/",
  },
  {
    text: "Stranding index - drift simulation, fitted against observed " +
      "strandings.",
  },
];

const FACTS = [
  {
    value: "1,000+",
    label: "strandings recorded each year in France",
    href: "https://www.observatoire-pelagis.cnrs.fr/echouages/reseau-national-echouage/",
  },
  {
    value: "3,005",
    label: "marine mammals stranded in mainland France, 2023",
    href: "https://www.observatoire-pelagis.cnrs.fr/wp-content/uploads/2024/12/rapport_echouage_2023.pdf",
  },
  {
    value: "86%",
    label: "of examined stranded cetaceans showed signs of bycatch",
    href: "https://www.observatoire-pelagis.cnrs.fr/wp-content/uploads/2022/11/Rapport_CAPECET_DEB_2021.pdf",
  },
  {
    value: "82%",
    label: "of dead dolphins sink before ever reaching shore",
    href: "https://www.observatoire-pelagis.cnrs.fr/situation-preoccupante-pour-les-dauphins-communs-du-golfe-de-gascogne/",
  },
];

const MONTH_LABELS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

// Fixed-order categorical palette (dark-surface steps), one hue per year.
const YEAR_COLORS = [
  "#3987e5", "#d95926", "#199e70", "#c98500",
  "#d55181", "#008300", "#9085e9", "#e66767",
];
const EARLIER_COLOR = "#8a8a8a";
const MAX_YEAR_SERIES = 8;

function buildChartLayout(byYear) {
  const width = 400;
  const height = 190;
  const topPad = 16;
  const bottomPad = 34;
  const padX = 8;
  const plotHeight = height - topPad - bottomPad;
  const innerWidth = width - 2 * padX;
  const step = innerWidth / (MONTH_LABELS.length - 1);
  const baseline = topPad + plotHeight;
  const xAt = (i) => padX + i * step;
  const max = Math.max(
    1,
    ...byYear.flatMap((row) => row.months.filter((value) => value !== null))
  );
  const yAt = (value) => baseline - (value / max) * plotHeight;

  // Beyond MAX_YEAR_SERIES individually colored lines, the oldest years fold
  // into one muted "earlier years" bucket so the chart doesn't turn into an
  // unreadable rainbow as history grows. The bucket itself counts as one of
  // the MAX_YEAR_SERIES groups, so at most (MAX_YEAR_SERIES - 1) years get
  // their own hue once bucketing kicks in.
  const recentCount =
    byYear.length > MAX_YEAR_SERIES ? MAX_YEAR_SERIES - 1 : byYear.length;
  const cutoffIndex = byYear.length - recentCount;
  const series = byYear.map((row, i) =>
    i < cutoffIndex
      ? { ...row, color: EARLIER_COLOR, group: "earlier", earlier: true }
      : { ...row, color: YEAR_COLORS[i - cutoffIndex], group: String(row.year), earlier: false }
  );

  const pointsByMonth = Array.from({ length: 12 }, () => []);
  for (const row of series) {
    row.months.forEach((value, m) => {
      if (value === null) return;
      pointsByMonth[m].push({
        year: row.year,
        value,
        y: yAt(value),
        group: row.group,
      });
    });
  }

  return { width, height, topPad, bottomPad, padX, plotHeight, step, baseline, xAt, yAt, max, series, pointsByMonth };
}

function chartMarkup(layout) {
  const { width, height, topPad, padX, baseline, xAt, max, series } = layout;

  const lines = series
    .map((row) => {
      const segments = [];
      let segment = [];
      row.months.forEach((value, i) => {
        if (value === null) {
          if (segment.length) segments.push(segment);
          segment = [];
          return;
        }
        segment.push(`${segment.length === 0 ? "M" : "L"}${xAt(i)},${layout.yAt(value)}`);
      });
      if (segment.length) segments.push(segment);
      if (!segments.length) return "";
      return (
        `<path class="about-line${row.earlier ? " about-line-earlier" : ""}" ` +
        `data-group="${escapeHtml(row.group)}" stroke="${row.color}" ` +
        `d="${segments.map((s) => s.join(" ")).join(" ")}" fill="none"></path>`
      );
    })
    .join("");

  const ticks = MONTH_LABELS.map(
    (name, i) =>
      `<text class="about-axis-label" x="${xAt(i)}" y="${height - 18}" text-anchor="middle">${name}</text>`
  ).join("");

  const legendGroups = [];
  const seenGroups = new Set();
  for (const row of series) {
    if (seenGroups.has(row.group)) continue;
    seenGroups.add(row.group);
    legendGroups.push(row);
  }
  const earlierRow = legendGroups.find((row) => row.earlier);
  const legend = legendGroups
    .filter((row) => !row.earlier)
    .map(
      (row) =>
        `<span data-group="${escapeHtml(row.group)}"><i class="about-line-swatch" ` +
        `style="background:${row.color}"></i>${row.year}</span>`
    )
    .join("");
  const earlierLegend = earlierRow
    ? `<span data-group="earlier"><i class="about-line-swatch" ` +
      `style="background:${EARLIER_COLOR}"></i>Before ${series.find((r) => !r.earlier).year}</span>`
    : "";

  return (
    `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Strandings per month, one line per year">` +
    `<line class="about-baseline" x1="${padX}" y1="${baseline}" x2="${width - padX}" y2="${baseline}" />` +
    `<text class="about-axis-label" x="2" y="${topPad + 3}">${max.toLocaleString()}</text>` +
    lines +
    ticks +
    `<line id="about-crosshair" x1="0" y1="${topPad}" x2="0" y2="${baseline}" />` +
    `</svg>` +
    `<div class="about-tooltip"></div>` +
    `<div class="about-chart-legend">${earlierLegend}${legend}</div>`
  );
}

function renderAboutChart(container, byYear) {
  if (byYear.length === 0) {
    container.innerHTML = '<p class="about-chart-empty">No stranding history yet.</p>';
    return;
  }
  const layout = buildChartLayout(byYear);
  container.innerHTML = chartMarkup(layout);
  wireAboutChartInteractions(container, layout);
}

function wireAboutChartInteractions(container, layout) {
  const svg = container.querySelector("svg");
  const tooltip = container.querySelector(".about-tooltip");
  const crosshair = container.querySelector("#about-crosshair");
  const paths = container.querySelectorAll(".about-line");
  const legendItems = container.querySelectorAll(".about-chart-legend [data-group]");
  if (!svg) return;

  function clear() {
    crosshair.style.opacity = "0";
    tooltip.style.opacity = "0";
    paths.forEach((path) => path.classList.remove("is-active", "is-dimmed"));
    legendItems.forEach((item) => item.classList.remove("is-active", "is-dimmed"));
  }

  svg.addEventListener("mousemove", (event) => {
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0) return;
    const scale = layout.width / rect.width;
    const mouseX = (event.clientX - rect.left) * scale;
    const mouseY = (event.clientY - rect.top) * scale;
    const monthIndex = Math.min(
      11,
      Math.max(0, Math.round((mouseX - layout.padX) / layout.step))
    );
    const candidates = layout.pointsByMonth[monthIndex];
    if (!candidates.length) {
      clear();
      return;
    }
    const nearest = candidates.reduce((best, candidate) =>
      Math.abs(candidate.y - mouseY) < Math.abs(best.y - mouseY) ? candidate : best
    );

    crosshair.setAttribute("x1", layout.xAt(monthIndex));
    crosshair.setAttribute("x2", layout.xAt(monthIndex));
    crosshair.style.opacity = "1";

    paths.forEach((path) => {
      const isActive = path.dataset.group === nearest.group;
      path.classList.toggle("is-active", isActive);
      path.classList.toggle("is-dimmed", !isActive);
    });
    legendItems.forEach((item) => {
      const isActive = item.dataset.group === nearest.group;
      item.classList.toggle("is-active", isActive);
      item.classList.toggle("is-dimmed", !isActive);
    });

    tooltip.textContent =
      `${nearest.year} · ${MONTH_LABELS[monthIndex]}: ${nearest.value.toLocaleString()} strandings`;
    tooltip.style.opacity = "1";
    const containerRect = container.getBoundingClientRect();
    const left = Math.min(
      event.clientX - containerRect.left + 12,
      containerRect.width - 160
    );
    tooltip.style.left = `${Math.max(0, left)}px`;
    tooltip.style.top = `${Math.max(0, event.clientY - containerRect.top - 30)}px`;
  });

  svg.addEventListener("mouseleave", clear);
}

function renderAboutSources() {
  const list = document.getElementById("about-sources");
  list.innerHTML = DATA_SOURCES.map(
    (item) =>
      `<li>${escapeHtml(item.text)}${
        item.href
          ? ` <a href="${escapeHtml(item.href)}" target="_blank" rel="noopener noreferrer">Source</a>`
          : ""
      }</li>`
  ).join("");
}

function renderAboutFacts() {
  const list = document.getElementById("about-facts");
  list.innerHTML = FACTS.map(
    (fact) =>
      `<li class="fact">` +
      `<span class="fact-value">${escapeHtml(fact.value)}</span>` +
      `<span class="fact-label">${escapeHtml(fact.label)} ` +
      `<a href="${escapeHtml(fact.href)}" target="_blank" rel="noopener noreferrer">Source</a>` +
      `</span>` +
      `</li>`
  ).join("");
}

let aboutMonthly = null;

async function openAbout() {
  aboutBackdrop.classList.remove("hidden");
  if (aboutMonthly === null) {
    const chart = document.getElementById("about-chart");
    chart.textContent = "Loading…";
    try {
      aboutMonthly = await fetchJson("/api/strandings/monthly");
      renderAboutChart(chart, aboutMonthly);
    } catch {
      chart.textContent = "Could not load stranding history.";
    }
  }
}

function closeAbout() {
  aboutBackdrop.classList.add("hidden");
}

const aboutBackdrop = document.getElementById("about-backdrop");
renderAboutSources();
renderAboutFacts();
document.getElementById("about-toggle").addEventListener("click", openAbout);
document.getElementById("about-close").addEventListener("click", closeAbout);
aboutBackdrop.addEventListener("click", (event) => {
  if (event.target === aboutBackdrop) closeAbout();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !aboutBackdrop.classList.contains("hidden")) closeAbout();
});

let riskFitted = true;
let selectedTick = null;
let hours = [];
let pendingHour = 0;
const hidden = new Set(["wind", "currents", "sea"]);

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
      // Colour alone carries the index: an even width keeps segments comparable
      // instead of making a long stretch of coast look more urgent than a short one.
      "line-color": rampExpression(RISK),
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 3, 11, 9],
      "line-opacity": 0.9,
    },
  });
}

function addArrowLayer(layer) {
  addLayer(layer, {
    type: "symbol",
    layout: {
      "icon-image": iconImageExpression(layer),
      "icon-rotate": layer.from
        ? ["+", ["get", layer.direction], 180]
        : ["get", layer.direction],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "icon-size": [
        "interpolate", ["linear"], ["get", layer.speed], 0, 0.45, layer.max, 1.0,
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
  const parts = [];
  if ((risk.features ?? []).some((f) => f.properties.source === "forecast")) {
    parts.push("computed from forecast conditions");
  }
  if (risk.fitted === false) {
    parts.push(
      "seasonal climatology only \u2014 the sea-state and drift signals are " +
        "not in use until the coefficients are fitted"
    );
  }
  return parts.length ? { risk: parts.join("; ") } : {};
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

  riskFitted = data.risk.fitted !== false;
  map.getSource(RISK.id).setData(data.risk);
  const counts = { risk: data.risk.features.length };
  for (const layer of CONDITIONS) {
    const rows = conditionRows(data.conditions, layer);
    map.getSource(layer.id).setData(toFeatureCollection(rows));
    counts[layer.id] = rows.length;
  }
  for (const layer of POINTS) {
    map.getSource(layer.id).setData(toFeatureCollection(data[layer.id]));
    counts[layer.id] = data[layer.id].length;
  }
  renderLegend(counts, riskNote(data.risk));
}

map.on("load", async () => {
  for (const layer of CONDITIONS) {
    for (let i = 0; i < RAMP_STEPS; i++) {
      map.addImage(
        `${layer.id}-${i}`,
        arrowImage(rampColor(layer.stops, i / (RAMP_STEPS - 1)), layer.thick)
      );
    }
  }
  addRiskLayer();
  CONDITIONS.forEach(addArrowLayer);
  POINTS.forEach(addPointLayer);
  renderLegend({});

  const range = await fetchJson("/api/range");
  if (!range.min || !range.max) {
    label.textContent = "No data ingested yet";
    return;
  }
  buildTimeline(floorHour(range.min), floorHour(range.max));
});
