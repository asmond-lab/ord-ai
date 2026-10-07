// Korean display formats and labels. The summary stays raw; only presentation happens here.
const LOCALE = "ko-KR";
const integer = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const percent = new Intl.NumberFormat(LOCALE, { style: "percent", maximumFractionDigits: 1 });
const clock = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const moment = new Intl.DateTimeFormat(LOCALE, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const relative = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });
const fixedFormats = new Map();

const STATUS = {
  PLAYING: { label: "진행 중", tone: "playing" },
  WIN: { label: "승리", tone: "win" },
  LOSS: { label: "패배", tone: "loss" },
  WAITING: { label: "대기", tone: "waiting" },
};

// The control actions of ord_ai.catalog.BASE_ACTIONS and SHOP_ACTIONS; fusions keep their unit names.
const BASE_ACTIONS = {
  wait: "대기",
  summon_random_wisp: "랜덤위습 소환",
  gamble_wisp_for_lumber: "위습 목재 도박",
  restore_helper_mana: "도움소 마나 회복",
  activate_evolution_wisp: "진화체위습 사용",
  send_one_unit_to_story: "스토리로 한 기 보내기",
  return_one_unit_to_line: "라인으로 한 기 회수",
  summon_all_random_wisps: "랜덤위습 전부 소환",
  gold_gamble_low: "돈 도박 하급",
  gold_gamble_high: "돈 도박 고급",
  upgrade_common: "흔함류 업그레이드",
  upgrade_special: "특별 업그레이드",
  upgrade_rare: "희귀 업그레이드",
  create_rare_unit: "고급 유닛 생성",
};
const FUSION_PREFIX = "fusion: ";
// Schema-3 player inputs (ord_ai.inputs): "sell: 물품 지원 (h0AX)" shows as "구매: 물품 지원 (h0AX)".
const INPUT_PREFIXES = {
  "sell: ": "구매",
  "train: ": "훈련",
  "research: ": "연구",
  "ability: ": "능력",
  "sync: ": "UI 선택",
  "region: ": "이동",
  "select: ": "선택",
  "chat: ": "채팅",
};

function fixed(digits) {
  if (!fixedFormats.has(digits)) {
    fixedFormats.set(digits, new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits }));
  }
  return fixedFormats.get(digits);
}

export const fmt = {
  int: (value) => (value === null || value === undefined ? "-" : integer.format(value)),
  // Values that round to zero print as 0.00, never as -0.00.
  dec: (value) => (value === null || value === undefined ? "-" : decimal.format(Math.abs(value) < 0.005 ? 0 : value)),
  pct: (value) => (value === null || value === undefined ? "-" : percent.format(value)),
  clock: (date) => clock.format(date),
  moment: (iso) => moment.format(new Date(iso)),

  /** "12초 전", "3분 전": how long before `now` (ms) the ISO time was. */
  ago(iso, now) {
    const seconds = Math.min(0, Math.round((Date.parse(iso) - now) / 1000));
    const size = Math.abs(seconds);
    if (size < 60) return relative.format(seconds, "second");
    if (size < 3600) return relative.format(Math.round(seconds / 60), "minute");
    if (size < 86400) return relative.format(Math.round(seconds / 3600), "hour");
    return relative.format(Math.round(seconds / 86400), "day");
  },

  /** Game seconds as m:ss or h:mm:ss. */
  gameTime(seconds) {
    if (seconds === null || seconds === undefined) return "-";
    const total = Math.floor(seconds);
    const pad = (value) => String(value).padStart(2, "0");
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return hours ? `${hours}:${pad(minutes)}:${pad(total % 60)}` : `${minutes}:${pad(total % 60)}`;
  },

  /** A loss value with precision that suits its size. */
  loss(value) {
    if (!Number.isFinite(value)) return "-";
    const size = Math.abs(value);
    return fixed(size >= 100 ? 1 : size >= 1 ? 3 : 4).format(value);
  },

  /** An axis tick at the precision its tick step needs. */
  axis(value, step) {
    const digits = Math.max(0, Math.min(4, -Math.floor(Math.log10(step) + 1e-9)));
    return fixed(digits).format(Math.abs(value) < step / 1e6 ? 0 : value);
  },
};

export function statusOf(name) {
  return Object.hasOwn(STATUS, name) ? STATUS[name] : { label: name, tone: "waiting" };
}

/** Display parts of an action: "fusion: 타시기 - 안흔함" is { label: "조합: 타시기", tag: "안흔함" }. */
export function actionParts(name) {
  if (Object.hasOwn(BASE_ACTIONS, name)) return { label: BASE_ACTIONS[name], tag: null };
  const input = Object.keys(INPUT_PREFIXES).find((prefix) => name.startsWith(prefix));
  if (input) return { label: `${INPUT_PREFIXES[input]}: ${name.slice(input.length)}`, tag: null };
  if (!name.startsWith(FUSION_PREFIX)) return { label: name, tag: null };
  const rest = name.slice(FUSION_PREFIX.length);
  const cut = rest.lastIndexOf(" - ");
  if (cut < 0) return { label: `조합: ${rest}`, tag: null };
  return { label: `조합: ${rest.slice(0, cut)}`, tag: rest.slice(cut + 3) };
}

export function actionLabel(name) {
  const { label, tag } = actionParts(name);
  return tag ? `${label} (${tag})` : label;
}

export function fileName(path) {
  return path.split("/").at(-1);
}

/** Milliseconds since the epoch for an optional ISO time; 0 when absent. */
export function timeOf(iso) {
  return iso ? Date.parse(iso) : 0;
}
