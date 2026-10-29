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
  N8N009 WARN   pinned data in workflow

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
from enum import Enum, auto
from pathlib import Path

SECRET_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|apikey|private[_-]?key|"
    r"access[_-]?token|auth[_-]?token|bearer|client[_-]?secret)"
)
HTTP_REQUEST_TYPE = "n8n-nodes-base.httpRequest"
NON_EXECUTABLE_TYPES = {"n8n-nodes-base.stickyNote"}


class IssueSeverity(Enum):
    WARN = auto()
    ERROR = auto()

class IssueCode(Enum):
    N8N001 = (IssueSeverity.WARN,  'non-trigger node without explicit onError handling')
    N8N002 = (IssueSeverity.WARN,  'HTTP Request node without a timeout')
    N8N003 = (IssueSeverity.WARN,  'HTTP Request node without retryOnFail')
    N8N004 = (IssueSeverity.ERROR, 'hardcoded credential/secret value in node parameters')
    N8N005 = (IssueSeverity.ERROR, 'workflow has no trigger node')
    N8N006 = (IssueSeverity.WARN,  'workflow has multiple trigger nodes')
    N8N007 = (IssueSeverity.WARN,  'disabled node left in the workflow')
    N8N008 = (IssueSeverity.WARN,  'node with no connections at all')
    N8N009 = (IssueSeverity.WARN,  'has pinned data')


class Issue:
    def __init__(self, code: IssueCode, node: str|None = None, message: str|None = None, hint: str|None = None, resolved: bool|None = None):
        self.code = code
        self.severity = code.value[0]
        self.node = node if node else '(workflow)'
        self.message = message if message else code.value[1]
        self.hint = hint
        self.resolved = resolved

    def __str__(self) -> str:
        return (f"{self.severity.name:5} [{self.code.name}] node {self.node!r}: "
                f"{self.message}\n         fix: {self.__fix_string()}")

    def to_dict(self) -> dict[str,str]:
        return { attr: (value if not isinstance(value, Enum) else value.name) for attr, value in self.__dict__.items() }

    def __fix_string(self):
        if self.resolved is None:
            return self.hint
        return 'done' if self.resolved else 'possible'


def is_trigger(node: dict) -> bool:
    ntype = str(node.get("type", ""))
    short = ntype.split(".")[-1].lower()
    return short in ("start", "webhook") or short.endswith("trigger")


AUTH_HEADER_NAMES = ("authorization", "x-api-key", "apikey", "api-key")

def _exit_code(errors: list[Issue], warnings: list[Issue]) -> int:
    if [ error for error in errors if error.resolved != True]:
        return 1
    if [ warning for warning in warnings if warning.resolved != True]:
        return 2
    return 0

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

def invalid_filters(allowlist: list[str], denylist: list[str]) -> list[str]:
    invalid = []
    for issue in (allowlist + denylist):
        if not issue in IssueCode.__members__:
            invalid.append(issue)

    return invalid

def filter_issues(issues: list[Issue], allowlist: list[str], denylist: list[str]) -> list[Issue]:
    if not allowlist and not denylist:
        return issues
    elif allowlist:
        return [ issue for issue in issues if issue.code.name in allowlist ]
    elif denylist:
        return [ issue for issue in issues if issue.code.name not in denylist ]
    else:
        return issues

