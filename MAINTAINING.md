# How the platform is built

This file is for people changing the platform rather than using it, and for reviewers
reading the acceptance evidence it produces. **A researcher never needs it** — everything
required to submit a run and to understand what happens to it afterwards is in
[`guides/the-platform.md`](guides/the-platform.md), and nothing there depends on anything
here. What follows is what this repository contains, how to run its checks, and what each
check is claiming when it reports.

Shared control plane for eduLLM research workloads. This repository holds the
contracts that decide whether a compute run is valid and who may approve it, the
reviewed bindings those contracts are checked against, and the tooling that verifies
the whole thing hangs together.

It also provisions the cloud infrastructure those rules run on, and submits runs to it.
`infra/` holds the CloudFormation for the image registry, the three S3 buckets, the
admission state machine and validator, and the CPU and GPU AWS Batch compute environments.
`.github/workflows/` holds the workflow that builds and publishes a research image and the
one that compiles a submission, routes it to an approval gate, and hands the approved
manifest to admission. Nine submissions have been through that path in the sandbox account,
four on the CPU queue and five on the GPU one. Eight reached AWS Batch and ran; the ninth
was refused at admission before anything was launched, which is also a result worth keeping.
What each of the nine left behind is committed under `fixtures/evidence/`.

## Do not make this repository private

**The approval gate exists because this repository is public, and converting it to private
deletes the gate rather than weakening it.** There is no warning, no failure and no red run.
Every job that was waiting for a lead proceeds, submissions carry on working, and the only
difference is that nobody is asked.

GitHub offers required reviewers on an environment for public repositories on every plan, and
for private repositories only on Pro, Team or Enterprise. Read from the account on
2026-08-06:

```bash
$ gh api orgs/edu-llm --jq '.plan.name'
free
$ gh api repos/edu-llm/platform --jq '.visibility'
public
```

So the gate holds by the narrowest margin GitHub sells. A job whose environment carries no
protection rule is a job that runs, which is why the failure is silent: there is nothing for
it to fail. Five repositories in this organization are already private, so this is a thing
somebody here does by habit rather than a hypothetical.

**If the repository has to become private, move the organization off Free first**, and
re-read `run-approval-lead` afterwards to confirm the protection rule survived:

```bash
gh api repos/edu-llm/platform/environments/run-approval-lead \
  --jq '[.protection_rules[].type]'
```

`required_reviewers` must be in that list. If it is not, no submission should be dispatched
until it is back.

**What actually catches this is not this paragraph.** Nobody reads a file before changing a
repository setting. The guard is `the-gate-still-exists`, the first job in
`.github/workflows/audit.yml`, which reads the live environments every morning and goes red on
either the visibility or the protection rule; and `tools/verify_the_gate.py`, which is what
that job runs and which anybody can run by hand in about four seconds:

```bash
uv run python tools/verify_the_gate.py
uv run python tools/verify_the_gate.py --check-team-membership   # needs a session that can list the team
```

What the repository declares the gate must be is `DECLARED_GATES` in
`src/edullm_platform/approval_gate.py`. That constant is the first time the approval
environments have been written down anywhere in this tree: everything else this platform
enforces is a reviewed file, and the control deciding whether a run waits for a person was a
browser setting plus a capture with a thirty-day expiry. **Changing an approval environment in
GitHub means changing that constant in the same ten minutes**, which is the point — it is the
commit those changes have never had, and the audit names the line when the two disagree.

The one thing neither can see is who is in the `team-leads` team, because listing a team needs
the Members organization permission and no `GITHUB_TOKEN` holds one. That half is the
`lead-gate` job beside it, which compares the committed capture against the roster and puts a
clock over how old the capture is; `--check-team-membership` above is the same comparison
against live GitHub, from a session that can list the team. Between the two jobs: `lead-gate`
asks who stands behind the reviewer slot, `the-gate-still-exists` asks whether the slot is
still there, and neither substitutes for the other.

## Layout

| Path | Contents |
| --- | --- |
| `guides/` | The researcher-facing guides: submitting a run, and turning OLMo-core work into something this platform can run. The only files here a researcher needs |
| `src/edullm_platform/` | The validation library: contracts, canonical hashing, config loading, evidence models, the operational inventory checks |
| `config/` | Reviewed bindings — organization roster, approval policy, repository registry, workload and compute catalog, execution targets |
| `infra/` | CloudFormation for everything this platform deploys, plus the runbook for the procedures that need a laptop |
| `.github/workflows/` | Publishing an image, and submitting a run for admission |
| `fixtures/` | Representative run manifests, sanitized captures of what the account did, and the recorded digests of both |
| `schemas/` | JSON Schemas generated from the models |
| `tools/` | Maintainer scripts, run by hand |
| `tests/` | The suite that keeps all of the above honest |

