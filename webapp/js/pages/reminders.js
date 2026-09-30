import { api } from "../api.js";

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}

function formatDeadline(iso) {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export async function renderReminders(root, user) {
  const isAdmin = user.role === "admin";
  const [inbox, outbox, committees] = await Promise.all([
    api.get("/api/reminders/inbox"),
    api.get("/api/reminders/outbox"),
    isAdmin ? api.get("/api/committees") : Promise.resolve([]),
  ]);

  root.innerHTML = `
    <h1>Reminders</h1>
    <h2>Inbox</h2>
    <div id="reminder-inbox">${inbox.length ? inbox.map((r) => inboxCard(r, isAdmin)).join("") : `<div class="empty-state"><p>Nothing here yet.</p></div>`}</div>

    ${isAdmin ? broadcastForm(committees) : nudgeForm()}

    <details class="proposal-section" style="margin-top:24px;">
      <summary>Outbox ${outbox.length ? `(${outbox.length})` : ""}</summary>
      <div class="proposal-section-content" id="reminder-outbox">
        ${outbox.length ? outbox.map((r) => outboxCard(r, isAdmin)).join("") : `<div class="empty-state"><p>You haven't sent any reminders yet.</p></div>`}
      </div>
    </details>
  `;

  wireUp(root, user);
}

function nudgeForm() {
  return `<form id="reminder-form" class="card">
      <h3>Nudge your portfolio director</h3>
      <div class="field"><label for="reminder-message">Message</label><textarea id="reminder-message" required placeholder="What needs attention?"></textarea></div>
      <div id="reminder-error"></div>
      <button class="btn" type="submit">Send Reminder</button>
    </form>`;
}

function broadcastForm(committees) {
  const social = committees.filter((c) => (c.portfolio || "social") === "social");
  const welfare = committees.filter((c) => (c.portfolio || "social") === "welfare");
  const committeeCheckbox = (c) => `<label style="display:flex; align-items:center; gap:6px; font-size:13px; margin-bottom:6px;">
      <input type="checkbox" name="broadcast-committee" value="${c.id}" /> ${escapeHtml(c.name)}
    </label>`;

  return `<form id="broadcast-form" class="card">
      <h3>Send a reminder</h3>
      <div class="field"><label for="broadcast-message">Message</label><textarea id="broadcast-message" required placeholder="What do you need from them?"></textarea></div>
      <div class="field"><label for="broadcast-deadline">Deadline (optional)</label><input type="date" id="broadcast-deadline" /></div>
      <details class="cca-request-panel">
        <summary>Select CCAs to remind</summary>
        <div class="cca-request-fields">
          ${social.length ? `<p style="font-weight:600; margin:8px 0 4px;">Social</p>${social.map(committeeCheckbox).join("")}` : ""}
          ${welfare.length ? `<p style="font-weight:600; margin:12px 0 4px;">Welfare</p>${welfare.map(committeeCheckbox).join("")}` : ""}
          ${!committees.length ? `<p class="field-hint">No CCAs available to target.</p>` : ""}
        </div>
      </details>
      <div id="broadcast-error" style="margin-top:8px;"></div>
      <button class="btn" type="submit" style="margin-top:8px;">Send Reminder</button>
    </form>`;
}

function inboxCard(reminder, isAdmin) {
  if (reminder.direction === "to_user") {
    // A regular user's inbox: broadcasts sent by an admin, optionally with a deadline.
    const dismissBtn = reminder.is_done
      ? `<button class="reminder-dismiss" data-dismiss-reminder="${reminder.id}" title="Remove from inbox" aria-label="Remove from inbox">&times;</button>`
      : "";
    return `<div class="card ${reminder.is_read ? "reminder-read" : "reminder-unread"}">
      ${cardHeader(reminder, dismissBtn)}
      <p style="color:var(--text); white-space:pre-wrap;">${escapeHtml(reminder.message)}</p>
      ${reminder.deadline ? `<p style="font-size:12px; color:var(--text-muted); margin-top:4px;">⏰ Due: ${formatDeadline(reminder.deadline)}</p>` : ""}
      ${
        reminder.is_done
          ? `<p style="font-size:12px; color:var(--green-text); margin-top:6px;">✅ Marked done</p>`
          : reminder.deadline
          ? `<div class="btn-row"><button class="btn btn-secondary" data-mark-done="${reminder.id}">Mark as done</button></div>`
          : !reminder.is_read
          ? `<div class="btn-row"><button class="btn btn-secondary" data-mark-read="${reminder.id}">Mark as read</button></div>`
          : ""
      }
    </div>`;
  }
  // Admin inbox: nudges from users — unchanged behavior from before this feature.
  return `<div class="card ${reminder.is_read ? "reminder-read" : "reminder-unread"}">
    ${cardHeader(reminder)}
    <p style="color:var(--text); white-space:pre-wrap;">${escapeHtml(reminder.message)}</p>
    ${
      isAdmin
        ? `<div class="btn-row">
             ${!reminder.is_read ? `<button class="btn btn-secondary" data-mark-read="${reminder.id}">Mark as read</button>` : ""}
             <button class="btn btn-secondary" data-delete-reminder="${reminder.id}">Delete</button>
           </div>`
        : ""
    }
  </div>`;
}

function outboxCard(reminder, isAdmin) {
  if (reminder.direction === "to_user") {
    const recipients = reminder.recipients || [];
    const doneCount = recipients.filter((r) => r.is_done).length;
    return `<div class="card">
      ${cardHeader(reminder)}
      <p style="color:var(--text); white-space:pre-wrap;">${escapeHtml(reminder.message)}</p>
      ${reminder.committees?.length ? `<p style="font-size:12px; color:var(--text-muted); margin-top:4px;">To: ${reminder.committees.map((c) => escapeHtml(c.name)).join(", ")}</p>` : ""}
      ${reminder.deadline ? `<p style="font-size:12px; color:var(--text-muted);">⏰ Due: ${formatDeadline(reminder.deadline)}</p>` : ""}
      <div class="btn-row" style="margin-top:8px;"><button class="btn btn-secondary" data-delete-reminder="${reminder.id}">Delete reminder</button></div>
      ${
        recipients.length
          ? `<details style="margin-top:8px;"><summary style="cursor:pointer; font-size:12px; color:var(--text-muted);">${doneCount}/${recipients.length} marked done</summary>
               <div style="margin-top:6px;">
                 ${recipients.map((r) => `<div style="font-size:13px; display:flex; justify-content:space-between; gap:8px; padding:2px 0;"><span>${escapeHtml(r.name)}</span><span>${r.is_done ? "✅ Done" : r.is_read ? "Seen" : "Unseen"}</span></div>`).join("")}
               </div>
             </details>`
          : ""
      }
    </div>`;
  }
  // A user's own sent nudges — same shape as the old "Sent reminders" list.
  return `<div class="card">
    ${cardHeader(reminder)}
    <p style="color:var(--text); white-space:pre-wrap;">${escapeHtml(reminder.message)}</p>
  </div>`;
}

function cardHeader(reminder, extra = "") {
  return `<div style="display:flex; justify-content:space-between; align-items:baseline; gap:12px; margin-bottom:6px;">
      <span style="font-weight:600; font-size:13px;">${escapeHtml(reminder.sender_name || "")}</span>
      <span style="display:flex; align-items:center; gap:6px;">
        <span style="font-size:12px; color:var(--text-muted); white-space:nowrap;">${new Date(reminder.created_at).toLocaleString()}</span>
        ${extra}
      </span>
    </div>`;
}

function wireUp(root, user) {
  root.querySelector("#reminder-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const error = root.querySelector("#reminder-error");
    try {
      await api.post("/api/reminders", { message: root.querySelector("#reminder-message").value.trim() });
      await renderReminders(root, user);
    } catch (err) {
      error.innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`;
    }
  });

  root.querySelector("#broadcast-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const error = root.querySelector("#broadcast-error");
    const committeeIds = [...root.querySelectorAll('input[name="broadcast-committee"]:checked')].map((el) => Number(el.value));
    const deadline = root.querySelector("#broadcast-deadline").value || null;
    if (!committeeIds.length) {
      error.innerHTML = `<div class="error-banner">Select at least one CCA to remind.</div>`;
      return;
    }
    try {
      await api.post("/api/reminders/broadcast", {
        message: root.querySelector("#broadcast-message").value.trim(),
        committee_ids: committeeIds,
        deadline,
      });
      await renderReminders(root, user);
    } catch (err) {
      error.innerHTML = `<div class="error-banner">${escapeHtml(err.message)}</div>`;
    }
  });

  root.querySelectorAll("[data-mark-read]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api.patch(`/api/reminders/${button.dataset.markRead}/read`, {});
      await renderReminders(root, user);
    });
  });
  root.querySelectorAll("[data-mark-done]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api.patch(`/api/reminders/${button.dataset.markDone}/done`, {});
      await renderReminders(root, user);
    });
  });
  root.querySelectorAll("[data-dismiss-reminder]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api.delete(`/api/reminders/${button.dataset.dismissReminder}/mine`);
      await renderReminders(root, user);
    });
  });
  root.querySelectorAll("[data-delete-reminder]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!confirm("Delete this reminder?")) return;
      await api.delete(`/api/reminders/${button.dataset.deleteReminder}`);
      await renderReminders(root, user);
    });
  });
}
