# PURPOSE: Tests session discovery — that metadata-only sidecar files are
# skipped, and that the (path, size, mtime) cache is used and invalidated.

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


def real_log(cwd: str) -> list[str]:
    return [
        '{"type":"user","cwd":"%s","timestamp":"2025-08-12T10:00:00.000Z",'
        '"message":{"role":"user","content":"hello"}}' % cwd,
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


class Base(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.root = Path(self._td.name)
        # Isolate the cache so tests never touch the real ~/.cache/quarry.
        self._cache = TemporaryDirectory()
        self._prev = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = self._cache.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self._prev
        self._td.cleanup()
        self._cache.cleanup()


class MetadataOnlyFiles(Base):
    def test_load_session_rejects_metadata_only_file(self):
        p = write_log(self.root, "-Users-x-Code-real", "aaaa", SUMMARY_ONLY)
        self.assertIsNone(cs.load_session(p))

    def test_discover_skips_metadata_only_files(self):
        write_log(self.root, "-Users-x-Code-real", "real0001", real_log("/Users/x/Code/real"))
        write_log(self.root, "-Users-x-Code-real", "summary1", SUMMARY_ONLY)
        write_log(self.root, "-Users-x-Code-portfolio", "title001", TITLE_ONLY)

        sessions = cs.discover(self.root)
        uuids = sorted(s.uuid for s in sessions)
        self.assertEqual(uuids, ["real0001"])
        self.assertEqual(sessions[0].cwd, "/Users/x/Code/real")


class Cache(Base):
    def test_second_discover_returns_same_result(self):
        write_log(self.root, "-Users-x-Code-real", "real0001", real_log("/Users/x/Code/real"))
        first = cs.discover(self.root)
        second = cs.discover(self.root)
        self.assertEqual([s.uuid for s in first], [s.uuid for s in second])
        self.assertEqual(first[0].cwd, second[0].cwd)

    def test_cache_is_used_when_size_and_mtime_unchanged(self):
        # Two logs of identical byte length but different cwd. After the first
        # discover caches variant A, swapping in B while preserving size+mtime
        # must yield the cached A — proving the parse was skipped.
        a = "/Users/x/Code/reala"
        b = "/Users/x/Code/realb"
        p = write_log(self.root, "-Users-x-Code-real", "real0001", real_log(a))
        st = p.stat()
        self.assertEqual([s.cwd for s in cs.discover(self.root)], [a])

        p.write_text("\n".join(real_log(b)) + "\n", encoding="utf-8")
        self.assertEqual(p.stat().st_size, st.st_size)    # same length
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore mtime
        self.assertEqual([s.cwd for s in cs.discover(self.root)], [a])  # cached

    def test_cache_is_invalidated_when_mtime_changes(self):
        a = "/Users/x/Code/reala"
        b = "/Users/x/Code/realb"
        p = write_log(self.root, "-Users-x-Code-real", "real0001", real_log(a))
        st = p.stat()
        self.assertEqual([s.cwd for s in cs.discover(self.root)], [a])

        p.write_text("\n".join(real_log(b)) + "\n", encoding="utf-8")
        bumped = st.st_mtime_ns + 1_000_000_000
        os.utime(p, ns=(st.st_atime_ns, bumped))
        self.assertEqual([s.cwd for s in cs.discover(self.root)], [b])  # reparsed


if __name__ == "__main__":
    unittest.main()
