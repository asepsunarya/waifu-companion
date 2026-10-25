// Her text cards (SPEC, "kartu teks (v1.55)"): the long part of a reply, shown instead of read aloud, beside her on the
// PC (index.html imports this file from /phone/card.js) and in her bubble on the phone (phone.js). A card's text is the
// model's output, so it only ever reaches a page as text nodes: no HTML is parsed from it, and a link is an <a> whose
// href is a checked http(s) URL. No DOM globals here (the builder takes `document`), so `node --test` covers it all.

// The server's caps (waifu.card), applied again to what arrives.
export const MAX_TITLE_CHARS = 60;
export const MAX_BODY_CHARS = 4000;
export const MAX_BODY_LINES = 60;
export const MAX_LINK_CHARS = 2000;
// Where the avatar page posts a clicked link, which Cyrene opens in the default browser.
export const OPEN_LINK_URL = "/open-link";

const ELLIPSIS = "…";
// http:// or https:// and what may follow in a link here: no whitespace, quotes, angle brackets, or backtick, no
// control or invisible formatting characters, and no CJK punctuation or full-width forms (Japanese text around a link).
const LINK =
  /https?:\/\/[^\s<>"'`\u0000-\u001f\u007f​-‏‪-‮⁠-⁩﻿　-〿＀-￯]+/gi;
// The host as written: ASCII letters, digits, dots, and hyphens only, so no user name or port hides the real host (as
// waifu.notify.is_web_link checks it before the PC opens a link).
const PLAIN_HOST = /^https?:\/\/[a-z0-9.-]+(?:[/?#]|$)/i;
// Sentence punctuation after a link is not part of it, nor a closing bracket it never opened.
const TRAILING = new Set([".", ",", ";", ":", "!", "?"]);
const OPENERS = { ")": "(", "]": "[" };
// A bullet starts its line; an indented one (a step under a step) stays a line as written.
const BULLET = /^[-*•]\s+/;

const isPlainObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function cut(text, limit) {
  return text.length <= limit ? text : `${text.slice(0, limit - 1).trimEnd()}${ELLIPSIS}`;
}

/** A card `{title, body}` from the PC with its caps applied again; null when it is not one or has nothing to show. */
export function parseCard(data) {
  if (!isPlainObject(data) || typeof data.title !== "string" || typeof data.body !== "string") return null;
  let lines = data.body.split(/\r?\n/);
  if (lines.length > MAX_BODY_LINES) lines = [...lines.slice(0, MAX_BODY_LINES - 1), ELLIPSIS];
  const body = cut(lines.join("\n"), MAX_BODY_CHARS);
  if (body.trim() === "") return null;
  return { title: cut(data.title.trim(), MAX_TITLE_CHARS), body };
}

/**
 * How many milliseconds the card of a `card` event `{show_s, elapsed}` still shows; null to keep it until it is closed
 * (show_s 0 or missing). A replayed card (`elapsed`) hides that much sooner.
 */
export function cardHideMs(event) {
  const showS = event?.show_s;
  if (typeof showS !== "number" || !Number.isFinite(showS) || showS <= 0) return null;
  const elapsed = event.elapsed;
  const shownS = typeof elapsed === "number" && Number.isFinite(elapsed) && elapsed > 0 ? elapsed : 0;
  return Math.max(0, showS - shownS) * 1000;
}

/** Whether `url` is a link the card may make: http or https with a plain host, parsed alike by `URL`. */
export function isOpenableLink(url) {
  if (url.length > MAX_LINK_CHARS || !PLAIN_HOST.test(url)) return false;
  try {
    const parsed = new URL(url);
    return (parsed.protocol === "http:" || parsed.protocol === "https:") && parsed.hostname !== "";
  } catch {
    return false;
  }
}

function count(text, char) {
  return text.split(char).length - 1;
}

/** A link found in text without the punctuation after it. */
function trimLink(found) {
  let url = found;
  for (;;) {
    const last = url.at(-1);
    if (TRAILING.has(last)) url = url.slice(0, -1);
    else if (Object.hasOwn(OPENERS, last) && count(url, last) > count(url, OPENERS[last])) url = url.slice(0, -1);
    else return url;
  }
}

/** `text` as pieces in order: `{text}` for plain text, `{text, url}` for a link (`isOpenableLink`). */
export function linkPieces(text) {
  const pieces = [];
  let shown = 0;
  for (const match of text.matchAll(LINK)) {
    const url = trimLink(match[0]);
    if (!isOpenableLink(url)) continue;
    if (match.index > shown) pieces.push({ text: text.slice(shown, match.index) });
    pieces.push({ text: url, url });
    shown = match.index + url.length;
  }
  if (shown < text.length) pieces.push({ text: text.slice(shown) });
  return pieces;
}

/**
 * The card's lines: `{kind: "blank" | "bullet" | "text", pieces}`, a bullet ("- ", "* ", "• ") without its mark, and
 * a text line with its indentation and runs of spaces (code, aligned rows), which the pages show (white-space).
 */
export function cardLines(body) {
  return body.split("\n").map((line) => {
    if (line.trim() === "") return { kind: "blank", pieces: [] };
    const bullet = BULLET.exec(line);
    return bullet
      ? { kind: "bullet", pieces: linkPieces(line.slice(bullet[0].length).trimEnd()) }
      : { kind: "text", pieces: linkPieces(line.trimEnd()) };
  });
}

/** The fetch arguments that hand a clicked `url` to Cyrene with the page's `token`. */
export function linkRequest(token, url) {
  return [
    OPEN_LINK_URL,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token, url }) },
  ];
}

function linkElement(document, url, onLink) {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener noreferrer";
  link.target = "_blank";
  link.textContent = url;
  if (onLink) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      onLink(url);
    });
  }
  return link;
}

function lineElement(document, tagName, pieces, onLink) {
  const element = document.createElement(tagName);
  for (const piece of pieces) {
    element.append(piece.url ? linkElement(document, piece.url, onLink) : document.createTextNode(piece.text));
  }
  return element;
}

/**
 * The card as a `div.card`: its title as `h2.card-title` (when it has one), its text lines as `p.card-line`, and each
 * run of bullets as a `ul.card-list`; a blank line ends a run. Built from elements and text nodes only. A link follows
 * the page's own way of opening links, or with `onLink` a click hands its URL to `onLink` instead.
 */
export function buildCard(document, card, { onLink = null } = {}) {
  const root = document.createElement("div");
  root.className = "card";
  if (card.title) {
    const title = document.createElement("h2");
    title.className = "card-title";
    title.textContent = card.title;
    root.append(title);
  }
  let list = null;
  for (const { kind, pieces } of cardLines(card.body)) {
    if (kind === "blank") {
      list = null;
    } else if (kind === "bullet") {
      if (list === null) {
        list = document.createElement("ul");
        list.className = "card-list";
        root.append(list);
      }
      list.append(lineElement(document, "li", pieces, onLink));
    } else {
      list = null;
      const line = lineElement(document, "p", pieces, onLink);
      line.className = "card-line";
      root.append(line);
    }
  }
  return root;
}
