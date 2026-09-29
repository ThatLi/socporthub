import { api } from "../api.js";
import { renderGrading } from "../components/grading.js?v=grading-3";
import { statusBadge } from "../components/status-badge.js";
import { renderDisposableSection } from "../components/disposable-form.js";
import { collectExternalFormData, loadCommitteeFormFields, renderCommitteeFormsSection } from "../components/committee-forms.js";
import { renderAiReview } from "../components/ai-review.js";

const NEXT_STATUS = {
  draft: "in_review",
  needs_action: "in_review",
  in_review: "submitted",
  submitted: "finished",
};

const NEXT_STATUS_LABEL = {
  draft: "Submit for Review",
  needs_action: "Resubmit for Review",
  in_review: "Mark Submitted",
  submitted: "Mark Finished",
};
const STATUS_LABELS = {
  draft: "Draft",
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

const CATEGORY_LABELS = {
  event: "Event",
  initiative: "Initiative",
  welfare: "Welfare",
  decor: "Decor",
  pantry_cleaning: "Pantry Cleaning",
  merch: "Merch",
  pubs: "Pubs",
};
const PORTFOLIO_CATEGORIES = {
  social: ["event", "initiative", "welfare", "decor", "pantry_cleaning", "merch", "pubs"],
  welfare: ["event", "initiative"],
};
const CATEGORY_REQUIRES_POSTER = ["event", "initiative", "welfare", "merch"];
const CATEGORY_REQUIRES_DOC = ["event", "merch"];
const CATEGORY_REQUIRES_BLAST = ["event", "initiative", "welfare"];
const CATEGORY_REQUIRES_EVENT_DATE = ["event", "initiative", "welfare", "pantry_cleaning"];
const EXTERNAL_CCAS = ["BOP", "Tech Crew", "AnG", "BnC", "Devs", "Commotion", "PP", "PS"];

function externalCcaOptions(selected = []) {
  return EXTERNAL_CCAS
    .map((cca) => `<details class="cca-request-panel" data-cca="${escapeHtml(cca)}">
      <summary><label><input type="checkbox" name="requested_ccas" value="${escapeHtml(cca)}" ${selected.includes(cca) ? "checked" : ""} /> ${escapeHtml(cca)}</label></summary>
      <div class="cca-request-fields"><span class="field-hint">Loading configured fields…</span></div>
    </details>`)
    .join("");
}

function categoryOptions(selected, portfolio = "social") {
  const values = PORTFOLIO_CATEGORIES[portfolio] || PORTFOLIO_CATEGORIES.social;
  // Keep a legacy category visible while editing an older proposal that is no
  // longer part of the portfolio's current menu.
  const options = values.includes(selected) ? values : [selected, ...values].filter(Boolean);
  return options.map((value) => [value, CATEGORY_LABELS[value] || value])
    .map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`)
    .join("");
}

function userPortfolio(user) {
  const committees = [...(user?.committees || [])].sort((a, b) => a.id - b.id);
  return committees[0]?.portfolio || "social";
}

// Shared by the new-proposal and edit forms: shows/hides the poster field and updates
// each requirement hint based on the selected category.
function applyCategoryRequirements(root, categoryValue) {
  const needsPoster = CATEGORY_REQUIRES_POSTER.includes(categoryValue);
  const needsDoc = CATEGORY_REQUIRES_DOC.includes(categoryValue);
  const needsBlast = CATEGORY_REQUIRES_BLAST.includes(categoryValue);
  const needsEventDate = CATEGORY_REQUIRES_EVENT_DATE.includes(categoryValue);

  const posterField = root.querySelector("[data-poster-field]");
  if (posterField) posterField.style.display = needsPoster ? "block" : "none";

  const docHint = root.querySelector("[data-doc-hint]");
  if (docHint) docHint.textContent = needsDoc ? "(required for this category)" : "(optional)";

  const blastLabel = root.querySelector("[data-blast-label]");
  if (blastLabel) {
    blastLabel.innerHTML = `Blast message ${
      needsBlast ? '<span class="field-hint">(required for this category)</span>' : "(optional)"
    }`;
  }

  const eventDateHint = root.querySelector("[data-event-date-hint]");
  if (eventDateHint) eventDateHint.textContent = needsEventDate ? "(required for this category)" : "(optional)";

  return { needsPoster, needsDoc, needsBlast, needsEventDate };
}

async function saveDisposableRequest(proposal, form) {
  const values = ["plates", "cups", "bowls", "forks", "spoons"].reduce((result, item) => {
    result[item] = Number(form.querySelector(`#d-${item}`)?.value) || 0;
    return result;
  }, {});
  const collectionDate = form.querySelector("#d-collection_date")?.value || "";
  const collectionTime = form.querySelector("#d-collection_time")?.value || null;
  const hasRequest = Object.values(values).some((value) => value > 0) || collectionDate || collectionTime;
  if (!hasRequest) return;
  if (!collectionDate) throw new Error("Please provide a collection date for the disposable request.");
  await api.post(`/api/disposables/${proposal.id}`, {
    ...values,
    collection_date: collectionDate,
    collection_time: collectionTime,
  });
}