The library is the single implementation. The submission workflow, the admission validator
running inside AWS, and the test suite all run these same contracts, rather than
reimplementing the rules.

## Commands

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked          # install
uv run pytest -q          # tests
uv run ruff check .       # lint
uv run mypy               # types
```

`uv run pytest -q` runs every test. It is also the slowest way to run it. When the wait matters, run the whole suite in
parallel rather than running less of it:

```bash
uv run pytest -q -n4 --dist loadgroup   # every test, in about a quarter of the time
```

`--dist loadgroup` is not optional. Without the groups `tests/conftest.py` assigns, xdist
distributes individual tests, rebuilds every module's session fixtures on every worker, and
gives most of the parallelism back. `.github/workflows/ci.yml` runs exactly this command and
records the measurements beside it.

Around three hundred tests start a subprocess — a real git repository, a bash workflow body,
a stubbed CLI, a nested pytest — and they are marked `slow`. **`-m "not slow"` is not the
fast path, and this file used to advertise it as one.** What it claimed was "nine tests in
ten, in a few seconds", and only the first half was ever true.

Measured on 2026-08-04, over 4,835 tests:

| command | what it runs | wall clock |
| --- | --- | --- |
| `pytest -q` | everything | 651s |
| `pytest -q -m "not slow"` | 4,507 tests, 328 deselected | 240s |
| `pytest -q -n4 --dist loadgroup` | everything | 107s |

The escape hatch is beaten by a complete run, so there is nothing left for it to be good
for. The two lower rows were measured in adjacent runs on one machine, which is what makes
that comparison worth anything; the machine was busy throughout, so read the ratios rather
than the seconds.

There is no marking that would rescue the claim, which is worth stating because "the wrong
tests are marked" is the obvious next thought. `--durations=0` over one serial run puts
644s of measured test time behind those 4,835 tests, and it is spread rather than pooled:
the slowest single test is 10.2s, the 25 slowest are 17% of the total, the hundred slowest
are 44%, and the median timed test is 0.07s. Getting to "a few seconds" means deselecting
the five hundred slowest tests, which is 92% of the runtime and not a suite any more.

The marker is worth keeping for what it says — `slow` means *starts a subprocess*, which is
a real category and the reason those tests are worth knowing about. It is not a proxy for
expensive, and reading it as one is the mistake behind the deleted claim: 186 unmarked tests
take half a second or more and 320s between them, and the slowest unmarked test at 7.5s is
slower than every marked test but two.

Wherever the exclusion is used it belongs on the command line and nowhere else: written into
`addopts` it would make the standard command quietly run less than it claims, and
`tests/test_suite_budget.py` fails if anyone tries.

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

**Do not run `uv run ruff format`, and do not commit what it writes.** Nothing in CI runs
it, `ruff check .` is the whole of what lint means here, and this tree has never been
formatted by it. Measured on 2026-08-05 under ruff 0.16.0, it rewrites 201 of the 366
Python files: 1,583 lines out, 1,081 in, a tree some 500 lines shorter. That is not a lint
failure being repaired. It is a repository-wide diff that conflicts with every branch open
at the time, and an agent who runs it and commits the result hands that conflict to
everybody else.

**It is not a stale setting**, which is worth ruling out first because it would be the
cheap fix. `line-length = 100` already disagrees least: the formatter rewrites 243 files at
96, 213 at 99, 214 at 101 and 290 at 110. One hundred is the floor, so there is no number
to change it to, and no configuration that makes the command a no-op.

**Reformatting is the right end state, and the reason to defer it is timing rather than
risk.** That was measured against a copy rather than argued about: not one `#` comment
changes, in any of the 366 files, because ruff reflows no comment and `docstring-code-format`
is off, so the dense reasoning this repository is written in survives it exactly. The only
docstring change anywhere is a space inserted after `"""` in the three files whose text
opens with a quote, and no other AST moves. So run it on a day the branch list is short, as
one commit that does nothing else, and add `ruff format --check .` to `ci.yml` in that same
commit so that the command run here and the gate in CI can never drift apart again. Until
that day the formatter wanting to change things is known, and is not yours to fix in
passing.

