// Meal planning — a "meal" is a named group of recipes (e.g. "Friday dinner").
// Deliberately separate from tags: a tag describes what a recipe IS, a meal
// is a plan for one occasion, and the same recipe can sit in many meals.
//
// Meals live in the database (unlike the shopping marks) because a plan is
// made on the couch and cooked from the phone in the kitchen — it has to
// follow the user across devices.

// Each meal: { id, name, created_at, recipes: [full recipe rows] }.
let mealsCache = [];
let mealsLoaded = false;
const expandedMeals = new Set();     // which meal cards are open (survives repaints)
const MEALS_EVENT = "meals-changed";

// One seam for the meal endpoints: JSON in, JSON out, and the backend's own
// error message (its "detail") surfaced instead of a bare status code.
async function mealApi(path, method = "GET", body = null) {
  const res = await apiFetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail;
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function loadMeals() {
  const list = document.getElementById("meal-list");
  if (!mealsLoaded) list.textContent = "Loading…";
  try {
    mealsCache = await mealApi("/meals");
    mealsLoaded = true;
    mealsChanged();
  } catch (err) {
    // Like the recipe list: meals already on screen beat an error message.
    if (!mealsLoaded) list.textContent = `Could not load meals: ${err.message}`;
  }
}

// Call after ANY change to mealsCache: repaints the Meals view and tells the
// 🍽 buttons on the recipe cards to refresh their on/off state.
function mealsChanged() {
  renderMeals();
  publish(MEALS_EVENT);
}

function renderMeals() {
  const list = document.getElementById("meal-list");
  list.innerHTML = "";
  if (!mealsCache.length) {
    list.textContent = "No meals yet. Name one above, then pick its recipes.";
    return;
  }
  for (const meal of mealsCache) list.appendChild(renderMealCard(meal));
}

async function createMeal(name) {
  const meal = await mealApi("/meals", "POST", { name });
  mealsCache.unshift(meal);            // newest first, same order the server uses
  return meal;
}

// Put a recipe into a meal or take it out; the local copy is updated only
// after the server agrees, so the screen never shows a change that failed.
async function setMealRecipe(meal, recipe, on) {
  await mealApi(`/meals/${meal.id}/recipes/${recipe.id}`, on ? "PUT" : "DELETE");
  meal.recipes = meal.recipes.filter((r) => r.id !== recipe.id);
  if (on) meal.recipes.push(recipe);
}

const mealHas = (meal, recipe) => meal.recipes.some((r) => r.id === recipe.id);

function renderMealCard(meal) {
  const card = document.createElement("article");
  card.className = "meal-card";
  const open = expandedMeals.has(meal.id);

  // Header: name + count, tap to fold/unfold.
  const head = document.createElement("div");
  head.className = "meal-head";
  const name = document.createElement("h3");
  name.textContent = meal.name;
  name.dir = "auto";
  const count = document.createElement("span");
  count.className = "meal-count";
  const n = meal.recipes.length;
  count.textContent = `${n} recipe${n === 1 ? "" : "s"} ${open ? "▴" : "▾"}`;
  head.append(name, count);
  head.addEventListener("click", () => {
    if (open) expandedMeals.delete(meal.id); else expandedMeals.add(meal.id);
    card.replaceWith(renderMealCard(meal));
  });
  card.appendChild(head);

  if (!open) {
    // Folded: a strip of thumbnails says what's inside at a glance.
    const strip = document.createElement("div");
    strip.className = "meal-thumbs";
    for (const r of meal.recipes.filter((r) => r.thumbnail).slice(0, 6)) {
      const img = document.createElement("img");
      img.referrerPolicy = "no-referrer";
      img.loading = "lazy";
      img.src = r.thumbnail;
      img.alt = r.title || "";
      strip.appendChild(img);
    }
    if (strip.children.length) {
      strip.addEventListener("click", () => head.click());
      card.appendChild(strip);
    }
    return card;
  }

  const body = document.createElement("div");
  body.className = "meal-body";
  if (!n) {
    const empty = document.createElement("p");
    empty.className = "meal-empty";
    empty.textContent = "Nothing here yet — add some recipes.";
    body.appendChild(empty);
  }
  for (const recipe of meal.recipes) body.appendChild(renderMealRecipe(meal, recipe));

  // One shopping list for the whole meal: everything marked missing across
  // its recipes, grouped under each recipe's title.
  body.appendChild(renderShoppingBar(() => meal.recipes));

  const actions = document.createElement("div");
  actions.className = "meal-actions";
  const add = document.createElement("button");
  add.type = "button";
  add.className = "meal-add";
  add.textContent = "+ Add recipes";
  add.addEventListener("click", () => openMealPicker(meal));

  const rename = document.createElement("button");
  rename.type = "button";
  rename.textContent = "Rename";
  rename.addEventListener("click", async () => {
    const next = (prompt("Meal name:", meal.name) || "").trim();
    if (!next || next === meal.name) return;
    try {
      await mealApi(`/meals/${meal.id}`, "PATCH", { name: next });
      meal.name = next;
      mealsChanged();
    } catch (err) {
      alert(`Could not rename: ${err.message}`);
    }
  });

  const del = document.createElement("button");
  del.type = "button";
  del.className = "meal-delete";
  del.textContent = "Delete meal";
  del.addEventListener("click", async () => {
    // Say what survives: people hesitate to delete a group if they fear it
    // takes its contents with it.
    if (!confirm(`Delete the meal "${meal.name}"? Its recipes stay in your cookbook.`)) return;
    del.disabled = true;
    try {
      await mealApi(`/meals/${meal.id}`, "DELETE");
      mealsCache = mealsCache.filter((m) => m.id !== meal.id);
      expandedMeals.delete(meal.id);
      mealsChanged();
    } catch (err) {
      alert(`Could not delete: ${err.message}`);
      del.disabled = false;
    }
  });
  actions.append(add, rename, del);
  body.appendChild(actions);

  card.appendChild(body);
  return card;
}

// One recipe inside an open meal: a compact row; tapping it unfolds the same
// ingredients + steps block the recipe cards use (renderDetails in app.js).
function renderMealRecipe(meal, recipe) {
  const row = document.createElement("div");
  row.className = "meal-recipe";

  const top = document.createElement("div");
  top.className = "meal-recipe-top";
  if (recipe.thumbnail) {
    const img = document.createElement("img");
    img.referrerPolicy = "no-referrer";
    img.loading = "lazy";
    img.src = recipe.thumbnail;
    img.alt = "";
    top.appendChild(img);
  }
  const title = document.createElement("span");
  title.className = "meal-recipe-title";
  title.dir = "auto";
  title.textContent = recipe.title || "Untitled";
  top.appendChild(title);

  if (recipe.steps?.length) top.appendChild(makeCookButton(recipe, "▶ Cook"));

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "meal-recipe-remove";
  remove.textContent = "×";
  remove.title = "Remove from this meal";
  remove.addEventListener("click", async (e) => {
    e.stopPropagation();
    remove.disabled = true;
    row.classList.add("pending");      // fade NOW; the repaint confirms later
    try {
      await setMealRecipe(meal, recipe, false);
      mealsChanged();
    } catch (err) {
      alert(`Could not remove: ${err.message}`);
      row.classList.remove("pending");
      remove.disabled = false;
    }
  });
  top.appendChild(remove);
  row.appendChild(top);

  const details = renderDetails(recipe, { cookButton: false });  // row has its own
  row.appendChild(details);
  top.addEventListener("click", () => { details.hidden = !details.hidden; });
  return row;
}

// The picker needs EVERY recipe, but the Recipes tab may be showing a
// filtered subset — reuse its list only when it is the unfiltered one.
async function allRecipes() {
  if (!currentFilter.tags && !currentFilter.collection && recipesCache.length) {
    return recipesCache;
  }
  const res = await apiFetch("/recipes");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// Full-screen checklist of all recipes for one meal. Each tick saves on its
// own, immediately — there is no Save button to forget, and "Done" only
// closes. A failed save un-ticks itself and says why.
async function openMealPicker(meal) {
  const el = document.getElementById("meal-picker");
  const list = document.getElementById("meal-picker-list");
  const search = document.getElementById("meal-picker-search");
  document.getElementById("meal-picker-title").textContent = meal.name;
  search.value = "";
  list.textContent = "Loading…";
  // Repaint the Meals view once on close, not on every tick behind the overlay.
  openOverlay(el, mealsChanged);

  let recipes;
  try {
    recipes = await allRecipes();
  } catch (err) {
    list.textContent = `Could not load recipes: ${err.message}`;
    return;
  }

  const paint = () => {
    const q = search.value.trim().toLowerCase();
    const shown = recipes.filter((r) => (r.title || "").toLowerCase().includes(q));
    list.innerHTML = "";
    if (!shown.length) {
      list.textContent = recipes.length ? "No recipe matches." : "Your cookbook is empty.";
      return;
    }
    for (const recipe of shown) {
      const row = document.createElement("label");
      row.className = "picker-row";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = mealHas(meal, recipe);
      row.classList.toggle("picked", box.checked);
      box.addEventListener("change", async () => {
        const on = box.checked;
        box.disabled = true;
        row.classList.toggle("picked", on);      // optimistic: looks done at once
        try {
          await setMealRecipe(meal, recipe, on);
        } catch (err) {
          box.checked = !on;                      // roll the tick back
          row.classList.toggle("picked", !on);
          alert(`Could not update the meal: ${err.message}`);
        } finally {
          box.disabled = false;
        }
      });
      row.appendChild(box);
      if (recipe.thumbnail) {
        const img = document.createElement("img");
        img.referrerPolicy = "no-referrer";
        img.loading = "lazy";
        img.src = recipe.thumbnail;
        img.alt = "";
        row.appendChild(img);
      }
      const title = document.createElement("span");
      title.dir = "auto";
      title.textContent = recipe.title || "Untitled";
      row.appendChild(title);
      list.appendChild(row);
    }
  };
  search.oninput = paint;     // property, not addEventListener: reopening must not stack handlers
  paint();
}

document.getElementById("meal-picker-close").addEventListener("click", closeOverlay);

// 🍽 on a recipe card: the reverse direction — from a recipe, choose a meal.
// Swaps into a select (the same pattern as the tag "+" and Share pickers).
function makeMealButton(recipe) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "flag-toggle meal-btn";
  btn.textContent = "🍽";
  const paint = () => {
    const names = mealsCache.filter((m) => mealHas(m, recipe)).map((m) => m.name);
    btn.classList.toggle("active", names.length > 0);
    btn.title = names.length ? `In: ${names.join(", ")}` : "Add to a meal";
  };
  paint();
  subscribe(btn, MEALS_EVENT, paint);
  btn.addEventListener("click", (e) => {
    e.stopPropagation();                  // don't expand the card
    btn.replaceWith(makeMealSelect(recipe));
  });
  return btn;
}

function makeMealSelect(recipe) {
  const select = document.createElement("select");
  select.className = "tag-select";
  select.addEventListener("click", (e) => e.stopPropagation());
  const restore = () => select.replaceWith(makeMealButton(recipe));

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Add to meal…";
  placeholder.disabled = true;
  placeholder.selected = true;
  select.appendChild(placeholder);
  for (const meal of mealsCache) {
    const opt = document.createElement("option");
    opt.value = meal.id;
    // ✓ marks meals that already hold this recipe; picking one removes it.
    opt.textContent = mealHas(meal, recipe) ? `✓ ${meal.name} (remove)` : meal.name;
    select.appendChild(opt);
  }
  const fresh = document.createElement("option");
  fresh.value = "__new__";
  fresh.textContent = "＋ New meal…";
  select.appendChild(fresh);

  let busy = false;                // guards the blur-restore mid-request
  select.addEventListener("change", async () => {
    busy = true;
    try {
      let meal;
      if (select.value === "__new__") {
        const name = (prompt("New meal name (e.g. Friday dinner):") || "").trim();
        if (!name) { restore(); return; }
        meal = await createMeal(name);
      } else {
        meal = mealsCache.find((m) => String(m.id) === select.value);
      }
      await setMealRecipe(meal, recipe, !mealHas(meal, recipe));
    } catch (err) {
      alert(`Could not update the meal: ${err.message}`);
    }
    restore();
    mealsChanged();
  });
  select.addEventListener("blur", () => { if (!busy) restore(); });
  setTimeout(() => select.focus(), 0);
  return select;
}

// New-meal form: create, open the card, and go straight to picking recipes —
// an empty meal is never what the user came for.
document.getElementById("new-meal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("new-meal-name");
  const button = e.target.querySelector("button");
  const name = input.value.trim();
  if (!name) return;
  button.disabled = true;
  try {
    const meal = await createMeal(name);
    input.value = "";
    expandedMeals.add(meal.id);
    mealsChanged();
    openMealPicker(meal);
  } catch (err) {
    alert(`Could not create the meal: ${err.message}`);
  } finally {
    button.disabled = false;
  }
});

// Recipes | Meals tabs. Opening Meals refetches, so edits made to a recipe
// on the other tab show up here (the last list stays visible meanwhile).
function showView(view) {
  document.getElementById("view-recipes").hidden = view !== "recipes";
  document.getElementById("view-meals").hidden = view !== "meals";
  document.querySelectorAll("#view-tabs button").forEach((tab) =>
    tab.classList.toggle("active", tab.dataset.view === view)
  );
  if (view === "meals") loadMeals();
}
document.querySelectorAll("#view-tabs button").forEach((tab) =>
  tab.addEventListener("click", () => showView(tab.dataset.view))
);
