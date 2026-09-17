"use strict";
const CC_TERMINAL = ["complete", "failed", "cancelled", "upload_failed", "interrupted"];
async function pollControlCenter() {
  const root = document.getElementById("cc-root");
  if (!root) return;
  let stop = false;
  try {
    const response = await fetch(`/api/control-center/run/${root.dataset.run}`, {cache: "no-store"});
    if (!response.ok) throw new Error("Unavailable");
    const r = await response.json();
    const set = (id, value) => { const node = document.getElementById(id); if (node) node.textContent = value; };
    set("cc-phase", r.phase.replace(/_/g, " "));
    set("cc-message", r.message);
    set("cc-title", r.title || "—");
    set("cc-description", r.description || "—");
    set("cc-tags", (r.tags || []).join(", "));
    set("cc-requested-privacy", r.requested_privacy || r.privacy || "—");
    set("cc-actual-privacy", r.live_privacy || r.returned_privacy || "Not yet confirmed");
    set("cc-video-id", r.video_id || "—");
    const link = document.getElementById("cc-video-link");
    if (link) {
      link.replaceChildren();
      if (r.video_id) {
        const a = document.createElement("a");
        a.href = `https://www.youtube.com/watch?v=${r.video_id}`;
        a.target = "_blank"; a.rel = "noopener"; a.textContent = "Watch on YouTube";
        link.append(a);
      } else {
        link.textContent = "—";
      }
    }
    if (CC_TERMINAL.includes(r.phase)) stop = true;
  } catch (_) {
    const message = document.getElementById("cc-message");
    if (message) message.textContent = "Connection paused. Saved work is safe. Reconnecting…";
  }
  if (!stop) window.setTimeout(pollControlCenter, 1500);
}
if (document.getElementById("cc-root")) window.setTimeout(pollControlCenter, 1000);