**If `mypy` reports errors here that CI does not, delete `.mypy_cache` before believing
them.** The cache records where each module was resolved from, and it survives deleting
`.venv` and re-syncing — so a virtualenv built once on the wrong interpreter goes on
producing that interpreter's answers afterwards. It shows up as `boto3` being reported as
installed-but-untyped, on the two `# type: ignore[import-not-found]` comments that are
correct when it is genuinely absent, and it has cost time twice. Building the environment on
a uv-managed CPython rather than a conda base avoids causing it: `uv venv --python 3.13
--managed-python`.

Regenerate the published schemas after changing any contract. The output is
byte-reproducible, so a second run should produce no diff:

```bash
uv run python tools/export_schemas.py
```

## What `edullm` exits with

Four codes and the signal one. The number is the only part of the CLI a script can read
without parsing prose, so it is a published interface under
`docs-frank/reference/writing-releases-and-docs.md` and changing one is a major version.
`src/edullm_platform/cli/main.py` declares them and `tests/test_cli_exit_codes.py` holds
every verb to them, deriving the verbs from the parser rather than listing them.

| Code | Meaning | What a caller should do |
| --- | --- | --- |
| 0 | it stands | carry on |
| 1 | refused on the merits | read the refusal code, fix the submission or the run id |
| 2 | the tool could not be driven, by input or by installation | fix the command, the configuration, or the install |
| 3 | the platform could not be asked | sleep and try again |
| 130 | interrupted | nothing, though a dispatched workflow is still running |

**2 and 3 are the split worth understanding.** They were one code, and a retry loop is the
first script anybody writes against this. A mistyped flag and a GitHub that would not answer
both exited 2, so a caller either retried a typo forever or retried nothing. 2 is the
caller's fault and repeating it reaches the same place. 3 is nobody's and repeating it is
the reasonable next move.

**1 means a verdict, and only a verdict.** `check` and `submit` refuse submissions, and all
three run verbs refuse a run id they cannot read or cannot resolve. Nothing else may use it.
A reporting workflow that failed for its own reasons used to exit 1 through `logs` and
`status`, which refuse nothing, and it told a script a submission had been declined.

**130 is 128 plus SIGINT**, which is what a shell reports for a process a signal killed.
Ctrl-C during a wait prints one line and exits with it, and where a workflow was already
dispatched that line names it, because nothing here cancels a dispatch on the way out.

`tools/compile_submission.py` gives 0, 1 and 2 the same meanings inside the workflow, which
is deliberate and is where the CLI took them from.

## The checks that read the shipped configuration

Two of them, both ordinary tests on the pull-request path, and both relocated on 2026-08-05
out of an acceptance-gate apparatus that was deleted. The section they replace described six
phase gates, five proof-bundle generators and a `proof/` directory. That model was replaced
by the slice plans, its evidence was void because twelve of the thirteen run ids it cited
named container images an ECR lifecycle rule had already deleted, and the platform's own
Phase 1 study establishes that a rebuild never reproduces a digest, so nothing could recover
them.

### The nine operational inventory checks

`src/edullm_platform/operational_inventory.py`, run against the live tree by
`tests/test_operational_inventory.py`. Each one asks whether the reviewed configuration is
still the configuration that was reviewed.

| Check | What it holds |
| --- | --- |
| `inventory_ownership` | the admin and team-lead rosters are the recorded ones |
| `inventory_pilots` | OLMo-core and dolma are the two pilot repositories |
| `inventory_workload_coverage` | the catalog prices both a CPU shape and a GPU shape |
| `inventory_approval_paths` | routine routes to a lead, exception includes an admin, and the six outright denials are all in policy |
| `inventory_checkpoint_expectations` | a workload that allows retries declares a checkpoint contract |
| `inventory_github_plan` | the captured plan is fresh and supports the controls this organization needs |
| `inventory_aws_capacity` | the captured quotas are fresh, describe the sandbox in us-east-1, and cover the catalog |
| `inventory_representative_manifests` | every fixture manifest names registered things and classifies as its filename says |
| `inventory_cost_estimates` | every reviewed cost matches what the catalog computes, and a routine one stays inside the programme ceiling |

