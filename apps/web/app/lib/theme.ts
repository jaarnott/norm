export const breakpoints = {
  mobile: 768,
  tablet: 1024,
};

/**
 * Colours as CSS variables from styles/tokens.css, so a restyle is one edit
 * there rather than a hunt through inline styles. Keys are kept flat and
 * string-valued: ThreadCard and AdminThreadsPanel look them up by domain name.
 *
 * Values are mapped by how each key is USED, not by its name — `primary` is
 * the tan used for selected borders and focus, not the near-black primary
 * button (--primary). The per-member accents are retired (one neutral accent);
 * until their call sites move to the shared components they map to a neutral
 * that still reads under white text where a page used them as a button fill.
 *
 * Inside Claude these resolve against the same tokens.css, which the MCP
 * bundle imports. Never append alpha to these (`${colors.x}18`): use
 * color-mix() or a token.
 */
export const colors = {
  // Brand
  primary: 'var(--brand-soft)',      // tan: selected borders, focus, accents
  primaryHover: 'var(--brand)',
  sidebar: 'var(--text)',

  // Backgrounds
  pageBg: 'var(--canvas)',
  cardBg: 'var(--bg)',
  inputBg: 'var(--bg)',
  selectedBg: 'var(--selected)',
  waitingBg: 'var(--accent-soft)',

  // Former per-member accents — neutral now (see above)
  procurement: 'var(--text-soft)',
  hr: 'var(--text-soft)',
  time_attendance: 'var(--text-soft)',
  marketing: 'var(--text-soft)',
  reports: 'var(--text-soft)',
  executive_chef: 'var(--text-soft)',
  app_builder: 'var(--text-soft)',
  home: 'var(--muted)',
  norm: 'var(--text-soft)',
  unknown: 'var(--muted)',

  // Text
  textPrimary: 'var(--text)',
  textSecondary: 'var(--text-soft)',
  textMuted: 'var(--muted)',
  textOnDark: 'var(--on-primary)',
  textOnPrimary: 'var(--on-primary)',

  // Borders
  border: 'var(--line)',
  borderLight: 'var(--line-soft)',

  // Semantic
  success: 'var(--ok)',
  error: 'var(--error)',
  warning: 'var(--warn)',
  info: 'var(--info)',

  // Status dots
  statusApproval: 'var(--warn)',
  statusInput: 'var(--brand-soft)',
  statusClarification: 'var(--error)',
  statusApproved: 'var(--ok)',
  statusRejected: 'var(--muted)',
  statusSubmitted: 'var(--info)',

  // Status badges (text on tint)
  badgeApproval: { bg: 'var(--warn-bg)', text: 'var(--warn)' },
  badgeInput: { bg: 'var(--accent-soft)', text: 'var(--accent)' },
  badgeClarification: { bg: 'var(--error-bg)', text: 'var(--error)' },
  badgeApproved: { bg: 'var(--ok-bg)', text: 'var(--ok)' },
  badgeRejected: { bg: 'var(--line-soft)', text: 'var(--text-soft)' },
  badgeSubmitted: { bg: 'var(--info-bg)', text: 'var(--info)' },

  // Avatars
  adminAvatar: 'var(--primary)',
  managerAvatar: 'var(--selected)',
};
