// share-queue.js — tiny IndexedDB queue for fire-and-forget shares.
// Plain script (no modules) so BOTH share.html and sw.js can load it.
// IndexedDB is the only storage a page and its service worker share.

const SHARE_DB = "cookbook-share";
const SHARE_STORE = "queue";

// IndexedDB's API predates Promises (it's callback-based); these two helpers
// wrap it so callers can use await like everywhere else in the app.
function openShareDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(SHARE_DB, 1);
    req.onupgradeneeded = () =>
      req.result.createObjectStore(SHARE_STORE, { keyPath: "id", autoIncrement: true });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function idbDone(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

// Add a shared URL to the queue (called by share.html). `owner` rides with
// the entry because the service worker that drains it can't read
// localStorage — identity must travel with the job itself.
async function enqueueShare(url, owner = null) {
  const db = await openShareDb();
  const id = await idbDone(
    db.transaction(SHARE_STORE, "readwrite")
      .objectStore(SHARE_STORE)
      .add({ url, owner, queuedAt: Date.now() })
  );
  db.close();
  return id;
}

// Read the whole queue (called by sw.js when a sync fires).
async function listShares() {
  const db = await openShareDb();
  const items = await idbDone(
    db.transaction(SHARE_STORE, "readonly").objectStore(SHARE_STORE).getAll()
  );
  db.close();
  return items;
}

// Remove one entry after a successful POST /import (called by sw.js).
async function removeShare(id) {
  const db = await openShareDb();
  await idbDone(
    db.transaction(SHARE_STORE, "readwrite").objectStore(SHARE_STORE).delete(id)
  );
  db.close();
}

// Keep a share the server REJECTED (4xx), with its reason, instead of deleting
// it. Rejections used to be deleted silently, so a failed share just vanished
// and nobody could tell why. Retrying can't fix a rejected link, so both
// drains skip entries marked failed; the app shows them until dismissed.
async function markShareFailed(share, error) {
  const db = await openShareDb();
  await idbDone(
    db.transaction(SHARE_STORE, "readwrite")
      .objectStore(SHARE_STORE)
      .put({ ...share, failed: true, error })
  );
  db.close();
}

// The server's reason for a 4xx (FastAPI sends {"detail": "..."}), else the
// bare status code.
async function rejectionReason(res) {
  try { return (await res.json()).detail || `HTTP ${res.status}`; }
  catch { return `HTTP ${res.status}`; }
}
