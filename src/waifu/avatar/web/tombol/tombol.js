// The quick buttons on the PC (SPEC, "tombol cepat di PC (v1.42)"). `waifu tombol` serves this page at
// http://127.0.0.1:<port>/t/<token>/ and opens it in the PC's browser; every URL here is relative, so it keeps the
// token's prefix. The page shows the phone's buttons (GET actions), always open: the owner's switches and routines,
// run on the PC without asking Cyrene. A click sends the button's id to POST action (actions.js) and the PC's answer
// shows as a toast; one runs at a time. The buttons are read again every REFRESH_MS, when the window gets the focus,
// and after a press of an alarm's button or "Dengar lagi", since an alarm that rings (SPEC, "alarm bangun (v1.46)")
// adds its own, first, while it rings, and a pause of her hearing (SPEC, "hotkey global (v1.48)") adds "Dengar lagi".
import { actionOutcome, actionRequest, isPassingAction, parseActionGroups } from "../phone/actions.js";
import { deskText, requestDesk } from "./desk.js";

const ACTIONS_URL = "actions";
const ACTION_URL = "action";
const NOTICE_MS = 6000;
const REFRESH_MS = 15_000;
const NO_BUTTONS_TEXT = "Belum ada tombol cepat di PC.";

const page = {
  message: document.getElementById("message"),
  notice: document.getElementById("notice"),
  groups: document.getElementById("groups"),
};

let buttons = [];
// Whether the PC is running a button; clicks meanwhile are ignored, and the buttons are not read again.
let running = false;
let noticeTimer = 0;
// The groups the page shows, as JSON, so a new listing rebuilds them only when they changed; null before the first.
let listed = null;

/** Reads the PC's buttons and shows them, or says in the message why they cannot show; then reads them again. */
async function boot() {
  await loadButtons({ first: true });
  window.addEventListener("focus", () => loadButtons({ first: false }));
  setInterval(() => loadButtons({ first: false }), REFRESH_MS);
}

/**
 * Reads the PC's buttons and shows them when they changed. A problem, or an answer it cannot read, is said in the
 * message the first time; later the buttons shown stay. A press that runs meanwhile keeps them as they are.
 */
async function loadButtons({ first }) {
  if (running) return;
  const result = await requestDesk(fetch, ACTIONS_URL);
  if (running) return;
  const groups = result.problem ? null : parseActionGroups(result.data);
  if (groups === null) {
    if (first) showMessage(deskText(result.problem ?? "bad_reply"));
    return;
  }
  const shown = JSON.stringify(groups);
  if (shown === listed) return;
  listed = shown;
  showGroups(groups);
}

/** The groups in place of the message, or the message that there are none. */
function showGroups(groups) {
  buttons = [];
  page.groups.replaceChildren(...groups.map(quickGroup));
  page.groups.hidden = groups.length === 0;
  if (groups.length === 0) {
    showMessage(NO_BUTTONS_TEXT);
    return;
  }
  page.message.hidden = true;
}

function showMessage(text) {
  page.message.textContent = text;
  page.message.hidden = false;
}

/** One group: its heading, which names the group for a screen reader, over its buttons. */
function quickGroup({ name, actions }, index) {
  const group = document.createElement("div");
  group.className = "quick-group";
  group.setAttribute("role", "group");
  const heading = document.createElement("h2");
  heading.id = `quick-group-${index}`;
  heading.textContent = name;
  group.setAttribute("aria-labelledby", heading.id);
  const list = document.createElement("div");
  list.className = "quick-buttons";
  list.append(...actions.map(quickButton));
  group.append(heading, list);
  return group;
}

function quickButton({ id, label }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "quick-action";
  button.textContent = label;
  button.addEventListener("click", () => runQuick(button, id));
  buttons.push(button);
  return button;
}

/**
 * While the PC runs a button: that one says it is busy and every one that it is unavailable. aria-disabled, not
 * disabled, so the focus stays on the clicked button.
 */
function setRunning(pressed, value) {
  running = value;
  pressed.setAttribute("aria-busy", String(value));
  for (const button of buttons) button.setAttribute("aria-disabled", String(value));
}

/**
 * Runs the button `id`, clicked as `button`, on the PC and toasts what it did or why it could not. An alarm's button
 * or "Dengar lagi" reads the buttons again, as the press may have taken its group away.
 */
async function runQuick(button, id) {
  if (running) return;
  setRunning(button, true);
  const result = await requestDesk(fetch, ACTION_URL, actionRequest(id));
  setRunning(button, false);
  if (isPassingAction(id)) loadButtons({ first: false });
  if (result.problem) {
    showToast(deskText(result.problem));
    return;
  }
  const outcome = actionOutcome(result.data);
  showToast(outcome === null ? deskText("bad_reply") : outcome.text);
}

/** Shows `text` in the notice (its role="status" reads it out) for NOTICE_MS. */
function showToast(text) {
  clearTimeout(noticeTimer);
  page.notice.textContent = text;
  page.notice.hidden = false;
  noticeTimer = setTimeout(() => {
    page.notice.hidden = true;
  }, NOTICE_MS);
}

boot();
