# Social Port Hub — Project Specification

> Telegram Bot + WebApp for consolidating Raffles Hall committee management under the Social Director and the Welfare portfolio.
>
> Last updated 2026-11-02. This document describes the product **as built** (Alembic migrations `0001`–`0029`) and the **target deployment** (Vercel + Supabase). Where the two differ, the section says so under "Deployment gaps".

---

## 1. Project Overview

A single platform for the hall's **Social** and **Welfare** admins to manage event proposals, hall disposables, borrowable equipment, calendars, reminders and communications across every RH committee. Members (committee) submit proposals and requests; the admin reviews, approves, and tracks everything from one dashboard and from Telegram.

**Scale constraints**
- A handful of admins, split across two portfolios (Social, Welfare)
- ~100 end users maximum
- No plans to scale beyond this

**What the system does**

| Area | Summary |
|---|---|
| Access | Telegram sign-in (no passwords), registration via WebApp or bot, admin approval, committee assignment |
| Proposals | Draft → in review → submitted → finished → grading → final; seven categories with per-category requirements; poster, blast message, document link; comments with replies; status history |
| Email | Admin previews/edits a confirmation email to the submitter (cc committee), sends via Resend, proposal advances to Submitted |
| Disposables | Plates/cups/bowls/forks/spoons requests, from a proposal (WebApp) or standalone (bot `/disposable`); approve/reject with reason; daily collection digest |
| Borrowing | Borrow the 4 foldable tables and the cambro via bot `/borrow`; owner approves, return proof photo, overdue reminders, delete |
| Calendar | Database-backed events colour-coded per committee; public iCalendar feed |
| Reminders | User → admin nudges; admin → committee broadcasts with deadline, inbox/outbox, "mark done", scheduled nudges |
| Grading | Post-event rubric self-assessment (14-day window) plus independent admin assessment; optional Google Drive evidence folders |
| AI reviewer | Admin pastes proposal text; Gemini returns structured suggestions (flag-gated) |
| Misc | Bug reports to admins, announcement relay, external CCA Google Form prefill links, dark mode |

---

## 2. Architecture

### 2.1 Stack

| Layer | Tech | Notes |
|---|---|---|
| Interface | Telegram Bot + Telegram WebApp | Bot for notifications, conversations and quick actions; WebApp for the full UI |
| Frontend | Vanilla JS SPA, no build step | Hash router, `innerHTML` templates, CSS custom properties, light/dark theme |
| Backend | Python, FastAPI, **synchronous** SQLAlchemy 2.0 | Serverless functions on Vercel (target); Pydantic v2 schemas |
| Database | PostgreSQL on **Supabase** | Free tier: 500 MB. App uses the pooler URL; migrations use a session/direct URL |
| Hosting/CI | **Vercel** | Free tier; auto-deploy from GitHub |
| Email | Resend | Draft generation, send with PDF attachment of the linked Google Doc |
| Calendar | Own `calendar_events` table + public `.ics` feed | **No Google Calendar API** (needs a billing account; removed for cost) |
| Google | Docs export (PDF), Drive folders for grading evidence | Service account; both optional |
| AI | Gemini `generateContent` | Optional, server-side key, off by default |

### 2.2 Architecture notes

- **Telegram identity** — the WebApp sends `Telegram.WebApp.initData` in the `X-Telegram-Init-Data` header on every request; the backend validates its HMAC with the bot token and rejects data older than 24 hours. There is no password or session auth. In `ENVIRONMENT=development` with **no** bot token set, a `dev_telegram_id` shortcut is accepted so the app can be tested in a browser; it is disabled the moment a token exists.
- **Roles and portfolios** — `user` and `admin` roles. A committee belongs to a **portfolio** (`social` or `welfare`). Admins are scoped to their portfolio: which one is decided from the env lists `SOCIAL_ADMIN_TELEGRAM_IDS` / `WELFARE_ADMIN_TELEGRAM_IDS` (with `ADMIN_TELEGRAM_IDS` as the Social fallback). Non-admins only ever see their own committees. Scoping is enforced in the route layer via `api/portfolio.py`.
- **Telegram bot** — webhook mode only (`POST /api/telegram/webhook`, protected by `TELEGRAM_WEBHOOK_SECRET`). Multi-step conversations (`/register`, `/disposable`, `/borrow`, `/return`, admin rejection/deletion reasons) are persisted in the `bot_conversations` table, so they survive restarts and multiple instances. Outbound Telegram calls are fire-and-forget: a Telegram failure never fails the database operation that triggered it.
- **Optional integrations are flag-gated and off by default** (`GRADING_ENABLED`, `GOOGLE_DRIVE_MODE`, `AI_REVIEWER_ENABLED`, `ANNOUNCEMENT_TELEGRAM_ID`); they are read through `api/config.py` only.
- **Status transitions** are validated server-side against `PROPOSAL_STATUS_TRANSITIONS` (draft/needs_action/in_review/submitted/finished); grading and final moves are made by the grading routes.
- **Schema changes always go through Alembic.** No raw SQL in route handlers.

