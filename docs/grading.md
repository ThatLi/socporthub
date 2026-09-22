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

## Enable grading and Drive

Apply migration `0021` using `alembic upgrade head`, then configure:

```env
GRADING_ENABLED=true
GRADING_REMIND_ADMINS=true
GOOGLE_DRIVE_MODE=live
GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/private/service-account.json
GOOGLE_DRIVE_SOCIAL_PARENT_FOLDER_ID=your-social-folder-id
GOOGLE_DRIVE_WELFARE_PARENT_FOLDER_ID=your-welfare-folder-id
```

Use a real Workspace shared drive. A `GOOGLE_SERVICE_ACCOUNT_JSON` hosting secret
can replace the credential file. `GOOGLE_DRIVE_PARENT_FOLDER_ID` is the fallback
if portfolio-specific parent IDs are absent. Keep credentials out of Git.

Give the service account permission to create folders/files and grant the
submitter writer access to its subfolder. Sharing policies must permit the
submitter's registered email, which must be usable as a Google account. Admin
access is inherited from the chosen parent; use separate portfolio parents to
keep that access scoped. The integration never enables public sharing.

Folders are created on first submission for review, named
`[committee-name]-[proposal-title]`, and reused for grading. Drafts do not create
folders. The scheduler also picks up existing submitted proposals. The app checks
the parent for an existing folder tagged with the proposal ID before creating one,
so a retry after an uncertain response can reconcile a folder instead of duplicating
it. Accessible supporting
Google Docs are copied as PDFs; this is a snapshot at the first successful copy.
Folder, sharing or PDF errors do not prevent grading. Setup retries every
10 minutes, with a manual retry in the grading form.

The integration uses `drive.file` and `supportsAllDrives=true`.
See [Google folder creation](https://developers.google.com/workspace/drive/api/guides/folder),
[shared-drive support](https://developers.google.com/workspace/drive/api/guides/enable-shareddrives),
and [uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads).

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
