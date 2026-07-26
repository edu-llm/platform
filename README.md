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
| `proof/` | Phase acceptance bundles — what a reviewer reads instead of the test suite |
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

Prints one JSON object with two clearly separate groups, and exits:

| Code | Meaning |
| --- | --- |
| `0` | both groups passed |
| `1` | the gate ran and something in either group failed |
| `2` | inputs could not be read or did not validate |

`phase_criteria` are the thirteen Phase 0 acceptance criteria. Each one names the pytest
node ids that are cited for it, and the gate **executes them** before reporting — it does
not read coverage off a table. A criterion is one of exactly three things:

| Status | Meaning | Verdict |
| --- | --- | --- |
| `covered` | cited tests prove it against the shipped configuration, and all of them pass | passes |
| `deferred` | an explicit recorded decision, with a written reason and a written trigger for when it becomes live again | passes |
| `gap` | anything else | **fails** |

Whatever status the definition records, a criterion whose cited tests do not all exist and
pass is reported as a `gap`. A node id pytest cannot collect is a gap too, which is what
catches a cited test being renamed or deleted out from under the mapping.

`operational_inventory_checks` are the nine roster, pilot, plan, quota, manifest, and cost
checks. They are useful and they all still run, but they are **not** acceptance criteria —
they predate the current definition of the phase. Their ids are prefixed `inventory_` so
they cannot be mistaken for one, and passing all nine says nothing about whether Phase 0 is
done. The overall verdict is the AND of both groups.

Every check and every criterion is evaluated even after one fails, so a single run reports
everything that needs attention. No check trusts a self-reported verdict — capacity, for
example, is recomputed from the quota records rather than read from a summary field.

The criterion-to-test mapping lives in exactly one place,
`src/edullm_platform/phase0_criteria.py`. The gate and the proof-bundle generator both
import it, so the matrix in the bundle and the gate's verdict cannot disagree.

## Proof bundle

```bash
uv run python tools/build_phase0_proof.py
```

Writes `proof/phase-0/`: summarised test counts, the recorded canonical digest of every
fixture, an inventory of every contract model and its structural digest, and a matrix
mapping each Phase 0 criterion to the tests cited for it by node id. Start at
`proof/phase-0/README.md`.

The generator runs every node id it cites and refuses to write a bundle citing a test
pytest cannot collect, so the matrix cannot claim coverage it does not have. It also
refuses to overwrite a recorded digest that has drifted; re-recording takes
`--regenerate-goldens` and is meant to be reviewed alongside whatever caused the drift.
The bundle is committed because a tripwire nobody can diff is not a tripwire.

## Capacity evidence

`fixtures/evidence/` holds sanitized, read-only observations of the GitHub
organization plan and applied AWS service quotas. Records expire after 30 days so a
stale reading cannot pass as current; when that happens the gate reports
`evidence_stale` and the capture tool must be re-run by a maintainer with credentials.

Evidence is captured from a sandbox account and is labelled as such. A sandbox
observation never attests production capacity.
