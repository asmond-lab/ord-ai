// One renderer per dashboard section. Each fills the static skeleton in index.html.
import { barChart, lossChart, minimap, mountChart } from "./charts.js";
import { byId, h, s, showEmpty } from "./dom.js";
import { actionLabel, actionParts, fileName, fmt, statusOf, timeOf } from "./format.js";

const CHART_EPISODES = 40;
const RECORDING_MS = 30_000;

const badge = (tone, text) => h("span", { class: `badge tone-${tone}` }, text);
const cell = (text, numeric = false) => h("td", numeric ? { class: "num" } : {}, text);
const emptyRow = (columns, text) => h("tr", {}, h("td", { class: "empty-row", colspan: columns }, text));

export function renderRunOptions(select, runs, selected) {
  select.replaceChildren(...runs.map((run) => h("option", { value: run.name }, run.name)));
  select.value = selected ?? "";
  select.disabled = runs.length === 0;
}

function kpi(label, value, note) {
  const empty = value === "-";
  return h("div", { class: "kpi panel" }, h("dt", {}, label), h("dd", { class: empty ? "kpi__value kpi__value--empty" : "kpi__value" }, value), h("dd", { class: "kpi__note" }, note));
}

function runRow(run, current, now) {
  const checkpoint = run.checkpoint;
  return h(
    "tr",
    current ? { "aria-current": "true" } : {},
    h("th", { scope: "row" }, h("span", { class: "run-name" }, run.name), current ? badge("current", "보는 중") : null),
    cell(fmt.int(run.transitions), true),
    cell(fmt.int(run.episodes.length), true),
    cell(run.transitions ? fmt.pct(run.telemetry_fraction) : "-", true),
    cell(checkpoint?.steps === null || checkpoint?.steps === undefined ? "-" : fmt.int(checkpoint.steps), true),
    cell(checkpoint?.saved_at ? fmt.moment(checkpoint.saved_at) : "-"),
    cell(run.updated_at ? fmt.ago(run.updated_at, now) : "-"),
  );
}

export function renderCollected(summary, run, now) {
  const count = summary.runs.length;
  byId("runs-dir").textContent = summary.runs_dir;
  byId("collected-note").textContent = count ? `${summary.runs_dir} 아래 실행 ${count}개` : `${summary.runs_dir} 아래에 실행 폴더가 없습니다.`;
  const checkpoint = run?.checkpoint ?? null;
  const episodes = run?.episodes ?? [];
  const span = episodes.length > 1 ? `#${episodes[0].episode} ~ #${episodes.at(-1).episode}` : episodes.length ? `#${episodes[0].episode}` : "기록 없음";
  const read = !run?.updated_at ? "transitions.jsonl 없음" : run.skipped_lines ? `불완전한 줄 ${fmt.int(run.skipped_lines)}개 제외` : "모든 줄을 읽었습니다";
  byId("kpis").replaceChildren(
    kpi("전이", fmt.int(run?.transitions ?? null), read),
    kpi("에피소드", fmt.int(run ? episodes.length : null), span),
    kpi("텔레메트리", run?.transitions ? fmt.pct(run.telemetry_fraction) : "-", `${fmt.int(run?.telemetry_rows ?? 0)} / ${fmt.int(run?.transitions ?? 0)}행`),
    kpi("체크포인트 step", fmt.int(checkpoint?.steps ?? null), checkpoint ? (checkpoint.algorithm ?? "metadata.json 없음") : "저장된 가중치 없음"),
    kpi("마지막 저장", checkpoint?.saved_at ? fmt.ago(checkpoint.saved_at, now) : "-", checkpoint?.saved_at ? fmt.moment(checkpoint.saved_at) : "policy.safetensors 없음"),
  );
  const fields = Object.entries(run?.telemetry_fields ?? {});
  byId("fields").replaceChildren(...fields.map(([name, rows]) => h("li", { class: rows ? "chip" : "chip chip--off" }, `${name} ${fmt.int(rows)}`)));
  const rows = summary.runs.map((item) => runRow(item, item.name === run?.name, now));
  byId("runs-body").replaceChildren(...(rows.length ? rows : [emptyRow(7, "실행 폴더가 없습니다.")]));
}