def lint_workflow(data, fix: bool) -> tuple[dict,list[Issue]]:
    issues: list[Issue] = []

    if not isinstance(data, dict):
        return {},[Issue(IssueCode.N8N005,
                       message="workflow root must be a JSON object",
                       hint="export the workflow again from n8n")]

    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return {},[Issue(IssueCode.N8N005,
                       message='missing "nodes" array',
                       hint="export the workflow again from n8n")]
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
    pinned = data.get("pinData")
    if isinstance(pinned, dict) and pinned:
        resolved = bool(fix)
        for name in pinned:
            issues.append(Issue(IssueCode.N8N009, name,
                            hint="clear pinned data and export the workflow again from n8n",
                            resolved=resolved))
        if fix:
            data = {key: value for key, value in data.items() if key != "pinData"}
    if not triggers:
        issues.append(Issue(
            IssueCode.N8N005,
            message="no trigger node: an active production workflow needs a trigger",
            hint="add a Schedule, Webhook, or other trigger node as the entry point"))
    elif len(triggers) > 1:
        names = ", ".join(n.get("name", "?") for n in triggers)
        issues.append(Issue(IssueCode.N8N006,
            message=f"multiple trigger nodes ({names})",
            hint="keep one trigger per workflow unless fan-in is intentional; "
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
                IssueCode.N8N007, node=name,
                message="node is disabled",
                hint="remove it or re-enable it before relying on this workflow "
                "in production"))

        if name not in sources and name not in targets:
            issues.append(Issue(
                IssueCode.N8N008, node=name,
                message="node has no connections",
                hint="connect it into the flow or delete it if it is leftover"))

        if not trigger and not node.get("onError"):
            issues.append(Issue(
                IssueCode.N8N001, node=name,
                message="no explicit onError handling (defaults to stopWorkflow)",
                hint='set onError explicitly, e.g. "continueErrorOutput" with an '
                "error branch, or confirm stop-on-error is intended"))

        if ntype == HTTP_REQUEST_TYPE:
            params = node.get("parameters") or {}
            options = params.get("options") or {}
            if not isinstance(options, dict) or not options.get("timeout"):
                issues.append(Issue(
                    IssueCode.N8N002, node=name,
                    message="HTTP Request has no timeout configured",
                    hint="set options.timeout (ms), e.g. 10000, so a hung "
                    "endpoint cannot stall the workflow"))
            if node.get("retryOnFail") is not True:
                issues.append(Issue(
                    IssueCode.N8N003, node=name,
                    message="HTTP Request has retryOnFail disabled",
                    hint="enable retryOnFail with maxTries for transient failures"))

        for path, value in find_hardcoded_secrets(node.get("parameters")):
            masked = value[:4] + "..." if len(value) > 4 else "****"
            issues.append(Issue(
                IssueCode.N8N004, node=name,
                message=f"hardcoded secret in parameters.{path} ({masked})",
                hint="move the secret into n8n Credentials and reference it; "
                "never commit literal secrets in workflow JSON"))

    return data,issues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Lint an n8n workflow JSON for production-readiness smells.",
        epilog="Exit codes: 0 = clean, 1 = errors found, 2 = warnings only.")
    ap.add_argument("workflow", help="path to exported workflow JSON")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of text")
    ap.add_argument("--fix", action="store_true",
                    help="write a separate .fixed.json file for fixable issues (never overwrite the input)")
    filters = ap.add_argument_group('filters').add_mutually_exclusive_group(required=False)
    filters.add_argument("--ignore", action='append', default=[], help="an issue codes to ignore, can be specified multiple times")
    filters.add_argument("--only", action='append', default=[], help="an issue codes to report, can be specified multiple times")
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

    invalid_filter_list = invalid_filters(allowlist = args.only, denylist=args.ignore)
    if invalid_filter_list:
        print(f"ERROR: passed invalid issue filters: {', '.join(invalid_filter_list)}", file=sys.stderr)
        return 1

    # --fix never modifies the source file. The user reviews and applies
    # the separate .fixed.json result if they want to keep the changes.
    fix_pin = (args.fix and (not args.only or "N8N009" in args.only)
               and "N8N009" not in args.ignore)
    original_data = data
    data,issues = lint_workflow(data, fix_pin)
    if data != original_data:
        source = Path(args.workflow)
        target = source.with_name(f"{source.stem}.fixed{source.suffix}")
        try:
            with target.open("x", encoding="utf-8") as output:
                json.dump(data, output, indent=2, ensure_ascii=False)
                output.write("\n")
        except FileExistsError:
            print(f"ERROR: fixed output already exists; refusing to overwrite: {target}", file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"ERROR: cannot write fixed output {target}: {exc}", file=sys.stderr)
            return 1
        print(f"Fixed workflow written to {target}; original unchanged", file=sys.stderr)
    issues = filter_issues(issues, allowlist=args.only, denylist=args.ignore)
    errors = [i for i in issues if i.severity == IssueSeverity.ERROR]
    warns = [i for i in issues if i.severity == IssueSeverity.WARN]

    if args.json:
        print(json.dumps({
            "file": args.workflow,
            "errors": [i.to_dict() for i in errors],
            "warnings": [i.to_dict() for i in warns],
            "exit_code": _exit_code(errors=errors, warnings=warns),
        }, indent=2))
    else:
        if not issues:
            print("clean: no production-readiness smells found")
        for issue in issues:
            print(issue)
        print(f"{len(errors)} error(s), {len(warns)} warning(s)")

    return _exit_code(errors=errors, warnings=warns)


if __name__ == "__main__":
    sys.exit(main())
