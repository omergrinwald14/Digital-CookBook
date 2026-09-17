// Frontend behavior — talks to the backend and renders the result.
// Separate from index.html so structure (HTML) and logic (JS) don't mix.

const API_BASE = "https://digital-cookbook-api.onrender.com"; // live backend (Render)

// Identity (5-3c): the user's email, typed once on the login screen and kept
// in localStorage. Family-trust model — the backend trusts the X-User header.
const USER_KEY = "cookbook-user";

// One seam for every backend call: inject the X-User header here, so all
// requests are identified the same way and a future auth upgrade (real
// tokens) only touches this function.
function apiFetch(path, options = {}) {
  const user = localStorage.getItem(USER_KEY);
  return fetch(`${API_BASE}${path}`, {
    ...options,
    // Caller headers spread LAST so an explicit X-User (e.g. a queued
    // share's original owner) beats the logged-in default.
    headers: { ...(user ? { "X-User": user } : {}), ...(options.headers || {}) },
  });
}

// Cache of tags (id+name), refreshed by loadTags(). Card pickers
// read this so each card can list every tag without its own fetch.
let tagsCache = [];

// Last-fetched recipe list. Search filters this in the browser — no refetch,
// so it stays instant even when the backend is cold.
let recipesCache = [];

// The server-side filter behind the current list, remembered so widgets can
// re-apply it after changing a recipe (e.g. untagging while filtered).
let currentFilter = { tags: null, collection: null };

// Filter state. Tags AND-combine with each other and with ONE collection
// (Favorites/Up Next toggle each other off — the backend takes one).
// "Untagged" is a pseudo-tag: it excludes real tag picks but pairs with a
// collection. "All" is just "no filters".
let activeTags = new Set();
let activeCollection = null;   // "favorites" | "up_next" | null
let untaggedOn = false;

// Build one clickable tag chip that hands its li to the handler (each chip
// manages its own highlight). If tagId is given, add a ✕ to delete it.
function makeChip(label, onClick, tagId = null) {
  const li = document.createElement("li");
  const text = document.createElement("span");
  text.textContent = label;
  li.appendChild(text);
  li.addEventListener("click", () => onClick(li));
  if (tagId !== null) {
    const del = document.createElement("button");
    del.className = "chip-delete";
    del.textContent = "×";
    del.title = `Delete "${label}"`;
    del.addEventListener("click", (e) => {
      e.stopPropagation();                 // don't also trigger the filter
      deleteTag(tagId, label);
    });
    li.appendChild(del);
  }
  return li;
}

// Fetch the tag list and paint it as clickable filter chips.
async function loadTags() {
  const list = document.getElementById("tag-list");
  try {
    const res = await apiFetch("/tags");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const tags = await res.json();
    tagsCache = tags; // keep the cache in sync for card pickers
    list.innerHTML = ""; // clear "Loading…"
    activeTags.clear(); // fresh chips = fresh selection state
    activeCollection = null;
    untaggedOn = false;

    // One place recomputes the query from the toggle states; "All" lights
    // up only when nothing is filtered.
    const refresh = () => {
      const noFilter = !activeTags.size && !activeCollection && !untaggedOn;
      all.classList.toggle("active", noFilter);
      const names = untaggedOn ? ["Untagged"] : [...activeTags];
      loadRecipes(names.length ? names : null, activeCollection);
    };

    const all = makeChip("All", () => {
      activeTags.clear(); activeCollection = null; untaggedOn = false;
      list.querySelectorAll("li.active").forEach((li) => li.classList.remove("active"));
      refresh();
    });
    list.appendChild(all);
    all.classList.add("active"); // "All" highlighted on load
    // Cross-tag collections: toggles, one at a time, combinable with tags.
    const fav = makeChip("★ Favorites", (li) => {
      activeCollection = li.classList.toggle("active") ? "favorites" : null;
      if (activeCollection) upNext.classList.remove("active");
      refresh();
    });
    const upNext = makeChip("🔖 Up Next", (li) => {
      activeCollection = li.classList.toggle("active") ? "up_next" : null;
      if (activeCollection) fav.classList.remove("active");
      refresh();
    });
    list.append(fav, upNext);
    const untagged = makeChip("Untagged", (li) => {
      untaggedOn = li.classList.toggle("active");
      if (untaggedOn) {
        // Untagged replaces any real-tag selection (they can't co-match).
        activeTags.clear();
        list.querySelectorAll("li.tag-filter.active")
          .forEach((c) => c.classList.remove("active"));
      }
      refresh();
    });
    untagged.classList.add("extra");
    list.appendChild(untagged);
    for (const tag of tags) {
      // Tag chips TOGGLE (multi-select, AND). Turning the last filter off
      // falls back to "All".
      const chip = makeChip(tag.name, (li) => {
        const on = li.classList.toggle("active");
        if (on) {
          activeTags.add(tag.name);
          untaggedOn = false;
          untagged.classList.remove("active");
        } else {
          activeTags.delete(tag.name);
        }
        refresh();
      }, tag.id);
      chip.classList.add("extra", "tag-filter");
      list.appendChild(chip);
    }
    // Progressive disclosure: chips past the three pinned ones hide behind
    // a More/Less toggle so tags don't swallow the screen.
    const toggle = document.createElement("li");
    toggle.textContent = "More ▾";
    toggle.addEventListener("click", () => {
      const collapsed = list.classList.toggle("collapsed");
      toggle.textContent = collapsed ? "More ▾" : "Less ▴";
    });
    list.appendChild(toggle);
    list.classList.add("collapsed");
  } catch (err) {
    list.textContent = `Could not load tags: ${err.message}`;
  }
}

