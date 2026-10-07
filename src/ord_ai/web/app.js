// Entry point: poll /api/summary every 5 seconds and redraw what changed.
import { byId } from "./dom.js";
import { fmt, timeOf } from "./format.js";
import * as view from "./sections.js";

const POLL_MS = 5000;
const TIMEOUT_MS = 4000;
const select = byId("run-select");
const state = { summary: null, selected: runFromHash(), drawn: new Map(), timer: 0, loading: false };

function runFromHash() {
  const match = /^#run=(.+)$/.exec(window.location.hash);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null; // a hand-edited, malformed hash selects the default run instead
  }
}

/** The selected run, else the run whose transitions changed most recently. */
function pickRun(summary) {
  const wanted = summary.runs.find((run) => run.name === state.selected);
  if (wanted) return wanted;
  const recorded = summary.runs.filter((run) => run.transitions > 0);
  recorded.sort((a, b) => timeOf(b.updated_at) - timeOf(a.updated_at));
  return recorded[0] ?? summary.runs[0] ?? null;
}

/** Run draw() only when its inputs changed since the last call under this name. */
function whenChanged(name, inputs, draw) {
  const key = JSON.stringify(inputs);
  if (state.drawn.get(name) === key) return;
  state.drawn.set(name, key);
  draw();
}

function render() {
  const { summary } = state;
  const run = pickRun(summary);
  const now = Date.now();
  state.selected = run?.name ?? null;
  whenChanged("options", [summary.runs.map((item) => [item.name, item.transitions]), state.selected], () => view.renderRunOptions(select, summary.runs, state.selected));
  view.renderCollected(summary, run, now);
  whenChanged("episodes", [state.selected, run?.episodes], () => view.renderEpisodes(run));
  whenChanged("learning", [state.selected, run?.log_files, run?.losses], () => view.renderLearning(run));
  whenChanged("actions", [state.selected, run?.actions, run?.transitions], () => view.renderActions(run));
  view.renderLive(run, now);
  whenChanged("map", [state.selected, run?.latest?.step, run?.latest?.units], () => view.renderMinimap(run));
}

/** The connection badge is a live region, so its text changes only when the state does. */
function setConnection(mode, text) {
  const badge = byId("connection");
  const label = byId("connection-text");
  if (badge.dataset.state === mode && label.textContent === text) return;
  badge.dataset.state = mode;
  label.textContent = text;
}

async function load() {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch("/api/summary", { cache: "no-store", signal: controller.signal });
    if (!response.ok) return { mode: "error", text: `오류 HTTP ${response.status}` };
    return { mode: "online", text: "실시간", summary: await response.json() };
  } catch {
    return { mode: "offline", text: "오프라인" }; // server stopped or unreachable; keep the last data
  } finally {
    window.clearTimeout(timeout);
  }
}

async function refresh() {
  if (state.loading) return; // the request in flight schedules the next poll itself
  state.loading = true;
  window.clearTimeout(state.timer);
  try {
    const result = await load();
    setConnection(result.mode, result.text);
    if (result.summary) {
      state.summary = result.summary;
      const refreshed = byId("refreshed");
      const at = new Date();
      refreshed.dateTime = at.toISOString();
      refreshed.textContent = fmt.clock(at);
      render();
    }
  } finally {
    state.loading = false;
    if (!document.hidden) state.timer = window.setTimeout(refresh, POLL_MS);
  }
}

select.addEventListener("change", () => {
  state.selected = select.value;
  window.history.replaceState(null, "", `#run=${encodeURIComponent(select.value)}`);
  if (state.summary) render();
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) window.clearTimeout(state.timer);
  else void refresh();
});

void refresh();
