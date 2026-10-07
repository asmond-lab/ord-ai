// Inline SVG charts drawn at the well's real pixel size, so tick text stays at caption size.
import { s } from "./dom.js";
import { fmt } from "./format.js";

const PAD = { top: 14, right: 14, bottom: 26, left: 52 };
const TICK_GAP = 8;
const MIN_MAP_SPAN = 512;
const mounted = new WeakMap();

const observer = new ResizeObserver((entries) => {
  for (const { target } of entries) paint(target, false);
});

function paint(well, force) {
  const chart = mounted.get(well);
  const width = Math.floor(well.clientWidth);
  const height = Math.floor(well.clientHeight);
  if (!chart || width === 0 || height === 0) return;
  if (!force && width === chart.width && height === chart.height) return;
  Object.assign(chart, { width, height });
  well.replaceChildren(chart.build(width, height));
}

/** Draw build(width, height) into a well now and again whenever the well is resized. */
export function mountChart(well, build) {
  if (!mounted.has(well)) observer.observe(well);
  mounted.set(well, { build, width: 0, height: 0 });
  paint(well, true);
}

/** A 1, 2 or 5 times power-of-ten step nearest to span / count (Heckbert's nice numbers). */
function niceStep(span, count) {
  const raw = span / Math.max(count, 1);
  const power = 10 ** Math.floor(Math.log10(raw));
  const ratio = raw / power;
  return (ratio < 1.5 ? 1 : ratio < 3 ? 2 : ratio < 7 ? 5 : 10) * power;
}

function frame(width, height, label, children) {
  return s("svg", { class: "chart__svg", role: "img", "aria-label": label, viewBox: `0 0 ${width} ${height}`, width, height }, children);
}

function yAxis(ticks, y, width, step) {
  return ticks.flatMap((value) => [
    s("line", { class: "grid", x1: PAD.left, x2: width - PAD.right, y1: y(value), y2: y(value) }),
    s("text", { class: "tick", x: PAD.left - TICK_GAP, y: y(value), "text-anchor": "end", "dominant-baseline": "middle" }, fmt.axis(value, step)),
  ]);
}

/** Which of n evenly spaced labels to draw so they do not collide; the last is always drawn. */
function labelled(count, plotWidth, labelWidth) {
  const every = Math.max(1, Math.ceil(count / Math.max(1, Math.floor(plotWidth / labelWidth))));
  return (index) => (count - 1 - index) % every === 0;
}

/** An x-axis label; the last one ends at its point so it never runs off the right edge. */
function xLabel(x, height, text, last) {
  return s("text", { class: "tick", x, y: height - TICK_GAP, "text-anchor": last ? "end" : "middle" }, text);
}

/** Vertical bars: [{ key, value, tone, title }]. */
export function barChart({ width, height, bars, label }) {
  const plotWidth = width - PAD.left - PAD.right;
  const plotHeight = height - PAD.top - PAD.bottom;
  const max = Math.max(1, ...bars.map((bar) => bar.value));
  const step = max <= 6 ? 1 : niceStep(max, Math.max(2, Math.floor(plotHeight / 40)));
  const top = Math.ceil(max / step) * step;
  const y = (value) => PAD.top + plotHeight - (value / top) * plotHeight;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, index) => index * step);
  const band = plotWidth / Math.max(bars.length, 1);
  const barWidth = Math.max(2, Math.min(36, band * 0.68));
  const showLabel = labelled(bars.length, plotWidth, 34);
  const marks = bars.flatMap((bar, index) => {
    const center = PAD.left + band * (index + 0.5);
    const rect = s("rect", { class: `mark tone-${bar.tone}`, x: center - barWidth / 2, y: y(bar.value), width: barWidth, height: y(0) - y(bar.value) }, s("title", {}, bar.title));
    const tick = showLabel(index) ? xLabel(center, height, bar.key, false) : null;
    return [rect, tick];
  });
  const baseline = s("line", { class: "axis", x1: PAD.left, x2: width - PAD.right, y1: y(0), y2: y(0) });
  return frame(width, height, label, [...yAxis(ticks, y, width, step), baseline, ...marks]);
}

function triangle(x, y, up) {
  const size = 6;
  const tip = up ? y - size : y + size;
  const base = up ? y + size / 2 : y - size / 2;
  return `M${x},${tip}L${x + size},${base}L${x - size},${base}Z`;
}

/**
 * One loss series: [{ step, value, terminal }]. The y range fits the updates that did not end
 * an episode, so the terminal spikes never flatten the rest; values outside it are drawn as
 * triangles on the edge.
 */
