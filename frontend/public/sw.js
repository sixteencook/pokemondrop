/* Service worker du Drop Monitor — offline minimal.
 *
 * Stratégie volontairement simple et sûre :
 *   - /api/* et le WebSocket ne sont JAMAIS interceptés (données temps réel) ;
 *   - assets buildés (/assets/*, hachés) : cache-first (immuables) ;
 *   - navigations : network-first avec repli sur le shell en cache (offline).
 *
 * Évolutions prévues (architecture prête) : notifications push navigateur
 * (self.addEventListener("push", …)) et pré-cache plus riche.
 */

const VERSION = "v1";
const SHELL_CACHE = `dropmon-shell-${VERSION}`;
const ASSET_CACHE = `dropmon-assets-${VERSION}`;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(["/"]))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => ![SHELL_CACHE, ASSET_CACHE].includes(key))
          .map((key) => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;
  if (url.pathname.startsWith("/api/")) return; // jamais de cache API

  // Assets hachés du build : cache-first.
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) {
    event.respondWith(
      caches.open(ASSET_CACHE).then(async (cache) => {
        const cached = await cache.match(event.request);
        if (cached) return cached;
        const response = await fetch(event.request);
        if (response.ok) cache.put(event.request, response.clone());
        return response;
      })
    );
    return;
  }

  // Navigations : network-first, repli offline sur le shell.
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          caches.open(SHELL_CACHE).then((cache) => cache.put("/", response.clone()));
          return response.clone();
        })
        .catch(() => caches.match("/"))
    );
  }
});
