// Saves each mark as it is clicked (POST /marks), so nothing is lost on a reload.
// Clicking the verdict a spot already has clears it.
(() => {
  const main = document.getElementById("result");
  const text = main.dataset.request;

  async function save(body) {
    const resp = await fetch("/marks", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({request: text, ...body}),
    });
    // Errors come as JSON from /marks, plain text when the index is missing,
    // and as Flask's HTML page on a crash; show something readable for each.
    const type = resp.headers.get("Content-Type") || "";
    const data = type.includes("application/json") ? await resp.json()
      : {error: type.includes("text/plain") ? await resp.text() : `server error ${resp.status}`};
    if (!resp.ok) throw new Error(`Not saved: ${data.error || resp.statusText}`);
    return data;
  }

  main.addEventListener("click", async (e) => {
    const button = e.target.closest(".spot button[data-verdict]");
    if (!button) return;
    const spot = button.closest(".spot");
    const verdict = spot.dataset.verdict === button.dataset.verdict ? "clear" : button.dataset.verdict;
    try {
      await save({loc_id: spot.dataset.loc, verdict,
                  tier: spot.dataset.tier, origin: spot.dataset.origin});
      spot.dataset.verdict = verdict === "clear" ? "" : verdict;
      spot.querySelector(".error")?.remove();
    } catch (err) {
      alertIn(spot, err.message);
    }
  });

  const form = main.querySelector("form.add-missed");
  const list = main.querySelector(".missed-list");
  const error = form.querySelector(".error");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    error.textContent = "";
    try {
      const m = await save({loc_id: form.loc_id.value, verdict: "add-missed"});
      list.querySelector(`li[data-loc="${CSS.escape(m.loc_id)}"]`)?.remove();
      list.append(missedItem(m));
      form.reset();
    } catch (err) {
      error.textContent = err.message;
    }
  });

  list.addEventListener("click", async (e) => {
    if (!e.target.matches("button.remove")) return;
    const li = e.target.closest("li");
    try {
      await save({loc_id: li.dataset.loc, verdict: "clear"});
      li.remove();
    } catch (err) {
      error.textContent = err.message;
    }
  });

  function missedItem(m) {
    const li = document.createElement("li");
    li.dataset.loc = m.loc_id;
    const cite = document.createElement("span");
    cite.className = "cite";
    cite.textContent = m.cite;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove";
    remove.title = "Remove this added spot";
    remove.textContent = "Remove";
    li.append(cite, " ", remove);
    return li;
  }

  function alertIn(spot, message) {
    let p = spot.querySelector(".error");
    if (!p) {
      p = document.createElement("p");
      p.className = "error";
      p.setAttribute("role", "alert");
      spot.append(p);
    }
    p.textContent = message;
  }
})();
