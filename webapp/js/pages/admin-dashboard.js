import { api } from "../api.js";
import { statusBadge } from "../components/status-badge.js";
import { proposalCard } from "../components/proposal-card.js";

// No "draft" here — drafts are private to their owner until submitted, so admins
// never need to see draft counts or draft proposals in their dashboard.
const STATUS_LABELS = {
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

export async function renderAdminDashboard(root, tab = "proposals") {
  root.innerHTML = `
    <h1>Admin Dashboard</h1>
    <div class="chip-row" id="section-tabs">
      <div class="chip ${tab === "proposals" ? "active" : ""}" data-tab="proposals">Proposals</div>
      <div class="chip ${tab === "disposables" ? "active" : ""}" data-tab="disposables">Disposables</div>
      <div class="chip ${tab === "users" ? "active" : ""}" data-tab="users">Users</div>
    </div>
    <div id="section-content"><div class="loading">Loading…</div></div>
  `;

  root.querySelectorAll("#section-tabs .chip").forEach((chip) => {
    chip.addEventListener("click", () => renderAdminDashboard(root, chip.dataset.tab));
  });

  const content = root.querySelector("#section-content");
  if (tab === "users") {
    await renderUsersSection(content);
  } else if (tab === "disposables") {
    await renderDisposablesSection(content);
  } else {
    await renderProposalsSection(content);
  }
}

async function renderDisposablesSection(content) {
  const requests = await api.get("/api/disposables");
  const todayStr = new Date().toISOString().slice(0, 10);

  const byDate = new Map();
  for (const r of requests) {
    if (!byDate.has(r.collection_date)) byDate.set(r.collection_date, []);
    byDate.get(r.collection_date).push(r);
  }
  const dates = Array.from(byDate.keys()).sort();

  if (!dates.length) {
    content.innerHTML = `<div class="empty-state"><div class="icon">🍽️</div><p>No disposable requests yet.</p></div>`;
    return;
  }

  content.innerHTML = dates
    .map((d) => {
      const items = byDate.get(d);
      const totals = items.reduce(
        (acc, r) => ({
          plates: acc.plates + r.plates,
          cups: acc.cups + r.cups,
          bowls: acc.bowls + r.bowls,
          forks: acc.forks + r.forks,
          spoons: acc.spoons + r.spoons,
        }),
        { plates: 0, cups: 0, bowls: 0, forks: 0, spoons: 0 }
      );
      const isToday = d === todayStr;
      return `
        <div class="card" style="${isToday ? "border: 2px solid var(--accent);" : ""}">
          <h3>${d}${isToday ? " · Today" : ""}</h3>
          <p style="color:var(--text)">Totals — Plates: ${totals.plates} · Cups: ${totals.cups} · Bowls: ${totals.bowls} · Forks: ${totals.forks} · Spoons: ${totals.spoons}</p>
          ${items
            .map(
              (r) => `
            <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-top:1px solid var(--border);">
              <div>
                <div style="font-size:13px; font-weight:600;">${escapeHtml(r.proposal_title)}</div>
                <div style="font-size:12px; color:var(--text-muted)">${escapeHtml(r.committee_name)} · ${escapeHtml(r.requester_name || "")}${r.requester_cca ? ` · ${escapeHtml(r.requester_cca)}` : ""}</div>
                ${r.description ? `<div style="font-size:12px; margin-top:4px;">${escapeHtml(r.description)}</div>` : ""}
              </div>
              ${
                r.approved
                  ? `<span class="badge badge-approved">Approved</span>`
                  : r.rejected
                    ? `<span class="badge">Rejected</span>`
                    : `<span><button class="btn" style="width:auto; padding:6px 14px;" data-approve-disposable="${r.id}">Approve</button> <button class="btn btn-secondary" style="width:auto; padding:6px 14px;" data-reject-disposable="${r.id}">Reject</button></span>`
              }
            </div>`
            )
            .join("")}
        </div>
      `;
    })
    .join("");

  content.querySelectorAll("[data-approve-disposable]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await api.patch(`/api/disposables/${btn.dataset.approveDisposable}`, { approved: true });
      renderDisposablesSection(content);
    });
  });
  content.querySelectorAll("[data-reject-disposable]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await api.patch(`/api/disposables/${btn.dataset.rejectDisposable}`, { approved: false });
      renderDisposablesSection(content);
    });
  });
}

