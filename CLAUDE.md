# Social Port Hub — Coding Guidelines

See `SOCIAL-PORT-HUB.md` for the full product spec. This file covers conventions for working in this codebase.

## Scope note

This codebase currently implements **Phases 1–8** of the spec: registration/auth,
committees, the full proposal lifecycle (draft → in_review → submitted → finished,
plus a post-event grading → final stage), a comment thread with replies and a status
history, an in-app calendar, hall disposables requests/approval, admin email
drafts/sending, reminders, category-specific requirements (poster/blast
message/doc-link uploads per `ProposalCategory`), Social/Welfare **portfolio**
separation (scoped admins, committees, and notifications — see `api/portfolio.py`),
config-driven external CCA form linking (`GOOGLE_FORM_CONFIGS`, no migration needed to
add a form), and **proposal grading** — a post-event rubric self-assessment with a
14-day deadline, Telegram reminders, and optional Google Drive evidence folders
(`docs/grading.md`, `api/services/grading.py`, `api/services/google_drive.py`).

**Phase 9 — AI Proposal Reviewer — is the final phase and is not yet built.** Per the
spec (`SOCIAL-PORT-HUB.md` §4.3.5 / §11), it will read a proposal's linked Google Doc,
send it to an LLM for review, generate suggested comments, and let the admin vet each
comment before it's posted back to the doc. `api/services/google_docs.py` already
gives read access to linked docs (currently used only for PDF export on submission) and
`google-auth` is already a dependency, but there is no LLM/Anthropic client anywhere in
`requirements.txt` and no review route yet — don't assume either exists. When it's
built, follow the same feature-flag pattern as grading/Drive (`GRADING_ENABLED`,
`GOOGLE_DRIVE_MODE`): default off, admin vets before anything is posted back, and
Telegram notifications around it stay fire-and-forget per the convention below.

The calendar is database-backed with a public iCalendar feed; do not reintroduce
Google Calendar dependencies (see `SOCIAL-PORT-HUB.md` §2.2 — Google Calendar was
swapped out for cost reasons before Phase 3 shipped).

Check `api/models.py` and `api/routes/` before referencing anything from Phase 9 (or
any other phase) as if it already exists — the spec describes the target, not
necessarily the current code.

There's also a **backlog item — bidirectional reminders** — that is not built and not
scheduled (comes after Phase 9, if at all). Today `Reminder`
(`api/models.py`) / `api/routes/reminders.py` / `webapp/js/pages/reminders.js` only
support **user → admin** nudges. The plan (see `SOCIAL-PORT-HUB.md` §4.3.6) is to let
admins compose and broadcast reminders to specific CCAs (multi-select, grouped
Social-then-Welfare via `committee_portfolio()` in `api/portfolio.py`, inside a
collapsible picker), give users a real inbox for those admin-sent reminders, and
restructure the shared Reminders tab into an **Inbox** (top, always expanded) plus a
collapsible **Outbox** (renamed from "outgoing reminders" — shorter, pairs with
Inbox) for both roles. Don't assume any admin-to-user reminder path, a `direction`
field on `Reminder`, or an Inbox/Outbox split exists until it's actually in the code.

**Calendar deviates from the spec**: it does not use the Google Calendar API (that
requires a GCP billing account). Events are stored in our own `calendar_events` table
and exposed read-only via a public iCalendar feed (`routes/calendar.py:calendar_feed`)
that people can subscribe to from their own calendar app — see README "Calendar".
Don't reintroduce a Google Calendar dependency without checking this decision first.

## Stack

- Backend: FastAPI (sync SQLAlchemy 2.0, not async — simplest correct choice at ~100 users)
- DB: PostgreSQL via Alembic migrations (never hand-edit the schema; always add a migration)
- Bot: raw Telegram Bot API over `httpx` (no bot framework) — webhook-based, delivered as
  a route inside the same FastAPI app so Railway only runs one process
- Frontend: vanilla JS SPA served as static files by FastAPI, no build step

## Conventions

- All DB access goes through `api/models.py` SQLAlchemy models + a request-scoped session
  (`api/database.py:get_db`). No raw SQL in route handlers.
- Auth: every WebApp API request must pass Telegram `initData` for HMAC validation
  (`api/auth.py`). There is no separate password/session auth.
- Committee-scoped visibility is enforced in the route layer, not the frontend — a
  non-admin user's queries must always filter by their committee membership. Admins are
  further scoped by **portfolio** (Social vs Welfare) via `api/portfolio.py` helpers
  (`admin_committee_filter`, `committee_portfolio`) — don't add a second, ad-hoc way to
  scope admin queries in a new route.
- Status transitions (`draft → in_review → submitted → finished → grading → final`) are
  validated server-side against `PROPOSAL_STATUS_TRANSITIONS` in `api/models.py`; don't
  trust a status value posted from the client.
- Optional integrations (grading, Google Drive) are off by default and gated by a
  settings flag (`GRADING_ENABLED`, `GOOGLE_DRIVE_MODE=live`) read via
  `api/config.py:get_settings()` — follow this pattern for any new external
  integration (including the eventual AI reviewer) rather than assuming credentials
  are present.
- Telegram notifications are fire-and-forget (log and continue on failure) — never let a
  Telegram API error fail the underlying DB operation that triggered it.
- Environment variables are read once via `api/config.py` (pydantic-settings); don't call
  `os.environ` directly elsewhere.
