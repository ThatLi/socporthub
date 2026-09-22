Social Port Hub — Project Specification

> Telegram Bot + WebApp for consolidating Raffles Hall committee management under the Social Director.

---

## 1. Project Overview

A single platform for the Social Director (or successor) to manage event proposals, hall disposables requests, calendars, and communications across all RH committees. Users (committee members) submit proposals and requests; the admin reviews, approves, and tracks everything from one dashboard.

**Scale constraints:**
- 1 admin (or up to 11 committee-level admins)
- ~100 end users maximum
- No plans to scale beyond this

---

## 2. Architecture

### 2.1 Stack

| Layer | Tech | Notes |
|---|---|---|
| Interface | Telegram Bot + Telegram WebApp | Bot for notifications/reminders; WebApp for full UI |
| Frontend | Vanilla HTML/CSS/JS (or lightweight framework) | Served as Telegram WebApp; keep bundle small |
| Backend | Python (Flask or FastAPI) | REST API; FastAPI recommended for async + type hints |
| Database | PostgreSQL (Railway-managed) | Relational fits well — proposals, users, committees, requests |
| Hosting/CI/CD | Railway | One-click deploy from GitHub, managed Postgres addon |
| Email | Resend (free tier: 100 emails/day) | Draft generation + sending |
| Calendar | Google Calendar API | One shared calendar, events colour-coded per committee |
| File review | Google Docs API | Read-only access to linked docs for the AI reviewer (deferred) |

### 2.2 Architecture Assessment

The proposed stack is sufficient. Specific notes:

- **Database is missing from your list.** You need one. PostgreSQL on Railway is the simplest — it's a one-click addon on the same platform as your backend. SQLite could work at this scale but complicates Railway deploys (ephemeral filesystem).
- **Vercel vs Railway:** Pick one. Railway is better here — it runs Python natively, bundles Postgres, and you already used it for the check-in bot. Vercel is optimised for Node/edge and would need workarounds for Python (serverless functions with cold starts). **Recommendation: Railway only.**
- **Resend free tier** gives 100 emails/day and 1 custom domain. More than enough for this scale. You'll generate draft emails for users to copy-paste, so actual send volume is low.
- **Google Calendar API** requires a GCP project with Calendar API enabled. Use a service account that owns a single shared calendar. Events are colour-coded per committee using Google Calendar's `colorId` field. Share the calendar with committee members for read access in their own Google Calendar app.
- **Google Docs API** is only needed for the deferred AI reviewer. For now, users just paste a link.
- **Telegram WebApp auth** — Telegram provides `initData` with user identity, validated via HMAC. No separate auth system needed for Telegram users; the registration flow just maps a Telegram user to an approved email + committee.

### 2.3 System Diagram

```
┌─────────────────────────────────────────────────────┐
│                    Telegram                         │
│  ┌──────────┐    ┌────────────────────────────────┐ │
│  │   Bot    │    │         WebApp (UI)            │ │
│  │ (notifs) │    │  Landing · Calendar · Reminders│ │
│  └────┬─────┘    └──────────────┬─────────────────┘ │
└───────┼─────────────────────────┼───────────────────┘
        │                         │
        ▼                         ▼
┌─────────────────────────────────────────────────────┐
│              Python Backend (FastAPI)                │
│  /api/auth  /api/proposals  /api/disposables        │
│  /api/calendar  /api/reminders  /api/admin           │
├─────────────┬──────────────┬────────────────────────┤
│  PostgreSQL │ Google Cal   │  Resend                │
│  (Railway)  │ API          │  (email drafts)        │
└─────────────┴──────────────┴────────────────────────┘
```

---

## 3. Data Model

### 3.1 Tables

