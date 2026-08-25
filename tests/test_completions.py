# PURPOSE: Tests that the emitted shell completions offer the top-level
# --help/--version flags, so `quarry --<TAB>` suggests them. bash is driven for
# real; fish and zsh are checked at the text level (fish may not be installed,
# and zsh's compsys needs an interactive shell to exercise).

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
QUARRY = ROOT / "bin" / "quarry"

# How each shell's completion syntax spells the long flags.
SPELLINGS = {
    "fish": ("-l help", "-l version"),
    "zsh": ("--help", "--version"),
    "bash": ("--help", "--version"),
}

# Source the emitted script, then ask the completion function what the given
# partial word offers, exactly as bash would on <TAB>.
DRIVER = """
source "%(script)s"
COMP_WORDS=(quarry %(word)s)
COMP_CWORD=1
_quarry
printf '%%s\\n' "${COMPREPLY[@]}"
"""


def emit(shell: str) -> str:
    out = subprocess.run(
        [sys.executable, str(QUARRY), "completions", shell],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def complete_bash(word: str) -> list[str]:
    """What bash would suggest for `quarry <word><TAB>`."""
    with TemporaryDirectory() as tmp:
        script = Path(tmp) / "quarry.bash"
        script.write_text(emit("bash"), encoding="utf-8")
        out = subprocess.run(
            ["bash", "-c", DRIVER % {"script": script, "word": word}],
            capture_output=True, text=True, check=True,
        )
    assert not out.stderr.strip(), f"unexpected stderr: {out.stderr}"
    return out.stdout.split()


class CompletionsTest(unittest.TestCase):
    def test_bash_offers_the_flags_for_a_double_dash(self):
        self.assertEqual(sorted(complete_bash("--")), ["--help", "--version"])

    def test_bash_still_offers_subcommands_for_a_bare_word(self):
        """Adding flags must not displace the subcommands."""
        self.assertEqual(sorted(complete_bash("r")), ["rename", "rm"])

    def test_each_shell_script_mentions_the_flags(self):
        for shell, spellings in SPELLINGS.items():
            script = emit(shell)
            for spelling in spellings:
                with self.subTest(shell=shell, spelling=spelling):
                    self.assertIn(spelling, script)


if __name__ == "__main__":
    unittest.main()
