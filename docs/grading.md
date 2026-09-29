# Proposal grading

## Workflow

- A matching portfolio admin opens grading on a Finished proposal, starting a
  14-day window. Only the original submitter edits the self-assessment.
- Category rubrics: Event (food, decor, activities, creativity), Initiative
  (activities, creativity), Decor and Pubs (creativity, visual quality), Welfare
  (quality, creativity), Pantry Cleaning (cleanliness, decor).
- Decor/Pubs allow multiple subtype selections; Welfare requires one subtype.
- Scores are whole numbers from 0 to 10: 0 is not applicable, 10 is maximum.
  Each score needs a justification, including N/A. Partial drafts are allowed.
- User submission moves the proposal to Final. Admins can save an independent
  private draft and submit their assessment after the user's submission.
- Submitted assessments are locked. Late user submissions remain accepted;
  overdue proposals show a banner until submitted.
- An admin can push the 14-day deadline further out (`POST
  /api/proposals/:id/grading/extend`, only while the self-assessment is still
  outstanding) — the new date must be later than the current one. Extending
  drops any already-queued 7/3/1-day Telegram reminders for the old deadline so
  they get recomputed, and can fire again, against the new one.
- Merch has no grading rubric. Pubs is a Social category; Welfare committees
  continue to use Event and Initiative.

## Enable grading and Drive folders

Apply migration `0021` using `alembic upgrade head`, then configure:

```env
GRADING_ENABLED=true
GRADING_REMIND_ADMINS=true
GOOGLE_DRIVE_MODE=live
GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/private/service-account.json
GOOGLE_DRIVE_SOCIAL_PARENT_FOLDER_ID=your-social-folder-id
GOOGLE_DRIVE_WELFARE_PARENT_FOLDER_ID=your-welfare-folder-id
```

Drive is used only to create/reuse proposal evidence folders. Users and admins can
open the folder from the grading screen and upload proof images themselves. The
application does not export or upload proposal PDFs to Drive.

## Reminders

Opening grading notifies the submitter and matching portfolio admins. Further
reminders are sent during the 24-hour windows starting 7, 3 and 1 days before the
deadline, provided the proposal remains in Grading. The scheduler runs every
minute and stores delivery receipts. Failed sends retry; a crash after Telegram
accepts a message but before the receipt is saved can duplicate that send.

Set `GRADING_REMIND_ADMINS=false` after testing to stop copying deadline reminders
to admins. User submissions always notify their matching portfolio admins.
Social notifications explicitly exclude Welfare admin IDs.

Grading defaults to disabled and Drive defaults to disabled. The local demo
pages and identity switcher are not included in the app.