### 2.3 System diagram

```
┌──────────────────────────────────────────────────────────────┐
│                          Telegram                            │
│  ┌──────────────┐       ┌──────────────────────────────────┐ │
│  │     Bot      │       │          WebApp (SPA)            │ │
│  │ commands,    │       │ Home · Calendar · Reminders ·    │ │
│  │ buttons,     │       │ Admin (Proposals, Disposables,   │ │
│  │ photos       │       │ Borrowing, Users) · Bug report   │ │
│  └──────┬───────┘       └───────────────┬──────────────────┘ │
└─────────┼───────────────────────────────┼────────────────────┘
          │ webhook                       │ fetch + initData
          ▼                               ▼
┌──────────────────────────────────────────────────────────────┐
│               FastAPI on Vercel (serverless)                 │
│ /api/auth /proposals /disposables /borrow /calendar          │
│ /reminders /email /admin /ai-review /telegram/webhook        │
├────────────┬──────────┬──────────┬─────────┬─────────────────┤
│ PostgreSQL │ Resend   │ Google   │ Gemini  │ Telegram Bot API│
│ (Supabase) │ (email)  │ Docs/    │ (AI     │ (outbound)      │
│            │          │ Drive    │ review) │                 │
└────────────┴──────────┴──────────┴─────────┴─────────────────┘
        ▲
        └── scheduled jobs (Vercel Cron / pg_cron) call protected endpoints
```

### 2.4 Deployment gaps (current code → Vercel + Supabase)

The code was developed to run as one long-lived process (uvicorn). Moving to serverless needs these changes; none is done yet.

| # | Gap | Needed |
|---|---|---|
| 1 | **Scheduled loops** — four `asyncio` tasks start in `api/main.py` (`collection_reminder_loop`, `reminder_nudge_loop`, `borrow_reminder_loop`, `grading_loop`). A serverless function has no persistent process. | Move each tick into a protected endpoint (e.g. `POST /api/cron/<job>` checked against a `CRON_SECRET`) and trigger it from Vercel Cron, Supabase `pg_cron` + `pg_net`, or an external scheduler. Vercel's free tier limits cron frequency, so check before relying on per-minute jobs. |
| 2 | **Webhook background work** — the webhook returns immediately and runs the handler as a `BackgroundTask`. | On serverless, process the update before returning (within the function time limit) so the work is not frozen after the response. |
| 3 | **DB connections** — SQLAlchemy's default pool does not suit serverless. | **Done.** `api/database.py` uses `NullPool` automatically for a transaction-pooler URL (port 6543); Alembic reads `DATABASE_URL_DIRECT` when set (`Settings.migration_database_url`). |
| 4 | **Row-level security** — tables in the `public` schema are exposed by Supabase's REST API. | **Done in code:** migration `0029` enables RLS on every table, and new-table migrations must do the same (see `CLAUDE.md`). Also turn the Data API off in the Supabase dashboard. |
| 5 | **Binary storage** — poster images and borrow return photos are `bytea` columns. | Move to Supabase Storage (or similar) before the 500 MB database limit becomes a problem. |
| 6 | **Static files** — FastAPI mounts `webapp/` at `/`. | Serve the SPA from Vercel's static hosting and keep the API under `/api`. |
| 7 | **Function timeout** — free-tier functions have a short time limit. | Keep requests short; the AI reviewer (up to 90 s) may need a longer limit. |
| 8 | **Insecure defaults** — `SECRET_KEY=dev-secret-key`, blank webhook secret, blank calendar token, `ENVIRONMENT=development`. | Fail at start-up when these are unset in production. |

---

## 3. Data Model

All tables are created and changed only by Alembic migrations (`migrations/versions/0001`–`0029`). Enum columns are stored as `VARCHAR` (non-native enums).

### 3.1 Tables

