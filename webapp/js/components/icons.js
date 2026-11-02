const wrap = (body) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const ICONS = {
  file: wrap('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>'),
  draft: wrap('<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>'),
  alert: wrap('<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>'),
  eye: wrap('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  send: wrap('<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>'),
  check: wrap('<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>'),
  star: wrap('<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8-6.2-3.3L5.8 21 7 14.2 2 9.3l6.9-1z"/>'),
  award: wrap('<circle cx="12" cy="8" r="6"/><path d="M15.5 13.5 17 22l-5-3-5 3 1.5-8.5"/>'),
};

export const STATUS_ICON = {
  draft: "draft",
  needs_action: "alert",
  in_review: "eye",
  submitted: "send",
  finished: "check",
  grading: "star",
  final: "award",
};
