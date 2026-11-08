import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "codex-session-extract"
    / "scripts"
    / "extract_codex_session.py"
)
SPEC = importlib.util.spec_from_file_location("extract_codex_session", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def message(role, text, ordinal=0):
    return {
        "timestamp": f"2026-01-01T00:00:{ordinal:02d}.000Z",
        "ordinal": ordinal,
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
        },
    }


def tool_call(name, cwd, ordinal=0):
    return {
        "timestamp": f"2026-01-01T00:00:{ordinal:02d}.000Z",
        "ordinal": ordinal,
        "type": "response_item",
        "payload": {"type": "custom_tool_call", "name": name, "cwd": cwd},
    }


class ExtractCodexSessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / ".codex"
        self.project = Path(self.temp.name) / "project"
        self.other_project = Path(self.temp.name) / "other"
        self.project.mkdir()
        self.other_project.mkdir()
        self.write_index(
            [
                {"id": "sess-good", "thread_name": "good thread", "updated_at": "2026-01-02T00:00:00Z"},
                {"id": "sess-other", "thread_name": "other thread", "updated_at": "2026-01-03T00:00:00Z"},
            ]
        )
        self.write_rollout(
            "sess-good",
            [
                {
                    "timestamp": "2026-01-01T00:00:00.000Z",
                    "ordinal": 0,
                    "type": "session_meta",
                    "payload": {
                        "session_id": "sess-good",
                        "cwd": str(self.project),
                        "cli_version": "0.166.0",
                    },
                },
                message("user", "fix session loading", ordinal=1),
                message("user", "fix session loading", ordinal=2),
                tool_call("exec", str(self.project), ordinal=3),
                message("assistant", "implemented the fix", ordinal=4),
                tool_call("exec", str(self.other_project), ordinal=5),
                message("user", "unrelated work", ordinal=6),
            ],
        )
        self.write_rollout(
            "sess-other",
            [
                {
                    "timestamp": "2026-01-01T00:00:00.000Z",
                    "ordinal": 0,
                    "type": "session_meta",
                    "payload": {"session_id": "sess-other", "cwd": str(self.other_project)},
                },
                message("user", "other work", ordinal=1),
            ],
        )
        self.write_rollout("sess-empty", [])

    def tearDown(self):
        self.temp.cleanup()

    def write_index(self, rows):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "session_index.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    def write_rollout(self, session_id, records):
        path = self.root / "sessions" / "2026" / "01" / "02" / f"rollout-2026-01-02T00-00-00-{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    def run_cli(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = MODULE.main(["--root", str(self.root), *args])
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_list_filters_by_project_and_reports_status(self):
        exit_code, stdout, stderr = self.run_cli(
            "--project", str(self.project), "--list", "--format", "json"
        )
        self.assertEqual(0, exit_code, stderr)
        sessions = json.loads(stdout)
        self.assertEqual(["sess-good"], [session["id"] for session in sessions])
        self.assertEqual("nonempty", sessions[0]["transcript_status"])
        self.assertEqual("fix session loading", sessions[0]["title"])
        self.assertTrue(sessions[0]["mapped_to_project"])

    def test_extract_deduplicates_prompts_and_keeps_project_tools(self):
        exit_code, stdout, stderr = self.run_cli(
            "--project", str(self.project), "--session", "sess-good", "--format", "json"
        )
        self.assertEqual(0, exit_code, stderr)
        payload = json.loads(stdout)
        self.assertEqual(["fix session loading"], payload["prompts"])
        tool_part = next(part for part in payload["parts"] if "tool_calls" in part)
        self.assertEqual("exec", tool_part["tool_calls"]["name"])
        self.assertEqual("implemented the fix", payload["parts"][-1]["text"])
        self.assertNotIn("unrelated work", json.dumps(payload, ensure_ascii=False))

    def test_session_prefix_matching(self):
        exit_code, stdout, stderr = self.run_cli(
            "--project", str(self.project), "--session", "sess-g", "--format", "json"
        )
        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("sess-good", json.loads(stdout)["session"]["id"])

    def test_empty_rollout_fails_without_database_fallback(self):
        exit_code, stdout, stderr = self.run_cli("--session", "sess-empty", "--format", "json")
        self.assertEqual(2, exit_code)
        self.assertEqual("", stdout)
        self.assertIn("rollout is empty", stderr)
        self.assertIn("database recovery is unsupported", stderr)

    def test_keyword_tail_and_atomic_output_use_public_cli(self):
        output = Path(self.temp.name) / "result.json"
        exit_code, stdout, stderr = self.run_cli(
            "--project",
            str(self.project),
            "--latest",
            "--keyword",
            "implemented",
            "--tail",
            "1",
            "--format",
            "json",
            "--output",
            str(output),
        )
        self.assertEqual(0, exit_code, stderr)
        self.assertEqual("", stdout)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(["implemented the fix"], [part["text"] for part in payload["parts"]])


    def test_fork_dedupe_prefers_latest_and_skips_boilerplate_title(self):
        fork = self.root / "sessions" / "2026" / "01" / "03" / "rollout-2026-01-03T00-00-00-sess-good.jsonl"
        fork.parent.mkdir(parents=True, exist_ok=True)
        fork.write_text(
            json.dumps(
                {
                    "timestamp": "2026-01-03T00:00:00.000Z",
                    "ordinal": 0,
                    "type": "session_meta",
                    "payload": {"session_id": "sess-good", "cwd": str(self.project)},
                }
            )
            + "\n"
            + json.dumps(message("user", "# AGENTS.md instructions\n# Global policy", ordinal=1))
            + "\n"
            + json.dumps(message("user", "fix session loading", ordinal=2))
            + "\n",
            encoding="utf-8",
        )
        exit_code, stdout, stderr = self.run_cli(
            "--project", str(self.project), "--list", "--format", "json"
        )
        self.assertEqual(0, exit_code, stderr)
        sessions = json.loads(stdout)
        self.assertEqual(["sess-good"], [session["id"] for session in sessions])
        self.assertEqual("fix session loading", sessions[0]["title"])


if __name__ == "__main__":
    unittest.main()