```
users                 id, telegram_id (BIGINT UNIQUE), email (UNIQUE), telegram_username (UNIQUE),
                      display_name, role ('user'|'admin'), status ('pending'|'approved'|'rejected'), created_at
committees            id, name (UNIQUE), color (#hex), portfolio ('social'|'welfare'|NULL), rf_name, rf_email
user_committees       user_id, committee_id                              (PK on both)

proposals             id, committee_id, submitted_by, title, category, description, doc_link,
                      blast_message, poster_filename, poster_content_type, poster_data (BYTEA),
                      status, event_date, event_time, requested_ccas (JSON text),
                      external_form_data (JSON text), drive_folder_id, drive_ready, drive_error,
                      drive_pdf_id (unused), created_at, updated_at
proposal_comments     id, proposal_id, author_id, reply_to_comment_id, body, created_at
proposal_comment_reads  user_id, proposal_id, read_at
proposal_status_history id, proposal_id, changed_by, from_status, to_status, created_at
proposal_gradings     proposal_id, started_by, started_at, deadline, user_data (JSON text),
                      admin_data (JSON text), user_submitted_at, admin_submitted_at,
                      admin_author_id, evidence_done_at
grading_notifications id, proposal_id, milestone, telegram_id, message, sent_at   (delivery receipts)
email_drafts          id, proposal_id, recipient, subject, body, created_at, updated_at

disposable_requests   id, proposal_id (NULL for standalone, UNIQUE), committee_id, requested_by,
                      activity_name, description, plates, cups, bowls, forks, spoons,
                      collection_date, collection_time, status ('pending'|'approved'|'rejected'),
                      rejection_reason, created_at
borrow_requests       id, committee_id, requested_by, activity_name, description, tables, cambro,
                      borrow_date, return_date,
                      status ('pending'|'approved'|'return_submitted'|'returned'|'rejected'),
                      rejection_reason, decided_by, decided_at, return_proof_file_id,
                      return_proof_content_type, return_proof_data (BYTEA, deferred),
                      return_submitted_at, returned_at, last_reminder_kind, last_reminded_on, created_at

calendar_events       id, committee_id, created_by, title, description, event_date, created_at

reminders             id, from_user, message, direction ('to_admin'|'to_user'),
                      target_type ('proposal'|'disposable'|'general'), target_id, is_read,
                      deadline, created_at
reminder_target_committees  reminder_id, committee_id
reminder_recipients   id, reminder_id, user_id, is_read, is_done, completed_at
reminder_nudges       id, reminder_recipient_id, milestone, message, sent_at   (delivery receipts)

bot_conversations     telegram_id (PK), flow, step, payload (JSON text), created_at, updated_at
```

Proposal statuses: `draft`, `needs_action`, `in_review`, `submitted`, `finished`, `grading`, `final`.
Proposal categories: `event`, `initiative`, `welfare`, `decor`, `pantry_cleaning`, `merch`, `pubs`.

### 3.2 Seed data

Committees and their portfolio, colour and RF (resident fellow) contacts are seeded by migrations (`0001`, `0002`, `0008`–`0013`). Welfare committees are Welfare Comm, HeaRHtfelt, Green Commm, BakeRHs and Cooks, Children, Pioneers, Special Projects, Special Needs and Welfare D (`WELFARE_COMMITTEE_NAMES` in `api/portfolio.py`, a fallback for rows whose `portfolio` is empty); every other committee (the Blocks, Intl Comm, Soccom and so on) is Social. RF contact details are real staff data; do not copy them into documents.

---

## 4. Feature Specification

### 4.1 Registration & Auth

| Step | Detail |
|---|---|
| 1 | User opens the WebApp (or sends `/register` to the bot); the backend validates Telegram identity |
| 2 | Unregistered → registration: name, email, Telegram username (WebApp), or name, email and CCA (bot) |
| 3 | Saved with `status = 'pending'`; every API call returns 403 until approved |
| 4 | Admins get a Telegram alert with Approve/Reject buttons (reject asks for an optional reason) |
| 5 | Admin assigns committees in the Users tab (bot registrations already carry a chosen CCA) |
| 6 | User gets a Telegram message on approval or rejection |

IDs in the admin env lists skip the queue and become admin and approved. Admin users also exist by promotion at first request.

### 4.2 User features

**Dashboard (Home)** — greeting, status summary tiles (Drafts, Needs Action, In Review, Submitted, Finished, Grading, Final) scoped to the user's committees, recent proposal cards with status badge, unread-comment counts, and a "New Proposal" action.

**Proposal submission** — fields: title, category (by portfolio), description, event date and time, Google Doc/link, blast message, poster upload, optional disposables request, optional external CCA requests. Saved as a draft or submitted for review. Editable while `draft` or `needs_action`. Category requirements:

