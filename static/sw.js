/* G & M - service worker minimale (necessario perché il sito sia installabile come app).
 * Network-first: prova sempre la rete, usa la cache solo se la rete non risponde.
 * Le chiamate /api/* vanno SEMPRE in rete: sono dati live. */
const CACHE_NAME = "gm-v1";
const STATIC_ASSETS = ["/", "/static/style.css", "/static/app.js", "/static/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) =>
    Promise.all(STATIC_ASSETS.map((url) => cache.add(url).catch(() => null)))));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/conferma/")) return;
  event.respondWith(
    fetch(event.request).then((res) => {
      if (res.ok && url.origin === location.origin) {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((c) => c.put(event.request, copy));
      }
      return res;
    }).catch(() => caches.match(event.request, { ignoreSearch: true }))
  );
});