export function renderEpisodes(run) {
  const episodes = run?.episodes ?? [];
  const best = Math.max(0, ...episodes.map((episode) => episode.max_round));
  byId("episodes-note").textContent = episodes.length ? `${fmt.int(episodes.length)}개, 최고 라운드 ${fmt.int(best)}` : "이 실행에는 전이 기록이 없습니다.";
  const shown = episodes.slice(-CHART_EPISODES);
  byId("episode-meta").textContent = shown.length < episodes.length ? `최근 ${shown.length}개, 막대 색은 결과` : "막대 색은 에피소드 결과";
  showEmpty("episode-empty", "episode-well", episodes.length ? null : "그릴 에피소드가 없습니다.");
  if (episodes.length) {
    const bars = shown.map((episode) => ({
      key: String(episode.episode),
      value: episode.max_round,
      tone: statusOf(episode.status).tone,
      title: `에피소드 ${episode.episode}: 최고 라운드 ${episode.max_round}, ${statusOf(episode.status).label}`,
    }));
    const label = `에피소드별 최고 라운드 막대 그래프, ${shown.length}개 에피소드, 최고 ${best} 라운드`;
    mountChart(byId("episode-well"), (width, height) => barChart({ width, height, bars, label }));
  }
  const rows = [...episodes].reverse().map((episode) => {
    const status = statusOf(episode.status);
    return h(
      "tr",
      {},
      h("th", { scope: "row", class: "num" }, `#${episode.episode}`),
      cell(fmt.int(episode.max_round), true),
      cell(fmt.dec(episode.total_reward), true),
      h("td", {}, badge(status.tone, status.label)),
      cell(fmt.pct(episode.acceptance), true),
      cell(fmt.int(episode.steps), true),
      cell(fmt.gameTime(episode.game_time), true),
    );
  });
  byId("episodes-body").replaceChildren(...(rows.length ? rows : [emptyRow(7, "기록 없음")]));
}

function lossMeta(points) {
  const last = `마지막 ${fmt.loss(points.at(-1).value)}`;
  const terminal = points.filter((point) => point.terminal && Number.isFinite(point.value)).map((point) => point.value);
  if (!terminal.length) return `${last}, x축 step`;
  return `${last}, 종료 업데이트 ${terminal.length}회 ${fmt.loss(Math.min(...terminal))} ~ ${fmt.loss(Math.max(...terminal))}`;
}

export function renderLearning(run) {
  const logs = run?.log_files ?? [];
  const losses = run?.losses ?? [];
  const names = logs.map(fileName).join(", ");
  byId("learning-note").textContent = logs.length ? `로그 ${names}, 업데이트 ${fmt.int(losses.length)}회` : "학습 로그 없음";
  const name = run?.name ?? "실행";
  const missing = logs.length
    ? [h("code", {}, names), "에 손실 기록이 없습니다. 학습하지 않는 기록 실행일 수 있습니다."]
    : ["학습 로그를 찾지 못했습니다. ", h("code", {}, `${name}/*.log`), " 또는 ", h("code", {}, `${name}-*.log`), " 파일을 읽습니다."];
  showEmpty("learning-empty", "learning-charts", losses.length ? null : missing);
  if (!losses.length) return;
  const charts = [
    ["policy_loss", "policy", "series-1", "정책 손실"],
    ["value_loss", "value", "series-2", "가치 손실"],
  ];
  for (const [key, id, series, title] of charts) {
    const points = losses.map((point) => ({ step: point.step, value: point[key], terminal: point.terminal }));
    byId(`${id}-meta`).textContent = lossMeta(points);
    const label = `${title} 선 그래프, 업데이트 ${points.length}회, 마지막 값 ${fmt.loss(points.at(-1).value)}`;
    mountChart(byId(`${id}-well`), (width, height) => lossChart({ width, height, points, series, label }));
  }
  byId("losses-body").replaceChildren(
    ...[...losses].reverse().map((point) =>
      h("tr", {}, h("th", { scope: "row", class: "num" }, fmt.int(point.step)), cell(fmt.int(point.round), true), cell(fmt.dec(point.reward), true), cell(fmt.loss(point.policy_loss), true), cell(fmt.loss(point.value_loss), true), h("td", {}, point.terminal ? badge("loss", "종료") : "")),
    ),
  );
}