| Requirement | Categories |
|---|---|
| Event date | event, initiative, pantry_cleaning |
| Poster | event, initiative, welfare, merch |
| Document link | event, merch |
| Blast message | event, initiative, welfare |

Social committees can use all seven categories; Welfare committees only `event` and `initiative`.

**Proposal detail** — full info and status badge; document link; comment thread with replies and unread tracking; status history; disposables section; confirmation-email preview (read-only for the user); grading section when applicable.

**Disposables** — request plates, cups, bowls, forks, spoons and a collection date/time, from a proposal or from the bot (`/disposable`, no proposal needed). Shows approval status and rejection reason.

**Borrowing (bot)** — `/borrow` to request foldable tables (4 in stock) and/or the cambro drink dispenser (1) for a date range; the bot offers only quantities that are free for those dates. `/return` to send a photo as proof; reminders the day before, on the return date, and daily when overdue.

**Calendar** — monthly view, events colour-coded by committee, filter by committee, admins add/edit/delete events, and a "subscribe from your calendar app" link to the `.ics` feed.

**Reminders** — an **Inbox** (always shown) and a collapsible **Outbox**. Users nudge admins about a proposal, disposable request or in general; admins' broadcasts to their committee appear in the user inbox with an optional deadline and a "mark as done" action.

**Bug report** — title and description, sent to the admins on Telegram.

**Theme** — light/dark toggle, falling back to Telegram's colour scheme or the system setting.

### 4.3 Admin features

#### 4.3.1 Admin dashboard

Tabs: **Proposals**, **Disposables**, **Borrowing**, **Users**. Proposals tab: status summary tiles, committee filter chips, sortable proposal cards. Admin views are limited to the admin's portfolio.

#### 4.3.2 Proposal management

