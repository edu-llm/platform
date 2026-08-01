# edu-llm platform

**If you came here to run a training job, read [GETTING-STARTED.md](GETTING-STARTED.md)
instead.** It is about five minutes to your first run, and it lists the six things that
will otherwise waste a twelve-hour one. Everything below this line is about how the
platform is built, which you do not need to know to use it.

---

Shared control plane for eduLLM research workloads. This repository holds the
contracts that decide whether a compute run is valid and who may approve it, the
reviewed bindings those contracts are checked against, and the tooling that verifies
the whole thing hangs together.

It also provisions the cloud infrastructure those rules run on, and submits runs to it.
`infra/` holds the CloudFormation for the image registry, the three S3 buckets, the
admission state machine and validator, and the CPU and GPU AWS Batch compute environments.
`.github/workflows/` holds the workflow that builds and publishes a research image and the
one that compiles a submission, routes it to an approval gate, and hands the approved
manifest to admission. Eight submissions have been through that path in the sandbox account,
four on the CPU queue and four on the GPU one. Seven reached AWS Batch and ran; the eighth
was refused at admission before anything was launched, which is also a result worth keeping.
What each of the eight left behind is committed under `fixtures/evidence/` and rendered in
`proof/`.

## Layout

| Path | Contents |
| --- | --- |
| `GETTING-STARTED.md` | How to submit a run. The only file here a researcher needs |
| `src/edullm_platform/` | The validation library: contracts, canonical hashing, config loading, evidence models, acceptance gate |
| `config/` | Reviewed bindings — organization roster, approval policy, repository registry, workload and compute catalog, execution targets |
| `infra/` | CloudFormation for everything this platform deploys, plus the runbook for the procedures that need a laptop |
| `.github/workflows/` | Publishing an image, and submitting a run for admission |
| `fixtures/` | Representative run manifests, and sanitized captures of what the account did |
| `schemas/` | JSON Schemas generated from the models |
| `proof/` | Phase acceptance bundles — what a reviewer reads instead of the test suite |
| `tools/` | Maintainer scripts, run by hand |
| `tests/` | The suite that keeps all of the above honest |

The library is the single implementation. The submission workflow, the admission validator
running inside AWS, and the acceptance gates all run these same contracts, rather than
reimplementing the rules.

## Commands

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked          # install
uv run pytest -q          # tests
uv run ruff check .       # lint
uv run mypy               # types
```

`uv run pytest -q` runs every test and is the command every proof bundle asks a reviewer
for. Around two hundred of those tests start a subprocess — a real git repository, a bash
workflow body, a stubbed CLI, a nested pytest — and they are marked `slow`. While working
on something else you can leave them out:

```bash
uv run pytest -q -m "not slow"   # nine tests in ten, in a few seconds
```

That is a convenience and not a default. The exclusion belongs on the command line and
nowhere else: written into `addopts` it would make the standard command quietly run less
than it claims, and `tests/test_suite_budget.py` fails if anyone tries.

`ruff check .` respects `.gitignore`, so a file listed there is never linted by the
standard command. Three Phase 3 files were ignored for a while — `phase3_evidence.py`,
`tools/capture_phase3_evidence.py` and `tests/test_phase3_ec2_authorization.py` — and one
of them was found holding an `ISC004` that nothing in CI would have reported until its
`.gitignore` line came out. `mypy` reads an ignored file regardless, so lint is the only
blind one.

If a work-in-progress file is ever ignored again, lint it by explicit path while working
on it, and lint it **before** the `.gitignore` line comes out rather than after. Naming a
path is enough; ruff reads a file it is handed:

```bash
uv run ruff check src/edullm_platform/some_ignored_file.py
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

### The later phases

Each phase has a gate of its own, reading a definition of its own and applying the same
three statuses through the same shared machinery in `src/edullm_platform/criteria.py`.

```bash
uv run python tools/validate_phase1.py
uv run python tools/validate_phase2.py
uv run python tools/validate_phase3.py
uv run python tools/validate_phase4.py
uv run python tools/validate_phase5.py
```

