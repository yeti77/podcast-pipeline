#!/usr/bin/env python3
"""Run the repository's hermetic regression suite without production calls."""

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence, Tuple


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def run_command(label: str, command: Sequence[str], env: dict) -> Tuple[bool, str]:
    completed = subprocess.run(
        list(command),
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return completed.returncode == 0, completed.stdout


def main() -> int:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    checks: List[Tuple[str, Sequence[str]]] = []

    for path in sorted(SCRIPTS.glob("test_*.py")):
        checks.append((str(path.relative_to(ROOT)), [sys.executable, str(path)]))
    checks.append((
        "scripts/test_podcast_screener_cron_lock.sh",
        ["bash", str(SCRIPTS / "test_podcast_screener_cron_lock.sh")],
    ))
    for path in sorted(SCRIPTS.glob("*.py")):
        checks.append((f"py_compile {path.name}", [sys.executable, "-m", "py_compile", str(path)]))
    for path in sorted(SCRIPTS.glob("*.sh")):
        checks.append((f"bash -n {path.name}", ["bash", "-n", str(path)]))
    checks.append(("git diff --check", ["git", "diff", "--check"]))
    checks.append(("git diff --cached --check", ["git", "diff", "--cached", "--check"]))

    failures = []
    for label, command in checks:
        ok, output = run_command(label, command, env)
        print(f"{'PASS' if ok else 'FAIL'} {label}")
        if not ok:
            failures.append((label, output))

    print(f"SUMMARY checks={len(checks)} passed={len(checks) - len(failures)} failed={len(failures)}")
    for label, output in failures:
        print(f"\n--- {label} ---\n{output[-12000:]}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