// Fetch recipes (optionally filtered by tags) and render each as a card.
// `tags` is an array of names; ?tag= repeats and the backend ANDs them.
async function loadRecipes(tags = null, collection = null) {
  currentFilter = { tags, collection };
  const container = document.getElementById("recipe-list");
  container.textContent = "Loading…";
  try {
    // Build an encoded query string; filter by tags OR collection.
    const params = new URLSearchParams();
    for (const t of tags || []) params.append("tag", t);
    if (collection) params.set("collection", collection);
    const qs = params.toString();
    const res = await apiFetch(`/recipes${qs ? `?${qs}` : ""}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    recipesCache = await res.json();
    renderRecipes(applySearch(recipesCache));
  } catch (err) {
    container.textContent = `Could not load recipes: ${err.message}`;
  }
}

// Paint a list of recipes as cards (used by both fetch and search).
function renderRecipes(recipes) {
  const container = document.getElementById("recipe-list");
  container.innerHTML = ""; // clear "Loading…" / previous cards
  if (recipes.length === 0) {
    container.textContent = "No recipes here yet.";
    return;
  }
  for (const recipe of recipes) {
    container.appendChild(renderRecipeCard(recipe));
  }
}

// Narrow a recipe list by the search box: match title, ingredient, or tag names.
function applySearch(recipes) {
  const q = document.getElementById("search-box").value.trim().toLowerCase();
  if (!q) return recipes;
  return recipes.filter((r) =>
    (r.title || "").toLowerCase().includes(q) ||
    (r.ingredients || []).some((i) => (i.name || "").toLowerCase().includes(q)) ||
    (r.tags || []).some((t) => (t.name || "").toLowerCase().includes(q))
  );
}

// Re-filter on every keystroke — pure in-browser work, so it's instant.
document.getElementById("search-box").addEventListener("input",
  () => renderRecipes(applySearch(recipesCache)));

// Split a textarea into trimmed, non-empty lines (edit form + manual entry).
const toLines = (s) => s.split("\n").map((l) => l.trim()).filter(Boolean);

// Format one ingredient: "320 g Spaghetti", "2 cloves Garlic", or just
// "Olive oil" when quantity/unit are null. filter(Boolean) drops the blanks.
function formatIngredient(ing) {
  return [ing.quantity, ing.unit, ing.name].filter(Boolean).join(" ");
}

// Build the collapsible details (ingredients + steps) for a recipe.
function renderDetails(recipe) {
  const details = document.createElement("div");
  details.className = "recipe-details";
  details.hidden = true; // collapsed until the card is clicked

  const ingTitle = document.createElement("h4");
  ingTitle.textContent = "Ingredients";
  details.appendChild(ingTitle);
  if (recipe.ingredients?.length) {
    const ul = document.createElement("ul");
    for (const ing of recipe.ingredients) {
      const li = document.createElement("li");
      li.textContent = formatIngredient(ing);
      li.dir = "auto";                 // Hebrew -> RTL, English -> LTR (per item)
      ul.appendChild(li);
    }
    details.appendChild(ul);
  } else {
    const p = document.createElement("p");
    p.textContent = "No ingredients listed.";
    details.appendChild(p);
  }

  const stepTitle = document.createElement("h4");
  stepTitle.textContent = "Steps";
  details.appendChild(stepTitle);
  if (recipe.steps?.length) {
    const ol = document.createElement("ol");
    for (const step of recipe.steps) {
      const li = document.createElement("li");
      li.textContent = step;
      li.dir = "auto";                 // Hebrew -> RTL, English -> LTR (per item)
      ol.appendChild(li);
    }
    details.appendChild(ol);
  } else {
    const p = document.createElement("p");
    p.textContent = "No steps listed.";
    details.appendChild(p);
  }
  return details;
}

// Build a flag toggle button for a recipe card (★ Favorites / 🔖 Up Next).
function makeFlagToggle(recipe, key, onGlyph, offGlyph, label) {
  const btn = document.createElement("button");
  btn.className = "flag-toggle";
  const render = () => {
    btn.textContent = recipe[key] ? onGlyph : offGlyph;
    btn.classList.toggle("active", recipe[key]);
    btn.title = recipe[key] ? `Remove from ${label}` : `Add to ${label}`;
  };
  render();
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();                  // don't expand the card
    btn.disabled = true;
    try {
      const res = await apiFetch(`/recipes/${recipe.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: !recipe[key] }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      recipe[key] = !recipe[key];          // reflect new state locally
      render();
    } catch (err) {
      alert(`Could not update: ${err.message}`);
    } finally {
      btn.disabled = false;
    }
  });
  return btn;
}

