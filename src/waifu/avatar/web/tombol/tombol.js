// The quick buttons on the PC (SPEC, "tombol cepat di PC (v1.42)"). `waifu tombol` serves this page at
// http://127.0.0.1:<port>/t/<token>/ and opens it in the PC's browser; every URL here is relative, so it keeps the
// token's prefix. The page shows the phone's buttons (GET actions), always open: the owner's switches and routines,
// run on the PC without asking Cyrene. A click sends the button's id to POST action (actions.js) and the PC's answer
// shows as a toast; one runs at a time.
import { actionOutcome, actionRequest, parseActionGroups } from "../phone/actions.js";
import { deskText, requestDesk } from "./desk.js";

const ACTIONS_URL = "actions";
const ACTION_URL = "action";
const NOTICE_MS = 6000;
const NO_BUTTONS_TEXT = "Belum ada tombol cepat di PC.";

const page = {
  message: document.getElementById("message"),
  notice: document.getElementById("notice"),
  groups: document.getElementById("groups"),
};

let buttons = [];
// Whether the PC is running a button; clicks meanwhile are ignored.
let running = false;
let noticeTimer = 0;

/** Reads the PC's buttons and shows them, or says in the message why they cannot show. */
async function boot() {
  const result = await requestDesk(fetch, ACTIONS_URL);
  if (result.problem) {
    showMessage(deskText(result.problem));
    return;
  }
  const groups = parseActionGroups(result.data);
  if (groups === null) {
    showMessage(deskText("bad_reply"));
    return;
  }
  if (groups.length === 0) {
    showMessage(NO_BUTTONS_TEXT);
    return;
  }
  buttons = [];
  page.groups.replaceChildren(...groups.map(quickGroup));
  page.message.hidden = true;
  page.groups.hidden = false;
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

/** Runs the button `id`, clicked as `button`, on the PC and toasts what it did or why it could not. */
async function runQuick(button, id) {
  if (running) return;
  setRunning(button, true);
  const result = await requestDesk(fetch, ACTION_URL, actionRequest(id));
  setRunning(button, false);
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
