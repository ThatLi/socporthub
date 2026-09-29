import { api } from "../api.js";

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

function severityClass(severity) {
  return `ai-review-severity ai-review-severity-${severity || "medium"}`;
}

function proposalText(proposal) {
  return [
    `Title: ${proposal.title}`,
    `Category: ${proposal.category}`,
    `Committee: ${proposal.committee_name}`,
    `Event date: ${proposal.event_date || "Not provided"}`,
    `Event time: ${proposal.event_time || "Not provided"}`,
    `Description:\n${proposal.description || "Not provided"}`,
    `Blast message:\n${proposal.blast_message || "Not provided"}`,
    `Supporting document: ${proposal.doc_link || "Not provided"}`,
  ].join("\n\n");
}

function renderSuggestion(suggestion, index) {
  return `<article class="ai-review-finding">
    <div class="ai-review-finding-header">
      <strong>${index + 1}. ${escapeHtml(suggestion.concern)}</strong>
      <span class="${severityClass(suggestion.severity)}">${escapeHtml(suggestion.severity)}</span>
    </div>
    <p class="ai-review-rule">${escapeHtml(suggestion.rule_id)} · confidence ${Math.round((suggestion.confidence || 0) * 100)}%</p>
    ${suggestion.evidence ? `<div class="ai-review-detail"><strong>Evidence${suggestion.page ? ` · page ${escapeHtml(suggestion.page)}` : ""}</strong><p>“${escapeHtml(suggestion.evidence)}”</p></div>` : ""}
    ${suggestion.existing_mitigation ? `<div class="ai-review-detail"><strong>Existing mitigation</strong><p>${escapeHtml(suggestion.existing_mitigation)}</p></div>` : ""}
    ${suggestion.gap ? `<div class="ai-review-detail"><strong>Remaining gap</strong><p>${escapeHtml(suggestion.gap)}</p></div>` : ""}
    <div class="ai-review-detail"><strong>Recommended clarification</strong><p>${escapeHtml(suggestion.recommended_clarification)}</p></div>
  </article>`;
}

export function renderAiReview(slot, proposal, getCurrentProposal = () => proposal) {
  if (!slot) return;
  slot.innerHTML = `<section class="card ai-review-card">
    <div class="ai-review-heading">
      <div><h3>AI proposal review</h3><p>Run the common safety and conduct rubric before approving this proposal. AI suggestions require human review.</p></div>
      <span class="ai-review-badge">Admin</span>
    </div>
    <details class="ai-review-source">
      <summary>Review source text</summary>
      <textarea id="ai-review-text" rows="10">${escapeHtml(proposalText(proposal))}</textarea>
      <p class="field-hint">Paste the full proposal text here for a more complete review. The prefilled text only contains fields available in this proposal record.</p>
    </details>
    <div class="btn-row"><button type="button" class="btn" id="run-ai-review">Review with AI</button></div>
    <div id="ai-review-status"></div>
    <div id="ai-review-results"></div>
  </section>`;

  const button = slot.querySelector("#run-ai-review");
  const source = slot.querySelector("#ai-review-text");
  const status = slot.querySelector("#ai-review-status");
  const results = slot.querySelector("#ai-review-results");

  button.addEventListener("click", async () => {
    const documentText = source.value.trim();
    if (!documentText) {
      status.innerHTML = `<div class="error-banner">Add proposal text before starting the review.</div>`;
      return;
    }
    button.disabled = true;
    button.textContent = "Reviewing…";
    status.innerHTML = `<p class="ai-review-loading">Reviewing the proposal against the common rubric…</p>`;
    results.innerHTML = "";
    try {
      const currentProposal = getCurrentProposal();
      const review = await api.post("/api/ai-review", {
        proposal_title: currentProposal.title,
        proposal_category: currentProposal.category,
        proposal_description: currentProposal.description || "",
        document_text: documentText,
      });
      status.innerHTML = `<div class="ai-review-summary"><strong>Executive summary</strong><p>${escapeHtml(review.summary)}</p></div>`;
      results.innerHTML = review.suggestions?.length
        ? `<div class="ai-review-findings"><h4>Suggested findings (${review.suggestions.length})</h4>${review.suggestions.map(renderSuggestion).join("")}</div>`
        : `<p class="ai-review-empty">No potential concerns were returned.</p>`;
    } catch (error) {
      status.innerHTML = `<div class="error-banner">${escapeHtml(error.message)}${error.status === 404 ? " Enable the AI reviewer in the server configuration first." : ""}</div>`;
    } finally {
      button.disabled = false;
      button.textContent = "Review with AI";
    }
  });
}
