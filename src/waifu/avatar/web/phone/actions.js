// Pure helpers of the phone page's quick buttons (SPEC, "tombol cepat di HP (v1.35)"): the owner's switches and
// routines, one tap each, run on the PC without asking Cyrene. The PC lists the buttons (GET /actions) and runs the
// one whose id the page sends back (POST /action); the page never names a device or a command itself. No DOM here,
// so `node --test` covers them.

const JSON_TYPE = "application/json";
// The quick buttons of an alarm that rings on the PC (SPEC, "alarm bangun (v1.46)").
const ALARM_PREFIX = "al.";
// "Dengar lagi", while her hearing is paused by Ctrl+Alt+Q (SPEC, "hotkey global (v1.48)").
const LISTEN_PREFIX = "mic.";

const isPlainObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const isText = (value) => typeof value === "string" && value.trim() !== "";

/** One button {id, label} of GET /actions' answer, or null when it is not well formed. */
function parseAction(action) {
  if (!isPlainObject(action) || !isText(action.id) || !isText(action.label)) return null;
  return { id: action.id, label: action.label };
}

/**
 * GET /actions' answer `{groups: [{name, actions: [{id, label}]}]}` as the groups to show, in the PC's order: a
 * button or a group that is not well formed is dropped, and so is a group left without buttons. Null when the answer
 * has no list of groups.
 */
export function parseActionGroups(data) {
  if (!isPlainObject(data) || !Array.isArray(data.groups)) return null;
  return data.groups.flatMap((group) => {
    if (!isPlainObject(group) || !isText(group.name) || !Array.isArray(group.actions)) return [];
    const actions = group.actions.map(parseAction).filter(Boolean);
    return actions.length > 0 ? [{ name: group.name, actions }] : [];
  });
}

/** Whether the button `id` is one of a ringing alarm's: pressing it takes the alarm's buttons away. */
export function isAlarmAction(id) {
  return id.startsWith(ALARM_PREFIX);
}

/**
 * Whether the button `id` shows only for a while: a ringing alarm's, or "Dengar lagi" while her hearing is paused.
 * Pressing one may take it away, and it may go by itself, so the list is read again.
 */
export function isPassingAction(id) {
  return isAlarmAction(id) || id.startsWith(LISTEN_PREFIX);
}

/** The body of POST /action for the button `id`: {body, contentType}. */
export function actionRequest(id) {
  return { body: JSON.stringify({ id }), contentType: JSON_TYPE };
}

/** POST /action's answer `{ok, message}` as {ok, text}: what the PC did, or why a device failed. Null otherwise. */
export function actionOutcome(data) {
  if (!isPlainObject(data) || typeof data.ok !== "boolean" || !isText(data.message)) return null;
  return { ok: data.ok, text: data.message };
}
