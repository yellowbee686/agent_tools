#!/usr/bin/env python3
"""Read-only local trace viewer for Codex and Claude Code.

    trace-tracker
    trace-tracker --default-agent codex
    trace-tracker --data-root /path/to/snapshots --list-trace-roots

By default, read ~/.codex and ~/.claude. Add offline trace catalogs with
repeatable --data-root arguments, or replace an agent's roots with
--codex-root / --cc-root.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from collections.abc import Iterable
import os
import sys
from pathlib import Path


# Allow direct execution from a source checkout without installing.
_THIS_DIR = Path(__file__).resolve().parent
_PACKAGE_PARENT = _THIS_DIR.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))


from trace_tracker.adapters import build_registry  # noqa: E402  (sys.path tweak above)
from trace_tracker.core.adapter import AdapterRoot
from trace_tracker.core.server import run_server
from trace_tracker.core.sources import (
    TRACE_SOURCE_TRACES,
    TRACE_SOURCE_USER,
)


# An uncommon high-range port chosen to avoid collisions with the usual
# dev-server suspects (3000/5000/8000/8080/8888/9000/...).
_DEFAULT_PORT = 47318
_DISCOVERY_IGNORED_DIRS = {
    ".agents",
    ".git",
    ".venv",
    "__pycache__",
    "benchmarks",
    "cache",
    "harness",
    "harness-state",
    "logs",
    "mcp-tokens",
    "node_modules",
    "session-homes",
    "skills",
    "workspace",
}


@dataclass(frozen=True)
class TraceRoots:
    """Resolved local roots consumed by the adapters."""

    codex: tuple[AdapterRoot, ...]
    cc: tuple[AdapterRoot, ...]


@dataclass(frozen=True)
class TraceRootInfo:
    """A discovered offline trace root and the agents it can serve."""

    path: Path
    agents: tuple[str, ...]


@dataclass(frozen=True)
class _PartialTraceRoots:
    codex: tuple[Path, ...] = ()
    cc: tuple[Path, ...] = ()

    def agent_names(self) -> tuple[str, ...]:
        names: list[str] = []
        if self.codex:
            names.append("codex")
        if self.cc:
            names.append("cc")
        return tuple(names)


def _default_trace_roots() -> TraceRoots:
    return TraceRoots(
        codex=(AdapterRoot(path=Path.home() / ".codex", source=TRACE_SOURCE_USER),),
        cc=(AdapterRoot(path=Path.home() / ".claude", source=TRACE_SOURCE_USER),),
    )


def _expand_path(path: Path) -> Path:
    return Path(path).expanduser()


def _detect_trace_root(root: Path) -> _PartialTraceRoots:
    """Detect whether ``root`` is a mixed bundle or a single-agent root."""
    root = _expand_path(root)

    codex_roots: list[Path] = []
    cc_roots: list[Path] = []

    if (root / "codex" / "sessions").is_dir():
        codex_roots.append(root / "codex")
    if (root / "cc" / "projects").is_dir():
        cc_roots.append(root / "cc")
    if (root / "claude" / "projects").is_dir():
        cc_roots.append(root / "claude")
    if (root / "sessions").is_dir():
        codex_roots.append(root)
    if (root / "projects").is_dir():
        cc_roots.append(root)

    return _PartialTraceRoots(
        codex=tuple(dict.fromkeys(codex_roots)),
        cc=tuple(dict.fromkeys(cc_roots)),
    )


def _merge_partial_roots(parts: list[_PartialTraceRoots]) -> _PartialTraceRoots:
    codex: list[Path] = []
    cc: list[Path] = []
    for part in parts:
        codex.extend(part.codex)
        cc.extend(part.cc)
    return _PartialTraceRoots(
        codex=tuple(dict.fromkeys(codex)),
        cc=tuple(dict.fromkeys(cc)),
    )


def _discover_data_roots(data_roots: Iterable[Path]) -> _PartialTraceRoots:
    """Merge every recognizable agent root found under ``data_roots``."""
    parts = [
        _detect_trace_root(info.path)
        for data_root in data_roots
        for info in discover_trace_roots(data_root)
    ]
    return _merge_partial_roots(parts)


def _data_roots_from_args(args: argparse.Namespace) -> tuple[Path, ...]:
    """Explicit ``--data-root`` values; no offline catalogs by default."""
    candidates = args.data_roots or ()
    return tuple(dict.fromkeys(_expand_path(path) for path in candidates))


def resolve_trace_roots(args: argparse.Namespace) -> TraceRoots:
    """Aggregate user roots and explicit offline catalogs, unless overridden."""
    defaults = _default_trace_roots()
    discovered = _discover_data_roots(_data_roots_from_args(args))

    def _resolve(
        name: str,
        live: tuple[AdapterRoot, ...],
        offline: tuple[Path, ...],
    ) -> tuple[AdapterRoot, ...]:
        override = getattr(args, f"{name}_root")
        if override is not None:
            return (
                AdapterRoot(
                    path=_expand_path(override),
                    source=TRACE_SOURCE_USER,
                ),
            )
        offline_roots = tuple(
            AdapterRoot(path=path, source=TRACE_SOURCE_TRACES)
            for path in offline
        )
        return tuple(dict.fromkeys((*live, *offline_roots)))

    return TraceRoots(
        codex=_resolve("codex", defaults.codex, discovered.codex),
        cc=_resolve("cc", defaults.cc, discovered.cc),
    )


def discover_trace_roots(data_root: Path) -> list[TraceRootInfo]:
    """Find recognizable offline trace roots below ``data_root``."""
    root = _expand_path(data_root)
    if not root.is_dir():
        return []

    found: list[TraceRootInfo] = []
    for current, dirnames, _ in os.walk(root, topdown=True):
        path = Path(current)
        dirnames[:] = sorted(
            name for name in dirnames if name not in _DISCOVERY_IGNORED_DIRS
        )
        detected = _detect_trace_root(path)
        agents = detected.agent_names()
        if agents:
            found.append(TraceRootInfo(path=path, agents=agents))
            # A recognized root already describes all of its native agent
            # subdirectories. Do not descend into session data or run artifacts.
            dirnames.clear()
    return found


def _format_trace_roots(data_root: Path, roots: list[TraceRootInfo]) -> str:
    lines = [f"Trace roots under {data_root}:"]
    if not roots:
        lines.append("  (none found)")
        return "\n".join(lines)
    for item in roots:
        detected = _detect_trace_root(item.path)
        agent_roots = {
            "codex": detected.codex,
            "cc": detected.cc,
        }
        for agent in item.agents:
            for path in agent_roots.get(agent, (item.path,)):
                suffix = ""
                if path != item.path and item.path != _expand_path(data_root):
                    suffix = f"  (bundle: {item.path})"
                lines.append(f"  {path}  [{agent}]{suffix}")
    return "\n".join(lines)


def _format_all_trace_roots(data_roots: tuple[Path, ...]) -> str:
    sections = list(
        _format_trace_roots(data_root, discover_trace_roots(data_root))
        for data_root in data_roots
    )
    return "\n\n".join(sections)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=_DEFAULT_PORT)
    p.add_argument(
        "--default-agent",
        default="none",
        choices=["codex", "cc", "none"],
        help=(
            "agent that '/' jumps to. 'none' (default) shows a landing-page "
            "picker so you can choose at runtime."
        ),
    )
    p.add_argument("--limit", type=int, default=50, help="default # of sessions on list pages")
    p.add_argument(
        "--data-root",
        "--traces-root",
        dest="data_roots",
        type=Path,
        action="append",
        default=None,
        help=(
            "directory whose offline trace roots are loaded alongside the live "
            "user data; repeatable (default: no offline catalogs)"
        ),
    )
    p.add_argument(
        "--list-trace-roots",
        action="store_true",
        help="print trace roots discovered under --traces-root/--data-root and exit",
    )
    p.add_argument("--codex-root", type=Path, default=None)
    p.add_argument(
        "--cc-root", "--claude-root", dest="cc_root", type=Path, default=None
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_trace_roots:
        print(_format_all_trace_roots(_data_roots_from_args(args)))
        return 0

    roots = resolve_trace_roots(args)
    registry = build_registry(
        codex_root=roots.codex,
        cc_root=roots.cc,
    )
    default_agent = "" if args.default_agent == "none" else args.default_agent
    run_server(
        host=args.host,
        port=args.port,
        registry=registry,
        default_agent=default_agent,
        default_limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