Every check runs even after one fails, so a single run reports everything that needs
attention. No check trusts a self-reported verdict. Capacity, for example, is recomputed
from the quota records rather than read from a summary field.

### The recorded digests

Four sets, committed under `fixtures/goldens/` and recomputed against the tree on every run.

| File | Subject | Read back by |
| --- | --- | --- |
| `contract-fixtures.json` | the nine fixture manifests and authorization scenarios | `tests/test_serialization_goldens.py` |
| `iam-role-templates.json` | what each of the nine committed IAM role templates grants | `tests/test_serialization_goldens.py` |
| `admitted-runs.json` | the three committed pilot-run captures | `tests/test_serialization_goldens.py` |
| `contract-models.json` | the structural digest of every contract model in the package | `tests/test_contract_inventory.py` |

Each digests the parsed thing rather than the file, so reindenting is not drift and a value
changing is. The role digests are taken over the projection the drift comparison acts on, so
a reordered key does not fire and a widened statement does. Seven of the nine roles have no
capture to compare against, which makes the recorded digest the only thing between a
template widened in the meantime and nobody noticing. The three pilot captures name workload
profiles that have since been retired and cannot be taken again.

The contract-model inventory is the one that reaches furthest. Sixteen models are exported
to `schemas/` and re-derived on every CI run, and that inventory covers the other hundred
and twenty-four. A moved structural digest means a field was added, removed, retyped or
reconstrained, and every payload already written against the old shape is one the new shape
may refuse. A lineage record in S3 cannot be rewritten.

Re-recording is deliberate and is a person's decision:

```bash
uv run python tools/record_goldens.py            # refuses to overwrite a drifted digest
uv run python tools/record_goldens.py --force    # re-records, having read what moved
```

Review the digest diff in the same commit as the change that caused it. A drift that was not
intended is a regression, and re-recording is the wrong repair.

## Captured evidence

`fixtures/evidence/` holds sanitized, read-only observations of the account, one directory
per phase: the GitHub organization plan and applied AWS service quotas at the top level;
under `phase-1/` the two IAM roles as IAM returned them and what one completed publish left
behind — the image, its scan, the publisher session that pushed it, the five refusals that
session met, and a second push the registry turned away; under `phase-2/` every admission
execution the state machine has run and the lineage records those executions wrote; under
`phase-3/` the CPU compute environment and one directory per run for the four CPU
submissions; under `phase-4/` the GPU compute environment, the workload role's measured
scope, and one directory per run for the five GPU submissions.

Account IDs are masked, and this account is masked differently from any other so that a
grant pointing somewhere else cannot be mistaken for a local one. An identity this
repository does not declare is not named at all, because in a shared sandbox account those
are people.

Records expire after 30 days so a stale reading cannot pass as current. When that happens
`tests/test_evidence.py` fails on the two top-level captures and the tests that read the
per-directory captures fail on theirs, all of them on the pull-request path. The remedy is a
maintainer with credentials re-running the capture tool. Nothing renews on its own. Re-capturing the run costs a read of the account
rather than another publish: the image, its scan, the session and the refusals are all
still in the registry and in CloudTrail, and what lapses is when somebody last looked.

Each set of records is worth committing because something reads them. A deployed role is
checked against the CloudFormation template that claims to describe it, in both
directions, by `edullm_platform.role_drift`; see `tests/test_phase1_role_drift.py`.
The run records are checked against each other. A scan filed under another digest, a
refusal on another tag or a matrix missing an action stops them counting as a record of
this run; see `tests/test_publisher_denials.py`.

`fixtures/evidence/phase-1/rebuild/` is different in kind and does not expire. It holds
the image configurations of one commit built several times from the same pinned base, and
the analysis of where they diverge is a test rather than a paragraph. See
`tests/test_phase1_rebuild_comparison.py`.

## Open decisions

`src/edullm_platform/open_decisions.py` records questions this repository has surfaced and
deliberately has not answered. A gap means unfinished work and a deferral means a
postponement with a written trigger; neither fits a question whose answer is a policy
choice, and one that is not written down gets settled by accident by whoever first trips
over it. No entry may carry a recommendation, and an entry with fewer than two options is
refused, so a question cannot become a decision by having its alternatives deleted.
Answering one means deleting it from there and putting the answer where it is enforced.
`tests/test_open_decisions.py` holds the register to its own rules.

Evidence is captured from a sandbox account and is labelled as such. A sandbox
observation never attests production capacity.
