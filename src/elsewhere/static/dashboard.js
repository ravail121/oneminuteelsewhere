"use strict";
const dollars = n => n === null ? "unknown" : "$" + Number(n).toFixed(6);
document.addEventListener("submit", event => {
  const button = event.submitter;
  if (button) { button.disabled = true; button.textContent = "Saving…"; }
});
async function poll() {
  const root = document.getElementById("project-root");
  if (!root) return;
  try {
    const response = await fetch(`/api/projects/${root.dataset.project}`, {cache:"no-store"});
    if (!response.ok) throw new Error("Progress unavailable");
    const p = await response.json();
    if (root.dataset.version !== String(p.version) || root.dataset.status !== p.status || root.dataset.busy !== (p.busy ? "yes" : "no") || root.dataset.review !== (p.review ? "yes" : "no")) {
      const panel = await fetch(`/projects/${p.id}/panel`, {cache:"no-store"});
      if (!panel.ok) throw new Error("Panel unavailable");
      // Same-origin, server-rendered Jinja markup; all user/model text is escaped.
      root.innerHTML = await panel.text();
      root.dataset.status = p.status; root.dataset.busy = p.busy ? "yes" : "no";
      root.dataset.version = String(p.version);
      root.dataset.review = p.review ? "yes" : "no";
    }
    const set = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = value; };
    set("message", p.message); set("cost", dollars(p.calculated_cost)); set("allowance", dollars(p.remaining_allowance));
    set("remaining", dollars(p.estimated_remaining)); set("percent", `${p.progress}%`);
    const progress = document.getElementById("progress"); if (progress) progress.value = p.progress;
    const rows = document.getElementById("stages");
    if (rows) {
      rows.replaceChildren(...p.stages.map(s => {
        const row = document.createElement("tr");
        [s.label,s.status,s.started_at || "—",s.completed_at || "—",dollars(s.cost),s.message].forEach(value => {
          const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
        }); return row;
      }));
    }
    const images = document.getElementById("thumbnails");
    if (images) p.images.forEach(name => {
      if (!images.querySelector(`[data-image="${name}"]`)) {
        const figure = document.createElement("figure"); figure.dataset.image = name;
        const img = document.createElement("img"); img.src = `/media/${p.id}/${p.revision}/${name}`; img.alt = name;
        const caption = document.createElement("figcaption"); caption.textContent = name;
        figure.append(img,caption); images.append(figure);
      }
    });
    const costRows = document.getElementById("cost-rows");
    if (costRows && p.busy) {
      costRows.replaceChildren(...p.requests.map(r => {
        const row = document.createElement("tr");
        [r.revision,r.stage,r.model_returned || r.model_requested || "Local",r.status,
         dollars(r.calculated_cost_usd),r.charge_status + " · " + JSON.stringify(r.usage)].forEach(value => {
          const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
        }); return row;
      }));
    }
  } catch (_) {
    const message = document.getElementById("message");
    if (message) message.textContent = "Connection paused. Saved work is safe. Reconnecting…";
  }
  window.setTimeout(poll, 1500);
}
if (document.getElementById("project-root")) window.setTimeout(poll, 1000);

// Only a deliberate Viral Material selection starts free research. Never generate a story here.
const newVideoForm = document.getElementById("new-video-form");
if (newVideoForm) {
  const mode = newVideoForm.elements.video_mode;
  const language = newVideoForm.elements.language;
  const panel = document.getElementById("viral-recommendation");
  const creativeFields = document.getElementById("creative-fields");
  const ideaField = document.getElementById("idea-field");
  const autoNote = document.getElementById("viral-auto-note");
  const languageField = document.getElementById("language-field");
  const discoveryHelp = document.getElementById("viral-discovery-help");
  let pending = null;
  let selectionVersion = 0;
  // Viral Material auto-picks the topic, language, category, visual style and voice: those
  // manual controls have nothing to do while it's selected, so they are hidden rather than
  // ignored — the live auto-recommendation panel takes over showing what was actually picked.
  const syncFieldVisibility = () => {
    const viral = mode.value === "viral";
    if (creativeFields) creativeFields.hidden = viral;
    if (ideaField) ideaField.hidden = viral;
    if (languageField) languageField.hidden = viral;
    if (discoveryHelp) discoveryHelp.hidden = viral;
    if (autoNote) autoNote.hidden = !viral;
  };
  syncFieldVisibility();
  // A browser can restore a <select>'s value from history (back/forward navigation, bfcache)
  // without firing "change" — re-sync whenever the page becomes visible again, not just on load.
  window.addEventListener("pageshow", syncFieldVisibility);
  mode.addEventListener("change", async () => {
    syncFieldVisibility();
    const version = ++selectionVersion;
    newVideoForm.elements.trend_snapshot.value = "";
    newVideoForm.elements.trend_id.value = "";
    language.options[0].textContent = "Auto — choose from the topic itself";
    panel.hidden = mode.value !== "viral";
    if (panel.hidden) return;
    panel.textContent = "Searching worldwide regional sources for the strongest rising-topic candidate… Free lookup; no story generation. This can take up to 45 seconds.";
    try {
      // Repeated toggles share an in-flight request; backend caching also deduplicates.
      if (!pending) {
        const body = new FormData();
        body.set("csrf", newVideoForm.elements.csrf.value);
        pending = fetch("/api/trends/recommendation", {method:"POST", body, cache:"no-store"})
          .then(response => {
            if (!response.ok) throw new Error("Lookup unavailable. Use See Top Topic and Alternatives to try again.");
            return response.json();
          }).finally(() => { pending = null; });
      }
      const result = await pending;
      if (version !== selectionVersion || mode.value !== "viral") return;
      if (!result.topic) throw new Error("No suitable recent topic found. Nothing was generated or charged. Try again later.");
      const topic = result.topic;
      newVideoForm.elements.trend_snapshot.value = result.snapshot_id;
      newVideoForm.elements.trend_id.value = topic.id;
      language.options[0].textContent = `Auto — ${result.language_name} (from this topic)`;
      panel.replaceChildren();
      const heading = document.createElement("h3");
      heading.textContent = `Auto-selected: ${topic.title}`;
      panel.append(heading);
      [`Language: ${result.language_name}. ${topic.language_reason}`,
       `Worldwide coverage: ${result.sources_responded}/${result.sources_attempted} regional feeds responded. ${topic.regions_seen} matching regions for this topic.`,
       `Estimated momentum: ${topic.momentum_estimate === null ? "unknown" : topic.momentum_estimate}. ${topic.ranking_reason}`,
       `${topic.youtube_status}. Sources: Google Search trends, not YouTube top-search rankings.`,
       "Lookup cost: $0.00. Generate Story still requires your paid confirmation; no media or uploads start here."
      ].forEach(text => { const p = document.createElement("p"); p.textContent = text; panel.append(p); });
    } catch (error) {
      if (version === selectionVersion && mode.value === "viral") panel.textContent = error.message;
    }
  });
}