- Change status along the allowed transitions; every change writes a history row and notifies the submitter on Telegram (with the optional reason as a comment).
- **Confirmation email** (events, plus Welfare initiatives): the admin previews and edits the draft; **Send & Submit** emails the submitter (to forward to the committee's RF contact), cc's the other committee members plus a configured address, strips links from the body, attaches the proposal PDF (exported from the linked Google Doc, or a signed link if too large) and moves the proposal to `submitted`. "Mark Submitted Without Email" skips the email.
- Event announcement relay: a finished event's poster and blast message can be posted to one Telegram chat (`ANNOUNCEMENT_TELEGRAM_ID`).

#### 4.3.3 Disposables

Requests grouped by collection date, today's collections highlighted; approve or reject (with reason) from the WebApp or Telegram inline buttons; delete. A morning digest lists the day's approved collections per portfolio.

#### 4.3.4 Borrowing

- New requests reach **every admin** with Approve/Reject buttons; approval re-checks that the items are still free. Reject asks for an optional reason.
- An item counts as out from approval until the owner confirms it is back, so availability reflects where things really are.
- Return proof: the borrower's photo is forwarded to the admins with **Confirm returned** / **Not yet**. The photo is also kept for the **Borrowing** tab.
- `/borrowed` shows what is out, with who has it, due dates and overdue flags, and Delete buttons.
- **Delete** (`/deleteborrow <id>`, `/borrowed` buttons, or the Borrowing tab) removes a request with an optional reason: its reminders stop, the borrower is told (unless it was already returned or rejected history), and the other admins are told.
- Reminders: day-before and due-date nudges to the borrower, a daily nudge when overdue, plus a daily overdue digest to admins.
- Inventory totals are constants in `api/services/borrowing.py` (`ITEM_TOTALS`).

#### 4.3.5 User management

Approve/reject registrations, change a user's email or Telegram handle, replace committee memberships.

#### 4.3.6 Reminders

User → admin nudges (with an unread badge on the admin nav), and admin → committee **broadcasts**: message, optional deadline, multi-select of committees grouped Social-then-Welfare. Recipients are fixed when the broadcast is sent. Each recipient can mark it done; scheduled Telegram nudges go out at send time, 3 days and 1 day before the deadline, skipping anyone already done. The admin's outbox shows who has finished.

#### 4.3.7 Grading (post-event)

Behind `GRADING_ENABLED` (default off). A matching admin opens grading on a Finished proposal, starting a 14-day window. The submitter files a self-assessment against a per-category rubric (Event: food, decor, activities, creativity; Initiative: activities, creativity; Decor/Pubs: creativity, visual quality; Welfare: quality, creativity; Pantry Cleaning: cleanliness, decor; Merch has none). Scores are 0–10 with a justification each. Submission moves the proposal to `final`. An admin files an independent assessment after the user's. Submitted assessments lock; admins can extend the deadline. Reminders go out 7, 3 and 1 days before the deadline. With `GOOGLE_DRIVE_MODE=live`, an evidence folder is created per proposal so people can upload proof images. See `docs/grading.md`.

#### 4.3.8 AI proposal reviewer

Behind `AI_REVIEWER_ENABLED` (default off). The admin pastes a proposal's text; the server sends it with the rubric in `config/ai_reviewer_rubrics.json` to Gemini and returns structured suggestions (severity, concern, recommended clarification). Nothing is stored. **Deferred:** reading the Google Doc directly and posting vetted comments back to it.

### 4.4 Shared features

#### 4.4.1 Navigation

Bottom tab bar: **Home**, **Calendar**, **Reminders**, **Bug Report**. Admins land on the admin dashboard from Home.

#### 4.4.2 Telegram bot commands

| Command | Who | What |
|---|---|---|
| `/start` | anyone | Open the WebApp |
| `/help` | anyone | List commands |
| `/register` | anyone | Register: name, email, CCA |
| `/status` | approved | Last proposals and their status |
| `/remind <message>` | approved | Nudge the admins |
| `/disposable` | approved | Request hall disposables (12-step chat) |
| `/borrow` | approved | Borrow tables / cambro |
| `/return` | approved | Send a photo proving borrowed items are returned |
| `/cancel` | anyone | Cancel the current flow |
| `/pending` | admin | Items needing action |
| `/approve <id>`, `/reject <id>` | admin | Decide a disposable request |
| `/borrowed` | admin | Where the borrowable items are now |
| `/deleteborrow <id>` | admin | Delete a borrow request |

Inline buttons carry the admin decisions (approve/reject disposables, registrations and borrow requests, confirm/not-yet on returns) and the borrower's "Send return photo" action.

#### 4.4.3 External CCA forms

A proposal can request other CCAs. For each CCA configured in `GOOGLE_FORM_CONFIGS` (or `config/google_forms.json`), the UI builds a prefilled Google Form link. Adding a form needs no migration. Completion is tracked in the browser only.

---

## 5. API Routes

60 operations, from the application's OpenAPI schema. Every route except health, the webhook, the `.ics` feed and the signed document link requires `X-Telegram-Init-Data`.

```
Health / Telegram
  GET    /api/health
  POST   /api/telegram/webhook            — Telegram updates (secret token header)

Auth
  POST   /api/auth/validate               — Is this Telegram user registered / approved
  POST   /api/auth/register               — Submit registration

Users (admin)
  GET    /api/admin/users
  PATCH  /api/admin/users/{user_id}       — Approve/reject, email, handle, committees

Committees
  GET    /api/committees                  — Scoped by portfolio for admins
  GET    /api/committees/forms            — Configured external CCA forms

Proposals
  GET    /api/proposals                   POST /api/proposals
  GET    /api/proposals/summary
  GET    /api/proposals/{id}              PATCH /api/proposals/{id}       — Edit or change status
  POST   /api/proposals/{id}/poster       GET   /api/proposals/{id}/poster
  GET    /api/proposals/{id}/document     — PDF export (signed token or login)
  POST   /api/proposals/{id}/announce     — Post a finished event to the announcement chat
  GET    /api/proposals/{id}/comments     POST  /api/proposals/{id}/comments
  POST   /api/proposals/{id}/comments/read
  GET    /api/proposals/{id}/status-history

Grading
  GET    /api/proposals/{id}/grading      PUT   /api/proposals/{id}/grading
  POST   /api/proposals/{id}/grading/start
  POST   /api/proposals/{id}/grading/extend
  POST   /api/proposals/{id}/grading/evidence
  POST   /api/proposals/{id}/grading/evidence/done

AI review (admin)
  POST   /api/ai-review

Email (admin)
  GET    /api/email/preview/{proposal_id}   PATCH /api/email/preview/{proposal_id}
  POST   /api/email/send/{proposal_id}      — Send via Resend, advance to 'submitted'

Disposables
  GET    /api/disposables                 POST  /api/disposables/{proposal_id}
  GET    /api/disposables/today
  PATCH  /api/disposables/{id}            DELETE /api/disposables/{id}
  POST   /api/disposables/{id}/decision  — approve / reject with reason

Borrowing (admin)
  GET    /api/borrow                      GET   /api/borrow/summary
  POST   /api/borrow/{id}/decision        — approve / reject with reason
  POST   /api/borrow/{id}/return          — confirm returned / not yet
  POST   /api/borrow/{id}/delete          — delete with optional reason
  GET    /api/borrow/{id}/proof           — return photo

Calendar
  GET    /api/calendar                    POST  /api/calendar
  PATCH  /api/calendar/{id}               DELETE /api/calendar/{id}
  GET    /api/calendar/feed-url           GET   /api/calendar/feed.ics   — token-protected feed

Reminders
  POST   /api/reminders                   — User → admin nudge
  GET    /api/reminders                   GET   /api/reminders/unread-count
  POST   /api/reminders/broadcast         — Admin → committees (optional deadline)
  GET    /api/reminders/inbox             GET   /api/reminders/outbox
  PATCH  /api/reminders/{id}/read         PATCH /api/reminders/{id}/done
  DELETE /api/reminders/{id}              DELETE /api/reminders/{id}/mine

Bug reports
  POST   /api/bug-reports
```

---

## 6. Email Flow

**Direction:** Admin → submitter (sent through Resend from the configured verified sender).

**Trigger:** the admin opens an `in_review` proposal of a type that uses the email workflow (events in either portfolio, and Welfare initiatives) and clicks **Send & Submit**.

1. The backend generates a draft from a template; the admin can edit subject and body.
2. Resend sends to the submitter, with the other members of the committee and a configured address (`RESEND_CC_EMAIL`, or `WELFARE_RESEND_CC_EMAIL` for Welfare) in cc. Links are stripped from the subject and body.
3. The proposal PDF (exported from the linked Google Doc) is attached; if it is too large, a signed 7-day link is used instead.
4. The proposal moves to `submitted` and the submitter is notified on Telegram.

Without `RESEND_API_KEY` and `RESEND_FROM_EMAIL`, drafts and previews still work but sending returns a configuration error. Only `docs.google.com` documents can be exported; for any other link the admin uses **Mark Submitted Without Email**.

---

## 7. Notification Flow

| Trigger | Who is notified | Channel |
|---|---|---|
| New registration | Admins | Telegram, with Approve/Reject buttons |
| Registration approved / rejected | The user | Telegram |
| New proposal submitted | Admins of the portfolio | Telegram |
| Status change on a proposal | Submitter | Telegram (with optional reason) |
| New comment | Admins or the proposal owner | Telegram |
| Email sent (→ submitted) | Submitter | Resend email + Telegram |
| Disposable request submitted | Admins | Telegram, with Approve/Reject buttons |
| Disposable approved / rejected | Requester | Telegram (rejection includes the reason) |
| Collection day (morning) | Admins, per portfolio | Telegram digest |
| Borrow request submitted | All admins | Telegram, with Approve/Reject buttons |
| Borrow approved / rejected | Borrower | Telegram |
| Return photo received | All admins | Telegram photo with Confirm / Not yet |
| Return confirmed / not confirmed | Borrower | Telegram |
| Borrow due soon / due / overdue | Borrower | Telegram, with "Send return photo" button |
| Items overdue | All admins | Telegram digest |
| Borrow request deleted | Borrower (active requests) and other admins | Telegram |
| User reminder to admin | Admins | Telegram + nav badge |
| Admin reminder broadcast | Recipients | Telegram + inbox |
| Broadcast deadline nudges (send, −3 d, −1 d) | Recipients not yet done | Telegram |
| Grading opened / 7, 3, 1 days before deadline / submitted | Submitter and portfolio admins | Telegram |
| Bug report | Admins | Telegram |
| Finished event announcement | Announcement chat | Telegram |

All Telegram sends log and continue on failure.

---

## 8. Scheduled Jobs

Today these run as in-process loops (`api/services/scheduled_notifications.py`). Under Vercel/Supabase each becomes a protected endpoint triggered by a scheduler (see 2.4 gap 1).

| Job | Cadence | Purpose | Receipt |
|---|---|---|---|
| Collection digest | every 10 min, from `COLLECTION_REMINDER_HOUR` | Today's approved disposable collections | in memory (re-sends after restart) — should move to the database |
| Reminder nudges | every minute | Broadcast deadline nudges | `reminder_nudges` |
| Borrow reminders | every 10 min, from `COLLECTION_REMINDER_HOUR` | Day-before, due and overdue reminders, admin overdue digest | `last_reminder_kind` / `last_reminded_on` on the row |
| Grading | every minute (Drive retry every 10th tick) | 7/3/1-day grading reminders, retry Drive folder creation | `grading_notifications` |

The first and second loops run only when `ENVIRONMENT=production`; the grading loop only when `GRADING_ENABLED`. Time zone for "today": `APP_TIMEZONE` (default `Asia/Kuala_Lumpur`).

---

## 9. UI Design Direction

**Goal:** clean, modern, friendly; clearly not generic AI output. The current look follows a Geex-style admin dashboard adapted to a phone-width Telegram WebApp.

- **Framework:** vanilla JS and CSS custom properties (`webapp/css/styles.css`), no build step.
- **Type:** Plus Jakarta Sans via Google Fonts.
- **Palette:** soft off-white page background with white cards, indigo accent, light/dark themes driven by `data-theme`.
- **Cards:** 20 px corners, soft diffuse shadow, no outlines; tinted status tiles with an icon bubble.
- **Status badges:** pastel pills with a dot — needs_action amber, in_review blue, submitted purple, finished green, grading amber, final purple, return_submitted blue, returned green.
- **Layout:** single column, mobile-first, 480 px max width.
- **Navigation:** floating pill bar at the bottom with line icons and an unread badge.
- **Empty states:** helpful, not cute. "No proposals yet — submit one to get started."
- **Motion:** minimal; tap feedback only.

---

## 10. Project Structure

```
social-port-hub/
├── CLAUDE.md                    # Coding guidelines for Claude Code
├── README.md
├── SOCIAL-PORT-HUB.md           # This specification
├── requirements.txt  requirements-dev.txt
├── alembic.ini  Procfile        # Procfile is the legacy Railway entry; replaced by Vercel config
├── .env.example
│
├── api/
│   ├── main.py                  # FastAPI app, routers, scheduler start-up
│   ├── config.py                # pydantic-settings; the only place env vars are read
│   ├── database.py  models.py  schemas.py
│   ├── auth.py                  # Telegram initData validation, current user/admin
│   ├── portfolio.py             # Social/Welfare scoping helpers
│   ├── routes/
│   │   ├── auth.py  users.py  committees.py
│   │   ├── proposals.py  grading.py  ai_review.py  email.py
│   │   ├── disposables.py  borrow.py  calendar.py
│   │   ├── reminders.py  bug_reports.py  telegram_webhook.py
│   └── services/
│       ├── telegram.py  resend_email.py  email_attachments.py
│       ├── google_docs.py  google_drive.py  google_forms.py  document_links.py
│       ├── grading.py  ai_reviewer.py
│       ├── disposables.py  borrowing.py
│       ├── reminder_broadcasts.py  scheduled_notifications.py
│
├── bot/
│   ├── handlers.py              # Webhook dispatch, simple commands
│   ├── conversations.py         # Persistent register / disposable flows, admin rejection
│   ├── borrowing.py             # /borrow, /return, /borrowed, delete, admin callbacks
│   └── notifications.py         # Outbound message templates
│
├── config/                      # ai_reviewer_rubrics.json, google_forms.json
├── docs/grading.md
├── migrations/                  # Alembic, 0001–0029
├── tests/                       # pytest
│
└── webapp/
    ├── index.html
    ├── css/styles.css
    └── js/
        ├── app.js  api.js  theme.js
        ├── pages/   home, admin-dashboard, proposal-detail, calendar, reminders,
        │            register, bug-report
        └── components/  nav, icons, status-badge, proposal-card, disposable-form,
                         committee-forms, grading, ai-review
```

Vercel's own configuration (`vercel.json` or the framework's zero-config layout) is added during the deployment phase.

