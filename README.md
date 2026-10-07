# n8n-workflow-linter by Payload

**Small software that earns its keep.** A free utility by Payload.

A zero-dependency Python CLI that statically lints an exported n8n workflow
JSON for production-readiness smells - the small misconfigurations that turn
into 3 a.m. pages.

## Install

Nothing. Python 3.10+ and this repo.

```bash
python3 lint.py workflow.json
```

## Usage

```bash
# lint a workflow exported from n8n (Download in the workflow menu)
python3 lint.py my-workflow.json

# machine-readable output
python3 lint.py my-workflow.json --json
```

Exit codes: `0` = clean, `1` = errors found, `2` = warnings only.

Real output:

```
$ python3 lint.py my-workflow.json
WARN  [N8N002] node 'Call webhook': HTTP Request has no timeout configured
         fix: set options.timeout (ms), e.g. 10000, so a hung endpoint cannot stall the workflow
ERROR [N8N004] node 'Authed call': hardcoded secret in parameters.headerParameters.parameters[0].value (Bear...)
         fix: move the secret into n8n Credentials and reference it; never commit literal secrets in workflow JSON
1 error(s), 1 warning(s)
```

## Rules

| Code | Severity | What it flags |
|------|----------|---------------|
| N8N001 | warn | Non-trigger node without explicit `onError` handling |
| N8N002 | warn | HTTP Request node with no `options.timeout` |
| N8N003 | warn | HTTP Request node with `retryOnFail` disabled |
| N8N004 | error | Hardcoded credential/secret literal in node parameters (n8n `={{ }}` expressions are not flagged) |
| N8N005 | error | Workflow has no trigger node |
| N8N006 | warn | Workflow has multiple trigger nodes |
| N8N007 | warn | Disabled node left in the workflow |
| N8N008 | warn | Node with no connections at all |

Notes on scope, stated plainly:

- Secret detection is heuristic: it looks for secret-like parameter names
  (`password`, `api_key`, `secret`, `token`, ...) and credential-style
  `{"name","value"}` pairs (e.g. an `Authorization` header) holding literal
  values. It is not a substitute for a secrets scanner.
- "Trigger" means a node whose type ends in `Trigger`, plus the `Start` and
  `Webhook` node types. Sticky notes are ignored.

## Tests

```bash
python3 tests/run_tests.py
```

8 fixtures: one clean workflow plus one workflow per smell. All must pass.

## What this doesn't do

This is a static linter, not a reliability system. It doesn't execute your
workflow, load-test it, simulate failure modes, add retries and circuit
breakers, or give you production-ready workflow templates.

For the full 10-workflow hardening system - failure alerting, timeout circuit
breakers, retry policies, and production patterns for AI agent workflows -
see the **[n8n Production AI Agent Reliability Kit](https://payloadtools.gumroad.com/l/n8n-agent-reliability-kit)**
($99) by Payload.

## License

MIT - see [LICENSE](LICENSE). Copyright 2026 Payload.

---

**Payload** - small, sharp tools for developers.
Developer portal: https://payloadhq.github.io/ ·
All products: https://payloadtools.gumroad.com/ ·
Contact: kylers.partners@gmail.com