```sql
-- Users & access
users (
    id            SERIAL PRIMARY KEY,
    telegram_id   BIGINT UNIQUE NOT NULL,
    email         VARCHAR(255) UNIQUE NOT NULL,
    display_name  VARCHAR(100),
    role          VARCHAR(20) DEFAULT 'user',    -- 'user' | 'admin'
    status        VARCHAR(20) DEFAULT 'pending', -- 'pending' | 'approved' | 'rejected'
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

committees (
    id    SERIAL PRIMARY KEY,
    name  VARCHAR(100) UNIQUE NOT NULL,  -- 'Block 2', 'Soccom', etc.
    color VARCHAR(7) NOT NULL             -- hex colour for calendar events, e.g. '#E67C73'
);

user_committees (
    user_id      INT REFERENCES users(id),
    committee_id INT REFERENCES committees(id),
    PRIMARY KEY (user_id, committee_id)
);

-- Proposals
proposals (
    id            SERIAL PRIMARY KEY,
    committee_id  INT REFERENCES committees(id),
    submitted_by  INT REFERENCES users(id),
    title         VARCHAR(255) NOT NULL,
    description   TEXT,
    doc_link      TEXT,                          -- Google Docs / any URL
    status        VARCHAR(20) DEFAULT 'needs_action',
                  -- 'needs_action' | 'in_review' | 'submitted' | 'finished'
    event_date    DATE,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Hall disposables
disposable_requests (
    id            SERIAL PRIMARY KEY,
    proposal_id   INT REFERENCES proposals(id),  -- tied to a proposal
    requested_by  INT REFERENCES users(id),
    plates        INT DEFAULT 0,
    cups          INT DEFAULT 0,
    forks         INT DEFAULT 0,
    spoons        INT DEFAULT 0,
    collection_date DATE NOT NULL,
    approved      BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Reminders
reminders (
    id            SERIAL PRIMARY KEY,
    from_user     INT REFERENCES users(id),
    message       TEXT NOT NULL,
    target_type   VARCHAR(20),  -- 'proposal' | 'disposable' | 'general'
    target_id     INT,          -- nullable FK to relevant entity
    is_read       BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Email drafts (generated)
email_drafts (
    id            SERIAL PRIMARY KEY,
    proposal_id   INT REFERENCES proposals(id),
    recipient     VARCHAR(255),
    subject       VARCHAR(255),
    body          TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.2 Seed Data — Committees

Pre-populate on first deploy:

```
Block 2, Block 3, Block 4, Block 5, Block 6, Block 7, Block 8, Intl Comm, Soccom
```

---

## 4. Feature Specification

### 4.1 Registration & Auth

| Step | Detail |
|---|---|
| 1 | User opens Telegram WebApp → backend validates `initData` HMAC |
| 2 | If `telegram_id` not in `users` table → show registration form |
| 3 | User inputs email → saved with `status = 'pending'` |
| 4 | Admin gets Telegram notification → approves/rejects in admin panel |
| 5 | On approval, admin assigns user to one or more committees |
| 6 | User gets Telegram message confirming access |

### 4.2 User Features

#### 4.2.1 Landing Page / Dashboard

- **Top section:** Status summary cards showing proposal counts by status:
  `[Needs Action] [In Review] [Submitted] [Finished]`
  Scoped to the user's committee(s).
- **Below:** Recent proposals list (most recent first), each showing title, status badge, event date, and submitter.
- Tapping a proposal opens its detail page.

#### 4.2.2 Proposal Submission

- Form fields: Title, Description, Event Date, Google Doc/Link (optional)
- On submit → `status = 'needs_action'`, admin notified via Telegram bot
- User can view and edit their own proposals while in `needs_action`

#### 4.2.3 Proposal Detail Page

- Full proposal info + status badge
- Disposables request section (if applicable):
  - Plates, Cups, Forks, Spoons quantity inputs
  - Collection date picker
  - Submit/edit disposables request
  - Shows approval status
- Email section (read-only for user):
  - Shows the auto-generated email draft that admin will send
  - User can preview but not edit or send
- Doc link (opens in external browser)
- Status timeline / history

#### 4.2.4 Calendar View

- Monthly calendar showing events from the shared Google Calendar
- Events colour-coded by committee
- User can add important dates (syncs to Google Calendar, tagged with their committee colour)
- Visual indicators for proposal event dates
- Filter by committee

#### 4.2.5 Committee Visibility

- Users can see all proposals from people in the same committee
- Read-only access to other members' submissions
- Own submissions are editable (when status allows)

#### 4.2.6 Reminders

- User can send a "nudge" to admin for any pending item
- Free-text message attached to a specific proposal or general
- Shows in admin dashboard as a notification badge
- **[PLANNED — NOT YET BUILT]** Users will also get an **Inbox** of reminders admins
  send *to* them (see §4.3.6). Today the Reminders tab only shows nudges the user has
  sent ("Sent reminders") — there is no path for an admin to message a user through
  this system.

### 4.3 Admin Features

#### 4.3.1 Admin Dashboard

- **Committee selector:** Tabs or dropdown for each committee
  `[Block 2] [Block 3] ... [Intl Comm] [Soccom] [All]`
- **Per-committee view:**
  - Summary cards: proposal counts by status
  - Proposals list with status badges, sortable/filterable
  - Calendar for that committee's events
  - Pending disposable requests (highlighted on collection day)

#### 4.3.2 Proposal Management

- View full proposal details
- Change status: `needs_action → in_review → submitted → finished`
- Each status change triggers a Telegram notification to the submitter
- **Email send action:** Admin previews the auto-generated email draft, can edit it, then hits "Send & Submit". This:
  1. Sends the email from admin's configured email address to the user's registered email via Resend
  2. Automatically advances the proposal status to `submitted`
  3. Notifies the user via Telegram that their proposal has been submitted
- View/approve linked disposables request

#### 4.3.3 Disposables Dashboard

- Aggregated view of all approved disposable requests
- Grouped by collection date
- Today's collections highlighted at the top
- Total quantities per day (useful for procurement)

#### 4.3.4 User Management

- Approve/reject pending registrations
- Assign users to committees
- View all users per committee

#### 4.3.5 AI Proposal Reviewer [DEFERRED — LOW PRIORITY]

- Reads the linked Google Doc via Docs API
- Sends content to an LLM for review
- Generates suggested comments
- Admin vets each comment before posting back to the doc
- **Not in MVP. Requires separate LLM training/prompting work.**

#### 4.3.6 Admin Reminder Broadcast [PLANNED — NOT YET BUILT]

Today `Reminder` (`api/models.py`) and `POST`/`GET /api/reminders`
(`api/routes/reminders.py`) only support **user → admin** nudges. This section specs
the reverse direction — admins sending reminders to users — which does not exist yet.
Don't assume any of the below when working in `api/routes/reminders.py`,
`api/models.py`, or `webapp/js/pages/reminders.js`; check the current code first.

**Goal:** give admins a compose UI — mirroring the user's existing "Nudge your
portfolio director" form — that drafts a reminder message and targets it at specific
CCAs (committees), instead of blasting every user.

- Same compose form shape users have today (message textarea + send), plus a **CCA
  picker**:
  - Multi-select list of committees, grouped under two headers in portfolio order —
    **Social**, then **Welfare** — using `committee_portfolio()` (`api/portfolio.py`)
    for grouping. An admin only sees/targets CCAs within their own portfolio, same
    scoping as everywhere else (`admin_committee_filter`).
  - The picker is a **collapsible**, collapsed by default, so composing a reminder
    doesn't require scrolling through every committee first.
  - Selecting a CCA targets every member of that committee (via `UserCommittee`), not
    a single user — this is a broadcast, not a DM.
- Sending delivers a Telegram notification to each targeted member (fire-and-forget,
  per convention) and needs a per-recipient record so the reminder shows up in each
  member's inbox with its own read state — either one `Reminder` row per recipient or
  a single row plus a recipient join table; decide at implementation time based on how
  granular read receipts need to be.
- Requires a new direction/target concept on the reminder model — the current schema
  only ever has one implicit direction (user → admin) and one target (a specific
  admin, implicitly). This needs an Alembic migration; do not hand-edit the schema.

**Reminders tab restructure (both roles), once this ships:**

1. **Inbox** (top, always expanded) — reminders addressed *to* the current user:
   - Regular user: reminders sent by admins.
   - Admin: nudges sent by users (today's behavior, including the existing unread
     badge count).
2. **Outbox** (below Inbox, collapsible, collapsed by default) — reminders the
   current user has *sent*:
   - Regular user: nudges they've sent to admin (today's "Sent reminders" list).
   - Admin: broadcasts they've sent, including which CCAs each one targeted.
   - "Outbox" replaces "outgoing reminders" as the section name — shorter, and pairs
     naturally with "Inbox".

This changes the shared `Reminders` tab (`webapp/js/pages/reminders.js`) for both
roles — it's not admin-only UI bolted on top of the existing page.

### 4.4 Shared Features

#### 4.4.1 Navigation

Three tabs in the WebApp:
```
[ Home ]  [ Calendar ]  [ Reminders ]
```
- Home: Dashboard (user or admin variant based on role)
- Calendar: Committee calendar view
- Reminders: Sent (user) or received (admin) reminders — see §4.3.6 for the planned
  Inbox/Outbox redesign covering both directions for both roles

#### 4.4.2 Telegram Bot Commands

```
/start        — Opens WebApp or shows welcome message
/status       — Quick status of user's proposals (inline, no WebApp needed)
/remind       — Quick-send a reminder to admin
/help         — Usage guide
```

Admin-only:
```
/pending      — List of items needing action across all committees
/approve <id> — Quick-approve a disposable request
```

---

## 5. API Routes

```
Auth
  POST   /api/auth/validate          — Validate Telegram initData
  POST   /api/auth/register          — Submit registration (email)

