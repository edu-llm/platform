# Phase 2 proof bundle

Phase: phase-2
Bundle schema version: 1
Source commit: 0f073e27a65e71d2f8d12d1219910866aa0f4806
Generated: 2026-07-29T01:53:38+00:00

This bundle exists so that a reviewer can decide whether Phase 2 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase2_proof.py` at generation time. It is not done, and the Result table below says by how much.

## Read this first

`tools/validate_phase2.py` exits 1 against this tree. Phase 2 is not accepted: criteria 2, 3, 6, 7, 9, 11, 12, 14, 19 are GAPs. That is the honest state of the phase, not a broken gate. Read the Gaps section of `negative-case-matrix.md` for what closes it.

This run evaluated 22 acceptance criteria: twelve criteria are covered, one criterion is deferred, and nine criteria are gaps.

| # | check that is not satisfied |
| --- | --- |
| 2 | Member submission without lead approval is rejected. |
| 3 | Any team lead approval succeeds while approval_scope is organization. |
| 6 | Wrong repository, ref, audience, or manifest hash cannot assume or use the role. |
| 7 | A job that omits the approval environment cannot assume the admission role, even from main. |
| 9 | A member cannot approve their own submission. |
| 11 | The approver sees submitter, team, repository, branch, short SHA, image digest, dataset release, compute profile and rate, the worst-case cost arithmetic, the classification, and the exceeded bound before the gate opens. |
| 12 | Duplicate execution names do not create duplicate intent records. |
| 14 | Admission failure does not create compute or partial accepted state. |
| 19 | The admission role holds no S3 permission, and the Lambda role holds none either. |

**Every one of those is a run that happened and was never captured, or a role nobody compared to its template.** Phase 2's path went end to end on 2026-07-27. What is committed is the state those runs left behind -- the lineage objects, the execution list, the GitHub configuration -- and that is what the covered checks rest on. A statement that can only be established by evidence nobody committed is open here however convincing the run was to whoever watched it, because the gate executes tests and a test that reads nothing proves nothing.

## Contents

- `negative-case-matrix.md` — each of the 22 Phase 2 acceptance criteria mapped to the tests cited for it, by node id, with every gap stated. Read this one first.
- `lineage-record-evidence.md` — every object in the lineage store with its VersionId and S3-attested checksum, and the decision beside every intent, joined by run id.
- `admission-execution-evidence.md` — every execution the admission state machine has run, and how each one ended.
- `approval-gate-evidence.md` — both approval environments as GitHub is configured, the secret and variable names at every level, and the three artifacts about a *run* that nobody captured.
- `authorization-matrix.md` — who may release what, evaluated against the shipped policy and roster while this bundle was generated.
- `admission-denial-matrix.md` — the six actions the admission session must not be able to take, how each probe is aimed so that being permitted would change nothing, and what choosing a probe has cost. The matrix has run and holds no committed refusal.
- `open-decisions.md` — what this repository has surfaced and not answered, and why none of D1 to D9 is among it.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of what each Phase 2 role template grants, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — the contract models Phase 2 added, with their structural digests.
- `unit-test-report.md` — summarised pass and fail counts, per module and for the whole suite, with the commands to reproduce them.
- `oidc-session-evidence.md` and `deployed-role-drift.md` — empty. Each says what it records, what would fill it, and which checks are waiting on it.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 3274 |
| suite tests executed | 3112 |
| suite passed | 3112 |
| suite failed | 0 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 562 |
| matrix node ids passed | 562 |
| matrix node ids failed | 0 |
| phase criteria | 22 |
| criteria COVERED | 12 (1, 5, 8, 10, 13, 15, 16, 17, 18, 20, 21, 22) |
| criteria DEFERRED | 1 (4) |
| criteria GAP (each one fails the gate) | 9 (2, 3, 6, 7, 9, 11, 12, 14, 19) |
| role templates with recorded digests | 3 |
| roles compared to a capture | 0 |
| admission executions captured | 7 |
| lineage objects captured | 10 |
| submissions accepted, of those captured | 4 |
| denial matrices captured | 0 |
| CloudTrail records captured | 0 |
| captures expire | 2026-08-26 |
| open decisions recorded | 0 |
| contract models added by this phase | 12 |

## Verification commands

Run these from the repository root.

```
uv run pytest -q
uv run ruff check .
uv run mypy
uv run python tools/export_schemas.py
uv run python tools/validate_phase2.py
uv run python tools/build_phase2_proof.py
```

## Inputs measured

Digests of the files this bundle was generated from, so a reviewer can confirm the bundle describes the tree in front of them. Verify with `shasum -a 256 <file>`.

| file | digest |
| --- | --- |
| .github/workflows/deploy-phase2-admission.yml | sha256:bed80da61dcae3ec6b6325b111e59a519add443fad6a2760f76e346741c0f687 |
| .github/workflows/submit-run.yml | sha256:389802db5f617673f77e7c9c7a918d0d941ea32c11428d02087f5b67fde37dae |
| config/organization.yaml | sha256:bb6b836e679464f5870225439664b9f6dacbed2e0fd39d80fbfcc47751c720a1 |
| config/policy.yaml | sha256:8efa2f00527f9ad1677ed27452a2b6093a6a8c9e8190cf3e0a583b0f68787b39 |
| fixtures/authorization/admin-exception.yaml | sha256:4ad48b8ecd405d11428cf446f74d0a8aeabf904365f3fee7b599b6a7ed0b6fa0 |
| fixtures/authorization/lead-self-authorization.yaml | sha256:0e65da633a3880b11e5f14d380d54497a2be7124da1121f34ec3d21d4b4e83d0 |
| fixtures/authorization/member-approval.yaml | sha256:a39cbdcbec68bf2fd8067f624ee1cf08aac008757e9fdaf69d812b76ea44e2de |
| fixtures/evidence/phase-2/executions.sanitized.json | sha256:d4ca6d9a4038e99a3c820cc4f743f7cb197ecaaf9874454d2d8a7599ad508a85 |
| fixtures/evidence/phase-2/github/environments.sanitized.json | sha256:90daedee358e2abb9d3b8d00d5855a0daf3fceae4a3d5b1125a35c38ad842f13 |
| fixtures/evidence/phase-2/github/secrets.sanitized.json | sha256:0ce26f6c0234a910099c4c869d5aa789cf36ba939a9ad3b4ad12c00b624ee9d4 |
| fixtures/evidence/phase-2/lineage.sanitized.json | sha256:fa56d78a4ec30a6e7f8cf66ed1ca52c795f9061f5af4eb105b9ddeb190553b5f |
| fixtures/evidence/phase-2/lineage/records/decision/run_019fa446-8a4e-7094-9e29-d44fffbd2491.json | sha256:0800beff17f8017340a0cd0840ef7a515ca6fdbeaa2983ddf88b6fd5d7b5bcbd |
| fixtures/evidence/phase-2/lineage/records/decision/run_019fa468-c9b5-706a-8849-87c1d0b5befb.json | sha256:0df3f1f05e9b45c4df017f03f5f0c7de1bb23f9094f018d3e6fb38a01f2392e8 |
| fixtures/evidence/phase-2/lineage/records/decision/run_019fa46a-5478-70ea-aab6-28de23c41f7f.json | sha256:9d057f51c7029ec591376f87096108af66aa24747c0fb1b3e1867890ddcee0c1 |
| fixtures/evidence/phase-2/lineage/records/decision/run_019fa471-0173-7050-a41b-22ca01969b52.json | sha256:e5bbdcab20eb169667a2c5c6f130081cb53f315dc545bff5ecccd2374ec46b3d |
| fixtures/evidence/phase-2/lineage/records/decision/run_019fa4c0-390d-7081-b539-08d9ff6b58be.json | sha256:72c02a559373f03818b069a00071bfb33421d373425698b0af85d64393709759 |
| fixtures/evidence/phase-2/lineage/records/intent/run_019fa446-8a4e-7094-9e29-d44fffbd2491.json | sha256:c9b0b4ade2a88077a854056e68473659b1afc9185c5f203a37018fba3c15fb91 |
| fixtures/evidence/phase-2/lineage/records/intent/run_019fa468-c9b5-706a-8849-87c1d0b5befb.json | sha256:edba6252123a2d7281dd98b37459943629e32cf80f499fdb22d0d41051370b98 |
| fixtures/evidence/phase-2/lineage/records/intent/run_019fa46a-5478-70ea-aab6-28de23c41f7f.json | sha256:89ad215366a6c1e6177e24a04de4db99f0687529175d9bccae39ef3dfadd4ba1 |
| fixtures/evidence/phase-2/lineage/records/intent/run_019fa471-0173-7050-a41b-22ca01969b52.json | sha256:e1e6f323427445671f554e4af5d23ea7a2020b578d926d6e934d3144604be0ba |
| fixtures/evidence/phase-2/lineage/records/intent/run_019fa4c0-390d-7081-b539-08d9ff6b58be.json | sha256:be35e6f2797c899e1a5b44544ed64d053b125f6d17cb47fb0cc6932f2fd976a5 |
| infra/admission-state-machine.yaml | sha256:7f29dcd4ed50e1f42cc3368bbbcaa3b5cd932b21f8bb63f99f065a59ba2d0533 |
| infra/iam/admission-role.yaml | sha256:e5e5b2db0ae9b7d1cdf8d46ab3568f35279a33344fe1ec337cf69e82b9c9e841 |
| infra/iam/admission-service-roles.yaml | sha256:6aeb0d2091c79dd6c4c4a42fed0796c612e88438655db97fcbc0c94860e0893d |
| infra/iam/infra-deployer-role.yaml | sha256:596abb25126c0f10d734cbecd01bec08495cac63b19a81ab46870318504774ac |
| infra/lineage-bucket.yaml | sha256:9bad0303f92659a47caa1fe57f1bf10c77a3b11a11a9f58f21b1c341befd761d |

## Known limitations

- The path ran and the runs were not captured. This is the limitation the nine open checks are consequences of: on 2026-07-27 a lead released a routine run, an exception routed to the admin gate, a duplicate execution name was refused, a tampered hash was refused, and a six-probe denial matrix came back refused on every entry. What is committed is the state those runs left behind rather than the runs, and a criterion that can only be established by evidence nobody committed is open however convincing the run was to whoever watched it.
- Check 7 -- that a job omitting the approval environment cannot assume the admission role -- is a gap, and it is the strongest thing this phase produced. The `deny-unapproved` job succeeded on every live run, meaning STS refused the ref-based subject, and none of it is in this repository.
- Check 6 is a gap and check 19 is a gap, on the same missing artifact. Both rest on committed CloudFormation templates, which are what the repository asks the account for rather than what the account holds. The three Phase 2 roles were deployed from a laptop and no capture has been compared against any of them, so the comparison that catches a role widened in the console does not run for them.
- Check 11 is a gap and cannot be closed by capturing harder. It asks for the branch, and `RunManifest` has no branch field: every source revision is a full commit SHA, because a branch is mutable and a commit is not. Closing it means either carrying the branch as advisory metadata that nothing authorizes on, or amending the check with that reason written down.
- Check 21 states what a decision record carries and is covered on that reading alone. It does not claim AWS verified the actor. The approver reaches AWS because the submitting job read it from the GitHub approvals API and passed it along; no OIDC claim names who approved, so a compromised runner could misreport it. The gate itself cannot be skipped.
- Every committed Phase 2 capture is a statement about one moment. They stop loading on 2026-08-26, this generator refuses to build from that date, and every check resting on them is open again. Nothing about GitHub or the lineage store will have changed; what will have lapsed is anybody's knowledge of them. Re-capturing is a read of the account rather than another run.
- The three recorded role digests describe committed templates and say nothing about the account. They catch a template widened between now and the next capture, which is the only thing standing in for a drift comparison that cannot run yet.
- The authorization matrix is an evaluation rather than an observation. Every row in it is `evaluate_authorization` run against the shipped policy and roster at generation time, which says what the platform decides and nothing about who GitHub let through.
- **There is no rollback result here, and the master plan asks every bundle for one.** The rollback is written down -- remove the reviewers from both environments, redeploy the admission role granting nothing, disable the submission workflow, leave the lineage bucket and the state machine alone -- and it has been described rather than rehearsed. Section 6 of the Phase 2 plan does not list a document for it and nothing in `src/edullm_platform/phase2_criteria.py` covers it, so this bundle would have passed over the omission silently. Recording it here is the alternative to that. What a rehearsal has to establish is that a submission dispatched after step 1 does not reach AWS, and that a record written before step 1 is still readable afterwards.
- The `S3.S3Exception` this phase reads as a duplicate-write refusal is the generic name for every unmodelled S3 error. It does not distinguish a genuine already-exists from a transient fault, because the 412 and its precondition message appear only in the `Cause`, which no `ErrorEquals` can match.
- The secret scan applied to this bundle masks its own content digests before scanning, and the S3 checksums here are rewritten from base64 into that hex form for the same reason. Both are presentation changes over bytes that are still fully recorded; no other exemption is applied.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded in the bundle index identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a template changes. Re-run `uv run python tools/build_phase2_proof.py` and read the diff before accepting a phase gate.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