All five exit `0` on a pass, `1` when the gate ran and a criterion failed, and `2` when the
inputs could not be read. They report criteria only; the `operational_inventory_checks`
group is Phase 0's and exists because that phase predates the current definition.

**Phases 2 and 3 exit 1 today, and that is the report working rather than a broken gate.**
All four deployed phases have run. Phase 2 reports thirteen of twenty-two criteria covered,
one deferred and eight gaps; Phase 3 reports thirteen of nineteen covered and six gaps;
Phase 4 reports nine of eleven covered and two deferred, with no gaps, and so exits 0. Run
the gates for the current numbers — the ones above are what they printed when this paragraph
was written, and a gate is the authority rather than this file.

Two of those numbers moved on 2026-07-31 and they moved for different reasons, which is
worth separating before anybody reads the trend as progress. Phase 2 closed a criterion by
capturing something nobody had recorded: who is on the `team-leads` team, which is the
effective reviewer list on the lead approval gate. Phase 4 reached 0 without any criterion
being satisfied — one was transferred to a later phase that owns the mechanism it needs, and
one became a deferral with a written trigger. A gate going green because work moved is a
different event from a gate going green because work landed. Of these two it is Phase 4
that went green, and it is the first kind: nothing about the account changed. Phase 2 is
the second kind and its gate still exits 1, because closing one criterion out of nine left
eight.

**Phase 5 exits 0 with one criterion outstanding, and that is worth reading rather than
skipping.** It reports fourteen of fifteen covered and one deferred, and both numbers moved on
2026-07-31 when the pilot limitations page left this README on a standing decision about what
this repository publishes. Criterion 11 used to assert that the page was here. It now asserts
that the three things a submitter cannot afford to learn by being caught out — cancelling does
not stop the job, a checkpoint omits optimizer state, `team` routes approval rather than
granting permission — are printed on the summary every accepted submission ends on. That is a
narrower promise than the page made, and it reaches everybody who submits rather than
everybody who goes looking.

Criterion 6 wants a GPU run claiming a team other than `platform` and writing a checkpoint.
The mechanism exists and each half has been exercised separately, so it closes on one
submission rather than on any work. Its deferral was withdrawn and re-granted inside the same
day: the first grant was conditioned on the limitations page and lapsed when the page moved,
and the second is conditioned on a warning printed to exactly the submissions it applies to,
which a cited test fails if anybody removes. **Phase 5 established that the two-person path
completes. It does not say anybody has run a research workload on this platform**: all three
pilot runs were a print statement on the CPU profile.

What the remaining gaps are about is worth knowing before reading them. They are not the
submission path, which works end to end: they are captures nobody has taken and shapes of
run nobody has aimed at a criterion yet. Each gap text says what was observed, what is
missing, and what would close it. Recording them as deferrals instead would turn a gate
green without anything changing in the account, which is exactly the move the three-status
rule exists to make visible.

That is the same sentence Phase 5's deferral has to answer, so the difference is worth
naming rather than leaving to inference. A deferral needs a written reason and a written
trigger, and it needs the work to be owned somewhere real; Phase 5's check has a phase, a
position in it and no build item in front of it, and a gate that reports it prints both the
reason and the trigger where a reviewer reads the verdict. A gap here has none of that, and
relabelling one would be the move rather than an instance of it.

One capability is missing rather than unobserved, and no gate measures it: **nothing here
can stop a job once it has started.** Cancelling the submission workflow in GitHub does not
cancel the Batch job — the workflow says so where an operator will see it, and the job runs
on. What bounds the cost is the mandatory per-attempt timeout, which every submission
carries and which has been observed stopping a real job. Building cancellation belongs to a
later phase, so Phase 3's criteria no longer carry it and its numbering skips 5, 6 and 7
where those checks used to be.

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

Phases 1, 2, 3 and 5 have generators of the same shape, writing `proof/phase-1/`,
`proof/phase-2/`, `proof/phase-3/` and `proof/phase-5/`:

