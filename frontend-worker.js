// Cloudflare Worker — serves the static frontend AND keeps the backend awake.
//
// Why this file exists: an assets-only Worker (no "main" script) can't run
// scheduled code. Cloudflare needs an exported `scheduled` handler to attach
// a cron trigger to, so the Worker gains a script whose only extra job is a
// periodic ping. The cron schedule itself lives in wrangler.jsonc.
//
// Replaces the external cron-job.org job, which silently disables itself
// after a few consecutive failures (e.g. during a Render redeploy).

const API_BASE = "https://digital-cookbook-api.onrender.com"; // matches app.js

export default {
  // Runs on the cron schedule in wrangler.jsonc — never in response to a user.
  // waitUntil keeps the Worker alive until the ping resolves; without it
  // Cloudflare kills the Worker the moment scheduled() returns and the
  // request is cut off mid-flight, waking nothing.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      fetch(`${API_BASE}/`).catch(() => {}) // a failed ping must not throw
    );
  },

  // Static assets are matched BEFORE this handler runs, so in practice it only
  // catches paths with no matching file. Delegating to the ASSETS binding
  // keeps 404s (and .html stripping) behaving exactly as they did before.
  async fetch(request, env) {
    return env.ASSETS.fetch(request);
  },
};