Users (admin)
  GET    /api/admin/users             — List all users
  PATCH  /api/admin/users/:id         — Edit email, approve/reject, assign committee

Proposals
  GET    /api/proposals               — List (filtered by committee, status)
  POST   /api/proposals               — Create new proposal
  GET    /api/proposals/:id           — Detail view
  PATCH  /api/proposals/:id           — Edit proposal (user) or change status (admin)

Disposables
  POST   /api/disposables             — Submit disposable request
  GET    /api/disposables             — List (admin: all; user: own)
  PATCH  /api/disposables/:id         — Approve/reject (admin)
  GET    /api/disposables/today       — Today's approved collections (admin)

Calendar
  GET    /api/calendar                 — Fetch events from shared Google Calendar (filterable by committee colour)
  POST   /api/calendar                 — Add event (colour-coded by committee)

Reminders (current — user → admin only)
  POST   /api/reminders               — Send reminder to admin
  GET    /api/reminders               — List reminders (admin)
  PATCH  /api/reminders/:id/read      — Mark as read

Reminders [PLANNED — NOT YET BUILT, see §4.3.6]
  POST   /api/reminders/broadcast     — Admin sends a reminder to one or more CCAs
  GET    /api/reminders/committees    — Committees available to target, grouped by portfolio

Email (admin)
  GET    /api/email/preview/:proposal_id — Generate/preview email draft
  PATCH  /api/email/preview/:proposal_id — Admin edits draft before sending
  POST   /api/email/send/:proposal_id    — Send email via Resend + advance status to 'submitted'