// Render a card's tags as small chips (× removes one) plus a "+" button
// that swaps into a select for adding one. Every change PATCHes the FULL
// list (replacement semantics) and rebuilds this widget from the response.
function renderTagChips(recipe) {
  const wrap = document.createElement("div");
  wrap.className = "recipe-tags";
  wrap.addEventListener("click", (e) => e.stopPropagation()); // don't toggle card

  const names = (recipe.tags || []).map((t) => t.name);

  // One save path for add and remove: send the complete new list, trust
  // the server's answer, repaint. No local bookkeeping to drift.
  async function saveTags(newNames) {
    const res = await apiFetch(`/recipes/${recipe.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tags: newNames }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    recipe.tags = (await res.json()).tags;
    // A tag change can add/remove this card from a tag-filtered view (incl.
    // "Untagged"), so refetch the list; otherwise repaint just this widget.
    if (currentFilter.tags?.length) {
      await loadRecipes(currentFilter.tags, currentFilter.collection);
    } else {
      wrap.replaceWith(renderTagChips(recipe));
    }
  }

  for (const name of names) {
    const chip = document.createElement("span");
    chip.className = "tag-chip";
    const label = document.createElement("span");
    label.textContent = name;
    chip.appendChild(label);
    const del = document.createElement("button");
    del.className = "chip-delete";
    del.textContent = "×";
    del.title = `Remove "${name}"`;
    del.addEventListener("click", async () => {
      del.disabled = true;
      chip.classList.add("pending");   // fade NOW; the repaint confirms later
      try {
        await saveTags(names.filter((n) => n !== name));
      } catch (err) {
        alert(`Could not remove tag: ${err.message}`);
        chip.classList.remove("pending");
        del.disabled = false;
      }
    });
    chip.appendChild(del);
    wrap.appendChild(chip);
  }

  // The "+" swaps into a select: existing tags not yet on the recipe,
  // plus the "new tag" sentinel (same create flow as the add form).
  function makeAddSelect() {
    const select = document.createElement("select");
    select.className = "tag-select";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Add tag…";
    placeholder.disabled = true;
    placeholder.selected = true;
    select.appendChild(placeholder);
    for (const t of tagsCache) {
      if (names.includes(t.name)) continue;   // already on the recipe
      const opt = document.createElement("option");
      opt.value = t.name;
      opt.textContent = t.name;
      select.appendChild(opt);
    }
    const newOpt = document.createElement("option");
    newOpt.value = "__new__";
    newOpt.textContent = "＋ New tag…";
    select.appendChild(newOpt);

    let busy = false; // guards the blur-restore while a change is in flight
    select.addEventListener("change", async () => {
      busy = true;
      let chosen = select.value;
      if (chosen === "__new__") {
        const name = (prompt("New tag name:") || "").trim();
        if (!name) { wrap.replaceWith(renderTagChips(recipe)); return; } // cancelled
        // Only create it if it's genuinely new (avoids duplicate-insert errors).
        if (!tagsCache.some((t) => t.name === name)) {
          try {
            const res = await apiFetch("/tags", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ name }),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            await loadTags(); // refresh filter chips + cache with the new one
          } catch (err) {
            alert(`Could not create tag: ${err.message}`);
            wrap.replaceWith(renderTagChips(recipe));
            return;
          }
        }
        chosen = name;
      }
      try {
        await saveTags([...names, chosen]);
      } catch (err) {
        alert(`Could not add tag: ${err.message}`);
        wrap.replaceWith(renderTagChips(recipe));
      }
    });
    // Clicking away without choosing restores the "+" button.
    select.addEventListener("blur", () => {
      if (!busy) wrap.replaceWith(renderTagChips(recipe));
    });
    setTimeout(() => select.focus(), 0);
    return select;
  }

  const add = document.createElement("button");
  add.className = "tag-add";
  add.textContent = "+";
  add.title = "Add tag";
  add.addEventListener("click", () => add.replaceWith(makeAddSelect()));
  wrap.appendChild(add);

  return wrap;
}

// Share (9-5): ↗ swaps into a picker — friends drop-list + "someone else…"
// by email. The backend validates the recipient (404 for an unknown email)
// and auto-adds new recipients to the friends list for next time.
function makeShareButton(recipe) {
  const btn = document.createElement("button");
  btn.className = "share-btn";
  // Feather "share-2" — the standard web share icon (three linked dots).
  btn.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/>' +
    '<circle cx="18" cy="19" r="3"/>' +
    '<line x1="8.6" y1="10.7" x2="15.4" y2="6.3"/>' +
    '<line x1="8.6" y1="13.3" x2="15.4" y2="17.7"/></svg>Share';
  btn.title = "Share recipe";
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();                  // don't expand the card
    btn.disabled = true;
    let friends = [];
    try {
      const res = await apiFetch("/friends");
      if (res.ok) friends = await res.json();
    } catch { /* offline — the picker still offers "someone else…" */ }
    btn.replaceWith(makeSharePicker(recipe, friends));
  });
  return btn;
}

function makeSharePicker(recipe, friends) {
  const select = document.createElement("select");
  select.className = "tag-select";
  select.addEventListener("click", (e) => e.stopPropagation());
  const restore = () => select.replaceWith(makeShareButton(recipe));

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Share with…";
  placeholder.disabled = true;
  placeholder.selected = true;
  select.appendChild(placeholder);
  for (const f of friends) {
    const opt = document.createElement("option");
    opt.value = f.friend_email;
    opt.textContent = f.friend_email;
    select.appendChild(opt);
  }
  const other = document.createElement("option");
  other.value = "__other__";
  other.textContent = "✉ Someone else…";
  select.appendChild(other);

  let busy = false;                // guards the blur-restore mid-request
  select.addEventListener("change", async () => {
    busy = true;
    let to = select.value;
    if (to === "__other__") {
      to = (prompt("Recipient's email (they must have used this app):") || "").trim();
      if (!to) { restore(); return; }
    }
    try {
      const res = await apiFetch(`/recipes/${recipe.id}/share`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to }),
      });
      if (!res.ok) {
        // Surface the backend's own message (e.g. "No user with the email …").
        const detail = (await res.json().catch(() => ({}))).detail;
        throw new Error(detail || `HTTP ${res.status}`);
      }
      alert(`Recipe shared with ${to}.`);
    } catch (err) {
      alert(`Could not share: ${err.message}`);
    }
    restore();
  });
  select.addEventListener("blur", () => { if (!busy) restore(); });
  setTimeout(() => select.focus(), 0);
  return select;
}

// Build one recipe card. Uses textContent/attributes (not innerHTML) so
// caption-derived text can't inject markup (XSS-safe).
function renderRecipeCard(recipe) {
  const card = document.createElement("article");
  card.className = "recipe-card";

  const del = document.createElement("button");
  del.className = "recipe-delete";
  del.textContent = "×";
  del.title = "Delete recipe";
  del.addEventListener("click", async (e) => {
    e.stopPropagation();                       // don't expand the card
    if (!confirm(`Delete "${recipe.title || "this recipe"}"? This can't be undone.`)) return;
    del.disabled = true;
    try {
      const res = await apiFetch(`/recipes/${recipe.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      card.remove();                           // drop just this card from the view
    } catch (err) {
      alert(`Could not delete: ${err.message}`);
      del.disabled = false;
    }
  });
  card.appendChild(del);

  const edit = document.createElement("button");
  edit.className = "recipe-edit";
  edit.textContent = "✎";
  edit.title = "Edit recipe";
  edit.addEventListener("click", (e) => {
    e.stopPropagation();                       // don't expand the card
    card.replaceWith(renderEditForm(recipe));  // swap card -> edit form
  });
  card.appendChild(edit);

  if (recipe.thumbnail) {
    const img = document.createElement("img");
    img.referrerPolicy = "no-referrer";   // Instagram's CDN 403s cross-site referers
    img.src = recipe.thumbnail;
    img.alt = recipe.title || "Recipe";
    card.appendChild(img);
  }

  const title = document.createElement("h3");
  title.textContent = recipe.title || "Untitled";
  title.dir = "auto";                  // Hebrew -> RTL, English -> LTR (per title)
  card.appendChild(title);

  card.appendChild(renderTagChips(recipe));

  const tools = document.createElement("div");
  tools.className = "recipe-tools";
  tools.appendChild(makeFlagToggle(recipe, "is_favorite", "★", "☆", "Favorites"));
  tools.appendChild(makeFlagToggle(recipe, "is_up_next", "🔖", "🔖", "Up Next"));
  tools.appendChild(makeShareButton(recipe));
  card.appendChild(tools);

  if (recipe.source_url) {
    const link = document.createElement("a");
    link.href = recipe.source_url;
    link.textContent = "Watch Video";
    link.target = "_blank";   // open in a new tab
    link.rel = "noopener";    // don't expose our page to the opened tab
    link.addEventListener("click", (e) => e.stopPropagation()); // don't toggle
    card.appendChild(link);
  }

  const details = renderDetails(recipe);
  card.appendChild(details);
  card.addEventListener("click", () => { details.hidden = !details.hidden; });

  return card;
}

// Build the in-place edit form that temporarily replaces a recipe card.
// Ingredients/steps are edited as plain text, one per line. Edited
// ingredients are saved as name-only (quantity/unit null) — cards display
// the same either way, we just stop guessing structure from free text.
function renderEditForm(recipe) {
  const card = document.createElement("article");
  card.className = "recipe-card editing";
  const form = document.createElement("form");
  form.className = "edit-form";

  // Small helper: a labelled field, so the form reads top-to-bottom.
  function field(labelText, el) {
    const label = document.createElement("label");
    label.textContent = labelText;
    label.appendChild(el);
    form.appendChild(label);
  }

  const titleIn = document.createElement("input");
  titleIn.type = "text";
  titleIn.value = recipe.title || "";
  titleIn.dir = "auto";
  field("Title", titleIn);

  const ingIn = document.createElement("textarea");
  ingIn.rows = 6;
  ingIn.dir = "auto";
  ingIn.value = (recipe.ingredients || []).map(formatIngredient).join("\n");
  field("Ingredients (one per line)", ingIn);

  const stepsIn = document.createElement("textarea");
  stepsIn.rows = 8;
  stepsIn.dir = "auto";
  stepsIn.value = (recipe.steps || []).join("\n");
  field("Steps (one per line)", stepsIn);

  const buttons = document.createElement("div");
  buttons.className = "edit-buttons";
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "Save";
  const cancel = document.createElement("button");
  cancel.type = "button";                     // type=button: don't submit
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => {
    card.replaceWith(renderRecipeCard(recipe)); // discard edits, restore card
  });
  buttons.append(save, cancel);
  form.appendChild(buttons);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      title: titleIn.value.trim(),
      ingredients: toLines(ingIn.value).map((name) => ({ name, quantity: null, unit: null })),
      steps: toLines(stepsIn.value),
    };
    if (!body.title) { alert("Title cannot be empty."); return; }
    // Disabling alone looked like nothing happened on a cold server. Say so.
    const label = save.textContent;
    save.disabled = true;
    save.textContent = "Saving…";
    try {
      const res = await apiFetch(`/recipes/${recipe.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      Object.assign(recipe, body);              // reflect saved edits locally

      if (photoAction === "replace") {
        save.textContent = "Uploading photo…";
        const fd = new FormData();
        fd.append("photo", fileIn.files[0]);
        const up = await apiFetch(`/recipes/${recipe.id}/photo`, { method: "POST", body: fd });
        if (!up.ok) throw new Error(`photo upload failed (HTTP ${up.status})`);
        recipe.thumbnail = (await up.json()).thumbnail;
      } else if (photoAction === "remove") {
        const rm = await apiFetch(`/recipes/${recipe.id}/photo`, { method: "DELETE" });
        if (!rm.ok) throw new Error(`photo removal failed (HTTP ${rm.status})`);
        recipe.thumbnail = null;
      }
      card.replaceWith(renderRecipeCard(recipe));
    } catch (err) {
      // The text edits may already be saved even if the photo step failed, so
      // say what went wrong and leave the form open rather than pretending.
      alert(`Could not save: ${err.message}`);
      save.disabled = false;
      save.textContent = label;
    }
  });

  card.appendChild(form);
  return card;
}

// Create a tag from the add form, then refresh the chips.
async function addTag(event) {
  event.preventDefault();           // don't reload the page on submit
  const input = document.getElementById("new-tag-name");
  const name = input.value.trim();
  if (!name) return;                // ignore empty; backend also guards (400)
  try {
    const res = await apiFetch("/tags", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    input.value = "";               // clear the box
    await loadTags();               // repaint chips, now including the new one
  } catch (err) {
    alert(`Could not add tag: ${err.message}`);
  }
}

document.getElementById("add-tag-form").addEventListener("submit", addTag);

// Import a recipe from a pasted URL via POST /import (slow: fetch + LLM parse).
async function importRecipe(event) {
  event.preventDefault();
  const input = document.getElementById("import-url");
  const button = event.target.querySelector("button");
  const status = document.getElementById("import-status");
  const url = input.value.trim();
  if (!url) return;

  button.disabled = true;                       // prevent double-submit
  status.hidden = false;
  status.textContent = "Importing… this can take 10–30s.";
  try {
    const res = await apiFetch("/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const recipe = await res.json();
    input.value = "";
    status.textContent = `Added: ${recipe.title || "Untitled"}`;
    await loadRecipes();                         // show the new recipe
  } catch (err) {
    status.textContent = `Import failed: ${err.message}`;
  } finally {
    button.disabled = false;                     // always re-enable
  }
}

document.getElementById("import-form").addEventListener("submit", importRecipe);

// Manual entry (8-2): the toggle reveals a form; submit POSTs /recipes.
const manualForm = document.getElementById("manual-form");
document.getElementById("manual-toggle").addEventListener("click", () => {
  manualForm.hidden = !manualForm.hidden;
});
document.getElementById("manual-cancel").addEventListener("click", () => {
  manualForm.reset();
  manualForm.hidden = true;
});
manualForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = document.getElementById("import-status");
  const title = document.getElementById("manual-title").value.trim();
  if (!title) return;                    // required attr guards; belt+braces
  const body = {
    title,
    ingredients: toLines(document.getElementById("manual-ingredients").value)
      .map((name) => ({ name, quantity: null, unit: null })),
    steps: toLines(document.getElementById("manual-steps").value),
  };
  const save = manualForm.querySelector('button[type="submit"]');
  const label = save.textContent;
  save.disabled = true;
  save.textContent = "Saving…";   // a disabled button alone reads as "nothing happened"
  try {
    const res = await apiFetch("/recipes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const rec = await res.json();
    const photo = document.getElementById("manual-photo").files[0];
    if (photo) {
      // FormData = the browser's multipart encoding; no manual Content-Type
      // header, or the boundary marker is lost and the upload breaks.
      const fd = new FormData();
      fd.append("photo", photo);
      save.textContent = "Uploading photo…";
      const up = await apiFetch(`/recipes/${rec.id}/photo`, { method: "POST", body: fd });
      if (!up.ok) alert("Recipe saved, but the photo upload failed.");
    }
    manualForm.reset();
    manualForm.hidden = true;
    status.hidden = false;
    status.textContent = `Added: ${title}`;
    await loadRecipes();
  } catch (err) {
    alert(`Could not save: ${err.message}`);
  } finally {
    save.disabled = false;
    save.textContent = label;
  }
});

// Remove a tag (after confirm), then refresh chips + recipes.
async function deleteTag(id, label) {
  if (!confirm(`Delete "${label}"? Its recipes will become Untagged.`)) return;
  try {
    const res = await apiFetch(`/tags/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await loadTags();         // repaint chips without the deleted one
    await loadRecipes();      // recipes may have become Untagged
  } catch (err) {
    alert(`Could not delete tag: ${err.message}`);
  }
}

// Shared-with-me inbox (9-6): pending offers land here. Add copies the
// recipe into this cookbook, Dismiss declines. Hidden when empty.
async function loadSharedInbox() {
  const section = document.getElementById("shared-inbox");
  const list = document.getElementById("shared-list");
  let offers = [];
  try {
    const res = await apiFetch("/shared");
    if (res.ok) offers = await res.json();
  } catch { /* offline — leave the section hidden */ }
  section.hidden = offers.length === 0;
  list.innerHTML = "";
  for (const offer of offers) list.appendChild(renderOffer(offer));
}

function renderOffer(offer) {
  const row = document.createElement("div");
  row.className = "shared-offer";
  if (offer.recipes?.thumbnail) {
    const img = document.createElement("img");
    img.referrerPolicy = "no-referrer";
    img.src = offer.recipes.thumbnail;
    img.alt = "";
    row.appendChild(img);
  }
  const text = document.createElement("span");
  text.className = "offer-title";
  text.dir = "auto";
  text.textContent = `${offer.recipes?.title || "Untitled"} — from ${offer.from_owner}`;
  row.appendChild(text);

  // One handler for both buttons: hit the endpoint, repaint the inbox.
  function actionButton(label, cls, path, refreshRecipes) {
    const btn = document.createElement("button");
    btn.className = cls;
    btn.textContent = label;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      row.classList.add("pending");   // fade NOW; the repaint confirms later
      try {
        const res = await apiFetch(`/shared/${offer.id}/${path}`, { method: "POST" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadSharedInbox();
        if (refreshRecipes) await loadRecipes();
      } catch (err) {
        alert(`Could not ${label.toLowerCase()}: ${err.message}`);
        row.classList.remove("pending");
        btn.disabled = false;
      }
    });
    return btn;
  }
  row.appendChild(actionButton("Add", "offer-add", "accept", true));
  row.appendChild(actionButton("Dismiss", "offer-dismiss", "dismiss", false));
  return row;
}

// Deliver queued shares from the page. Background Sync alone proved
// unreliable: Chrome stops retrying after ~3 attempts, and each attempt can
// die against Render's ~50s cold start — entries then sit in IndexedDB
// forever. Opening the app is now the guaranteed delivery path; the SW sync
// remains as a best-effort fast path. Mirrors sw.js's drop rule:
// ok or 4xx (retrying can't fix a bad URL) -> remove; 5xx/network -> keep.
async function drainPendingShares() {
  const banner = document.getElementById("pending-shares");
  let shares;
  try { shares = await listShares(); } catch { return; }  // IndexedDB unavailable
  if (!shares.length) return;
  banner.hidden = false;
  let saved = 0;
  for (const [i, share] of shares.entries()) {
    banner.textContent = `Saving ${shares.length} pending shared recipe(s)… (${i} done)`;
    try {
      // Queued entries carry who shared them; that owner wins over whoever
      // is logged in now (someone else may have opened the app since).
      const res = await apiFetch("/import", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(share.owner ? { "X-User": share.owner } : {}),
        },
        body: JSON.stringify({ url: share.url }),
      });
      if (res.ok || (res.status >= 400 && res.status < 500)) {
        await removeShare(share.id);
        if (res.ok) saved++;
      }
    } catch { /* offline / server down — keep queued for the next visit */ }
  }
  banner.textContent = saved
    ? `Saved ${saved} shared recipe(s).`
    : "Pending shares could not be saved — will retry on the next visit.";
  setTimeout(() => { banner.hidden = true; }, 6000);
  if (saved) await loadRecipes();
}

// Boot: tags first so card pickers have the cache; recipes render
// before the (possibly slow, cold-start) queue drain so the app is usable
// immediately.
function boot() {
  loadTags().then(loadRecipes).then(drainPendingShares).then(loadSharedInbox);
}

// Login gate (5-3c): with a stored identity, boot straight in; without one,
// show the login screen and boot after the email is saved.
const loginScreen = document.getElementById("login-screen");
const userBadge = document.getElementById("user-badge");

// The badge is just a 👤 icon (saves header width on phones); the email
// itself is revealed on tap, alongside the switch-user prompt.
let currentUserEmail = null;
function showUserBadge(email) {
  currentUserEmail = email;
  userBadge.textContent = "👤";
  userBadge.title = email;   // long-press / hover shows it too
  userBadge.hidden = false;
}

// The 👤 badge toggles a small dropdown (email + switch-user), instead of
// a native confirm — smoother, and it stays inside the app's own styling.
const userMenu = document.getElementById("user-menu");
userBadge.addEventListener("click", (e) => {
  e.stopPropagation();                       // don't trip the outside-click close
  document.getElementById("user-menu-email").textContent = currentUserEmail;
  userMenu.hidden = !userMenu.hidden;
});
document.getElementById("user-menu-switch").addEventListener("click", () => {
  localStorage.removeItem(USER_KEY);         // clear identity, start over
  location.reload();
});
// Tap anywhere outside the menu closes it.
document.addEventListener("click", (e) => {
  if (!userMenu.hidden && !userMenu.contains(e.target)) userMenu.hidden = true;
});

document.getElementById("login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  // Normalize (trim + lowercase) so "Omer@x" and "omer@x" are one cookbook.
  const email = document.getElementById("login-email").value.trim().toLowerCase();
  if (!email) return;
  localStorage.setItem(USER_KEY, email);
  apiFetch("/users", { method: "POST" }).catch(() => {}); // fire-and-forget
  loginScreen.hidden = true;
  showUserBadge(email);
  boot();
});

const storedUser = localStorage.getItem(USER_KEY);
if (storedUser) {
  showUserBadge(storedUser);
  boot();
} else {
  loginScreen.hidden = false;
}

// Install guide: auto-open the tab matching the visitor's phone; the tab
// pills let them switch anyway (covers iPads that report a Mac userAgent).
const installGuide = document.getElementById("install-guide");

function detectPlatform() {
  return /iPhone|iPad|iPod/.test(navigator.userAgent) ? "ios" : "android";
}

function selectGuideTab(platform) {
  document.getElementById("guide-android").hidden = platform !== "android";
  document.getElementById("guide-ios").hidden = platform !== "ios";
  document.querySelectorAll(".guide-tab").forEach((tab) =>
    tab.classList.toggle("active", tab.dataset.platform === platform)
  );
}

function openInstallGuide() {
  selectGuideTab(detectPlatform());
  installGuide.hidden = false;
}

document.getElementById("install-help").addEventListener("click", openInstallGuide);
document.getElementById("login-install-help").addEventListener("click", openInstallGuide);
document.getElementById("install-guide-close").addEventListener("click", () => {
  installGuide.hidden = true;
});
document.querySelectorAll(".guide-tab").forEach((tab) =>
  tab.addEventListener("click", () => selectGuideTab(tab.dataset.platform))
);

// Usage guide: same overlay pattern, no tabs — plain open/close.
const usageGuide = document.getElementById("usage-guide");
document.getElementById("usage-help").addEventListener("click", () => {
  usageGuide.hidden = false;
});
document.getElementById("usage-guide-close").addEventListener("click", () => {
  usageGuide.hidden = true;
});

// Register the service worker (enables PWA install + Web Share Target).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch((err) =>
    console.warn("SW registration failed:", err)
  );
}
