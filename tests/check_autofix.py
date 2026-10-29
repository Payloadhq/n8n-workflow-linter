#!/usr/bin/env python3
"""Regression tests: --fix must never destroy source data or ignore rule filters."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINT = ROOT / "lint.py"
FIXTURE = ROOT / "tests" / "fixtures" / "pinned.json"


def invoke(path, *args):
    return subprocess.run([sys.executable, str(LINT), *args, str(path)], capture_output=True, text=True)


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    original = FIXTURE.read_bytes()
    workflow = root / "workflow.json"
    workflow.write_bytes(original)
    output = root / "workflow.fixed.json"

    # No fixes are made if the selected rule does not include N8N009.
    excluded = invoke(workflow, "--fix", "--ignore", "N8N009")
    assert excluded.returncode == 2, excluded
    assert workflow.read_bytes() == original
    assert not output.exists()

    selected = invoke(workflow, "--fix", "--only", "N8N001")
    assert selected.returncode == 2, selected
    assert not output.exists()

    fixed = invoke(workflow, "--fix", "--only", "N8N009")
    assert fixed.returncode == 0, fixed
    assert workflow.read_bytes() == original, "Input was overwritten"
    assert output.exists(), "Expected separate fixed output"
    before = json.loads(original)
    after = json.loads(output.read_text(encoding="utf-8"))
    assert "pinData" not in after
    before.pop("pinData")
    assert before == after, "Unrelated data changed"

    # Never overwrite a previously produced fixed workflow.
    repeated = invoke(workflow, "--fix", "--only", "N8N009")
    assert repeated.returncode == 1
    assert workflow.read_bytes() == original

    # --fix on a clean workflow must not write anything.
    clean = root / "clean.json"
    clean.write_bytes((ROOT / "tests" / "fixtures" / "clean.json").read_bytes())
    assert invoke(clean, "--fix").returncode == 0
    assert not (root / "clean.fixed.json").exists()

    # Invalid JSON, wrong root type and missing nodes must not be rewritten.
    for name, payload in [("bad.json", "{bad"), ("root.json", "[]"), ("nodes.json", '{"other": true}')]:
        path = root / name
        path.write_text(payload)
        assert invoke(path, "--fix").returncode == 1
        assert path.read_text() == payload
        assert not path.with_name(path.stem + ".fixed.json").exists()

    # Machine-readable diagnostics must remain valid with --fix.
    other = root / "machine.json"
    other.write_bytes(original)
    machine = invoke(other, "--fix", "--only", "N8N009", "--json")
    assert machine.returncode == 0
    assert json.loads(machine.stdout)["exit_code"] == 0

print("safe autofix regression checks passed")