async function renderProposalsSection(content, committeeId = null, statusFilter = null) {
  const committees = await api.get("/api/committees");
  const query = committeeId ? `?committee_id=${committeeId}` : "";
  const [summary, allProposals] = await Promise.all([
    api.get(`/api/proposals/summary${query}`),
    api.get(`/api/proposals${query}`),
  ]);
  // Drafts are private to their owner until submitted — never show them to admins.
  const proposals = allProposals.filter((p) => p.status !== "draft" && (!statusFilter || p.status === statusFilter));

  content.innerHTML = `
    <div class="chip-row">
      <div class="chip ${!committeeId ? "active" : ""}" data-committee="">All</div>
      ${committees
        .map((c) => `<div class="chip ${committeeId == c.id ? "active" : ""}" data-committee="${c.id}">${escapeHtml(c.name)}</div>`)
        .join("")}
    </div>

    <div class="chip-row">
      <div class="chip ${!statusFilter ? "active" : ""}" data-status="">All</div>
      ${Object.entries(STATUS_LABELS)
        .map(([key, label]) => `<div class="chip ${statusFilter === key ? "active" : ""}" data-status="${key}">${label}</div>`)
        .join("")}
    </div>

    <div class="summary-grid">
      ${Object.entries(STATUS_LABELS)
        .map(([key, label]) => `<div class="summary-card"><div class="count">${summary[key] ?? 0}</div><div class="label">${label}</div></div>`)
        .join("")}
    </div>

    <h2>Proposals</h2>
    ${
      proposals.length
        ? proposals.map((p) => proposalCard(p, { showCommittee: !committeeId })).join("")
        : `<div class="empty-state"><div class="icon">📋</div><p>No proposals in this view.</p></div>`
    }
  `;

  content.querySelectorAll("[data-committee]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const id = chip.dataset.committee || null;
      renderProposalsSection(content, id, statusFilter);
    });
  });
  content.querySelectorAll("[data-status]").forEach((chip) => {
    chip.addEventListener("click", () => {
      renderProposalsSection(content, committeeId, chip.dataset.status || null);
    });
  });
}

async function renderUsersSection(content) {
  const [users, committees] = await Promise.all([
    api.get("/api/admin/users"),
    api.get("/api/committees?all_committees=true"),
  ]);

  const pending = users.filter((u) => u.status === "pending");
  const others = users.filter((u) => u.status !== "pending");

  content.innerHTML = `
    ${pending.length ? `<h2>Pending approval (${pending.length})</h2>` : ""}
    ${pending.map((u) => userRow(u, committees, { pendingActions: true })).join("")}
    <h2>All users</h2>
    ${others.map((u) => userRow(u, committees, { pendingActions: false })).join("")}
  `;

  content.querySelectorAll("[data-approve]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api.patch(`/api/admin/users/${btn.dataset.approve}`, { status: "approved" });
      renderUsersSection(content);
    });
  });
  content.querySelectorAll("[data-reject]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api.patch(`/api/admin/users/${btn.dataset.reject}`, { status: "rejected" });
      renderUsersSection(content);
    });
  });
  content.querySelectorAll("[data-save-committees]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const userId = btn.dataset.saveCommittees;
      const checked = Array.from(
        content.querySelectorAll(`input[data-user="${userId}"]:checked`)
      ).map((i) => Number(i.value));
      btn.disabled = true;
      btn.textContent = "Saving…";
      await api.patch(`/api/admin/users/${userId}`, { committee_ids: checked });
      renderUsersSection(content);
    });
  });

  content.querySelectorAll("[data-edit-email]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const form = content.querySelector(`[data-email-form="${btn.dataset.editEmail}"]`);
      form.hidden = false;
      btn.hidden = true;
      form.querySelector("input").focus();
    });
  });
  content.querySelectorAll("[data-cancel-email]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const form = content.querySelector(`[data-email-form="${btn.dataset.cancelEmail}"]`);
      form.hidden = true;
      content.querySelector(`[data-edit-email="${btn.dataset.cancelEmail}"]`).hidden = false;
    });
  });
  content.querySelectorAll("[data-save-email]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      button.textContent = "Saving…";
      try {
        await api.patch(`/api/admin/users/${form.dataset.saveEmail}`, { email: form.querySelector("input").value });
        await renderUsersSection(content);
      } catch (error) {
        button.disabled = false;
        button.textContent = "Save";
        window.alert(error.message);
      }
    });
  });
  content.querySelectorAll("[data-edit-telegram]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const form = content.querySelector(`[data-telegram-form="${btn.dataset.editTelegram}"]`);
      form.hidden = false;
      btn.hidden = true;
      form.querySelector("input").focus();
    });
  });
  content.querySelectorAll("[data-cancel-telegram]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const form = content.querySelector(`[data-telegram-form="${btn.dataset.cancelTelegram}"]`);
      form.hidden = true;
      content.querySelector(`[data-edit-telegram="${btn.dataset.cancelTelegram}"]`).hidden = false;
    });
  });
  content.querySelectorAll("[data-save-telegram]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      button.textContent = "Saving…";
      try {
        await api.patch(`/api/admin/users/${form.dataset.saveTelegram}`, { telegram_username: form.querySelector("input").value });
        await renderUsersSection(content);
      } catch (error) {
        button.disabled = false;
        button.textContent = "Save";
        window.alert(error.message);
      }
    });
  });
}

