import { api } from "../api.js";

const LABELS = { food: "Food", decor: "Decor", activities: "Activities", creativity: "Creativity", visual_quality: "Visual quality", quality: "Quality", cleanliness: "Cleanliness", noticeboard: "Noticeboard", block: "Block", tiktok: "TikTok", ig: "Instagram", other: "Other", arts_crafts: "Arts & crafts", gifts: "Gifts", welfare_pack: "Welfare pack" };
const label = (key) => LABELS[key] || key;
function esc(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

function summary(title, assessment, rubric, submittedAt) {
  if (!assessment) return `<section class="grading-summary"><h3>${title}</h3><p>The self-assessment has not been submitted yet.</p></section>`;
  return `<section class="grading-summary"><h3>${title}</h3>
    ${submittedAt ? `<p class="field-hint">Submitted ${new Date(submittedAt).toLocaleString()}</p>` : ""}
    ${assessment.selections?.length ? `<div class="grading-tags">${assessment.selections.map((s) => `<span>${esc(label(s))}</span>`).join("")}</div>` : ""}
    ${rubric.fields.map((field) => { const rating = assessment.ratings?.[field]; return `<div class="grading-result"><div><strong>${esc(label(field))}</strong><span>${rating?.score === 0 ? "N/A · 0/10" : `${rating?.score ?? "—"}/10`}</span></div><p>${esc(rating?.justification || "No justification yet.")}</p></div>`; }).join("")}
  </section>`;
}

function countdown(slot, deadline) {
  function update() {
    const target = slot.querySelector("[data-grading-countdown]");
    if (!target || !slot.isConnected) return;
    const milliseconds = new Date(deadline) - Date.now();
    const days = Math.floor(Math.abs(milliseconds) / 86400000);
    const hours = Math.floor(Math.abs(milliseconds) / 3600000) % 24;
    target.textContent = milliseconds > 0 ? `${days}d ${hours}h remaining` : `Overdue by ${days}d ${hours}h`;
    target.closest(".grading-deadline").classList.toggle("grading-overdue", milliseconds <= 0);
  }
  update();
  const timer = setInterval(() => {
    if (!slot.isConnected || !slot.querySelector("[data-grading-countdown]")) clearInterval(timer);
    else update();
  }, 60000);
}

export async function renderGrading(slot, user, proposal, refresh) {
  if (!proposal.grading_available) return;
  slot.innerHTML = '<div class="loading">Loading grading…</div>';
  try {
    const data = await api.get(`/api/proposals/${proposal.id}/grading`);
    if (!data.rubric || (!data.started_at && !data.can_start)) { slot.innerHTML = ""; return; }
    const grader = data.can_grade;
    const isAdmin = user.role === "admin";
    const peerGrader = grader && !isAdmin;
    const submitterName = proposal.submitter_name || "the submitter";
    const submitterPossessive = `${submitterName}${submitterName.endsWith("s") ? "'" : "'s"}`;
    const categoryLabel = label(proposal.category);
    const evidenceActions = data.drive_url
      ? `<div class="grading-evidence-actions"><a href="${esc(data.drive_url)}" target="_blank" rel="noopener" class="grading-drive-link">Open Google Drive folder ↗</a><button class="btn btn-secondary" type="button" data-mark-evidence-done ${data.evidence_done_at ? "disabled" : ""}>${data.evidence_done_at ? "Marked Done" : "Mark as Done"}</button></div>`
      : `<p>Evidence folder is being prepared.</p>${data.drive_error ? `<p class="field-hint">${esc(data.drive_error)}</p>` : ""}<button class="btn btn-secondary" type="button" data-retry-evidence>Retry folder setup</button>`;
    const heading = peerGrader ? `Grade the ${esc(categoryLabel)}` : grader ? "Proposal grading" : "Your self-assessment";
    const selfAssessmentTitle = peerGrader ? `${esc(submitterPossessive)} self-assessment` : "User self-assessment";
    const graderAssessmentTitle = isAdmin ? "Admin assessment" : "Committee assessment";
    const formTitle = peerGrader ? `Your assessment for ${esc(submitterName)}` : grader ? graderAssessmentTitle : "User self-assessment";
    const revealLabel = `Submit for ${esc(submitterName)} instead`;
    const submitLabel = "Submit";
    const reviewLabel = isAdmin ? "admin review" : peerGrader ? "your committee review" : "user review";
    const completeLabel = isAdmin ? "Admin grading is complete." : peerGrader ? "Committee grading is complete." : "User grading is complete.";
    const draftHint = peerGrader
      ? `You can draft scores for ${esc(submitterName)} now and submit after their self-assessment.`
      : "You can draft your scores now and submit after the user submits.";
    if (data.can_start) {
      slot.innerHTML = `<section class="card grading-open"><div><span class="grading-eyebrow">POST-EVENT REVIEW</span><h2>Ready for grading</h2><p>Open a 14-day window for the submitter to rate this proposal and share photo evidence.</p></div><button class="btn" data-start-grading>Grade</button><div class="grading-error" role="alert"></div></section>`;
      slot.querySelector("[data-start-grading]").addEventListener("click", async (event) => {
        event.target.disabled = true;
        try { await api.post(`/api/proposals/${proposal.id}/grading/start`, {}); await refresh(); }
        catch (err) { slot.querySelector(".grading-error").textContent = err.message; event.target.disabled = false; }
      });
      return;
    }
    const editable = grader ? data.can_edit_admin : data.can_edit_user;
    const assessment = (grader ? data.admin_assessment : data.user_assessment) || {};
    const selections = assessment.selections || (grader ? data.user_assessment?.selections : []) || [];
    const rubric = data.rubric;
    const hidePeerForm = peerGrader && editable;
    slot.innerHTML = `<section class="card grading-card">
      <div class="grading-heading"><div><span class="grading-eyebrow">POST-EVENT REVIEW</span><h2>${heading}</h2></div><span class="badge badge-${data.status}">${data.status === "final" ? "Final" : "Grading"}</span></div>
      ${data.status === "grading" ? `<div class="grading-deadline" role="status">
          <strong data-grading-countdown></strong>
          <span>Submit by ${new Date(data.deadline).toLocaleString()}. You have 2 weeks from the start of grading.</span>
          ${isAdmin ? `<div class="grading-extend">
              <button type="button" class="btn-link" data-show-extend>Extend deadline</button>
              <form class="grading-extend-form" hidden>
                <input type="date" data-extend-deadline min="${new Date(new Date(data.deadline).getTime() + 86400000).toISOString().slice(0, 10)}" required />
                <button class="btn btn-secondary" type="submit">Confirm</button>
                <span class="grading-extend-feedback"></span>
              </form>
            </div>` : ""}
        </div>` : `<div class="grading-complete" role="status">Self-assessment submitted. ${data.admin_submitted_at ? completeLabel : `Ready for ${reviewLabel}.`}</div>`}
      ${grader ? summary(selfAssessmentTitle, data.user_assessment, rubric, data.user_submitted_at) : ""}
      ${hidePeerForm ? `<div class="grading-actions"><button class="btn" type="button" data-show-peer-grading>${revealLabel}</button></div>` : ""}
      ${editable ? `<form class="grading-form" novalidate ${hidePeerForm ? "hidden" : ""}>
        <h3>${formTitle}</h3>
        <p class="grading-scale">Each field is rated from <strong>0 to 10</strong>. <strong>0 = not applicable</strong>; <strong>10 = maximum</strong>. Explain your score below each field.</p>
        ${rubric.options ? `<fieldset class="grading-options"><legend>${rubric.multiple ? "Select all that apply" : "Select one type"}</legend><div>${rubric.options.map((option) => `<label><input type="${rubric.multiple ? "checkbox" : "radio"}" name="grading-selection" value="${option}" ${selections.includes(option) ? "checked" : ""} /><span>${esc(label(option))}</span></label>`).join("")}</div></fieldset>` : ""}
        ${rubric.fields.map((field, index) => `<fieldset class="grading-field"><legend><span>${String(index + 1).padStart(2, "0")}</span> ${esc(label(field))}</legend><div class="grading-score"><label for="grade-${field}">Your rating</label><div><input type="number" id="grade-${field}" data-score="${field}" min="0" max="10" step="1" inputmode="numeric" value="${assessment.ratings?.[field]?.score ?? ""}" placeholder="—" /><span>/ 10</span></div></div><label for="justify-${field}">Justification</label><textarea id="justify-${field}" data-justification="${field}" maxlength="5000" placeholder="Explain why this score reflects the work done…">${esc(assessment.ratings?.[field]?.justification)}</textarea></fieldset>`).join("")}
        <div class="grading-evidence"><div class="grading-evidence-icon" aria-hidden="true">↗</div><div><h3>Photo evidence</h3><p>Upload proof images to the proposal folder.</p>${evidenceActions}</div></div>
        <div class="grading-feedback" role="status"></div>
        <div class="grading-actions"><button class="btn btn-secondary" type="button" data-save-grade>Save Draft</button><button class="btn" type="submit" ${grader && !data.user_submitted_at ? "disabled" : ""}>${submitLabel}</button></div>
        <p class="field-hint">${grader && !data.user_submitted_at ? draftHint : "You can edit saved drafts. Submitted assessments are locked."}</p>
      </form>` : `${!grader ? summary("Your submitted scores", data.user_assessment, rubric, data.user_submitted_at) : ""}${data.admin_submitted_at ? summary(graderAssessmentTitle, data.admin_assessment, rubric, data.admin_submitted_at) : ""}${data.drive_url ? `<div class="grading-evidence"><div class="grading-evidence-icon" aria-hidden="true">↗</div><div><h3>Photo evidence</h3>${evidenceActions}</div></div>` : ""}`}
    </section>`;
    if (data.status === "grading") countdown(slot, data.deadline);
    slot.querySelector("[data-show-extend]")?.addEventListener("click", (event) => {
      slot.querySelector(".grading-extend-form")?.removeAttribute("hidden");
      event.target.hidden = true;
    });
    slot.querySelector(".grading-extend-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.target.querySelector("button[type=submit]");
      const feedback = slot.querySelector(".grading-extend-feedback");
      const deadline = slot.querySelector("[data-extend-deadline]").value;
      feedback.textContent = "";
      button.disabled = true;
      try {
        await api.post(`/api/proposals/${proposal.id}/grading/extend`, { deadline });
        await refresh();
      } catch (err) {
        feedback.textContent = err.message;
        button.disabled = false;
      }
    });
    slot.querySelector("[data-show-peer-grading]")?.addEventListener("click", (event) => {
      slot.querySelector(".grading-form")?.removeAttribute("hidden");
      event.target.closest(".grading-actions")?.remove();
    });
    slot.querySelectorAll("[data-mark-evidence-done]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          if (editable && !(await save(false))) { button.disabled = false; return; }
          await api.post(`/api/proposals/${proposal.id}/grading/evidence/done`, {});
          await refresh();
        } catch (err) { button.disabled = false; button.textContent = "Mark as Done"; }
      });
    });
    if (!editable) return;
    const form = slot.querySelector(".grading-form");
    async function save(submit) {
      const feedback = form.querySelector(".grading-feedback");
      const buttons = [...form.querySelectorAll("button")];
      const disabled = buttons.map((button) => button.disabled);
      feedback.textContent = "";
      try {
        const ratings = {};
        for (const field of rubric.fields) {
          const input = form.querySelector(`[data-score="${field}"]`);
          const score = input.value === "" ? null : Number(input.value);
          const justification = form.querySelector(`[data-justification="${field}"]`).value.trim();
          if (input.validity.badInput || (score !== null && (!Number.isInteger(score) || score < 0 || score > 10))) throw new Error("Ratings must be whole numbers from 0 to 10.");
          if (submit && (score === null || !justification)) { input.focus(); throw new Error(`Add a score and justification for ${label(field)}.`); }
          ratings[field] = { score, justification };
        }
        const selected = [...form.querySelectorAll('[name="grading-selection"]:checked')].map((input) => input.value);
        if (submit && rubric.options && !selected.length) throw new Error("Select the type of work before submitting.");
        buttons.forEach((button) => { button.disabled = true; });
        await api.put(`/api/proposals/${proposal.id}/grading`, { ratings, selections: selected, submit });
        if (submit) await refresh();
        else { feedback.textContent = `Draft saved at ${new Date().toLocaleTimeString()}.`; feedback.className = "grading-feedback grading-saved"; }
      } catch (err) { feedback.textContent = err.message; feedback.className = "grading-feedback grading-error"; return false; }
      finally { buttons.forEach((button, index) => { button.disabled = disabled[index]; }); }
      return true;
    }
    form.addEventListener("submit", (event) => { event.preventDefault(); save(true); });
    form.querySelector("[data-save-grade]").addEventListener("click", () => save(false));
    form.querySelector("[data-retry-evidence]")?.addEventListener("click", async (event) => {
      event.target.disabled = true;
      try { await save(false); await api.post(`/api/proposals/${proposal.id}/grading/evidence`, {}); await refresh(); }
      catch (err) { form.querySelector(".grading-feedback").textContent = err.message; event.target.disabled = false; }
    });
  } catch (err) { slot.innerHTML = `<div class="error-banner">Could not load grading: ${esc(err.message)}</div>`; }
}
