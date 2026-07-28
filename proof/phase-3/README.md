# Phase 3 proof bundle

Phase: phase-3
Bundle schema version: 1
Source commit: 2cbc0e0f10bce90d9581e61f02bcfd84413d2e6a
Generated: 2026-07-28T18:11:13+00:00

This bundle exists so that a reviewer can decide whether Phase 3 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase3_proof.py` at generation time. It is not done, and the Result table below says by how much.

**Read this first.** Phase 3's claim is that one manifest becomes one container that runs on AWS Batch and lands its records in lineage. That has happened. Four submissions have gone from GitHub through OIDC, admission, Batch and EventBridge to S3: one succeeded, one exited non-zero deliberately, one was stopped by its own timeout, and one was refused before anything could be launched. What they left behind is captured and committed, and the checks that rest on it cite tests that read those records rather than describing what a run would show.

What is not done is the other end of a run's life. Nothing in this account can stop a job once it has started, so the three cancellation checks need a component built before they need a run. Four more need a run aimed at them, and two need a shape of capture the per-run records cannot produce. The Result table below says which.

## Contents

- `negative-case-matrix.md` — each of the 22 Phase 3 acceptance criteria mapped to the tests cited for it, by node id, with every gap stated. Read this one first.
- `measurement-method.md` — the two probes this phase depends on and the controls that make them believable. Included because an earlier revision of the plan opened with a confidently wrong finding from an uncontrolled simulation, and the correction is worth less than the method that caught it.
- `networking-evidence.md` — the dry-run authorization matrix for both regions, the VPC quota and its increase, the availability zones, and whose network the compute environment will run on.
- `batch-denial-matrix.md` — the two matrices, what each probe is aimed at so a permitted call changes nothing, and what choosing a probe has cost. The admission matrix has run against real sessions; the workload one has not.
- `open-decisions.md` — the question this phase answered and moved to where it is enforced, and the ones still open.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of what each Phase 3 role template grants, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — the contract models Phase 3 added, with their structural digests.
- `unit-test-report.md` — summarised pass and fail counts, per module and for the whole suite, with the commands to reproduce them.
- `batch-execution-evidence.md`, `log-stream-evidence.md`, `lineage-record-evidence.md`, `cancellation-and-timeout-evidence.md` and `deployed-role-drift.md` — what four completed runs left behind, rendered from the captures committed under `fixtures/evidence/phase-3/`.
- `rollback-evidence.md` — the rollback rehearsal, which has not been performed. It says so, and says why nothing in the acceptance list is waiting for it.
- `event-evidence.md` — empty. It says what it records, what would fill it, and which criteria are waiting on it.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 3265 |
| suite tests executed | 3103 |
| suite passed | 3103 |
| suite failed | 0 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 309 |
| matrix node ids passed | 309 |
| matrix node ids failed | 0 |
| phase criteria | 22 |
| criteria COVERED | 13 (1, 2, 3, 4, 8, 9, 15, 16, 17, 19, 20, 21, 22) |
| criteria DEFERRED | 0 |
| criteria GAP (each one fails the gate) | 9 (5, 6, 7, 10, 11, 12, 13, 14, 18) |
| role templates with recorded digests | 4 |
| roles compared to a capture | 0 |
| Batch jobs run | 0 |
| lineage records written by this phase | 0 |
| denial matrices executed | 0 |
| open decisions recorded | 0 |
| contract models added by this phase | 24 |

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

`tools/validate_phase3.py` exits 1 against this tree. Phase 3 is not accepted: criteria 5, 6, 7, 10, 11, 12, 13, 14, 18 are GAPs. That is the honest state of the phase, not a broken gate. Read the Gaps section of `negative-case-matrix.md` for what closes it.

## Inputs measured

Digests of the files this bundle was generated from, so a reviewer can confirm the bundle describes the tree in front of them. Verify with `shasum -a 256 <file>`.

| file | digest |
| --- | --- |
| .github/workflows/deploy-phase3-batch.yml | sha256:ddb0918c65ec6ef9323126aee8dbd16e3a57376e58cd56a4a6dc377fd9865014 |
| .github/workflows/submit-run.yml | sha256:389802db5f617673f77e7c9c7a918d0d941ea32c11428d02087f5b67fde37dae |
| config/execution-targets.yaml | sha256:0ca6ec32b9d5c8c95db8704f8f930fd66f7c958e473bf7f200e732a331115d70 |
| config/image-exceptions.yaml | sha256:0790a1dae907566399273eb2dd0aae3ffed36d2958b0b5bfeec557c4618491de |
| config/workload-catalog.yaml | sha256:9b0126893bd09f6befe6a598257104d1d58aaa4e040a8aad6442a25d3f167223 |
| fixtures/evidence/phase-3/account-measurements.sanitized.json | sha256:02a7cafae966f04037c13f4e2a57b958a7b5e2c435c46a0eba4a6839d4a4c127 |
| infra/admission-state-machine.yaml | sha256:4673531d979d7f517c37a97283d422f4968e1871af7e43a8c96fbf9d23c73d44 |
| infra/batch-compute.yaml | sha256:d601bb1e6c4f605737d1107334ef654e65c5c7862cc614e2c0eeb4536b93ae1d |
| infra/batch-events.yaml | sha256:f34e0aadac04e838545847e8db269b1d78122e10614480428cb8327783f49680 |
| infra/batch-network.yaml | sha256:699a82646fa0d9d2a3446471c5451a2847d4c5d1d8978ebe7a2525fa98d2154d |
| infra/iam/admission-service-roles.yaml | sha256:7bd2a23e234a6a398dc2e6adf683ccbc38d25b2049a9348bf5d280d144ebc8e7 |
| infra/iam/batch-roles.yaml | sha256:7d6b3dbd1c870d01c969137ffc4a45a4f49e0563efa7aeb1c26ec16288478b18 |
| infra/iam/infra-deployer-role.yaml | sha256:905fbf8444c271ee9337874643c01d76eefdaba31571142407f3220eeef1c019 |
| infra/iam/lifecycle-lambda-role.yaml | sha256:f23ec64e8e2cd611eedb4748cbd7ae7ed26a1cd74d9c8569d6d5af93ed28f53c |
| infra/outputs-bucket.yaml | sha256:7ea6c087d7e2e4f5531e1ccf88120d9836ad7591fec30c52e762b56c76347fe2 |

## Known limitations

- Everything live here rests on four runs, and four is a small number. One succeeded, one exited non-zero deliberately, one was stopped by its timeout and one was refused before submission. That is enough to establish that each path works once; it is not a sample from which anything about reliability follows.
- Check 1 -- that a valid run reaches SUCCEEDED -- is covered, and it is the phase's central claim. A reviewer should read this bundle as a description of a system that has been operated a handful of times rather than one in service.
- This phase still cannot stop a job it has started. No component in the account holds `batch:TerminateJob`, so checks 5, 6 and 7 are a gaps that need a component built rather than a run taken. What bounds the exposure is the mandatory attempt duration, which has been observed stopping a real job.
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
