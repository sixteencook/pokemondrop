/** Enregistrement du service worker (production uniquement).
 *
 * Point d'extension PWA : c'est ici qu'on branchera plus tard les
 * notifications push navigateur (PushManager.subscribe) et la détection
 * de mise à jour de l'application.
 */
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD) return;
  if (!("serviceWorker" in navigator)) return;

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // L'app fonctionne parfaitement sans SW ; échec silencieux.
    });
  });
}
