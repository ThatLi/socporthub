# Proposal comment deletion

Approved scope: an approved admin can delete only their own comments on proposals
they can currently access. This includes comments entered during status changes,
but deleting a comment never changes the proposal status or its status history.

Keep the comment record, author, posting time, and reply links. Set `deleted_at`
once and show “This comment was deleted.” in place of its body. Retain the original
body internally for audit purposes, but redact it from every comment API response.
There is no restore or view-original interface. This preserves conversation history
and reply references without introducing a separate audit system; removing rows
would break those references, while clearing bodies would prevent later auditing.

Existing replies remain visible and show the deleted placeholder for their parent.
Deleted comments hide their own reply context and cannot receive new replies.
Exclude deleted comments from unread counts; keep original activity timestamps.
Deletion does not notify users or retract previously delivered Telegram messages.

Implementation sequence:
1. Add nullable deletion metadata with an additive migration.
2. Add an authenticated, ownership-checked DELETE endpoint; redact responses,
   reject replies to deleted parents, and update unread counts, with API tests.
3. Add an accessible Delete control and confirmation, preserve unfinished drafts,
   handle failures, and verify rendering and interactions.

Deploy migration 0031 before the updated application. Rolling back the migration
removes deletion markers and makes retained bodies visible again; avoid downgrading
after this feature is in use without deciding how to preserve those markers.
