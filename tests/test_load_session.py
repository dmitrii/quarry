# PURPOSE: Tests that session discovery skips metadata-only files (summary /
# ai-title sidecars) which carry no conversation and so have no cwd or date.

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import claude_sessions as cs  # noqa: E402


def write_log(root: Path, project: str, uuid: str, lines: list[str]) -> Path:
    d = root / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{uuid}.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


REAL = [
    '{"type":"user","cwd":"/Users/x/Code/real","timestamp":"2025-08-12T10:00:00.000Z",'
    '"message":{"role":"user","content":"hello"}}',
    '{"type":"assistant","timestamp":"2025-08-12T10:00:05.000Z",'
    '"message":{"role":"assistant","content":[{"type":"text","text":"hi"}]}}',
]

# A summary sidecar Claude writes: no cwd, no timestamp, references another
# session by leafUuid. Not a session of its own.
SUMMARY_ONLY = [
    '{"type":"summary","summary":"Refactor query script","leafUuid":"31b6bc99-15c0-4d23-b4a8-544268d86746"}',
]

# An ai-title / agent-name sidecar: also no cwd, no timestamp.
TITLE_ONLY = [
    '{"type":"ai-title","aiTitle":"Design portfolio page","sessionId":"1bcc230c-2d2b-4e94-ba1f-6dfcc51537ef"}',
    '{"type":"agent-name","agentName":"Design portfolio page","sessionId":"1bcc230c-2d2b-4e94-ba1f-6dfcc51537ef"}',
]


class MetadataOnlyFiles(unittest.TestCase):
    def test_load_session_rejects_metadata_only_file(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            p = write_log(root, "-Users-x-Code-real", "aaaa", SUMMARY_ONLY)
            self.assertIsNone(cs.load_session(p))

    def test_discover_skips_metadata_only_files(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            write_log(root, "-Users-x-Code-real", "real0001", REAL)
            write_log(root, "-Users-x-Code-real", "summary1", SUMMARY_ONLY)
            write_log(root, "-Users-x-Code-portfolio", "title001", TITLE_ONLY)

            sessions = cs.discover(root)
            uuids = sorted(s.uuid for s in sessions)
            self.assertEqual(uuids, ["real0001"])
            self.assertEqual(sessions[0].cwd, "/Users/x/Code/real")


if __name__ == "__main__":
    unittest.main()
