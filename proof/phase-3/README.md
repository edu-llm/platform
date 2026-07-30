# Phase 3 proof bundle

Phase: phase-3
Bundle schema version: 1
Source commit: 9c23e8242b7f2555e494bb5c3606dc6d6d5a1f79
Generated: 2026-07-30T19:45:20+00:00

This bundle exists so that a reviewer can decide whether Phase 3 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase3_proof.py` at generation time. It is not done, and the Result table below says by how much.

**Read this first.** Phase 3's claim is that one manifest becomes one container that runs on AWS Batch and lands its records in lineage. That has happened. Four submissions have gone from GitHub through OIDC, admission, Batch and EventBridge to S3: one succeeded, one exited non-zero deliberately, one was stopped by its own timeout, and one was refused before anything could be launched. What they left behind is captured and committed, and the checks that rest on it cite tests that read those records rather than describing what a run would show.

What is not done is captures rather than mechanism, which is a change in this bundle rather than only in the account. Four checks name an observation no completed run produced, and two need a shape of capture the per-run records cannot make; nothing left in the list waits on code being written. The Result table below says which. Cancellation is the one capability this phase describes and does not have, and it is no longer measured here -- read the Known limitations for where it went.

## Contents

- `negative-case-matrix.md` — each of the nineteen Phase 3 acceptance criteria mapped to the tests cited for it, by node id, with every gap stated. Read this one first.
- `measurement-method.md` — the two probes this phase depends on and the controls that make them believable. Included because an earlier revision of the plan opened with a confidently wrong finding from an uncontrolled simulation, and the correction is worth less than the method that caught it.
- `networking-evidence.md` — the dry-run authorization matrix for both regions, the VPC quota and its increase, the availability zones, and whose network the compute environment will run on.
- `batch-denial-matrix.md` — the two matrices, what each probe is aimed at so a permitted call changes nothing, and what choosing a probe has cost. The admission matrix has run against real sessions; the workload one has not.
- `open-decisions.md` — the question this phase answered and moved to where it is enforced, and the ones still open.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of what each Phase 3 role template grants, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — the contract models the modules behind this bundle define, with the structural digest of each and what makes one move.
- `unit-test-report.md` — summarised pass and fail counts, per module and for the whole suite, with the commands to reproduce them.
- `batch-execution-evidence.md`, `log-stream-evidence.md`, `lineage-record-evidence.md`, `cancellation-and-timeout-evidence.md` and `deployed-role-drift.md` — what four completed runs left behind, rendered from the captures committed under `fixtures/evidence/phase-3/`.
- `rollback-evidence.md` — the rollback rehearsal, which has not been performed. It says so, and says why nothing in the acceptance list is waiting for it.
- `event-evidence.md` — empty. It says what it records, what would fill it, and which criteria are waiting on it.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 3693 |
| suite tests executed | 3527 |
| suite passed | 3527 |
| suite failed | 0 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 371 |
| matrix node ids passed | 371 |
| matrix node ids failed | 0 |
| phase criteria | 19 |
| criteria COVERED | 13 (1, 2, 3, 4, 8, 9, 15, 16, 17, 19, 20, 21, 22) |
| criteria DEFERRED | 0 |
| criteria GAP (each one fails the gate) | 6 (10, 11, 12, 13, 14, 18) |
| role templates with recorded digests | 4 |
| roles compared to a capture | 0 |
| Batch jobs run | 0 |
| lineage records written by this phase | 0 |
| denial matrices executed | 0 |
| open decisions recorded | 0 |
| contract models in schema-compatibility.md | 26 |

## Verification commands

Run these from the repository root.

```
uv run pytest -q
uv run ruff check .
uv run mypy
uv run python tools/export_schemas.py
uv run python tools/validate_phase3.py
uv run python tools/build_phase3_proof.py
```

`tools/validate_phase3.py` exits 1 against this tree. Phase 3 is not accepted: criteria 10, 11, 12, 13, 14, 18 are GAPs. That is the honest state of the phase, not a broken gate. Read the Gaps section of `negative-case-matrix.md` for what closes it.

## Inputs measured

Digests of the files this bundle was generated from, so a reviewer can confirm the bundle describes the tree in front of them. Verify with `shasum -a 256 <file>`.

| file | digest |
| --- | --- |
| .github/workflows/deploy-phase3-batch.yml | sha256:9de1368084d6620fd27488d8373793d632e278926be6bfed6e6b4562504c7ae5 |
| .github/workflows/submit-run.yml | sha256:8f51256b517c2100258956666d64b85eee6f249334d0aab4863d11941154ec90 |
| config/execution-targets.yaml | sha256:29ac71f9287e75d773cfd2b03de879eb26b20ea6709d2da68778956b8c5cc37e |
| config/image-exceptions.yaml | sha256:0828f4203385bbc3adbd8521f62768e7f4eb46f56382bc6223d37b89aae7a49c |
| config/workload-catalog.yaml | sha256:dbd10c9d4655be66f829b8c43e150544ba17ca778988f86d14d22e51600feec6 |
| fixtures/evidence/phase-3/account-measurements.sanitized.json | sha256:02a7cafae966f04037c13f4e2a57b958a7b5e2c435c46a0eba4a6839d4a4c127 |
| infra/admission-state-machine.yaml | sha256:3ad3d29cb5703079823caf9feccdec6c8eecbffb6be5a487989a3568c9299fbe |
| infra/batch-compute.yaml | sha256:d601bb1e6c4f605737d1107334ef654e65c5c7862cc614e2c0eeb4536b93ae1d |
| infra/batch-events.yaml | sha256:3d347ee3365870ab2a0c0d6514d7c923c76b3646bb9c1a497e525cb16f16cce0 |
| infra/batch-network.yaml | sha256:699a82646fa0d9d2a3446471c5451a2847d4c5d1d8978ebe7a2525fa98d2154d |
| infra/iam/admission-service-roles.yaml | sha256:fd826897cc8ff59b345a648cf6063dd470942ec0680751f8a550d9ccca3793d2 |
| infra/iam/batch-roles.yaml | sha256:7d6b3dbd1c870d01c969137ffc4a45a4f49e0563efa7aeb1c26ec16288478b18 |
| infra/iam/infra-deployer-role.yaml | sha256:596abb25126c0f10d734cbecd01bec08495cac63b19a81ab46870318504774ac |
| infra/iam/lifecycle-lambda-role.yaml | sha256:f23ec64e8e2cd611eedb4748cbd7ae7ed26a1cd74d9c8569d6d5af93ed28f53c |
| infra/outputs-bucket.yaml | sha256:7ea6c087d7e2e4f5531e1ccf88120d9836ad7591fec30c52e762b56c76347fe2 |

## Known limitations

- Everything live here rests on four runs, and four is a small number. One succeeded, one exited non-zero deliberately, one was stopped by its timeout and one was refused before submission. That is enough to establish that each path works once; it is not a sample from which anything about reliability follows.
- Check 1 -- that a valid run reaches SUCCEEDED -- is covered, and it is the phase's central claim. A reviewer should read this bundle as a description of a system that has been operated a handful of times rather than one in service.
- This phase still cannot stop a job it has started, and nothing in the list of checks below says so any more. No component in the account holds `batch:TerminateJob`, and the three checks that used to record the absence moved to the phase that will build cancellation -- so the acceptance list is a measure of what Phase 3 can be held to, and this sentence is the only thing in the bundle that tells a reviewer the capability is missing. What bounds the exposure is the mandatory attempt duration, which has been observed stopping a real job.
- Check 20 is covered on a committed CloudFormation template, which is what the repository asks the account for rather than what the account holds. The four Phase 3 roles are now captured from the account and compared, so that gap is closed for them; the two roles the validator and the state machine hold belong to Phase 2's registry and are not, which is why check 14 is a gap.
- Check 22 is covered because the open-decisions entry is gone and the answer is enforced in code and configuration this repository commits. Every run so far named an image whose findings are carried by a recorded exception, so the gate has been evaluated and passed and has never had to refuse anything.
- A compute environment reporting VALID is not on its own evidence that a job can run. Batch does not fail a job it cannot place; it waits. Only a job observed in RUNNING and then SUCCEEDED establishes placement, egress and the image pull, which is why checks 1 and 15 are separate and are cited separately.
- Three lineage bindings will never load. They were written before the `"Result": null` fix in the admission state machine and carry an admission payload where a fan-out size belongs; the store is write-once, so they are permanent. The runs holding one are reported as not traceable end to end rather than as nearly traceable, and the corrupt bodies are described in the attestation rather than committed, because they carry an approver's name and a full image scan.
- The captures every live check rests on expire thirty days after they were observed, and this generator refuses to build once they do. Nothing about the runs will have changed on that date -- every object is still in a write-once store -- and what will have lapsed is anybody's knowledge of the account they are in.
- The admission denial matrix has run against a real session and its result lives in a GitHub Actions artifact with a thirty-day retention, which is somewhere this repository cannot cite. The workload matrix has not run at all: it executes inside the container, and no command run there has ever invoked it. So every claim about what the workload role cannot do rests on a policy, and check 13 is a gap.
- The deployed workload role permits writes under `teams/*/runs/*` rather than under one team's prefix, so it can write into any team's output location. The template agrees, so this is deliberate rather than drift, and for a single-team pilot nothing is misattributed -- but the cross-team isolation the `teams/` segment exists to make expressible is not expressed yet.
- The rollback rehearsal has not been performed. It is written down and no acceptance check is waiting on it, which is exactly the condition in which work stops being done and then stops being remembered.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded in the bundle index identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a template changes. Re-run `uv run python tools/build_phase3_proof.py` and read the diff before accepting a phase gate.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