```

---

## 6. Email Flow

**Direction:** Admin → User (sent via Resend from admin's configured email).

**Trigger:** Admin clicks "Send & Submit" on a proposal in `in_review` status.

**What happens:**
1. Backend generates email from template (admin can edit before sending)
2. Resend sends from admin's verified email to user's registered email
3. Proposal status auto-advances to `submitted`
4. User gets Telegram notification

**Resend setup:** Requires a verified domain or single sender email (e.g. `welfare@raffleshall.sg` or admin's NUS email). Free tier supports 100 emails/day — more than enough.

```
Subject: [Committee Name] Event Proposal — {proposal.title}

Dear {user.display_name},

Your event proposal for {proposal.title} for {committee.name} has been reviewed and
submitted. Here is a summary:

{if disposables_request}
Hall disposables have been approved for collection
on {disposable.collection_date}:
  - Plates: {disposable.plates}
  - Cups: {disposable.cups}
  - Forks: {disposable.forks}
  - Spoons: {disposable.spoons}
{/if}

Please copy and paste everything below the line into a new email to your RF. Please reattach the PDF below.

To: {committee.rf_email}
CC: rh.social.i@u.nus.edu
Subject: [Committee Name] Event Proposal — {proposal.title}

Dear {committee
.rf_name},

Here is the proposal for {proposal.title} happening on {proposal.event_date} for your approval! Do let me know your comments. Many thanks!

