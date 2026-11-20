// Where Unfurl runs. Must be one of the "host_permissions" in manifest.json (SPEC §7.3).
// Default: your personal instance (D-155, D-156). To try the extension against the
// development server, use "http://localhost:8000" instead, reload the extension and
// sign in again (only one token is kept). Switch back afterwards, or test jobs end up
// in your real data. A deployed address comes with deploy (M13c, later).
export const APP_BASE_URL = "http://127.0.0.1:8100";