---

## 11. Environment Variables

All are read once through `api/config.py`.

```bash
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBAPP_URL=             # Public HTTPS URL of the deployed app
TELEGRAM_WEBHOOK_SECRET=         # Required in production

# Database (Supabase)
DATABASE_URL=                    # Transaction pooler URL (port 6543) used by the app
DATABASE_URL_DIRECT=             # Session pooler/direct URL used by Alembic only (port 5432)

# App
ENVIRONMENT=production           # development | production
SECRET_KEY=                      # Signs the document links; must not be the default
ADMIN_TELEGRAM_IDS=              # Comma-separated; Social fallback
SOCIAL_ADMIN_TELEGRAM_IDS=
WELFARE_ADMIN_TELEGRAM_IDS=
APP_TIMEZONE=Asia/Kuala_Lumpur
COLLECTION_REMINDER_HOUR=8
CALENDAR_FEED_TOKEN=             # Token for the .ics feed URL
CRON_SECRET=                     # PLANNED: protects the scheduled-job endpoints

# Email (Resend)
RESEND_API_KEY=
RESEND_FROM_EMAIL=               # Verified sender
RESEND_CC_EMAIL=
WELFARE_RESEND_CC_EMAIL=

# Optional integrations — all off by default
GRADING_ENABLED=false
GRADING_REMIND_ADMINS=true
GOOGLE_DRIVE_MODE=disabled       # disabled | live
GOOGLE_SERVICE_ACCOUNT_FILE=     # Path, or use the JSON variable below
GOOGLE_SERVICE_ACCOUNT_JSON=
GOOGLE_DRIVE_PARENT_FOLDER_ID=
GOOGLE_DRIVE_SOCIAL_PARENT_FOLDER_ID=
GOOGLE_DRIVE_WELFARE_PARENT_FOLDER_ID=
GOOGLE_FORM_CONFIGS={}           # JSON keyed by committee name
AI_REVIEWER_ENABLED=false
GEMINI_API_KEY=
AI_REVIEWER_MODEL=gemini-2.5-flash
AI_REVIEWER_MAX_CHARS=60000
AI_REVIEWER_TIMEOUT_SECONDS=90
ANNOUNCEMENT_TELEGRAM_ID=0       # 0 disables announcements
```

