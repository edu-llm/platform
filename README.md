# edu-llm platform

Shared control plane for eduLLM research workloads. This repository holds the
contracts that decide whether a compute run is valid and who may approve it, the
reviewed bindings those contracts are checked against, and the tooling that verifies
the whole thing hangs together.

Nothing here provisions cloud infrastructure or submits jobs. It defines and enforces
the rules that later phases execute against.

## Layout

| Path | Contents |
| --- | --- |
| `src/edullm_platform/` | The validation library: contracts, canonical hashing, config loading, evidence models, acceptance gate |
| `config/` | Reviewed bindings — organization roster, approval policy, workload and compute catalog |
| `fixtures/` | Representative run manifests and captured capacity evidence |
| `schemas/` | JSON Schemas generated from the models |
| `tools/` | Maintainer scripts, run by hand |
| `tests/` | The suite that keeps all of the above honest |

The library is the single implementation. Later phases run these same contracts
inside GitHub checks and AWS admission, rather than reimplementing the rules.

## Commands

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked          # install
uv run pytest -q          # tests
uv run ruff check .       # lint
uv run mypy               # types
```

Regenerate the published schemas after changing any contract. The output is
byte-reproducible, so a second run should produce no diff:

```bash
uv run python tools/export_schemas.py
```

## Acceptance gate

```bash
uv run python tools/validate_phase0.py
```

Prints one JSON object listing every check with a stable `reason_code`, and exits:

| Code | Meaning |
| --- | --- |
| `0` | every check passed |
| `1` | the gate ran and at least one check failed |
| `2` | inputs could not be read or did not validate |

Every check runs even after one fails, so a single run reports everything that needs
attention. No check trusts a self-reported verdict — capacity, for example, is
recomputed from the quota records rather than read from a summary field.

## Capacity evidence

`fixtures/evidence/` holds sanitized, read-only observations of the GitHub
organization plan and applied AWS service quotas. Records expire after 30 days so a
stale reading cannot pass as current; when that happens the gate reports
`evidence_stale` and the capture tool must be re-run by a maintainer with credentials.

Evidence is captured from a sandbox account and is labelled as such. A sandbox
observation never attests production capacity.