function userRow(user, committees, { pendingActions }) {
  const memberIds = new Set(user.committees.map((c) => c.id));
  return `
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div class="title">${escapeHtml(user.display_name || user.email)}</div>
          <div style="font-size:12px; color:var(--text-muted)">${escapeHtml(user.email)}</div>
          <div style="font-size:12px; color:var(--text-muted)">${escapeHtml(user.telegram_username || "No Telegram handle")}</div>
        </div>
        ${statusBadge(user.status)}
      </div>

      <button class="btn btn-secondary" data-edit-email="${user.id}" style="margin-top:12px; width:auto;">Edit email</button>
      <form data-email-form="${user.id}" data-save-email="${user.id}" hidden style="margin-top:12px;">
        <label style="display:block; font-size:13px; color:var(--text-muted);">Registered email</label>
        <input type="email" required value="${escapeHtml(user.email)}" style="width:100%; margin-top:4px;" />
        <div class="btn-row" style="margin-top:8px;">
          <button class="btn" type="submit">Save</button>
          <button class="btn btn-secondary" type="button" data-cancel-email="${user.id}">Cancel</button>
        </div>
      </form>

      <button class="btn btn-secondary" data-edit-telegram="${user.id}" style="margin-top:8px; width:auto;">Edit Telegram handle</button>
      <form data-telegram-form="${user.id}" data-save-telegram="${user.id}" hidden style="margin-top:12px;">
        <label style="display:block; font-size:13px; color:var(--text-muted);">Telegram handle</label>
        <input type="text" required pattern="@?[A-Za-z0-9_]{5,32}" value="${escapeHtml(user.telegram_username || "")}" style="width:100%; margin-top:4px;" />
        <div class="btn-row" style="margin-top:8px;">
          <button class="btn" type="submit">Save</button>
          <button class="btn btn-secondary" type="button" data-cancel-telegram="${user.id}">Cancel</button>
        </div>
      </form>

      ${
        pendingActions
          ? `<div class="btn-row" style="margin-top:12px">
               <button class="btn" data-approve="${user.id}">Approve</button>
               <button class="btn btn-secondary" data-reject="${user.id}">Reject</button>
             </div>`
          : user.role !== "admin"
          ? `<details style="margin-top:12px">
               <summary style="cursor:pointer; font-size:13px; color:var(--text-muted);">
                 Committees: ${escapeHtml(user.committees.map((c) => c.name).join(", ") || "None assigned")}
               </summary>
               <div style="margin-top:8px">
                 ${committees
                   .map(
                     (c) => `
                   <label style="display:flex; align-items:center; gap:6px; font-size:13px; margin-bottom:4px;">
                     <input type="checkbox" data-user="${user.id}" value="${c.id}" ${
                       memberIds.has(c.id) ? "checked" : ""
                     } />
                     ${escapeHtml(c.name)}
                   </label>`
                   )
                   .join("")}
                 <button class="btn btn-secondary" data-save-committees="${user.id}" style="margin-top:8px">Save Committees</button>
               </div>
             </details>`
          : ""
      }
    </div>
  `;
}