Best regards,
{user.display_name}
{user.committee_name}

{attach converted PDF from Google Doc link}
```

---

## 7. Notification Flow

| Trigger | Who gets notified | Channel |
|---|---|---|
| New proposal submitted | Admin | Telegram bot message |
| Status change on proposal | Submitter | Telegram bot message |
| Admin sends email (→ submitted) | Submitter | Resend email + Telegram bot message |
| Disposable request submitted | Admin | Telegram bot message |
| Disposable request approved | Submitter | Telegram bot message |
| User sends reminder | Admin | Telegram bot message + dashboard badge |
| New user registration | Admin | Telegram bot message |
| Collection day (morning of) | Admin | Telegram bot scheduled message |

---

## 8. UI Design Direction

**Goal:** Clean, modern, and clearly *not* AI slop.

- **Framework:** Vanilla JS + CSS custom properties. No heavy framework needed at this scale.
- **Type:** Inter or Geist Sans via Google Fonts — clean, neutral, not the serif/cream default.
- **Palette:** Derive from Raffles Hall branding if available; otherwise a restrained neutral base (white/slate) with one intentional accent colour for status badges and CTAs.
- **Status badges:** Colour-coded pills:
  - `needs_action` → amber/orange
  - `in_review` → blue
  - `submitted` → purple
  - `finished` → green
- **Layout:** Single-column, mobile-first (Telegram WebApp is phone-width). Cards for proposals, no heavy borders or shadows.
- **Navigation:** Bottom tab bar fixed to viewport — Home, Calendar, Reminders. Admin sees the same tabs but with admin-scoped content.
- **Empty states:** Helpful, not cute. "No proposals yet — submit one to get started."
- **Interactions:** Minimal animation. Tap feedback only. No scroll-triggered reveals.

---

## 9. Project Structure

```
social-port-hub/
├── CLAUDE.md                    # Coding guidelines for Claude Code
├── README.md
├── requirements.txt
├── Procfile                     # Railway entrypoint
├── .env.example
│
├── bot/
│   ├── __init__.py
│   ├── handlers.py              # Telegram bot command handlers
│   └── notifications.py         # Outbound notification logic
│
├── api/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory
│   ├── auth.py                  # Telegram initData validation
│   ├── routes/
│   │   ├── proposals.py
│   │   ├── disposables.py
│   │   ├── calendar.py
│   │   ├── reminders.py
│   │   ├── email.py
│   │   ├── users.py
│   │   └── admin.py
│   ├── models.py                # SQLAlchemy models
│   ├── schemas.py               # Pydantic request/response schemas
│   └── services/
│       ├── google_calendar.py   # Google Calendar API wrapper
│       ├── resend_email.py      # Resend API wrapper
│       └── telegram.py          # Bot API helpers
│
├── webapp/
│   ├── index.html               # SPA entry point
│   ├── css/
│   │   └── styles.css
│   ├── js/
│   │   ├── app.js               # Router + init
│   │   ├── api.js               # Fetch wrapper
│   │   ├── pages/
│   │   │   ├── home.js
│   │   │   ├── calendar.js
│   │   │   ├── reminders.js
│   │   │   ├── proposal-detail.js
│   │   │   ├── register.js
│   │   │   └── admin-dashboard.js
│   │   └── components/
│   │       ├── nav.js
│   │       ├── status-badge.js
│   │       ├── proposal-card.js
│   │       └── disposable-form.js
│   └── assets/
│
├── migrations/                  # Alembic migrations
│   └── ...
│
└── tests/
    └── ...
```

---

## 10. Environment Variables

```bash
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBAPP_URL=          # Public URL of the webapp

# Database
DATABASE_URL=                 # Railway provides this

# Google
GOOGLE_SERVICE_ACCOUNT_JSON=  # Base64-encoded service account key
GOOGLE_CALENDAR_ID=           # Single shared calendar ID

# Resend
RESEND_API_KEY=
RESEND_FROM_EMAIL=              # Verified sender, e.g. welfare@raffleshall.sg

