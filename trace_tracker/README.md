# Trace Tracker

A read-only local viewer for Codex and Claude Code sessions. It displays messages,
reasoning, tool calls, usage, and parent/subagent relationships without starting
agent tasks or rewriting native traces.

## Install and run

From the `agent_tools` repository root, using Python 3.10 or later:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install ./trace_tracker
.venv/bin/trace-tracker
```

Open <http://127.0.0.1:47318>. The default sources are `~/.codex` and `~/.claude`.
To browse offline catalogs or choose explicit agent roots:

```bash
trace-tracker --data-root /path/to/snapshots --list-trace-roots
trace-tracker --data-root /path/to/snapshots --data-root /another/catalog
trace-tracker --codex-root /path/to/codex --cc-root /path/to/claude
python -m trace_tracker --default-agent codex
```

A catalog can contain nested roots with `sessions/` (Codex) or `projects/`
(Claude Code), or bundles with `codex/sessions/` and `cc/projects/` or
`claude/projects/`. Offline roots are added to the live sources. Per-agent root
options replace that agent's automatic sources. Catalog discovery stops at a
recognized root and skips dependency and workspace directories; session lists
load each root independently with pagination.

No offline catalogs are scanned unless requested. Trace content can include
prompts, tool arguments/results, local paths, and request context; this tool
renders it locally and does not anonymize exports or make traces public.

## Reuse parsers

```python
from pathlib import Path
from trace_tracker.adapters.codex.parser import parse

session = parse(Path("/path/to/rollout-example.jsonl"))
if session is not None:
    print(session.summary)
```

`trace_tracker.adapters.cc.parser` reads native Claude Code JSONL and an optional
`.context.json` sidecar (`system_prompt` and `tools`).
`trace_tracker.adapters.dsh.parser` reads DeepSeek Harness plain or zstd-compressed
JSONL, including session-scoped RPC event logs. `adapters.dsh.live.LiveTraceReader`
reads incremental local stream events. DSH is a parser API; it does not currently
have a standalone viewer tab.

All parsers return the shared models in `trace_tracker.core.models` and require
no agent installation, account login, internal platform, or model endpoint.

## Development

```bash
python -m pip install -e ./trace_tracker
python -m unittest discover -s trace_tracker/tests -v
```

Tests use synthetic traces in temporary directories and make no model calls.
