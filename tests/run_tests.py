#!/usr/bin/env python3
"""Test runner for n8n-workflow-linter.

Runs lint.py against every fixture and asserts the expected exit code
and expected rule codes. Exit 0 only if all assertions pass.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINT = ROOT / "lint.py"
FIX = ROOT / "tests" / "fixtures"

# (fixture, expected_exit, expected_codes_that_must_appear)
CASES = [
    ("clean.json", 0, []),
    ("no_error_handling.json", 2, ["N8N001"]),
    ("http_no_timeout_retry.json", 2, ["N8N002", "N8N003"]),
    ("hardcoded_secret.json", 1, ["N8N004"]),
    ("no_trigger.json", 1, ["N8N005"]),
    ("multi_trigger.json", 2, ["N8N006"]),
    ("disabled_nodes.json", 2, ["N8N007"]),
    ("unconnected.json", 2, ["N8N008"]),
]

# codes that must NOT appear in the clean fixture
CLEAN_FORBIDDEN = ["N8N001", "N8N002", "N8N003", "N8N004",
                   "N8N005", "N8N006", "N8N007", "N8N008"]


def main() -> int:
    failures = 0
    for fixture, expected_exit, expected_codes in CASES:
        cmd = [sys.executable, str(LINT), str(FIX / fixture)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        output = proc.stdout + proc.stderr
        problems = []
        if proc.returncode != expected_exit:
            problems.append(f"exit={proc.returncode}, expected {expected_exit}")
        for want in expected_codes:
            if want not in output:
                problems.append(f"missing rule code {want}")
        if fixture == "clean.json":
            for bad in CLEAN_FORBIDDEN:
                if bad in output:
                    problems.append(f"clean fixture unexpectedly raised {bad}")
        if problems:
            failures += 1
            print(f"FAIL {fixture}: {'; '.join(problems)}")
            print("---- output ----")
            print(output.strip())
            print("----------------")
        else:
            print(f"ok   {fixture} (exit {proc.returncode})")
    print(f"\n{len(CASES) - failures}/{len(CASES)} fixtures passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
