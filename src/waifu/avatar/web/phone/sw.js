// Service worker of Cyrene's phone page, which Chrome installs to the home screen as an app. It keeps a copy of the
// page's own files only, network first, so a newer page always wins and the app still opens (and says the PC cannot be
// reached) when it is offline, or when Tailscale answers for a PC where Cyrene is not running (a 502). Everything else
// passes untouched: her answers, the settings in /config.json, pairing, the model, the Cubism Core, and the renderers,
// which all need the device cookie and are never stored here.
const CACHE_PREFIX = "cyrene-shell-";
const CACHE = `${CACHE_PREFIX}v1`;
const SHELL = new Set([
  "/",
  "/phone/phone.js",
  "/phone/audio.js",
  "/phone/camera.js",
  "/phone/phone.css",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
]);

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const stale = (await self.caches.keys()).filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE);
      await Promise.all(stale.map((name) => self.caches.delete(name)));
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || !SHELL.has(url.pathname)) return;
  event.respondWith(networkFirst(event, url.pathname));
});

/**
 * The network's answer, a copy of which is kept under `path`. Offline, or on a server error (5xx), the kept copy;
 * without one, the network's error.
 */
async function networkFirst(event, path) {
  const cache = await self.caches.open(CACHE);
  let response;
  try {
    response = await self.fetch(event.request);
  } catch (error) {
    const kept = await cache.match(path);
    if (kept) return kept;
    throw error;
  }
  if (response.status >= 500) return (await cache.match(path)) ?? response;
  if (response.ok && response.type === "basic") {
    event.waitUntil(
      cache.put(path, response.clone()).catch((error) => console.warn(`Could not keep ${path} for offline use`, error)),
    );
  }
  return response;
}
