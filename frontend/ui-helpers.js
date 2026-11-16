// Small UI plumbing shared by the feature files (shopping, cook mode, meals).
// Loaded first, so everything after it can rely on these three helpers.

// ---- Full-screen overlays that the phone's Back button can close ----------
// In an installed PWA, Back with nothing in the history EXITS the app — a
// nasty surprise mid-recipe. Opening an overlay therefore pushes a history
// entry, and Back (popstate) closes the overlay instead of the app.
// One stack, because overlays can nest (a picker opened from another view).
const overlayStack = [];

function openOverlay(el, onClose) {
  el.hidden = false;
  document.body.classList.add("overlay-open");   // freeze the page behind it
  overlayStack.push({ el, onClose });
  history.pushState({ overlay: overlayStack.length }, "");
}

// Every close goes through history.back(), so the on-screen × and the
// phone's Back button share ONE code path (the popstate handler below).
function closeOverlay() {
  if (overlayStack.length) history.back();
}

window.addEventListener("popstate", () => {
  const top = overlayStack.pop();
  if (!top) return;                               // a Back that wasn't ours
  top.el.hidden = true;
  if (!overlayStack.length) document.body.classList.remove("overlay-open");
  if (top.onClose) top.onClose();
});

// ---- Self-cleaning event subscriptions ------------------------------------
// Widgets repaint themselves when shared state changes (a "publish/subscribe"
// pattern): whoever changes the state fires one document event and never
// needs to know who is listening. Cards are rebuilt constantly, so a listener
// removes itself the first time it finds its element gone from the page —
// otherwise every re-render would leak one more listener.
function subscribe(el, eventName, repaint) {
  const handler = () => {
    if (!el.isConnected) {
      document.removeEventListener(eventName, handler);
      return;
    }
    repaint();
  };
  document.addEventListener(eventName, handler);
}

function publish(eventName) {
  document.dispatchEvent(new CustomEvent(eventName));
}

// ---- Clipboard -------------------------------------------------------------
// navigator.clipboard needs a secure context and permission; the textarea
// trick is the classic fallback. Resolves true/false, never throws.
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { /* stays false */ }
    ta.remove();
    return ok;
  }
}
