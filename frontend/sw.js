// Service worker — PWA install + Web Share Target + Background Sync retries.
importScripts("share-queue.js");

const API_BASE = "https://digital-cookbook-api.onrender.com"; // matches app.js
const SW_VERSION = "v6 drain-all-shares"; // bump on SW changes; readable at /sw-version

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
// NOTE: do NOT intercept the share_target navigation here. Chrome opens the
// PWA window for a share regardless of the SW's response (tested on-device;
// see GoogleChrome/workbox#2557), so a 204 can't keep the user in the
// sharing app — it only risks stranding a blank window. share.html handles
// shares visibly and lands in the app.
self.addEventListener("fetch", (event) => {
  // Diagnostic: the server has no /sw-version route, so only a controlling
  // SW can answer it — whatever appears there is the active SW's version.
  if (new URL(event.request.url).pathname === "/sw-version") {
    event.respondWith(new Response(SW_VERSION));
  }
});

// Fires when the browser decides we're online — even if the share page is
// long closed. waitUntil keeps the SW alive until the queue is drained.
self.addEventListener("sync", (event) => {
  if (event.tag === "share-import") {
    event.waitUntil(drainShareQueue());
  }
});

// Waits between tries inside ONE sync event: the first (failed) try wakes a
// cold Render server (~50s), the later tries land once it's up. waitUntil
// keeps the SW alive (~5 min budget), so sleeping here is legal.
const RETRY_DELAYS_MS = [0, 30000, 45000];

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function drainShareQueue() {
  let stuck = 0;
  for (const share of await listShares()) {
    let delivered = false;
    for (const delay of RETRY_DELAYS_MS) {
      if (delay) await sleep(delay); // give a cold server time to wake
      try {
        // Identity comes from the queue entry — a SW has no localStorage, and
        // the entry knows who queued it even if the app user changed since.
        const res = await fetch(`${API_BASE}/import`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(share.owner ? { "X-User": share.owner } : {}),
          },
          body: JSON.stringify({ url: share.url }),
        });
        // 4xx = bad URL, retrying can't fix it — drop the entry.
        // ok = saved (idempotent, so a re-run is harmless) — drop it too.
        // 5xx = transient server trouble — wait, then retry.
        if (res.ok || (res.status >= 400 && res.status < 500)) {
          await removeShare(share.id);
          delivered = true;
          break;
        }
      } catch { /* network error — wait, then retry */ }
    }
    // Still failing after all tries: count it, but keep going. Throwing here
    // abandoned every share further down the queue — one bad entry blocked
    // all the good ones behind it on this sync AND every sync after it.
    if (!delivered) stuck++;
  }
  // Report failure only once the whole queue has had a turn; the throw is what
  // makes the browser schedule another sync later.
  if (stuck) throw new Error(`${stuck} share(s) still failing after retries`);
}