# App
ADMIN_TELEGRAM_IDS=           # Comma-separated Telegram user IDs for admin role
SECRET_KEY=                   # For session/HMAC
```

---

## 11. Implementation Phases

### Phase 1 — Foundation (MVP)
- [ ] Railway project setup + PostgreSQL
- [ ] FastAPI app skeleton with health check
- [ ] Telegram bot with `/start` that opens WebApp
- [ ] `initData` HMAC validation
- [ ] User registration flow (submit email → admin approves)
- [ ] Database models + Alembic migrations
- [ ] Seed committees

### Phase 2 — Core Proposals
- [ ] Proposal CRUD (create, list, detail, edit)
- [ ] Admin status management (change status on proposals)
- [ ] Telegram notifications on status change
- [ ] Committee-scoped visibility (users see their committee's proposals)
- [ ] Landing page dashboard with status summary cards

### Phase 3 — Calendar & Disposables
- [ ] Google Calendar API integration (service account)
- [ ] Calendar view in WebApp (read + add events)
- [ ] Disposable request form on proposal detail page
- [ ] Admin disposables dashboard with collection-day view
- [ ] Disposable approval flow

### Phase 4 — Email & Reminders
- [x] Email draft auto-generation from proposal data
- [x] Admin email preview + edit UI on proposal detail page
- [x] Resend integration — admin sends email, proposal auto-advances to `submitted`
- [x] Verified sender setup (domain or single sender email)
- [x] Reminder system (user → admin nudges)
- [x] Admin reminder inbox with badge count

### Phase 5 — Polish
- [x] Admin committee tabs with per-committee filtering
- [x] Bot inline commands (`/status`, `/pending`)
- [x] Scheduled morning notification for today's disposable collections
- [x] Empty states, loading states, error handling
- [ ] Mobile UI pass — test in actual Telegram WebApp
- [x] Add time to disposables collection requests
- [x] Add save button and functionality for proposal submission for review
- [x] Add poster section and blast message section for user upload, for admin to view
- [x] Add a section in the New Proposal page above 'Description' named 'Category' with these categories: Event, Initiative, Decor, Pantry Cleaning
- [x] For Event and Initiative, a poster, link/PDF URL, and blast message must be submitted
- [x] For Decor, no poster or link/PDF is required
- [x] For Pantry Cleaning, no poster or link/PDF is required
- [x] For Merch, a poster and link/PDF URL are required; description, event date, and blast message are optional

### Phase 6 — AI Reviewer [DEFERRED]
- [ ] Google Docs API read access
- [ ] LLM integration for proposal review
- [ ] Comment suggestion UI for admin vetting
- [ ] Post-approved comments back to Google Doc

### Backlog — Bidirectional Reminders & Inbox [PLANNED, NOT SCHEDULED]

See §4.3.6 for the full spec.

- [ ] Direction/target-user concept on the `Reminder` model (Alembic migration)
- [ ] Admin compose UI mirroring the existing user nudge form
- [ ] CCA multi-select target picker, grouped by portfolio (Social, then Welfare),
      inside a collapsible
- [ ] Broadcast delivery to all members of each targeted committee (Telegram,
      fire-and-forget)
- [ ] Reminders tab redesign for both roles: Inbox (top, expanded) + Outbox
      (collapsible, renamed from "outgoing reminders")
- [ ] Per-recipient read state so unread badge counts stay accurate for broadcasts

---

## 12. Key Decisions — Resolved

1. **Framework** — FastAPI (async, auto OpenAPI docs, type hints).
2. **Google Calendar setup** — One shared calendar, colour-coded by committee.
3. **Sender email address** — New personal email address, verified as single sender in Resend.
4. **Admin granularity** — Single admin (Feng) for now. No committee-head sub-roles.
5. **Webapp hosting** — Serve static files from FastAPI on Railway (simplest), or GitHub Pages. Either works; Railway keeps everything in one deploy.

---

## 13. Reference

- [Telegram WebApp Docs](https://core.telegram.org/bots/webapps)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Google Calendar API](https://developers.google.com/calendar/api)
- [Resend Docs](https://resend.com/docs)
- [Railway Docs](https://docs.railway.app/)