export function lossChart({ width, height, points, series, label }) {
  const plotWidth = width - PAD.left - PAD.right;
  const plotHeight = height - PAD.top - PAD.bottom;
  const finite = points.filter((point) => Number.isFinite(point.value));
  const ordinary = finite.filter((point) => !point.terminal);
  const scope = (ordinary.length >= 2 ? ordinary : finite).map((point) => point.value);
  if (!scope.length) scope.push(0); // every value was non-finite (logged as nan): draw an empty axis
  let low = Math.min(...scope);
  let high = Math.max(...scope);
  if (!(high > low)) [low, high] = [low - (Math.abs(low) || 1), high + (Math.abs(high) || 1)];
  const step = niceStep(high - low, Math.max(2, Math.floor(plotHeight / 34)));
  [low, high] = [Math.floor(low / step) * step, Math.ceil(high / step) * step];
  const ticks = Array.from({ length: Math.round((high - low) / step) + 1 }, (_, index) => low + index * step);
  const x = (index) => PAD.left + (points.length <= 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);
  const y = (value) => PAD.top + ((high - Math.min(high, Math.max(low, value))) / (high - low)) * plotHeight;
  let path = "";
  let pen = false;
  const marks = [];
  points.forEach((point, index) => {
    if (!Number.isFinite(point.value)) {
      pen = false;
      return;
    }
    path += `${pen ? "L" : "M"}${x(index).toFixed(1)},${y(point.value).toFixed(1)}`;
    pen = true;
    const clipped = point.value > high || point.value < low;
    const title = s("title", {}, `step ${fmt.int(point.step)}: ${fmt.loss(point.value)}${point.terminal ? " (에피소드 종료)" : ""}`);
    if (clipped) {
      const tone = point.terminal ? "tone-loss" : series;
      marks.push(s("path", { class: `mark mark--clipped ${tone}`, d: triangle(x(index), y(point.value), point.value > high) }, title));
    } else if (point.terminal) {
      marks.push(s("circle", { class: "mark mark--terminal", cx: x(index), cy: y(point.value), r: 4 }, title));
    }
  });
  const showLabel = labelled(points.length, plotWidth, 64);
  const xTicks = points.map((point, index) => (showLabel(index) ? xLabel(x(index), height, fmt.int(point.step), index === points.length - 1 && points.length > 1) : null));
  const line = s("path", { class: `line ${series}`, d: path });
  return frame(width, height, label, [...yAxis(ticks, y, width, step), line, ...marks, ...xTicks]);
}

/**
 * Unit positions of one row on an equal-scale square: north is up, stacked units share one
 * larger blip. Returns the SVG and the grid spacing in map units.
 */
export function minimap({ size, units, label }) {
  const groups = new Map();
  for (const unit of units) {
    const key = `${unit.x},${unit.y}`;
    groups.set(key, [...(groups.get(key) ?? []), unit]);
  }
  const xs = units.map((unit) => unit.x);
  const ys = units.map((unit) => unit.y);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), MIN_MAP_SPAN) * 1.16;
  const left = (Math.max(...xs) + Math.min(...xs)) / 2 - span / 2;
  const top = (Math.max(...ys) + Math.min(...ys)) / 2 + span / 2;
  const px = (value) => ((value - left) / span) * size;
  const py = (value) => ((top - value) / span) * size;
  const grid = niceStep(span, 6);
  const lines = [];
  for (let value = Math.ceil(left / grid) * grid; value < left + span; value += grid) {
    lines.push(s("line", { class: "map-grid", x1: px(value), x2: px(value), y1: 0, y2: size }));
  }
  for (let value = Math.ceil((top - span) / grid) * grid; value < top; value += grid) {
    lines.push(s("line", { class: "map-grid", x1: 0, x2: size, y1: py(value), y2: py(value) }));
  }
  const blips = [...groups.values()].map((group) => {
    const names = group.slice(0, 4).map((unit) => unit.name).join(", ");
    const more = group.length > 4 ? ` 외 ${group.length - 4}기` : "";
    const where = `(${fmt.int(group[0].x)}, ${fmt.int(group[0].y)})`;
    const radius = 4 + Math.min(5, Math.sqrt(group.length - 1) * 1.6);
    return s("circle", { class: "blip", cx: px(group[0].x), cy: py(group[0].y), r: radius }, s("title", {}, `${names}${more} ${where}`));
  });
  return { svg: frame(size, size, label, [...lines, ...blips]), grid };
}
