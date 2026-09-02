# Codex usage

`codex-usage` reports token activity for the personal ChatGPT account currently
signed in to the local Codex CLI. It includes a local web dashboard and a CLI.
Both use the official Codex app-server
`account/usage/read` method, so it does not need an Enterprise workspace,
Admin key, Workspace ID, `.env`, or browser session extraction.

## Requirements

- Python 3.9 or newer.
- A current Codex CLI with `codex app-server` support.
- The Codex CLI signed in to the ChatGPT account whose usage you want to read.

Check the local account if needed:

```bash
codex login status
```

## Web dashboard

Start the local dashboard from this directory:

```bash
python3 web_server.py
```

It opens [http://127.0.0.1:8765](http://127.0.0.1:8765) automatically. The
dashboard provides:

- Inclusive start and end date selection.
- Daily token chart and sortable reading order in a detailed table.
- Period token total, active-day count, and daily average.
- API-equivalent cost estimates with editable pricing and token shares.
- A GPT-5.6 Sol preset using $4/M uncached input, $0.40/M cached input, and
  $20/M output.
- A detailed-response importer for exact cached, uncached, and output token
  categories.
- Per-day validation between the Codex app-server and imported ChatGPT
  Analytics response.
- A stacked daily chart and detailed table with cached input, uncached input,
  output, category total, app-server total, source difference, and exact
  category cost.

Use another local port or skip opening the browser when needed:

```bash
python3 web_server.py --port 9000
python3 web_server.py --no-open
```

The default estimate assumes 90% cached input, 9% uncached input, and 1%
output. These shares are adjustable in the page. They are assumptions because
the personal account usage method returns only daily total tokens.

### Import detailed ChatGPT Analytics data

1. Open `https://chatgpt.com/codex/cloud/settings/analytics` in Chrome.
2. Open DevTools and select **Network**.
3. Choose 7 days, 1 month, or a custom date range on the Analytics page.
4. Filter Network requests by `daily-token-usage-breakdown`.
5. Select the successful request and open its **Response** tab.
6. Right-click the response and choose **Copy response**. If that menu item is
   unavailable, select the response text and copy it normally.
7. In the local dashboard, select **Import detailed JSON**, paste, and select
   **Save and validate**.

The response body is safe to import because it does not contain request
authentication headers. Do not use **Copy as cURL** for this workflow because
that copy can include the short-lived bearer token.

When the imported response completely covers the selected dashboard range,
cost uses its exact cached-input, uncached-input, and output categories. When
coverage is incomplete, the dashboard falls back to the visible percentage
assumptions.

When a detailed snapshot exists, the dashboard initially selects that
snapshot's first and last dates. Every imported day always shows its exact
category cost in the daily table. Dates without detailed categories show no
daily cost instead of silently substituting an estimated value.

## Local snapshots

The dashboard saves the latest raw response from each source with file mode
`0600`:

```text
data/app_server.json
data/detailed.json
```

The entire `data/` directory is ignored by Git. Each refresh replaces the
app-server snapshot; each successful import replaces the detailed snapshot.
The comparison view matches dates and classifies them as exact, close (within
1%), mismatched, or present in only one source.

## CLI

Run directly from this directory:

```bash
python3 codex_usage.py month
```

The default window is the last 30 complete UTC calendar days. For example, a
run on August 24 covers July 25 through August 23.

Available formats:

```bash
python3 codex_usage.py month --format table
python3 codex_usage.py month --format json
python3 codex_usage.py month --format csv > usage.csv
```

Use explicit UTC boundaries when needed. `--start` is inclusive and `--end` is
exclusive:

```bash
python3 codex_usage.py month --start 2026-08-01 --end 2026-08-24
```

## Install the command

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
codex-usage month
codex-usage-web
```

## Data boundary

The personal-account method returns account-level summary metrics and one total
token count per day. The imported ChatGPT Analytics response supplies the
cached-input, uncached-input, and output breakdown used for exact cost math.

The CLI starts `codex app-server --stdio`, performs the required initialization
handshake, calls `account/usage/read`, and exits. Authentication remains owned
by Codex; this tool never reads browser cookies or stored auth tokens.

## Development

```bash
python3 -m unittest discover -s tests -v
node --test web/calculations.test.mjs
```

Official documentation: [Codex app-server account usage](https://learn.chatgpt.com/docs/app-server#7-token-usage-chatgpt).

Pricing references: [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
and [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching#measure-cache-reads-and-writes).
