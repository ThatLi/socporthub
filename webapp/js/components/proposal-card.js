import { statusBadge } from "./status-badge.js";
import { ICONS, STATUS_ICON } from "./icons.js";

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}

export function proposalCard(proposal, { showCommittee = false } = {}) {
  const dateStr = proposal.event_date
    ? new Date(proposal.event_date + "T00:00:00").toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      })
    : "No date set";

  return `
    <a class="card proposal-card tone-${proposal.status}" data-nav="proposal/${proposal.id}">
      <span class="icon-bubble">${ICONS[STATUS_ICON[proposal.status] || "file"]}</span>
      <div class="proposal-card-body">
      <div class="proposal-card-heading">
        <div class="title">${escapeHtml(proposal.title)}</div>
        ${proposal.unread_comment_count ? `<span class="comment-notification" aria-label="${proposal.unread_comment_count} unread comment${proposal.unread_comment_count === 1 ? "" : "s"}">${proposal.unread_comment_count}</span>` : ""}
      </div>
      <div class="meta">
        <span>${showCommittee ? escapeHtml(proposal.committee_name) + " · " : ""}${escapeHtml(
    proposal.submitter_name || ""
  )} · ${dateStr}</span>
        ${statusBadge(proposal.status)}
      </div>
      ${proposal.unread_comment_count ? `<div class="proposal-card-notice">💬 ${proposal.unread_comment_count} new comment${proposal.unread_comment_count === 1 ? "" : "s"}</div>` : ""}
      </div>
    </a>
  `;
}