export function renderNewProposal(root, navigate, user) {
  const saved = {};
  const portfolio = userPortfolio(user);
  root.innerHTML = `
    <div class="page-header">
      <span class="back" data-nav="home">&larr; Back</span>
    </div>
    <h1>New Proposal</h1>
    <form id="proposal-form">
      <details class="proposal-section" open>
        <summary>Event Details</summary>
        <div class="proposal-section-content">
          <div class="field">
            <label for="title">Title</label>
            <input type="text" id="title" name="title" required value="${escapeHtml(saved.title)}" />
          </div>
          <div class="field">
            <label for="category">Category</label>
            <select id="category" name="category" required>
              ${categoryOptions(saved.category || "event", portfolio)}
            </select>
          </div>
          <div class="field">
            <label for="description">Description</label>
            <textarea id="description" name="description">${escapeHtml(saved.description)}</textarea>
          </div>
          <div class="field">
            <label for="event_date">Event date <span data-event-date-hint></span></label>
            <input type="date" id="event_date" name="event_date" value="${escapeHtml(saved.event_date)}" />
          </div>
          <div class="field">
            <label for="event_time">Event time <span data-event-time-hint></span></label>
            <input type="time" id="event_time" name="event_time" value="${escapeHtml(saved.event_time)}" />
          </div>
          <div class="field" data-poster-field>
            <label for="poster">Poster <span class="field-hint">Required for Event, Initiative, Welfare, and Merch</span></label>
            <input type="file" id="poster" name="poster" accept="image/jpeg,image/png,image/webp,application/pdf" />
          </div>
          <div class="field">
            <label for="doc_link">Link / PDF URL <span data-doc-hint></span></label>
            <input type="url" id="doc_link" name="doc_link" placeholder="https://" value="${escapeHtml(saved.doc_link)}" />
          </div>
          <div class="field">
            <label for="blast_message" data-blast-label>Blast message (optional)</label>
            <textarea id="blast_message" name="blast_message" placeholder="Message to accompany the event announcement">${escapeHtml(saved.blast_message)}</textarea>
          </div>
        </div>
      </details>

      <details class="proposal-section">
        <summary>Hall Disposables</summary>
        <div class="proposal-section-content">
          <p class="field-hint">Leave quantities at zero if disposables are not needed.</p>
          <div class="disposable-grid">
            <div class="field"><label for="d-plates">Plates</label><input type="number" id="d-plates" min="0" value="0" /></div>
            <div class="field"><label for="d-cups">Cups</label><input type="number" id="d-cups" min="0" value="0" /></div>
            <div class="field"><label for="d-bowls">Bowls</label><input type="number" id="d-bowls" min="0" value="0" /></div>
            <div class="field"><label for="d-forks">Forks</label><input type="number" id="d-forks" min="0" value="0" /></div>
            <div class="field"><label for="d-spoons">Spoons</label><input type="number" id="d-spoons" min="0" value="0" /></div>
          </div>
          <div class="field"><label for="d-collection_date">Collection date</label><input type="date" id="d-collection_date" /></div>
          <div class="field"><label for="d-collection_time">Collection time</label><input type="time" id="d-collection_time" /></div>
        </div>
      </details>

      <details class="proposal-section" open>
        <summary>Media Request</summary>
        <div class="proposal-section-content">
          <p class="media-autofill-note"><strong>Autofill available:</strong> only Tech Crew and AnG can be autofilled for now.</p>
          <p class="form-disclaimer">Please note: not all fields in the external CCA forms can be filled in automatically. Review each form and complete any remaining fields before submitting it.</p>
          <div class="field">
            <label for="requested_ccas">External CCAs to request (optional)</label>
        <div id="requested_ccas" class="cca-request-list">
          ${externalCcaOptions([])}
        </div>
        <div class="field-hint">Tick one or more CCAs. Expand a CCA to review its configured fields.</div>
          </div>
        </div>
      </details>
      <div id="form-error"></div>
      <div class="btn-row"><button type="button" class="btn btn-secondary" id="save-draft">Save Draft</button><button type="submit" class="btn">Submit for Review</button></div>
    </form>
    ${user.role === "admin" ? `<div id="ai-review-slot"></div>` : ""}
  `;

  const form = root.querySelector("#proposal-form");
  const category = root.querySelector("#category");
  if (user.role === "admin") {
    renderAiReview(root.querySelector("#ai-review-slot"), {
      title: "Untitled proposal",
      category: category.value,
      description: "",
      event_date: "",
      event_time: "",
      committee_name: "",
      blast_message: "",
      doc_link: "",
    }, () => ({
      title: form.title.value.trim() || "Untitled proposal",
      category: form.category.value,
      description: form.description.value.trim(),
      event_date: form.event_date.value,
      event_time: form.event_time.value,
      committee_name: "",
      blast_message: form.blast_message.value.trim(),
      doc_link: form.doc_link.value.trim(),
    }));
  }
  category.addEventListener("change", () => applyCategoryRequirements(root, category.value));
  applyCategoryRequirements(root, category.value);
  loadCommitteeFormFields(root).catch((error) => {
    root.querySelectorAll(".cca-request-fields").forEach((slot) => {
      slot.innerHTML = `<span class="field-hint">Could not load configured fields: ${escapeHtml(error.message)}</span>`;
    });
  });

  root.querySelector("#save-draft").addEventListener("click", async () => {
    const saveButton = root.querySelector("#save-draft");
    saveButton.disabled = true;
    try {
      const proposal = await api.post("/api/proposals", {
        title: form.title.value.trim() || "Untitled draft",
        category: form.category.value,
        description: form.description.value.trim() || null,
        event_date: form.event_date.value || null,
        event_time: form.event_time.value || null,
        doc_link: form.doc_link.value.trim() || null,
        blast_message: form.blast_message.value.trim() || null,
        requested_ccas: Array.from(form.querySelectorAll("input[name=requested_ccas]:checked")).map((option) => option.value),
        external_form_data: collectExternalFormData(form),
        save_draft: true,
      });
      const poster = form.poster.files[0];
      if (poster) {
        const formData = new FormData();
        formData.append("poster", poster);
        await api.upload(`/api/proposals/${proposal.id}/poster`, formData);
      }
      await saveDisposableRequest(proposal, form);
      navigate(`proposal/${proposal.id}`);
    } catch (err) {
      root.querySelector("#form-error").innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`;
      saveButton.disabled = false;
    }
  });

  root.querySelector("#proposal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorEl = root.querySelector("#form-error");
    errorEl.innerHTML = "";
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    btn.textContent = "Submitting…";

    try {
      const categoryValue = e.target.category.value;
      const poster = e.target.poster.files[0];
      if (CATEGORY_REQUIRES_EVENT_DATE.includes(categoryValue) && !e.target.event_date.value) {
        throw new Error("Please provide an event date for this category.");
      }
      if (CATEGORY_REQUIRES_EVENT_DATE.includes(categoryValue) && !e.target.event_time.value) {
        throw new Error("Please provide an event time for this category.");
      }
      if (CATEGORY_REQUIRES_POSTER.includes(categoryValue) && !poster) {
        throw new Error("Please select a poster for this category.");
      }
      if (CATEGORY_REQUIRES_DOC.includes(categoryValue) && !e.target.doc_link.value.trim()) {
        throw new Error("Please provide a link or PDF URL for this category.");
      }
      if (CATEGORY_REQUIRES_BLAST.includes(categoryValue) && !e.target.blast_message.value.trim()) {
        throw new Error("Please provide a blast message for this category.");
      }
      const proposal = await api.post("/api/proposals", {
        title: e.target.title.value.trim(),
        category: categoryValue,
        description: e.target.description.value.trim() || null,
        event_date: e.target.event_date.value || null,
        event_time: e.target.event_time.value || null,
        doc_link: e.target.doc_link.value.trim() || null,
        blast_message: e.target.blast_message.value.trim() || null,
        requested_ccas: Array.from(e.target.querySelectorAll("input[name=requested_ccas]:checked")).map((option) => option.value),
        external_form_data: collectExternalFormData(e.target),
        save_draft: true,
      });
      if (poster) {
        const formData = new FormData();
        formData.append("poster", poster);
        await api.upload(`/api/proposals/${proposal.id}/poster`, formData);
      }
      await saveDisposableRequest(proposal, e.target);
      await api.patch(`/api/proposals/${proposal.id}`, { status: "in_review" });
      navigate(`proposal/${proposal.id}`);
    } catch (err) {
      errorEl.innerHTML = `<div class="error-banner">${err.message}</div>`;
      btn.disabled = false;
      btn.textContent = "Submit for Review";
    }
  });
}

export async function renderProposalDetail(root, user, proposalId, navigate) {
  root.innerHTML = `<div class="loading">Loading…</div>`;
  const proposal = await api.get(`/api/proposals/${proposalId}`);
  await api.post(`/api/proposals/${proposalId}/comments/read`);

  const isAdmin = user.role === "admin";
  const isOwner = proposal.submitted_by === user.id;
  const canEdit = isOwner && ["draft", "needs_action"].includes(proposal.status);
  const usesEmailWorkflow = proposal.category === "event" ||
    (proposal.category === "initiative" && proposal.portfolio === "welfare");
  const nextStatus = proposal.status === "in_review" && !usesEmailWorkflow
    ? "finished"
    : NEXT_STATUS[proposal.status];
  const nextStatusLabel = nextStatus === "finished" && proposal.status === "in_review"
    ? "Approve & Finish"
    : NEXT_STATUS_LABEL[proposal.status];
  const canSubmitWithoutEmail = isAdmin && proposal.status === "in_review" && usesEmailWorkflow;
  // Owner submits a fresh draft, and resubmits after being sent back to needs_action —
  // both land on in_review. Every other status change is admin-only.
  const canOwnerSubmit = isOwner && ["draft", "needs_action"].includes(proposal.status);
  const adminHasAction = isAdmin && ["in_review", "submitted"].includes(proposal.status);

  root.innerHTML = `
    <div class="page-header">
      <span class="back" data-nav="${isAdmin ? "admin" : "home"}">&larr; Back</span>
      <div class="page-header-actions">
        ${statusBadge(proposal.status)}
        ${canEdit ? `<button class="btn btn-secondary proposal-edit-top" id="edit-btn">Edit Proposal</button>` : ""}
      </div>
    </div>

    <h1>${escapeHtml(proposal.title)}</h1>
    <p>${escapeHtml(proposal.committee_name)} · Submitted by ${escapeHtml(proposal.submitter_name || "")}</p>
    ${isAdmin ? `<div id="ai-review-slot"></div>` : ""}
    <div id="grading-slot"></div>

    ${canEdit ? `<div id="edit-form-slot"></div>` : ""}

    <div class="card"><h3>Category</h3><p style="color:var(--text)">${escapeHtml(proposal.category.replaceAll("_", " "))}</p>
      ${proposal.blast_message ? `<h3>Blast message</h3><p style="color:var(--text); white-space:pre-wrap;">${escapeHtml(proposal.blast_message)}</p>` : ""}
      ${proposal.poster_filename ? `<div class="poster-banner">
        <div><strong>Poster uploaded</strong><span>${escapeHtml(proposal.poster_filename)}</span></div>
        <button class="btn btn-secondary" type="button" data-poster-view>View poster</button>
        <button class="btn btn-secondary" type="button" data-poster-download>Download poster</button>
      </div>` : `<div class="poster-banner poster-banner-empty"><strong>No poster uploaded yet</strong><span>A poster may be required before this proposal can be submitted.</span></div>`}
    </div>

    <div class="card">
      <h3>Description</h3>
      <p style="color: var(--text); white-space: pre-wrap;">${
        escapeHtml(proposal.description) || "<span style=\"color:var(--text-muted)\">No description</span>"
      }</p>

      <h3>Event date</h3>
      <p style="color: var(--text)">${proposal.event_date || "Not set"}</p>

      <h3>Event time</h3>
      <p style="color: var(--text)">${proposal.event_time || "Not set"}</p>

      <h3>Supporting document</h3>
      <p>${
        proposal.doc_link
          ? `<a class="proposal-document-link" href="${escapeHtml(proposal.doc_link)}" target="_blank" rel="noopener">${escapeHtml(
              proposal.doc_link
            )}</a>`
          : "None linked"
      }</p>
    </div>

    <div id="committee-forms-slot"></div>
    <div id="disposable-slot"></div>

    ${
      // Only shown alongside an actual admin action button below — otherwise it's a
      // dead field with nothing to attach the note to.
      adminHasAction
        ? `<div class="field" style="margin-top:16px">
             <label for="status-comment">Note to submitter (optional)</label>
             <textarea id="status-comment" placeholder="Explain what needs to change, or add context…"></textarea>
           </div>`
        : ""
    }
    ${
      // Submitting/resubmitting is the owner's action; advancing past in_review is
      // the admin's call — never show the other party a button for a step that isn't theirs.
      (canOwnerSubmit || (adminHasAction && (proposal.status !== "in_review" || !usesEmailWorkflow))) && nextStatus
        ? `<button class="btn" id="advance-btn">${nextStatusLabel}</button>`
        : ""
    }
    ${canSubmitWithoutEmail
      ? `<button class="btn btn-secondary" id="submit-without-email" style="margin-top:8px">Mark Submitted Without Email</button>`
      : ""}
    ${
      isAdmin && proposal.status === "in_review"
        ? `<button class="btn btn-secondary" id="revert-btn" style="margin-top:8px">Send back to Needs Action</button>`
        : ""
    }

    <div id="detail-error"></div>
    <div id="comments-slot" class="comments-slot"></div>
  `;

  renderCommitteeFormsSection(root.querySelector("#committee-forms-slot"), proposal);
  renderDisposableSection(root.querySelector("#disposable-slot"), user, proposal);
  renderCommentsSection(root.querySelector("#comments-slot"), user, proposal);
  if (isAdmin) renderAiReview(root.querySelector("#ai-review-slot"), proposal);
  await renderGrading(root.querySelector("#grading-slot"), user, proposal,
    () => renderProposalDetail(root, user, proposalId, navigate));
  if (proposal.status === "in_review" && usesEmailWorkflow) renderEmailSection(root, user, proposal, navigate);

  const posterPath = `/api/proposals/${proposal.id}/poster`;
  root.querySelector("[data-poster-view]")?.addEventListener("click", async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      const { blob, filename } = await api.getBlob(posterPath);
      const url = URL.createObjectURL(blob);
      const preview = document.createElement("div");
      preview.className = "poster-preview";
      preview.innerHTML = `
        <div class="poster-preview-backdrop" data-poster-close></div>
        <div class="poster-preview-panel" role="dialog" aria-modal="true" aria-label="Poster preview">
          <div class="poster-preview-header">
            <strong>${escapeHtml(filename)}</strong>
            <button class="btn btn-secondary" type="button" data-poster-close>Close</button>
          </div>
          ${blob.type === "application/pdf"
            ? `<iframe src="${url}" title="${escapeHtml(filename)}"></iframe>`
            : `<img src="${url}" alt="${escapeHtml(filename)}" />`}
        </div>`;
      root.appendChild(preview);
      preview.querySelectorAll("[data-poster-close]").forEach((close) => {
        close.addEventListener("click", () => {
          preview.remove();
          URL.revokeObjectURL(url);
        });
      });
    } catch (err) {
      root.querySelector("#detail-error").innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`;
    } finally {
      button.disabled = false;
    }
  });
  root.querySelector("[data-poster-download]")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const { blob, filename } = await api.getBlob(posterPath);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      root.querySelector("#detail-error").innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`;
    } finally {
      e.target.disabled = false;
    }
  });

  root.querySelector("#advance-btn")?.addEventListener("click", async (e) => {
    await changeStatus(e.target, nextStatus);
  });
  root.querySelector("#submit-without-email")?.addEventListener("click", async (e) => {
    await changeStatus(e.target, "submitted", { send_email: false });
  });
  root.querySelector("#revert-btn")?.addEventListener("click", async (e) => {
    await changeStatus(e.target, "needs_action");
  });
  root.querySelector("#edit-btn")?.addEventListener("click", () => {
    renderEditForm(
      root.querySelector("#edit-form-slot"),
      proposal,
      navigate,
      () => renderProposalDetail(root, user, proposalId, navigate)
    );
  });
  async function changeStatus(btn, newStatus, options = {}) {
    btn.disabled = true;
    const errorEl = root.querySelector("#detail-error");
    const comment = root.querySelector("#status-comment")?.value.trim() || null;
    try {
      await api.patch(`/api/proposals/${proposal.id}`, { status: newStatus, comment, ...options });
      renderProposalDetail(root, user, proposalId, navigate);
    } catch (err) {
      errorEl.innerHTML = `<div class="error-banner">${err.message}</div>`;
      btn.disabled = false;
    }
  }
}

async function renderEmailSection(root, user, proposal, navigate) {
  const slot = document.createElement("div");
  slot.id = "email-slot";
  const disposableSlot = root.querySelector("#disposable-slot");
  disposableSlot.after(slot);
  if (proposal.status !== "in_review") return;
  try {
    const draft = await api.get(`/api/email/preview/${proposal.id}`);
    slot.innerHTML = `<div class="card"><h3>Confirmation Email</h3>
      <div class="field"><label for="email-recipient">To</label><input id="email-recipient" value="${escapeHtml(draft.recipient)}" disabled /></div>
      <div class="field"><label for="email-subject">Subject</label><input id="email-subject" value="${escapeHtml(draft.subject)}" ${user.role === "admin" ? "" : "disabled"} /></div>
      <div class="field"><label for="email-body">Message</label><textarea id="email-body" ${user.role === "admin" ? "" : "disabled"}>${escapeHtml(draft.body)}</textarea></div>
      <div id="email-error"></div>${user.role === "admin" ? `<div class="btn-row"><button class="btn btn-secondary" id="save-email">Save Draft</button><button class="btn" id="send-email">Approve & Send PDF</button></div>` : `<p>Email preview only.</p>`}
    </div>`;
    if (user.role !== "admin") return;
    const save = async () => api.patch(`/api/email/preview/${proposal.id}`, { subject: slot.querySelector("#email-subject").value, body: slot.querySelector("#email-body").value });
    slot.querySelector("#save-email").addEventListener("click", async () => {
      try { await save(); slot.querySelector("#email-error").innerHTML = `<p>Draft saved.</p>`; } catch (err) { slot.querySelector("#email-error").innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`; }
    });
    slot.querySelector("#send-email").addEventListener("click", async (e) => {
      e.target.disabled = true;
      try { await save(); await api.post(`/api/email/send/${proposal.id}`); await renderProposalDetail(root, user, proposal.id, navigate); }
      catch (err) { slot.querySelector("#email-error").innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`; e.target.disabled = false; }
    });
  } catch (err) {
    slot.innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`;
  }
}

async function renderCommentsSection(slot, user, proposal) {
  if (!slot) return;
  slot.innerHTML = `<section class="card comments-card"><div class="comments-heading"><div><h3>Comments & review history</h3><p>Keep questions and requested changes here so everyone can follow the proposal.</p></div></div><div class="loading">Loading comments…</div></section>`;
  try {
    const [comments, statusHistory] = await Promise.all([
      api.get(`/api/proposals/${proposal.id}/comments`),
      api.get(`/api/proposals/${proposal.id}/status-history`),
    ]);
    const canReplyToAdmin = user.role !== "admin" && proposal.status === "needs_action";
    const commentById = new Map(comments.map((comment) => [comment.id, comment]));
    const historyItems = [
      ...comments.map((comment) => ({ ...comment, historyType: "comment" })),
      ...statusHistory.map((entry) => ({ ...entry, historyType: "status" })),
    ].sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
    const commentHtml = historyItems.length
      ? historyItems.map((item) => {
          if (item.historyType === "status") {
            const toLabel = STATUS_LABELS[item.to_status] || item.to_status;
            const from = item.from_status ? ` from ${STATUS_LABELS[item.from_status] || item.from_status}` : "";
            return `<article class="comment-item status-history-item">
              <div class="comment-rail"><span class="comment-dot"></span></div>
              <div class="comment-content"><div class="comment-meta"><strong>Status changed</strong><span>${escapeHtml(toLabel)}</span><time>${new Date(item.created_at).toLocaleString()}</time></div>
              <p>${escapeHtml(item.changer_name || "System")} changed the proposal status${from} to <strong>${escapeHtml(toLabel)}</strong>.</p></div>
            </article>`;
          }
          const comment = item;
          const parent = comment.reply_to_comment_id ? commentById.get(comment.reply_to_comment_id) : null;
          const isAdminComment = comment.author_role === "admin";
          return `<article class="comment-item ${isAdminComment ? "comment-admin" : "comment-user"}">
            <div class="comment-rail"><span class="comment-dot"></span></div>
            <div class="comment-content">
              <div class="comment-meta"><strong>${escapeHtml(comment.author_name)}</strong><span>${isAdminComment ? "Admin" : "Submitter"}</span><time>${new Date(comment.created_at).toLocaleString()}</time></div>
              ${parent ? `<div class="comment-reply-context">Replying to ${escapeHtml(parent.author_name)}: “${escapeHtml(parent.body)}”</div>` : ""}
              <p>${escapeHtml(comment.body)}</p>
              ${canReplyToAdmin && isAdminComment ? `<button type="button" class="comment-reply" data-reply-to="${comment.id}">Reply to this comment</button>` : ""}
            </div>
          </article>`;
        }).join("")
      : `<p class="comments-empty">No comments yet.</p>`;
    slot.innerHTML = `<section class="card comments-card">
      <div class="comments-heading"><div><h3>Comments & review history</h3><p>Keep questions and requested changes here so everyone can follow the proposal.</p></div></div>
      <div class="comment-thread">${commentHtml}</div>
      <form class="comment-form" id="comment-form">
        <div class="comment-replying" hidden></div>
        <label for="new-comment">${canReplyToAdmin ? "Reply or add a comment" : "Add a comment"}</label>
        <textarea id="new-comment" placeholder="Write a comment or update…" required></textarea>
        <input type="hidden" id="reply-to-comment" />
        <div class="comment-form-footer"><span>Visible to the submitter and admins</span><button class="btn" type="submit">Post Comment</button></div>
        <div id="comment-error"></div>
      </form>
    </section>`;

    const form = slot.querySelector("#comment-form");
    const textarea = slot.querySelector("#new-comment");
    const replyInput = slot.querySelector("#reply-to-comment");
    const replying = slot.querySelector(".comment-replying");
    slot.querySelectorAll("[data-reply-to]").forEach((button) => button.addEventListener("click", () => {
      const comment = commentById.get(Number(button.dataset.replyTo));
      replyInput.value = comment.id;
      replying.textContent = `Replying to ${comment.author_name}: “${comment.body}”`;
      replying.hidden = false;
      textarea.focus();
    }));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type=submit]");
      const error = slot.querySelector("#comment-error");
      button.disabled = true;
      error.innerHTML = "";
      try {
        await api.post(`/api/proposals/${proposal.id}/comments`, {
          body: textarea.value.trim(),
          reply_to_comment_id: replyInput.value ? Number(replyInput.value) : null,
        });
        await renderCommentsSection(slot, user, proposal);
      } catch (err) {
        error.innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`;
        button.disabled = false;
      }
    });
  } catch (err) {
    slot.innerHTML = `<section class="card comments-card"><h3>Comments & review history</h3><div class="error-banner">${escapeHtml(err.message)}</div></section>`;
  }
}

function renderEditForm(slot, proposal, navigate, onSaved) {
  slot.innerHTML = `
    <form id="edit-form" class="card" style="margin-top:16px">
      <div class="field">
        <label for="e-category">Category</label>
        <select id="e-category">${categoryOptions(proposal.category, proposal.portfolio)}</select>
      </div>
      <div class="field">
        <label for="e-title">Title</label>
        <input type="text" id="e-title" value="${escapeHtml(proposal.title)}" required />
      </div>
      <div class="field">
        <label for="e-description">Description</label>
        <textarea id="e-description">${escapeHtml(proposal.description)}</textarea>
      </div>
      <div class="field">
        <label for="e-event_date">Event date <span data-event-date-hint></span></label>
        <input type="date" id="e-event_date" value="${proposal.event_date || ""}" />
      </div>
      <div class="field">
        <label for="e-event_time">Event time <span data-event-time-hint></span></label>
        <input type="time" id="e-event_time" value="${proposal.event_time || ""}" />
      </div>
      <div class="field" data-poster-field>
        <label for="e-poster">Poster <span class="field-hint">Required for Event, Initiative, Welfare, and Merch</span></label>
        ${
          proposal.poster_filename
            ? `<p class="field-hint">Current: ${escapeHtml(proposal.poster_filename)} — choose a file below to replace it.</p>`
            : ""
        }
        <input type="file" id="e-poster" accept="image/jpeg,image/png,image/webp,application/pdf" />
      </div>
      <div class="field">
        <label for="e-doc_link">Link / PDF URL <span data-doc-hint></span></label>
        <input type="url" id="e-doc_link" value="${escapeHtml(proposal.doc_link)}" placeholder="https://" />
      </div>
      <div class="field">
        <label for="e-blast_message" data-blast-label>Blast message</label>
        <textarea id="e-blast_message">${escapeHtml(proposal.blast_message)}</textarea>
      </div>
      <div class="field">
        <p class="media-autofill-note"><strong>Autofill available:</strong> only Tech Crew and AnG can be autofilled for now.</p>
        <label for="e-requested_ccas">External CCAs to request (optional)</label>
        <div id="e-requested_ccas" class="cca-request-list">${externalCcaOptions(proposal.requested_ccas || [])}</div>
        <div class="field-hint">Tick one or more CCAs. Expand a CCA to review its configured fields.</div>
      </div>
      <div id="edit-error"></div>
      <div class="btn-row">
        <button type="button" class="btn btn-secondary" id="edit-cancel">Cancel</button>
        <button type="submit" class="btn" id="save-changes">Save Changes</button>
      </div>
    </form>
  `;

  const form = slot.querySelector("#edit-form");
  const category = slot.querySelector("#e-category");
  category.addEventListener("change", () => applyCategoryRequirements(slot, category.value));
  applyCategoryRequirements(slot, category.value);
  loadCommitteeFormFields(slot, proposal).catch(() => {});

  slot.querySelector("#edit-cancel").addEventListener("click", () => {
    slot.innerHTML = "";
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const errorEl = slot.querySelector("#edit-error");
    errorEl.innerHTML = "";
    const categoryValue = category.value;
    const poster = slot.querySelector("#e-poster").files[0];
    const hasExistingPoster = Boolean(proposal.poster_filename);

    if (CATEGORY_REQUIRES_EVENT_DATE.includes(categoryValue) && !slot.querySelector("#e-event_date").value) {
      errorEl.innerHTML = `<div class="error-banner">Please provide an event date for this category.</div>`;
      return;
    }
    if (CATEGORY_REQUIRES_EVENT_DATE.includes(categoryValue) && !slot.querySelector("#e-event_time").value) {
      errorEl.innerHTML = `<div class="error-banner">Please provide an event time for this category.</div>`;
      return;
    }
    if (CATEGORY_REQUIRES_POSTER.includes(categoryValue) && !poster && !hasExistingPoster) {
      errorEl.innerHTML = `<div class="error-banner">Please select a poster for this category.</div>`;
      return;
    }
    if (CATEGORY_REQUIRES_DOC.includes(categoryValue) && !slot.querySelector("#e-doc_link").value.trim()) {
      errorEl.innerHTML = `<div class="error-banner">Please provide a link or PDF URL for this category.</div>`;
      return;
    }
    if (CATEGORY_REQUIRES_BLAST.includes(categoryValue) && !slot.querySelector("#e-blast_message").value.trim()) {
      errorEl.innerHTML = `<div class="error-banner">Please provide a blast message for this category.</div>`;
      return;
    }

    const saveButton = slot.querySelector("#save-changes");
    const cancelButton = slot.querySelector("#edit-cancel");
    saveButton.disabled = true;
    cancelButton.disabled = true;
    saveButton.textContent = "Saving…";
    try {
      await api.patch(`/api/proposals/${proposal.id}`, {
        category: categoryValue,
        title: slot.querySelector("#e-title").value.trim(),
        description: slot.querySelector("#e-description").value.trim() || null,
        event_date: slot.querySelector("#e-event_date").value || null,
        event_time: slot.querySelector("#e-event_time").value || null,
        doc_link: slot.querySelector("#e-doc_link").value.trim() || null,
        blast_message: slot.querySelector("#e-blast_message").value.trim() || null,
        requested_ccas: Array.from(slot.querySelectorAll("input[name=requested_ccas]:checked")).map((option) => option.value),
        external_form_data: collectExternalFormData(slot),
      });
      if (poster) {
        const formData = new FormData();
        formData.append("poster", poster);
        await api.upload(`/api/proposals/${proposal.id}/poster`, formData);
      }
      await onSaved();
    } catch (err) {
      errorEl.innerHTML = `<div class="error-banner">${err.message}</div>`;
      saveButton.disabled = false;
      cancelButton.disabled = false;
      saveButton.textContent = "Save Changes";
    }
  });
}
