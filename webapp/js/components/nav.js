const svg = (body) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

const ICONS = {
  home: svg('<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>'),
  calendar: svg('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'),
  reminders: svg('<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'),
  "bug-report": svg('<path d="m8 2 1.9 1.9M14.1 3.9 16 2M9 7.1V6a3 3 0 0 1 6 0v1.1"/><path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6M12 20v-9M6.5 13H2M3 21c0-2.1 1.7-3.9 3.8-4M20.97 21c0-2.1-1.7-3.9-3.8-4M22 13h-4.5M3 5c0 2.1 1.7 3.9 3.8 4M20.97 5c0 2.1-1.7 3.9-3.8 4"/>'),
};

const TABS = [
  { route: "home", label: "Home" },
  { route: "calendar", label: "Calendar" },
  { route: "reminders", label: "Reminders" },
  { route: "bug-report", label: "Bug Report" },
];

export function renderBottomNav(activeRoute, reminderCount = 0) {
  return `
    <div class="bottom-nav">
      ${TABS.map(
        (t) =>
          `<a href="#" data-nav="${t.route}" class="${activeRoute === t.route ? "active" : ""}">${ICONS[t.route]}<span>${t.label}</span>${t.route === "reminders" && reminderCount ? `<span class="nav-badge">${reminderCount}</span>` : ""}</a>`
      ).join("")}
    </div>
  `;
}
