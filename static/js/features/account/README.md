# Hosted account controls

`security.js` owns Identity Platform account/security UI. `usage-consent.js`
owns the independent optional usage-statistics choice. Both are hosted-only;
local writing must never request an account preference or need sign-in.

The non-modal prompt offers equal one-click Allow statistics / No thanks
actions. Both answers are saved by the authenticated server, not browser
storage. Profile → Privacy exposes the same choice as a switch. Loading and
saving failures never imply consent, dismiss an unsaved first choice, or block
writing. Authentication loss clears the transient account choice.

The server owns notice version, scope hash, timestamp and compare-and-swap
revision. Records live privately on the account root, within the existing
lifecycle/erasure fence and account retention. They are not project content,
structural Undo, advertising permission or installation-wide consent.

This preference alone does not activate activity collection. The separately gated
`activity.js` sender and `hosted/activity` producers must
check current consent, the exact notice scope and active-account fence before
writing anything, apply the promised 30-day activity retention, and stop when
permission is withdrawn. A broader purpose requires a new notice version.