On Vercel, prefer `GOOGLE_SERVICE_ACCOUNT_JSON` (there is no persistent file path).

---

## 12. Implementation Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation: auth, registration, committees, migrations | Built |
| 2 | Proposals, status workflow, committee-scoped visibility, dashboard | Built |
| 3 | Calendar (database + `.ics` feed), disposables | Built |
| 4 | Email drafts and sending, user → admin reminders | Built |
| 5 | Admin committee filters, bot quick commands, scheduled digest, UI polish | Built |
| 6 | Comments, status history, category requirements, poster/blast/doc uploads | Built |
| 7 | Social/Welfare portfolio separation | Built |
| 8 | Post-event grading, Drive evidence folders, admin → user reminder broadcasts | Built |
| 9 | AI proposal reviewer | Built as v1 (pasted text, flag-gated); Google Doc read-through and posting comments back are deferred |
| — | Bot conversations: `/register`, `/disposable` with inline admin approval | Built |
| — | Borrowing tables and cambro (`/borrow`, `/return`, `/borrowed`, delete) | Built |
| — | UI refresh (Geex-style) | Built |
| **Next** | **Move to Vercel + Supabase** (section 2.4) | **Planned** |

### Migration to Vercel + Supabase

1. Create the Supabase project (Singapore region), save the password, copy the pooler and session/direct URLs, disable the Data API or enable RLS.
2. ~~Add `DATABASE_URL_DIRECT` support and `NullPool`; add the RLS migration.~~ Done (migration `0029`).
3. Run `alembic upgrade head` against Supabase; copy data from the current database with `pg_dump --data-only`.
4. Add scheduled-job endpoints and a scheduler; make the webhook process updates inline.
5. Move posters and return photos to object storage.
6. Add Vercel configuration; set environment variables; deploy.
7. Point `TELEGRAM_WEBAPP_URL` at the new domain and re-run `setWebhook`.

