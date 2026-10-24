// Pure helpers of the PC's quick-button page (SPEC, "tombol cepat di PC (v1.42)"): the requests to the page's own
// server, which `waifu tombol` runs on 127.0.0.1 for as long as its console is open, and what their problems tell the
// owner. No DOM here, so `node --test` covers them; `fetch` is passed in.

// A routine of several devices can take a while; a server that has not answered by then is not coming back.
export const REQUEST_TIMEOUT_MS = 60_000;

const PROBLEM_TEXTS = {
  closed: "Tombol cepat di PC sudah ditutup. Jalankan waifu tombol lagi, lalu pakai halaman yang dibukanya.",
  timeout: "PC terlalu lama tidak menjawab. Coba lagi sebentar lagi.",
  server: "Ada masalah di PC. Coba lagi nanti.",
  bad_reply: "Jawaban dari PC tidak bisa dibaca. Muat ulang halaman ini.",
  bad_request: "PC menolak permintaan ini. Muat ulang halaman ini.",
  forbidden: "PC menolak permintaan dari alamat ini. Buka tombol cepat dengan waifu tombol.",
  action_unknown: "Tombol itu sudah tidak ada di PC. Muat ulang halaman ini.",
  action_limit: "Tombolnya terlalu sering ditekan. Tunggu sebentar, lalu coba lagi.",
  action_busy: "Perintah tadi masih jalan. Tunggu sebentar.",
};

export const PROBLEM_KINDS = Object.freeze(Object.keys(PROBLEM_TEXTS));

/**
 * The problem an error `status` of `route` ("actions" or "action") means for the owner (a kind of PROBLEM_KINDS). A
 * press of a button the PC no longer has is a 404, one pressed too often a 429, and one pressed while the last still
 * runs (from another tab) a 409. A list the server does not know is a 404 too: another `waifu tombol` answers on this
 * port with a token of its own, so this page's server was closed.
 */
export function deskProblem(route, status) {
  switch (status) {
    case 404:
      return route === "action" ? "action_unknown" : "closed";
    case 429:
      return "action_limit";
    case 409:
      return "action_busy";
    case 403:
      return "forbidden";
    case 400:
    case 411:
    case 413:
    case 415:
      return "bad_request";
    default:
      return "server";
  }
}

/**
 * One request to the page's own server, at `route` relative to the page (so under its token): a POST of `body` (with
 * `contentType`) when given, else a GET; without cookies, and never from the HTTP cache. Resolves to {data} (the JSON
 * answer) or {problem} (deskProblem, "closed", "timeout", or "bad_reply"); it never rejects.
 *
 * The server refuses a POST whose Origin is not its own, and its pages carry `Referrer-Policy: no-referrer`. Under the
 * Fetch spec, a POST that is not in "cors" mode carries `Origin: null` with that policy (Firefox does so), so the mode
 * is "cors" (the relative URL keeps it same-origin) and the referrer policy same-origin: the page's Origin always goes.
 */
export async function requestDesk(fetchFn, route, { body, contentType, timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const init = {
    method: "GET",
    mode: "cors",
    referrerPolicy: "same-origin",
    credentials: "omit",
    cache: "no-store",
    signal: controller.signal,
  };
  if (body !== undefined) Object.assign(init, { method: "POST", headers: { "Content-Type": contentType }, body });
  try {
    const response = await fetchFn(route, init);
    if (!response.ok) return { problem: deskProblem(route, response.status) };
    return { data: await response.json() };
  } catch (error) {
    if (controller.signal.aborted) return { problem: "timeout" };
    return { problem: error instanceof SyntaxError ? "bad_reply" : "closed" };
  } finally {
    clearTimeout(timer);
  }
}

/** The Indonesian text of problem `kind`; an unknown one reads as a problem on the PC. */
export function deskText(kind) {
  return PROBLEM_TEXTS[kind] ?? PROBLEM_TEXTS.server;
}
