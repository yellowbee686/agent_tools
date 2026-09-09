# Codex usage development guide

## Scope

This directory contains a zero-runtime-dependency Python CLI and local web
dashboard for personal ChatGPT account token usage exposed by Codex app-server.
Do not use the Enterprise Analytics API for this tool.

## Interface contracts

- Start `codex app-server --stdio` and communicate with newline-delimited JSON.
- Send `initialize`, then the `initialized` notification, before
  `account/usage/read`.
- Reuse the current Codex-managed ChatGPT login. Never request, read, print, or
  persist browser cookies, auth tokens, API keys, or Workspace IDs.
- Treat dates as UTC. The default window is the last 30 complete calendar days;
  explicit `--start` is inclusive and `--end` is exclusive.
- Preserve the account summary in JSON. Table and CSV contain the selected
  daily total-token buckets.
- Personal account usage provides daily total tokens, not Enterprise
  per-user/client/model or input/output breakdowns.
- Save the latest raw app-server and imported detailed responses under the
  Git-ignored `data/` directory with file mode `0600`.
- Never import or persist request headers, cookies, bearer tokens, or copied
  cURL commands. The detailed importer accepts response JSON only.
- Normalize detailed daily token categories and model turn counts in
  `usage_store.py`; reject malformed, negative, or unclassified token data
  before exact cost math.
- Compare sources by UTC date. Treat exact values separately from differences
  within 1%, material mismatches, and single-source dates.
- Merge both sources into the daily chart and table. Use stacked detailed
  categories when available and a clearly labeled app-server-only bar when not.
- Daily table cost must use detailed category tokens only. Never substitute a
  percentage estimate for a missing detailed day. Price each detailed day with
  official Standard API rates weighted by that day's model turns.
- Bind the dashboard to `127.0.0.1` only and serve an explicit asset allowlist.
- Keep measured token data visually and semantically distinct from modeled API
  cost. Token-share assumptions remain visible and editable; model rates come
  from the imported mix and the official catalog, not from a UI model picker.
- `web/calculations.js` owns browser-side date filtering and cost math. Keep it
  free of DOM dependencies so Node tests can exercise it directly.

## Engineering conventions

- Code and comments must be in English.
- All Python functions must have type annotations.
- Prefer standard-library solutions and fail fast on malformed app-server
  responses.
- Log user-actionable CLI errors and return a non-zero exit code.
- Add or update tests for every behavior change.

## Verification

Run from this directory:

```bash
python3 -m unittest discover -s tests -v
node --test web/calculations.test.mjs
python3 codex_usage.py --help
python3 codex_usage.py month --help
python3 web_server.py --help
```
