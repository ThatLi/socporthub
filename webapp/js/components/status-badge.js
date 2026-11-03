const LABELS = {
  draft: "Draft",
  needs_action: "Needs Action",
  in_review: "In Review",
  submitted: "Submitted",
  finished: "Finished",
  grading: "Grading",
  final: "Final",
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  return_submitted: "Return sent",
  returned: "Returned",
};

export function statusBadge(status) {
  const label = LABELS[status] || status;
  return `<span class="badge badge-${status}">${label}</span>`;
}
