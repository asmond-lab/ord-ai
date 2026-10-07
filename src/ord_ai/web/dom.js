// Tiny DOM builders. Data reaches the page only as text nodes and attribute values,
// never as parsed HTML, so names from a map file cannot inject markup.
const SVG_NS = "http://www.w3.org/2000/svg";

function fill(element, attributes, children) {
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    element.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    element.append(typeof child === "object" ? child : String(child));
  }
  return element;
}

/** Create an HTML element: h("td", { class: "num" }, "12"). */
export function h(tag, attributes = {}, ...children) {
  return fill(document.createElement(tag), attributes, children);
}

/** Create an SVG element with the same signature as h(). */
export function s(tag, attributes = {}, ...children) {
  return fill(document.createElementNS(SVG_NS, tag), attributes, children);
}

/** The element with this id; the page skeleton guarantees it exists. */
export function byId(id) {
  return document.getElementById(id);
}

/** Show exactly one of an empty-state message (text or nodes) and its content element. */
export function showEmpty(emptyId, contentId, message) {
  const empty = byId(emptyId);
  empty.hidden = message === null;
  empty.replaceChildren(...(message === null ? [] : [message].flat()));
  byId(contentId).hidden = message !== null;
}
