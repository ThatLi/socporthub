import { api } from "../api.js";
import { proposalCard } from "../components/proposal-card.js";
import { ICONS, STATUS_ICON } from "../components/icons.js";

const STATUS_LABELS = {
  draft: "Drafts",
  needs_action: "Needs Action",
  in_review: "In Review",
  submitted: "Submitted",
  finished: "Finished",
  grading: "Grading",
  final: "Final",
};

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export async function renderHome(root, user) {
  root.innerHTML = `<div class="loading">Loading…</div>`;

  const [summary, proposals] = await Promise.all([
    api.get("/api/proposals/summary"),
    api.get("/api/proposals"),
  ]);


  root.innerHTML = `
    <div class="page-header">
      <div class="avatar">${escapeHtml((user.display_name || user.email || "?").trim().charAt(0).toUpperCase())}</div>
      <div class="greeting">
        <div class="greeting-sub">${greeting()}</div>
        <h1>${escapeHtml(user.display_name || user.email)}</h1>
      </div>
    </div>

    <div class="summary-grid">
      ${Object.entries(STATUS_LABELS)
        .map(
          ([key, label]) => `
        <div class="summary-card" data-status="${key}">
          <span class="icon-bubble">${ICONS[STATUS_ICON[key]]}</span>
          <div class="count">${summary[key] ?? 0}</div>
          <div class="label">${label}</div>
        </div>`
        )
        .join("")}
    </div>

    ${proposals.some((p) => p.status === "grading" && p.submitted_by === user.id) ? `<div class="grading-deadline"><strong>Self-assessments need your attention</strong><span>Open a proposal marked Grading to see its deadline, save scores, and submit.</span></div>` : ""}
    <div class="section-head"><h2>Recent proposals</h2><span class="section-sub">${escapeHtml((user.committees || []).map((c) => c.name).join(", "))}</span></div>
    <div id="proposal-list">
      ${
        proposals.length
          ? proposals.map((p) => proposalCard(p)).join("")
          : `<div class="empty-state"><div class="icon">📋</div><p>No proposals yet — submit one to get started.</p></div>`
      }
    </div>

    <div class="fab">
      <a class="btn" data-nav="proposal/new">+ New Proposal</a>
    </div>
  `;
}