export function renderActions(run) {
  const actions = run?.actions ?? [];
  const top = Math.max(1, ...actions.map((action) => action.count));
  const covered = actions.reduce((total, action) => total + action.count, 0);
  byId("actions-note").textContent = actions.length ? `전체 ${fmt.int(run.transitions)}회 중 상위 ${actions.length}개가 ${fmt.pct(covered / run.transitions)}` : "고른 행동이 없습니다.";
  const rows = actions.map((action) => {
    const bar = s("svg", { class: "bar", viewBox: "0 0 100 8", preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false" }, s("rect", { x: 0, y: 0, height: 8, width: Math.max(1, (action.count / top) * 100) }));
    const { label, tag } = actionParts(action.name);
    const name = h("th", { scope: "row", class: "action-name", title: action.name }, label, tag ? [" ", h("span", { class: "tag" }, tag)] : null);
    return h("tr", {}, name, h("td", {}, h("div", { class: "bar-cell" }, bar, h("span", { class: "num" }, fmt.int(action.count)))), cell(fmt.pct(action.acceptance), true));
  });
  byId("actions-body").replaceChildren(...(rows.length ? rows : [emptyRow(3, "기록 없음")]));
}

function fact(label, value, { modifier = "", note = "" } = {}) {
  const details = note ? [h("dd", {}, value), h("dd", { class: "fact__note" }, note)] : [h("dd", {}, value)];
  return h("div", modifier ? { class: modifier } : {}, h("dt", {}, label), ...details);
}

export function renderLive(run, now) {
  const latest = run?.latest ?? null;
  const note = byId("live-note");
  if (!run?.updated_at) {
    note.replaceChildren("이 실행에는 전이 기록이 없습니다.");
  } else {
    const recording = now - timeOf(run.updated_at) < RECORDING_MS;
    note.replaceChildren(badge(recording ? "live" : "idle", recording ? "기록 중" : "멈춤"), `마지막 기록 ${fmt.ago(run.updated_at, now)}`);
  }
  if (!latest) {
    byId("live-facts").replaceChildren(fact("상태", "기록 없음", { modifier: "fact--wide fact--text" }));
    return;
  }
  const status = statusOf(latest.status);
  byId("live-facts").replaceChildren(
    fact("에피소드", `#${latest.episode}`),
    fact("라운드", fmt.int(latest.round)),
    fact("적", fmt.int(latest.enemies)),
    fact("상태", badge(status.tone, status.label)),
    fact("게임 시간", fmt.gameTime(latest.game_time)),
    fact("스토리", fmt.int(latest.story)),
    fact("스텝", fmt.int(latest.step), { note: `에피소드 안 ${fmt.int(latest.sequence)}번째` }),
    fact("보상", fmt.dec(latest.reward)),
    fact("마지막 행동", `${actionLabel(latest.action)}, ${latest.accepted ? "수락" : "거부"}`, { modifier: "fact--wide fact--text" }),
  );
}

export function renderMinimap(run) {
  const units = run?.latest?.units ?? null;
  const message = !run?.latest ? "전이 기록이 없습니다." : units === null ? "이 기록에는 유닛 위치가 없습니다. 텔레메트리가 없는 실행입니다." : units.length ? null : "마지막 기록에 보이는 유닛이 없습니다.";
  showEmpty("map-empty", "map-well", message);
  byId("map-alt").hidden = message !== null;
  byId("map-meta").textContent = message === null ? `step ${fmt.int(run.latest.step)}의 유닛 ${fmt.int(units.length)}기, 북쪽이 위` : "마지막 기록 기준";
  if (message !== null) return;
  const label = `유닛 ${units.length}기의 위치 산점도`;
  mountChart(byId("map-well"), (width, height) => {
    const map = minimap({ size: Math.min(width, height), units, label });
    byId("map-meta").textContent = `step ${fmt.int(run.latest.step)}의 유닛 ${fmt.int(units.length)}기, 격자 한 칸 ${fmt.int(map.grid)}, 북쪽이 위`;
    return map.svg;
  });
  byId("map-body").replaceChildren(...units.map((unit) => h("tr", {}, h("th", { scope: "row" }, unit.name), cell(fmt.int(unit.x), true), cell(fmt.int(unit.y), true))));
}
