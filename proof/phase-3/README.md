# Phase 3 proof bundle

Phase: phase-3
Bundle schema version: 1
Source commit: 834c5fbe1c821cea9cc696a32ec4347ba7d2c955
Generated: 2026-07-28T01:10:22+00:00

This bundle exists so that a reviewer can decide whether Phase 3 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase3_proof.py` at generation time. It is not done, and the Result table below says by how much.

**Read this first.** Phase 3's claim is that one manifest becomes one container that runs on AWS Batch and lands its records in lineage. The software for that is built and tested. None of it has been deployed, and no Batch job has ever run in this account, because Wave 5 -- the laptop IAM stacks, the CI stacks and the live matrix -- is held. Seven of the documents below are therefore empty with a reason in each, and the Result table shows how few criteria that leaves standing.

## Contents

- `negative-case-matrix.md` — each of the 22 Phase 3 acceptance criteria mapped to the tests cited for it, by node id, with every gap stated. Read this one first.
- `measurement-method.md` — the two probes this phase depends on and the controls that make them believable. Included because an earlier revision of the plan opened with a confidently wrong finding from an uncontrolled simulation, and the correction is worth less than the method that caught it.
- `networking-evidence.md` — the dry-run authorization matrix for both regions, the VPC quota and its increase, the availability zones, and whose network the compute environment will run on.
- `batch-denial-matrix.md` — the two matrices, what each probe is aimed at so a permitted call changes nothing, and what choosing a probe has cost. Neither has run.
- `open-decisions.md` — the question this phase answered and moved to where it is enforced, and the ones still open.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of what each Phase 3 role template grants, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — the contract models Phase 3 added, with their structural digests.
- `unit-test-report.md` — summarised pass and fail counts, per module and for the whole suite, with the commands to reproduce them.
- `batch-execution-evidence.md`, `log-stream-evidence.md`, `event-evidence.md`, `lineage-record-evidence.md`, `cancellation-and-timeout-evidence.md`, `deployed-role-drift.md` and `rollback-evidence.md` — empty. Each says what it records, what would fill it, and which criteria are waiting on it.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 3168 |
| suite tests executed | 3006 |
| suite passed | 3006 |
| suite failed | 0 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 267 |
| matrix node ids passed | 267 |
| matrix node ids failed | 0 |
| phase criteria | 22 |
| criteria COVERED | 2 (20, 22) |
| criteria DEFERRED | 0 |
| criteria GAP (each one fails the gate) | 20 (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21) |
| role templates with recorded digests | 4 |
| roles compared to a capture | 0 |
| Batch jobs run | 0 |
| lineage records written by this phase | 0 |
| denial matrices executed | 0 |
| open decisions recorded | 1 |
| contract models added by this phase | 18 |

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

`tools/validate_phase3.py` exits 1 against this tree. Phase 3 is not accepted: criteria 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21 are GAPs. That is the honest state of the phase, not a broken gate. Read the Gaps section of `negative-case-matrix.md` for what closes it.

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
| infra/admission-state-machine.yaml | sha256:35cf140df478e1ff5bbe0629e0d08104d80a7b446a6d3051c5f4647319d3489f |
| infra/batch-compute.yaml | sha256:a8e41992fdfb907db51b70f8dd2e2b59886081c8c77ecfbdefee39b4114b7ccc |
| infra/batch-events.yaml | sha256:d12ae9b7094b4a3455093af5018ea84ab8e3876d13bd1524454e70516ae3c3f3 |
| infra/batch-network.yaml | sha256:77bc3b6b1b7fc41adea201388e016aacd50f3c9bcec61454a59f333931183d88 |
| infra/iam/admission-service-roles.yaml | sha256:829c2a55e96e74dabc3da803b118615701fdec0da6305569b860102a9c6676dc |
| infra/iam/batch-roles.yaml | sha256:7c70b84b97d75600d752bbe32ae0705de584b992962b756b500b4dac0fb0a0a0 |
| infra/iam/infra-deployer-role.yaml | sha256:feb32d24f24956cbab5a5fe47882557554ede17c811845214e3cf3c9d6a3af32 |
| infra/iam/lifecycle-lambda-role.yaml | sha256:f23ec64e8e2cd611eedb4748cbd7ae7ed26a1cd74d9c8569d6d5af93ed28f53c |
| infra/outputs-bucket.yaml | sha256:7ea6c087d7e2e4f5531e1ccf88120d9836ad7591fec30c52e762b56c76347fe2 |

## Known limitations

- Nothing has been deployed and nothing has run. This is the limitation every other one below is a consequence of: no Phase 3 stack has been applied to the account, no Batch job has ever run in it, and no lifecycle record exists. Seven documents in this bundle are therefore empty with a reason rather than absent.
- Check 1 -- that a valid run reaches SUCCEEDED -- is a gap, and it is the phase's central claim. A reviewer should read this bundle as a description of a system that has been built and not yet operated.
- Check 20 is covered on a committed CloudFormation template, which is what the repository asks the account for rather than what the account holds. The four Phase 3 roles have no capture at all, so the comparison that catches a role widened in the console does not run for any of them, and check 14 is a gap for that reason.
- Check 22 is covered because the open-decisions entry is gone and the answer is enforced in code and configuration this repository commits. Nothing here says the enforcement has ever refused a real submission.
- A compute environment reporting VALID would not be evidence that a job can run. Batch does not fail a job it cannot place; it waits. Only a job observed in RUNNING and then SUCCEEDED establishes placement, egress and the image pull, which is why checks 1 and 15 are separate.
- The account measurements this bundle's networking and method documents are rendered from expire thirty days after they were observed, and this generator refuses to build once they do. Nothing about the account will have changed on that date; what will have lapsed is anybody's knowledge of it.
- The recorded role digests are over four templates nobody has deployed. They catch a template widened between now and the deploy, and they say nothing about the account, because there is no account state to say anything about yet.
- The two denial matrices are written and have never run. Until a real session answers them, every claim about what these roles cannot do rests on a document rather than on a refusal.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded in the bundle index identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a template changes. Re-run `uv run python tools/build_phase3_proof.py` and read the diff before accepting a phase gate.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
