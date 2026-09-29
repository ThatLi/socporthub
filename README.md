# Social Port Hub

A Telegram-native Bot + WebApp that replaces spreadsheets, group chats, and manual
email drafting for running ~100-member student committees. Committee members submit
event proposals, hall-disposables requests, and post-event self-assessments; a
portfolio admin reviews, approves, and communicates — all from inside Telegram, with
no separate login system.

Built solo end-to-end: schema design, API, background jobs, Telegram bot, and a
vanilla-JS frontend, deployed on Railway.

Full product spec: [`SOCIAL-PORT-HUB.md`](./SOCIAL-PORT-HUB.md).

## Highlights

- **Zero-friction auth** — Telegram's `initData` is HMAC-validated server-side
  (`api/auth.py`); no passwords, sessions, or separate account system.
- **Server-enforced state machine** — proposals move through
  `draft → in_review → submitted → finished → grading → final`, with every legal
  transition (and who's allowed to trigger it) validated against
  `PROPOSAL_STATUS_TRANSITIONS` in `api/models.py`, never trusted from the client.
- **Multi-portfolio RBAC** — Social and Welfare are scoped independently (admins,
  committees, notifications, email templates) via one small helper module
  (`api/portfolio.py`) instead of scattered `if` checks.
- **Post-event grading workflow** — category-specific rubrics, a 14-day
  self-assessment window, scheduled Telegram reminders with delivery receipts, and
  optional Google Drive evidence folders — all behind feature flags, off by default.
- **Config-driven form linking** — new external (CCA) forms are added via one JSON
  config entry (`GOOGLE_FORM_CONFIGS`), not a migration.
- **Own calendar, no GCP bill** — events live in Postgres and are exposed as a public,
  subscribable iCalendar feed (`GET /api/calendar/feed.ics`) instead of depending on
  the paid Google Calendar API — see [Calendar](#calendar) for why.
- **Fire-and-forget notifications** — every Telegram push is logged-and-continued on
  failure so a flaky bot API call never blocks or rolls back the underlying DB write.
- **Tested** — pytest suite covering auth, proposal lifecycle, calendar/disposables,
  and grading (`tests/`); 22 reversible Alembic migrations, no hand-edited schema.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + sync SQLAlchemy 2.0 | Simplest correct choice at ~100 users; no async complexity to pay for |
| Database | PostgreSQL + Alembic | Relational fits the domain; every schema change is a migration |
| Bot | Raw Telegram Bot API over `httpx` | No bot framework; webhook runs inside the same FastAPI process |
| Frontend | Vanilla JS SPA | No build step, served as static files by FastAPI |
| Email | Resend | Admin-editable draft → send, auto-advances proposal status |
| Docs/Drive | Google Docs & Drive APIs (service account) | PDF export for email attachments; evidence folders for grading |
| Hosting | Railway | One process (`Procfile` runs migrations, then `uvicorn`) |

## Status

**Phases 1–8 shipped:** registration/approval, committees, the full proposal
lifecycle (comments with replies, status history, category-specific requirements,
poster/PDF/blast-message uploads), an in-app calendar with a public iCalendar feed,
hall disposables requests/approval, admin email drafts/sending via Resend, reminders,
Social/Welfare portfolio separation, config-driven external CCA form linking, and a
post-event grading workflow with Google Drive evidence folders.

**Phase 9 — AI Proposal Reviewer — is next and final** (deferred, not yet built): read
a proposal's linked Google Doc, send it to an LLM for review, and let the admin vet
suggested comments before they're posted back to the doc. See `SOCIAL-PORT-HUB.md` §11
and `CLAUDE.md` for the current boundary between "built" and "spec only."

**Also backlogged (not scheduled):** bidirectional reminders. Today reminders only
flow user → admin; the plan is an admin compose UI that broadcasts to specific CCAs
(multi-select, grouped Social-then-Welfare, collapsible), a real inbox for users, an
Inbox/Outbox redesign of the shared Reminders tab, and an optional per-reminder
deadline with a "mark as done" action plus scheduled Telegram nudges at send-time,
3-days-left, and 1-day-left (reusing the grading reminder scheduler's
delivery-receipt pattern). See `SOCIAL-PORT-HUB.md` §4.3.6.

The calendar deviates from the original spec: instead of the Google Calendar API
(which requires a GCP billing account), events live in our own database and are
exposed via a free, open **iCalendar (.ics) feed** that anyone can subscribe to from
Google/Apple/Outlook calendar apps — see [Calendar](#calendar) below.

## Local development

For grading setup and workflow rules, see [Grading](docs/grading.md).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN, DATABASE_URL, ADMIN_TELEGRAM_IDS, SECRET_KEY

# Postgres must be running and DATABASE_URL pointing at it
alembic upgrade head

uvicorn api.main:app --reload
```

Email sending requires `RESEND_API_KEY` and `RESEND_FROM_EMAIL` (a verified Resend
sender). Without them, drafts and reminder features still work, but sending an email
returns a clear configuration error.

The optional AI proposal reviewer uses Gemini's `generateContent` API with a structured
JSON response. Set `AI_REVIEWER_ENABLED=true`, `GEMINI_API_KEY`, and optionally
`AI_REVIEWER_MODEL` in `.env`; keep the key server-side and never expose it to the browser.

The webapp is served at `/` from static files in `webapp/`; the API lives under `/api`.
Health check: `GET /api/health`.

**Testing the webapp outside Telegram:** with `ENVIRONMENT=development` and no
`TELEGRAM_BOT_TOKEN` set, the frontend and `api/auth.py` fall back to a `dev_telegram_id`
identity so you can open `http://localhost:8000` directly in a browser. Set
`localStorage.dev_telegram_id` in devtools to switch identities (e.g. an ID listed in
`ADMIN_TELEGRAM_IDS` to see the admin dashboard). This bypass is hard-disabled the
moment `TELEGRAM_BOT_TOKEN` is set, so it can't leak into a real deploy.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Telegram setup

1. Create a bot via [@BotFather](https://t.me/BotFather), grab `TELEGRAM_BOT_TOKEN`.
2. Set `TELEGRAM_WEBAPP_URL` to your deployed HTTPS URL (WebApp requires HTTPS; use a
   tunnel like `ngrok` for local testing).
3. Register the webhook once your app is deployed:
   ```bash
   curl -F "url=${TELEGRAM_WEBAPP_URL}/api/telegram/webhook" \
        -F "secret_token=${TELEGRAM_WEBHOOK_SECRET}" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook"
   ```
4. In BotFather, set the WebApp menu button (`/setmenubutton`) to `TELEGRAM_WEBAPP_URL`.
5. Add your own numeric Telegram user ID to `ADMIN_TELEGRAM_IDS` (comma-separated) so you
   land in the admin dashboard instead of the registration flow.

## Calendar

Events live entirely in our own Postgres table (`calendar_events`) — created, listed,
and filtered through the webapp only. There's no external calendar API, no GCP project,
and no cost.

For people who want hall events to show up in their own phone's calendar app, the app
also publishes a public **iCalendar feed** at `GET /api/calendar/feed.ics`, which Google
Calendar, Apple Calendar, and Outlook can all "subscribe to" natively (Settings → Add
calendar → From URL). Subscribed calendars are read-only and typically refresh every few
hours, not instantly — fine for a hall events calendar. The webapp's Calendar tab shows
a "Subscribe from your phone's calendar app" link with the URL, so nobody needs to know
this endpoint exists.

Set `CALENDAR_FEED_TOKEN` (any random string) to require it as a `?token=` query param
on that URL — this keeps the feed from being trivially guessable by outsiders, similar
to how Google Calendar's own "secret address in iCal format" works. Leave it blank to
serve the feed with no token (fine for local dev).

## Deployment (Railway)

1. New Railway project → add a PostgreSQL plugin → copy its `DATABASE_URL`.
2. Deploy this repo (Railway reads `Procfile`, which runs migrations then starts uvicorn).
3. Set all variables from `.env.example` in Railway's dashboard.
4. Point `TELEGRAM_WEBAPP_URL` at the Railway-issued domain and re-run the `setWebhook`
   step above.

## Project layout

```
api/         FastAPI app, models, routes, services (Telegram, Resend, Google Docs/Drive/Forms)
bot/         Telegram webhook update handling (/start, /help, /status, /remind) + outbound notifications
webapp/      Vanilla JS SPA (Telegram WebApp UI)
migrations/  Alembic (22 migrations, schema history)
docs/        Feature-specific operational docs (e.g. grading setup)
tests/       pytest — auth, proposals, calendar/disposables, grading
```
