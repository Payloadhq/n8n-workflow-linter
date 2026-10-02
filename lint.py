#!/usr/bin/env python3
"""
n8n-workflow-linter — statically lint an exported n8n workflow JSON for
production-readiness smells.

Rules:
  N8N001 WARN   non-trigger node without explicit onError handling
  N8N002 WARN   HTTP Request node without a timeout
  N8N003 WARN   HTTP Request node without retryOnFail
  N8N004 ERROR  hardcoded credential/secret value in node parameters
  N8N005 ERROR  workflow has no trigger node
  N8N006 WARN   workflow has multiple trigger nodes
  N8N007 WARN   disabled node left in the workflow
  N8N008 WARN   node with no connections at all

Exit codes: 0 = clean, 1 = errors found, 2 = warnings only.

Usage:
  python3 lint.py workflow.json
  python3 lint.py workflow.json --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SECRET_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|apikey|private[_-]?key|"
    r"access[_-]?token|auth[_-]?token|bearer|client[_-]?secret)"
)
HTTP_REQUEST_TYPE = "n8n-nodes-base.httpRequest"
NON_EXECUTABLE_TYPES = {"n8n-nodes-base.stickyNote"}


class Issue:
    def __init__(self, code: str, severity: str, node: str, message: str,
                 hint: str):
        self.code = code
        self.severity = severity  # "ERROR" or "WARN"
        self.node = node
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return (f"{self.severity:5} [{self.code}] node {self.node!r}: "
                f"{self.message}\n         fix: {self.hint}")


def is_trigger(node: dict) -> bool:
    ntype = str(node.get("type", ""))
    short = ntype.split(".")[-1].lower()
    return short in ("start", "webhook") or short.endswith("trigger")


AUTH_HEADER_NAMES = ("authorization", "x-api-key", "apikey", "api-key")


def _is_literal(value) -> bool:
    return isinstance(value, str) and bool(value) and not value.startswith("=")


def _iter_param_strings(obj, path=""):
    """Yield (path, key, value, pre_vetted) for string values inside parameters.

    pre_vetted is True for n8n {"name": ..., "value": ...} pairs whose name
    already looks like a credential carrier, so callers need not re-check.
    """
    if isinstance(obj, dict):
        # n8n header/param style pairs: {"name": "X-Api-Key", "value": "..."}
        name = obj.get("name")
        value = obj.get("value")
        if _is_literal(name) and _is_literal(value):
            if SECRET_KEY_PATTERN.search(name) or name.lower() in AUTH_HEADER_NAMES:
                yield (f"{path}.value" if path else "value"), name, value, True
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if isinstance(v, str):
                yield p, k, v, False
            else:
                yield from _iter_param_strings(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_param_strings(v, f"{path}[{i}]")


def find_hardcoded_secrets(parameters) -> list[tuple[str, str]]:
    hits = []
    for path, key, value, pre_vetted in _iter_param_strings(parameters):
        if not _is_literal(value):
            continue  # empty or n8n expression, not a literal secret
        if pre_vetted or SECRET_KEY_PATTERN.search(key):
            hits.append((path, value))
    return hits


def lint_workflow(data) -> list[Issue]:
    issues: list[Issue] = []

    if not isinstance(data, dict):
        return [Issue("N8N005", "ERROR", "(workflow)",
                       "workflow root must be a JSON object",
                       "export the workflow again from n8n")]

    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return [Issue("N8N005", "ERROR", "(workflow)",
                       'missing "nodes" array',
                       "export the workflow again from n8n")]
    connections = data.get("connections") or {}

    # ---- connection graph ------------------------------------------------
    sources: set[str] = set()
    targets: set[str] = set()
    if isinstance(connections, dict):
        for src, outputs in connections.items():
            sources.add(src)
            if isinstance(outputs, dict):
                for branches in outputs.values():
                    if isinstance(branches, list):
                        for branch in branches:
                            if isinstance(branch, list):
                                for link in branch:
                                    if isinstance(link, dict) and link.get("node"):
                                        targets.add(link["node"])

    triggers = [n for n in nodes
                if isinstance(n, dict) and is_trigger(n)
                and n.get("type") not in NON_EXECUTABLE_TYPES]

    # ---- workflow-level checks -------------------------------------------
    if not triggers:
        issues.append(Issue(
            "N8N005", "ERROR", "(workflow)",
            "no trigger node: an active production workflow needs a trigger",
            "add a Schedule, Webhook, or other trigger node as the entry point"))
    elif len(triggers) > 1:
        names = ", ".join(n.get("name", "?") for n in triggers)
        issues.append(Issue(
            "N8N006", "WARN", "(workflow)",
            f"multiple trigger nodes ({names})",
            "keep one trigger per workflow unless fan-in is intentional; "
            "document why"))

    # ---- node-level checks ------------------------------------------------
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = node.get("name", "?")
        ntype = str(node.get("type", ""))
        if ntype in NON_EXECUTABLE_TYPES:
            continue
        trigger = is_trigger(node)

        if node.get("disabled") is True:
            issues.append(Issue(
                "N8N007", "WARN", name,
                "node is disabled",
                "remove it or re-enable it before relying on this workflow "
                "in production"))

        if name not in sources and name not in targets:
            issues.append(Issue(
                "N8N008", "WARN", name,
                "node has no connections",
                "connect it into the flow or delete it if it is leftover"))

        if not trigger and not node.get("onError"):
            issues.append(Issue(
                "N8N001", "WARN", name,
                "no explicit onError handling (defaults to stopWorkflow)",
                'set onError explicitly, e.g. "continueErrorOutput" with an '
                "error branch, or confirm stop-on-error is intended"))

        if ntype == HTTP_REQUEST_TYPE:
            params = node.get("parameters") or {}
            options = params.get("options") or {}
            if not isinstance(options, dict) or not options.get("timeout"):
                issues.append(Issue(
                    "N8N002", "WARN", name,
                    "HTTP Request has no timeout configured",
                    "set options.timeout (ms), e.g. 10000, so a hung "
                    "endpoint cannot stall the workflow"))
            if node.get("retryOnFail") is not True:
                issues.append(Issue(
                    "N8N003", "WARN", name,
                    "HTTP Request has retryOnFail disabled",
                    "enable retryOnFail with maxTries for transient failures"))

        for path, value in find_hardcoded_secrets(node.get("parameters")):
            masked = value[:4] + "..." if len(value) > 4 else "****"
            issues.append(Issue(
                "N8N004", "ERROR", name,
                f"hardcoded secret in parameters.{path} ({masked})",
                "move the secret into n8n Credentials and reference it; "
                "never commit literal secrets in workflow JSON"))

    return issues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Lint an n8n workflow JSON for production-readiness smells.")
    ap.add_argument("workflow", help="path to exported workflow JSON")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of text")
    args = ap.parse_args(argv)

    try:
        data = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"ERROR: file not found: {args.workflow}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"ERROR [N8N005] (workflow): invalid JSON: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: cannot read {args.workflow}: {exc}", file=sys.stderr)
        return 1

    issues = lint_workflow(data)
    errors = [i for i in issues if i.severity == "ERROR"]
    warns = [i for i in issues if i.severity == "WARN"]

    if args.json:
        print(json.dumps({
            "file": args.workflow,
            "errors": [vars(i) for i in errors],
            "warnings": [vars(i) for i in warns],
            "exit_code": 1 if errors else (2 if warns else 0),
        }, indent=2))
    else:
        if not issues:
            print("clean: no production-readiness smells found")
        for issue in issues:
            print(issue)
        print(f"{len(errors)} error(s), {len(warns)} warning(s)")

    if errors:
        return 1
    if warns:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
