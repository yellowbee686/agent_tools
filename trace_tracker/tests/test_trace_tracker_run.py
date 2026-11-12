from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from trace_tracker import run
from trace_tracker.adapters import build_registry
from trace_tracker.core import render
from trace_tracker.core.adapter import AdapterRoot


class TraceTrackerRunTests(unittest.TestCase):
    def test_defaults_use_only_user_roots(self) -> None:
        args = run.parse_args([])
        roots = run.resolve_trace_roots(args)
        self.assertEqual(run._data_roots_from_args(args), ())
        self.assertEqual(self.root_paths(roots.codex), (Path.home() / ".codex",))
        self.assertEqual(self.root_paths(roots.cc), (Path.home() / ".claude",))
        registry = build_registry(codex_root=roots.codex, cc_root=roots.cc)
        self.assertEqual(registry.names(), ["codex", "cc"])

    def test_list_trace_roots_prints_explicit_catalogs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "snapshots"
            native_root = catalog / "example" / "codex"
            self.write_codex_session(native_root, "rollout-example")
            out = io.StringIO()
            with redirect_stdout(out):
                status = run.main(["--data-root", str(catalog), "--list-trace-roots"])
            self.assertEqual(status, 0)
            self.assertIn(str(native_root), out.getvalue())
            self.assertIn("[codex]", out.getvalue())

    def root_paths(self, roots: tuple[run.AdapterRoot, ...]) -> tuple[Path, ...]:
        return tuple(root.path for root in roots)

    def root_sources(self, roots: tuple[run.AdapterRoot, ...]) -> tuple[str, ...]:
        return tuple(root.source for root in roots)

    def test_empty_data_root_yields_only_user_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = run.parse_args(["--data-root", str(Path(tmp) / "empty")])

            roots = run.resolve_trace_roots(args)

            self.assertEqual(self.root_paths(roots.codex), (Path.home() / ".codex",))
            self.assertEqual(self.root_paths(roots.cc), (Path.home() / ".claude",))
            self.assertEqual(self.root_sources(roots.codex), ("user",))

    def test_repeated_data_roots_merge_multiple_offline_catalogs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            offline_root = Path(tmp) / "offline"
            repo_root = Path(tmp) / "repo-traces"
            offline_codex = offline_root / "codex"
            repo_codex = repo_root / "codex"
            (offline_codex / "sessions").mkdir(parents=True)
            (repo_codex / "sessions").mkdir(parents=True)
            args = run.parse_args(["--data-root", str(offline_root), "--data-root", str(repo_root)])

            roots = run.resolve_trace_roots(args)

            self.assertEqual(
                self.root_paths(roots.codex),
                (Path.home() / ".codex", offline_codex, repo_codex),
            )
            self.assertEqual(
                self.root_sources(roots.codex), ("user", "traces", "traces")
            )

    def test_explicit_data_root_selects_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            explicit_root = Path(tmp) / "explicit"
            (explicit_root / "sessions").mkdir(parents=True)
            args = run.parse_args(["--data-root", str(explicit_root)])

            self.assertEqual(run._data_roots_from_args(args), (explicit_root,))

    def test_default_merges_user_home_and_data_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            codex_snapshot = data_root / "codex-traces" / "example"
            (codex_snapshot / "sessions").mkdir(parents=True)
            args = run.parse_args(["--data-root", str(data_root)])

            roots = run.resolve_trace_roots(args)

            self.assertEqual(self.root_paths(roots.codex), (Path.home() / ".codex", codex_snapshot))
            self.assertEqual(self.root_sources(roots.codex), ("user", "traces"))
            self.assertEqual(self.root_paths(roots.cc), (Path.home() / ".claude",))

    def test_default_merges_bundle_subdirectories_under_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            bundle = data_root / "mixed-snapshot"
            (bundle / "codex" / "sessions").mkdir(parents=True)
            (bundle / "cc" / "projects").mkdir(parents=True)
            args = run.parse_args(["--data-root", str(data_root)])

            roots = run.resolve_trace_roots(args)

            self.assertEqual(
                self.root_paths(roots.codex), (Path.home() / ".codex", bundle / "codex")
            )
            self.assertEqual(self.root_paths(roots.cc), (Path.home() / ".claude", bundle / "cc"))

    def test_default_merges_agent_directories_under_traces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            traces_root = Path(tmp) / "traces"
            (traces_root / "codex" / "sessions").mkdir(parents=True)
            (traces_root / "claude" / "projects").mkdir(parents=True)
            args = run.parse_args(["--traces-root", str(traces_root)])

            roots = run.resolve_trace_roots(args)

            self.assertEqual(
                self.root_paths(roots.codex),
                (Path.home() / ".codex", traces_root / "codex"),
            )
            self.assertEqual(
                self.root_paths(roots.cc),
                (Path.home() / ".claude", traces_root / "claude"),
            )

    def test_explicit_agent_root_replaces_merged_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            snapshot = data_root / "codex-traces" / "example"
            (snapshot / "sessions").mkdir(parents=True)
            explicit_codex_root = Path(tmp) / "custom-codex"
            (explicit_codex_root / "sessions").mkdir(parents=True)
            args = run.parse_args(
                [
                    "--data-root",
                    str(data_root),
                    "--codex-root",
                    str(explicit_codex_root),
                ]
            )

            roots = run.resolve_trace_roots(args)

            self.assertEqual(self.root_paths(roots.codex), (explicit_codex_root,))
            self.assertEqual(self.root_sources(roots.codex), ("user",))

    def test_registry_aggregates_multiple_codex_data_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            first = data_root / "codex-traces" / "first"
            second = data_root / "codex-traces" / "second"
            self.write_codex_session(first, "rollout-2026-06-04T10-00-00-first")
            self.write_codex_session(second, "rollout-2026-06-04T11-00-00-second")

            registry = build_registry(
                codex_root=(first, second),
                cc_root=(Path.home() / ".claude",),
            )
            sessions = registry["codex"].list_sessions(10)

            self.assertEqual(
                {s.session_id for s in sessions},
                {
                    "rollout-2026-06-04T10-00-00-first",
                    "rollout-2026-06-04T11-00-00-second",
                },
            )

    def test_registry_filters_sessions_by_root_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_root = Path(tmp) / "user-codex"
            traces_root = Path(tmp) / "traces" / "codex"
            self.write_codex_session(user_root, "rollout-2026-06-04T10-00-00-user")
            self.write_codex_session(
                traces_root, "rollout-2026-06-04T11-00-00-traces"
            )

            registry = build_registry(
                codex_root=(
                    AdapterRoot(path=user_root, source="user"),
                    AdapterRoot(path=traces_root, source="traces"),
                ),
                cc_root=(Path.home() / ".claude",),
            )

            user_sessions = registry["codex"].list_sessions_by_source(10, "user")
            traces_sessions = registry["codex"].list_sessions_by_source(10, "traces")

            self.assertEqual(
                [session.session_id for session in user_sessions],
                ["rollout-2026-06-04T10-00-00-user"],
            )
            self.assertEqual(
                [session.session_id for session in traces_sessions],
                ["rollout-2026-06-04T11-00-00-traces"],
            )
            self.assertEqual(user_sessions[0].extras["trace_source"], "user")
            self.assertEqual(traces_sessions[0].extras["trace_source"], "traces")

    def test_registry_filters_sessions_by_runtime_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user_root = Path(tmp) / "user-codex"
            runtime_root = Path(tmp) / "runtime" / "traces" / "codex"
            self.write_codex_session(user_root, "rollout-2026-06-04T10-00-00-user")
            self.write_codex_session(
                runtime_root, "rollout-2026-06-04T12-00-00-runtime"
            )

            registry = build_registry(
                codex_root=(
                    AdapterRoot(path=user_root, source="user"),
                    AdapterRoot(path=runtime_root, source="runtime"),
                ),
                cc_root=(Path.home() / ".claude",),
            )

            runtime_sessions = registry["codex"].list_sessions_by_source(
                10, "runtime"
            )

            self.assertEqual(
                [session.session_id for session in runtime_sessions],
                ["rollout-2026-06-04T12-00-00-runtime"],
            )
            self.assertEqual(runtime_sessions[0].extras["trace_source"], "runtime")

    def test_render_runtime_source_detail_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "runtime-codex"
            self.write_codex_session(
                runtime_root, "rollout-2026-06-04T12-00-00-runtime"
            )
            registry = build_registry(
                codex_root=(AdapterRoot(path=runtime_root, source="runtime"),),
                cc_root=(Path.home() / ".claude",),
            )
            adapter = registry["codex"]
            sessions = adapter.list_sessions_by_source(10, "runtime")

            html = render.render_list_page(
                registry=registry,
                adapter=adapter,
                sessions=sessions,
                limit=10,
                selected_source="runtime",
            )

            self.assertIn("runtime traces", html)
            self.assertIn(
                "/codex/session/rollout-2026-06-04T12-00-00-runtime?source=runtime",
                html,
            )

    def test_discover_trace_roots_finds_grouped_direct_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            codex_snapshot = data_root / "codex-traces" / "example"
            (codex_snapshot / "sessions").mkdir(parents=True)

            found = run.discover_trace_roots(data_root)

            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].path, codex_snapshot)
            self.assertEqual(found[0].agents, ("codex",))

    def test_discover_trace_roots_skips_project_dependency_trees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "catalog"
            real_root = data_root / "project" / "runtime" / "traces"
            (real_root / "codex" / "sessions").mkdir(parents=True)
            ignored_root = data_root / "project" / ".venv" / "fixture"
            (ignored_root / "codex" / "sessions").mkdir(parents=True)

            found = run.discover_trace_roots(data_root)

            self.assertEqual([item.path for item in found], [real_root])

    def write_codex_session(self, root: Path, session_id: str) -> None:
        session_dir = root / "sessions" / "2026" / "06" / "04"
        session_dir.mkdir(parents=True)
        (session_dir / f"{session_id}.jsonl").write_text(
            "\n".join(
                [
                    '{"type":"session_meta","timestamp":"2026-06-04T10:00:00Z","payload":{"timestamp":"2026-06-04T10:00:00Z","cwd":"/tmp"}}',
                    '{"type":"response_item","timestamp":"2026-06-04T10:00:01Z","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"hello"}]}}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