---

## 13. Key Decisions — Resolved

1. **Framework** — FastAPI with synchronous SQLAlchemy (simplest correct choice at ~100 users).
2. **Hosting** — Vercel (free tier), serverless Python functions plus static frontend.
3. **Database** — Supabase PostgreSQL (free tier, 500 MB). Pooler URL for the app, session/direct URL for migrations.
4. **Calendar** — our own `calendar_events` table and an `.ics` feed. No Google Calendar API; do not reintroduce it without revisiting this decision.
5. **Sender email** — a verified single sender in Resend.
6. **Admin granularity** — multiple admins, scoped by **portfolio** (Social, Welfare). No committee-head sub-roles.
7. **Telegram bot mode** — webhook only; conversation state lives in the database.
8. **Optional integrations** — flag-gated, off by default, read via `api/config.py`.
9. **Notifications** — fire-and-forget; a Telegram failure never fails the underlying operation.
10. **Equipment borrowing** — fixed inventory (4 tables, 1 cambro); items count as out until the owner confirms the return photo.

---

## 14. Reference

- [Telegram WebApp Docs](https://core.telegram.org/bots/webapps)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Supabase Docs — connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [Vercel Docs — Python / FastAPI](https://vercel.com/docs)
- [Resend Docs](https://resend.com/docs)
- [Google Docs API](https://developers.google.com/docs/api) · [Drive API](https://developers.google.com/drive/api)
- [Gemini API](https://ai.google.dev/gemini-api/docs)
