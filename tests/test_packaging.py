"""The installed package runs on its own: no sys.path changes, and everything it reads at
runtime (skills, the sandbox runner, the field pipeline) is inside scoop/ and shipped."""
import fnmatch
import re
import sys
import tomllib
import unittest
from pathlib import Path

import scoop

PACKAGE = Path(scoop.__file__).resolve().parent
REPO = PACKAGE.parent


def shipped_patterns() -> list[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    return data["tool"]["setuptools"]["package-data"]["scoop"]


def shipped(path: Path) -> bool:
    relative = path.relative_to(PACKAGE).as_posix()
    # setuptools globs: ** spans directories
    return any(fnmatch.fnmatch(relative, pattern.replace("**/", "*")) or fnmatch.fnmatch(relative, pattern)
               for pattern in shipped_patterns())


class PackagingTests(unittest.TestCase):
    def test_no_module_changes_sys_path(self):
        offenders = [str(p.relative_to(REPO)) for p in PACKAGE.rglob("*.py")
                     if re.search(r"sys\.path\.(insert|append)", p.read_text())]
        self.assertEqual(offenders, [])

    def test_skills_and_the_sandbox_runner_are_shipped(self):
        from scoop.agents.data_agent import SKILLS_DIR
        from scoop.runtime.sandbox import SANDBOX_DIR

        skills = sorted(SKILLS_DIR.rglob("*.md"))
        self.assertTrue(skills and SKILLS_DIR.is_relative_to(PACKAGE))
        runner = [SANDBOX_DIR / name for name in ("runner.mjs", "package.json", "package-lock.json")]
        self.assertTrue(SANDBOX_DIR.is_relative_to(PACKAGE))
        self.assertEqual([p for p in [*skills, *runner] if not shipped(p)], [])

    def test_the_server_runs_the_pipeline_as_a_module(self):
        from scoop.server.jobs import PIPELINE, JobManager

        self.assertEqual(JobManager(Path("fields"))._command(), [sys.executable, "-m", PIPELINE])
        self.assertTrue((PACKAGE / Path(*PIPELINE.split(".")[1:])).with_suffix(".py").exists())


if __name__ == "__main__":
    unittest.main()
