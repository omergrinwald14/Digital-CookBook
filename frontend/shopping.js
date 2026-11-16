// Shopping list — mark the ingredients you are MISSING, then send them.
//
// The marks live in localStorage, not the database: "what is missing from my
// kitchen today" is throwaway state for the device in your hand, and keeping
// it local makes every tap instant (no round trip to the server).
//
// Shape: { "<recipe id>": ["2 cups flour", "Eggs", ...] }
// Marks are stored by the ingredient's TEXT, not its position, so editing a
// recipe cannot silently move a mark onto a different ingredient.
const MISSING_KEY = "cookbook-missing";
const MISSING_EVENT = "missing-changed";

function readAllMissing() {
  try {
    return JSON.parse(localStorage.getItem(MISSING_KEY)) || {};
  } catch {
    return {};      // absent, corrupt, or storage blocked — all mean "none"
  }
}

function writeAllMissing(all) {
  try {
    localStorage.setItem(MISSING_KEY, JSON.stringify(all));
  } catch { /* private mode / quota — marks just won't survive a reload */ }
  publish(MISSING_EVENT);       // every list and bar on screen repaints itself
}

// The marked ingredients of one recipe, in recipe order. Filtering through
// the CURRENT ingredients drops marks whose ingredient was edited away.
function missingItems(recipe) {
  const marked = new Set(readAllMissing()[recipe.id] || []);
  return (recipe.ingredients || []).map(formatIngredient).filter((t) => marked.has(t));
}

function toggleMissing(recipe, text) {
  const all = readAllMissing();
  const set = new Set(all[recipe.id] || []);
  if (set.has(text)) set.delete(text); else set.add(text);
  if (set.size) all[recipe.id] = [...set]; else delete all[recipe.id];
  writeAllMissing(all);
}

function clearMissing(recipes) {
  const all = readAllMissing();
  for (const r of recipes) delete all[r.id];
  writeAllMissing(all);
}

// The tappable ingredient list. Each row toggles "missing" on tap.
function renderIngredientList(recipe) {
  const ul = document.createElement("ul");
  ul.className = "ingredient-list";
  ul.addEventListener("click", (e) => e.stopPropagation());  // don't fold the card

  const paint = () => {
    const missing = new Set(missingItems(recipe));
    ul.innerHTML = "";
    for (const ing of recipe.ingredients) {
      const text = formatIngredient(ing);
      const li = document.createElement("li");
      li.textContent = text;
      li.dir = "auto";                 // Hebrew -> RTL, English -> LTR (per item)
      li.setAttribute("role", "checkbox");
      li.setAttribute("aria-checked", missing.has(text));
      li.classList.toggle("missing", missing.has(text));
      li.addEventListener("click", () => toggleMissing(recipe, text));
      ul.appendChild(li);
    }
  };
  paint();
  subscribe(ul, MISSING_EVENT, paint);
  return ul;
}

// Plain text for WhatsApp / the clipboard. The recipe title is the heading
// (wrapped in *…* = bold in WhatsApp), which also keeps the message in the
// recipe's own language instead of mixing in an English "Shopping list".
function buildShoppingText(groups) {
  return groups
    .map((g) => [`🛒 *${g.title}*`, ...g.items.map((i) => `• ${i}`)].join("\n"))
    .join("\n\n");
}

// The action bar under an ingredient list (one recipe) or a meal (several).
// `getRecipes` is a function, not a list, so a meal's bar always reads the
// meal's CURRENT recipes. Hidden while nothing is marked.
function renderShoppingBar(getRecipes) {
  const bar = document.createElement("div");
  bar.className = "shopping-bar";
  bar.addEventListener("click", (e) => e.stopPropagation());

  const count = document.createElement("span");
  count.className = "shopping-count";

  const groups = () => getRecipes()
    .map((r) => ({ title: r.title || "Untitled", items: missingItems(r) }))
    .filter((g) => g.items.length);

  const button = (label, cls, onClick) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = label;
    b.addEventListener("click", () => onClick(b));
    return b;
  };

  // wa.me with no phone number opens WhatsApp's own "send to…" chooser with
  // the text pre-filled — on a phone it jumps straight into the app.
  const whatsapp = button("WhatsApp", "shopping-wa", () => {
    const url = `https://wa.me/?text=${encodeURIComponent(buildShoppingText(groups()))}`;
    window.open(url, "_blank", "noopener");
  });
  const copy = button("Copy", "shopping-copy", async (b) => {
    const ok = await copyText(buildShoppingText(groups()));
    b.textContent = ok ? "Copied ✓" : "Copy failed";
    setTimeout(() => { b.textContent = "Copy"; }, 1500);
  });
  const clear = button("Clear", "shopping-clear", () => clearMissing(getRecipes()));

  const paint = () => {
    const n = groups().reduce((sum, g) => sum + g.items.length, 0);
    bar.hidden = n === 0;
    count.textContent = `🛒 ${n} to buy`;
  };

  bar.append(count, whatsapp, copy, clear);
  paint();
  subscribe(bar, MISSING_EVENT, paint);
  return bar;
}