```bash
uv run python tools/build_phase1_proof.py
uv run python tools/build_phase2_proof.py
uv run python tools/build_phase3_proof.py
uv run python tools/build_phase5_proof.py
```

Phases 1, 2 and 3 each record the canonical digest of what a committed IAM role template
*grants* rather than of the file, so a reordered key does not fire and a widened statement
does. Phase 5 aims the same tripwire somewhere else, because what can move underneath that
phase is not a role: it digests each committed pilot-run capture, which is the only evidence
that anybody other than the author has used this platform.

All five count the suite the same way and each excludes all five generator test modules
from its own verification run, so adding a generator moves a cell in every bundle. Any
bundle recording four generator modules was written before Phase 5 had one and is stale.

Phase 4 has an acceptance gate and no generator, so there is no `proof/phase-4/`. Its
evidence is committed under `fixtures/evidence/phase-4/` and read by the tests the gate
cites.

The Phase 3 bundle is mostly full now and was mostly empty once. Its run documents —
`batch-execution-evidence.md`, `log-stream-evidence.md`, `lineage-record-evidence.md`,
`cancellation-and-timeout-evidence.md`, `deployed-role-drift.md` — are rendered from the
captures under `fixtures/evidence/phase-3/`. Two are still generated saying why they are
empty: `event-evidence.md`, which has no capture, and `rollback-evidence.md`, whose
rehearsal has not been performed. A document omitted because there was nothing to put in it
would make the phase look like it has fewer claims than it has.

## Captured evidence

`fixtures/evidence/` holds sanitized, read-only observations of the account, one directory
per phase: the GitHub organization plan and applied AWS service quotas at the top level;
under `phase-1/` the two IAM roles as IAM returned them and what one completed publish left
behind — the image, its scan, the publisher session that pushed it, the five refusals that
session met, and a second push the registry turned away; under `phase-2/` every admission
execution the state machine has run and the lineage records those executions wrote; under
`phase-3/` the CPU compute environment and one directory per run for the four CPU
submissions; under `phase-4/` the GPU compute environment, the workload role's measured
scope, and one directory per run for the four GPU submissions.

Account IDs are masked, and this account is masked differently from any other so that a
grant pointing somewhere else cannot be mistaken for a local one. An identity this
repository does not declare is not named at all, because in a shared sandbox account those
are people.

Records expire after 30 days so a stale reading cannot pass as current. When that
happens the Phase 0 gate reports `evidence_stale`, and in Phase 1 the tests that read the
committed captures fail, which takes the criteria citing them back to gaps and the
gate to exit 1. Either way the remedy is a maintainer with credentials re-running the
capture tool; nothing renews on its own. Re-capturing the run costs a read of the account
rather than another publish: the image, its scan, the session and the refusals are all
still in the registry and in CloudTrail, and what lapses is when somebody last looked.

Each set of records is worth committing because something reads them. A deployed role is
checked against the CloudFormation template that claims to describe it, in both
directions, by `edullm_platform.role_drift`; see `proof/phase-1/deployed-role-drift.md`.
The run records are checked against each other — a scan filed under another digest, a
refusal on another tag or a matrix missing an action stops them counting as a record of
this run; see `proof/phase-1/publisher-denial-matrix.md`.

`fixtures/evidence/phase-1/rebuild/` is different in kind and does not expire. It holds
the image configurations of one commit built several times from the same pinned base, and
the analysis of where they diverge is a test rather than a paragraph. See
`proof/phase-1/image-rebuild-comparison.md`.

## Open decisions

`src/edullm_platform/open_decisions.py` records questions this repository has surfaced and
deliberately has not answered. A gap means unfinished work and a deferral means a
postponement with a written trigger; neither fits a question whose answer is a policy
choice, and one that is not written down gets settled by accident by whoever first trips
over it. No entry may carry a recommendation, and an entry with fewer than two options is
refused, so a question cannot become a decision by having its alternatives deleted.
Answering one means deleting it from there and putting the answer where it is enforced.
The register is rendered into `proof/phase-1/open-decisions.md`.

Evidence is captured from a sandbox account and is labelled as such. A sandbox
observation never attests production capacity.
