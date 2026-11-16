// Cooking mode — one step at a time, big enough to read from across the
// counter, with the following step previewed beside (or under) it.
//
// The whole screen is a tiny "state machine": the state is just
// (recipe, step index), and ONE function — paintCookStep — draws the screen
// from it. Buttons and keys only change the index and repaint, so the screen
// can never disagree with the state.

const cook = {
  el: document.getElementById("cook-mode"),
  title: document.getElementById("cook-title"),
  count: document.getElementById("cook-count"),
  bar: document.getElementById("cook-progress-bar"),
  step: document.getElementById("cook-step"),
  nextBox: document.getElementById("cook-next"),
  nextLabel: document.getElementById("cook-next-label"),
  nextStep: document.getElementById("cook-next-step"),
  back: document.getElementById("cook-back"),
  forward: document.getElementById("cook-forward"),
  ingredients: document.getElementById("cook-ingredients"),
  ingredientsToggle: document.getElementById("cook-ingredients-toggle"),
};

let cookRecipe = null;
let cookIndex = 0;
let wakeLock = null;

// Where you were in each recipe, for this visit. Closing by accident (or to
// peek at another recipe) and coming back resumes instead of restarting.
const cookProgress = new Map();

function startCooking(recipe) {
  if (!recipe.steps?.length) return;
  cookRecipe = recipe;
  cookIndex = Math.min(cookProgress.get(recipe.id) || 0, recipe.steps.length - 1);

  cook.title.textContent = recipe.title || "Untitled";
  cook.ingredients.innerHTML = "";
  for (const ing of recipe.ingredients || []) {
    const li = document.createElement("li");
    li.textContent = formatIngredient(ing);
    li.dir = "auto";
    cook.ingredients.appendChild(li);
  }
  cook.ingredients.hidden = true;
  cook.ingredientsToggle.hidden = !recipe.ingredients?.length;
  cook.ingredientsToggle.classList.remove("active");

  paintCookStep();
  openOverlay(cook.el, stopCooking);
  keepScreenAwake();
}

function paintCookStep() {
  const steps = cookRecipe.steps;
  const last = cookIndex === steps.length - 1;
  cook.count.textContent = `Step ${cookIndex + 1} of ${steps.length}`;
  cook.bar.style.width = `${((cookIndex + 1) / steps.length) * 100}%`;
  cook.step.textContent = steps[cookIndex];
  cook.nextLabel.textContent = last ? "Last step" : "Up next";
  cook.nextStep.textContent = last ? "Enjoy your meal! 🎉" : steps[cookIndex + 1];
  cook.nextBox.classList.toggle("done", last);
  cook.back.disabled = cookIndex === 0;
  cook.forward.textContent = last ? "Finish ✓" : "Next step";
  cookProgress.set(cookRecipe.id, cookIndex);
  cook.el.querySelector(".cook-body").scrollTop = 0;   // long step -> start at its top
}

function cookForward() {
  if (cookIndex === cookRecipe.steps.length - 1) {
    cookProgress.delete(cookRecipe.id);    // finished: next time starts at step 1
    closeOverlay();
    return;
  }
  cookIndex++;
  paintCookStep();
}

function cookBack() {
  if (cookIndex === 0) return;
  cookIndex--;
  paintCookStep();
}

// Runs whenever the overlay closes, by × or by the phone's Back button.
function stopCooking() {
  cookRecipe = null;
  if (wakeLock) wakeLock.release().catch(() => {});
  wakeLock = null;
}

// Screen Wake Lock: stops the phone dimming and locking while your hands are
// covered in dough. Not every browser has it, and it can be refused (low
// battery) — cooking mode works fine without, so failures are ignored.
async function keepScreenAwake() {
  try {
    if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen");
  } catch { /* unsupported or refused */ }
}
// The browser drops the lock whenever the tab is hidden; take it back on return.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && cookRecipe) keepScreenAwake();
});

cook.forward.addEventListener("click", cookForward);
cook.back.addEventListener("click", cookBack);
cook.nextBox.addEventListener("click", cookForward);   // tapping the preview advances too
document.getElementById("cook-close").addEventListener("click", closeOverlay);
cook.ingredientsToggle.addEventListener("click", () => {
  cook.ingredients.hidden = !cook.ingredients.hidden;
  cook.ingredientsToggle.classList.toggle("active", !cook.ingredients.hidden);
});

// Keyboard, for a laptop on the counter: arrows / space step, Esc exits.
document.addEventListener("keydown", (e) => {
  if (!cookRecipe) return;
  if (e.key === "ArrowRight" || e.key === " " || e.key === "Enter") { e.preventDefault(); cookForward(); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); cookBack(); }
  else if (e.key === "Escape") closeOverlay();
});

// The "Start cooking" button, used on recipe cards and inside meals.
function makeCookButton(recipe, label = "▶ Start cooking") {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "cook-start";
  btn.textContent = label;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();                  // don't fold the card
    startCooking(recipe);
  });
  return btn;
}
