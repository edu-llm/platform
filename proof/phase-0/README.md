# Phase 0 proof bundle

Phase: phase-0
Bundle schema version: 1
Source commit: dd5c6eb50c0f07f9ff7c616fe91d99b3e0f5ef40
Generated: 2026-08-01T07:27:49+00:00

This bundle exists so that a reviewer can decide whether Phase 0 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase0_proof.py` at generation time.

## Contents

- `unit-test-report.md` — summarised pass and fail counts, per fixture and for the whole suite, with the commands to reproduce them.
- `negative-case-matrix.md` — each of the thirteen Phase 0 acceptance criteria mapped to the tests cited for it, by node id, with every gap and every deferral stated. Read this one first.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of every fixture, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — every contract model, its schema version, and its structural digest, split into repository configuration and runtime records.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 3923 |
| suite tests executed | 3730 |
| suite passed | 3726 |
| suite failed | 4 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 254 |
| matrix node ids passed | 254 |
| matrix node ids failed | 0 |
| phase criteria | 13 |
| criteria COVERED | 11 (1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13) |
| criteria DEFERRED | 2 (9, 10) |
| criteria GAP (each one fails the gate) | 0 |
| related recorded deferrals | 1 (D1) |
| fixtures with recorded digests | 9 |
| contract models inventoried | 141 |
| JSON Schema files exported | 16 |

## Contract versions

| contract | schema_version |
| --- | --- |
| AccountMeasurements | 1 |
| AdmissionDenialMatrix | 1 |
| AuthorizationScenario | 1 |
| BatchDenialMatrix | 1 |
| BatchJobBinding | 1 |
| CheckpointManifest | 1 |
| DatasetRegistry | 1 |
| DatasetRelease | 1 |
| DecisionRecord | 1 |
| ExecutionTargetCatalog | 1 |
| ImageProvenance | 1 |
| ImageScanExceptionRegistry | 1 |
| ImageScanSummary | 1 |
| IntentRecord | 1 |
| LifecycleEvent | 1 |
| LocalRebuildComparison | 1 |
| LogicalRun | 1 |
| PublisherDenialMatrix | 1 |
| ResultManifest | 1 |
| RunManifest | 1 |
| SchedulerAttempt | 1 |
| SourceIdentity | 1 |

Repository-configuration contracts are versioned by their exported JSON Schema rather than by a field. See `schema-compatibility.md`.

## Verification commands

Run these from the repository root.

```
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run python tools/export_schemas.py
uv run python tools/validate_phase0.py
uv run python tools/build_phase0_proof.py
```

`tools/validate_phase0.py` exits 0 against this tree: every phase criterion is covered or explicitly deferred, and every operational inventory check passes.

## Inputs measured

Digests of the files this bundle was generated from, so a reviewer can confirm the bundle describes the tree in front of them. Verify with `shasum -a 256 <file>`.

| file | digest |
| --- | --- |
| config/organization.yaml | sha256:cea2d2123d2fb5794614e6a4b8a362c8aaa33626f20ae28a13cb673fadf4ac78 |
| config/policy.yaml | sha256:8efa2f00527f9ad1677ed27452a2b6093a6a8c9e8190cf3e0a583b0f68787b39 |
| config/workload-catalog.yaml | sha256:caccfdc8ecf5877119c2c39277f1a6b1bfe05e55b3f0c2a963d63e97d8479531 |
| fixtures/authorization/admin-exception.yaml | sha256:4ad48b8ecd405d11428cf446f74d0a8aeabf904365f3fee7b599b6a7ed0b6fa0 |
| fixtures/authorization/lead-self-authorization.yaml | sha256:0e65da633a3880b11e5f14d380d54497a2be7124da1121f34ec3d21d4b4e83d0 |
| fixtures/authorization/member-approval.yaml | sha256:a39cbdcbec68bf2fd8067f624ee1cf08aac008757e9fdaf69d812b76ea44e2de |
| fixtures/manifests/cpu-routine.yaml | sha256:38bb87c5171d9bb9eb6ba74172d07cd975477c96cdbf6493c17a1ca1d3628467 |
| fixtures/manifests/gpu-exception.yaml | sha256:e604a1f06ca7dba632dfa180a3f865500f54552447ca4adf7fdde1051f4b0874 |
| fixtures/manifests/gpu-routine.yaml | sha256:e649a5d4099a8c282d8870d36bcec6654a28156e1fc2f08703cccbccb261b8d6 |
| fixtures/manifests/multiseed-routine.yaml | sha256:e4775ba478e52ecdb710d2d7a70cfc75177acfb3724e2b8f7d787a4f2223202d |
| fixtures/manifests/olmo-branch-routine.yaml | sha256:4921b4616fa15393b1a7631d6a25bf63ca19b2ae4233a3a7b34a4d2305fae55c |
| fixtures/manifests/sagemaker-routine.yaml | sha256:d5d57c6970bb5c8c618cd70bfedc9dcb3d7fef8393592d9543a6b22dd6e2af2b |
| schemas/batch-job-binding.schema.json | sha256:41a20845192e959dd91b32f87d57da0cf3b04e5a2177e0200f27aec2cece901c |
| schemas/checkpoint-manifest.schema.json | sha256:f51cc8c500c81cd1d286dc86c59ff5f24b09a640127034f1527494251d76a07e |
| schemas/datasets.schema.json | sha256:28c781ffa1a7df6b999fce428aa8488aec5b8d34081089205e53d5fa6e797f36 |
| schemas/decision-record.schema.json | sha256:852ee127b92bc2a592f5da86972e32ed50e949548951bd66fed94b237b6ce821 |
| schemas/image-exceptions.schema.json | sha256:e57e7388a87f331a31c9f1e871065306421772bf1da3a043b46f121bde1ffb17 |
| schemas/intent-record.schema.json | sha256:39b40c1375c470efe47179c52e898562a7623c1a79f429de8f078f46cf3ddc8d |
| schemas/lifecycle-event.schema.json | sha256:f747e330743b4f471021b38e161d26e24ecda8fb47ab02b08939ee298a1921ab |
| schemas/logical-run.schema.json | sha256:898f1d6b338ea810a75c0614035a49e0812147aef7816037c97447a602d37688 |
| schemas/organization.schema.json | sha256:a66e0170cc0aafce3765b5e7b8b4062baf28c421792d6a2f9f5bb93272289d6f |
| schemas/policy.schema.json | sha256:e57443df3ebf18a1b1858a441f99aec2d41121a3f2110d05be30b530ff2b7f67 |
| schemas/repositories.schema.json | sha256:ee5ef9172b9ab89aa0965cefda9d86fda855c4cd3f0eeda41ab50551327ff68e |
| schemas/result-manifest.schema.json | sha256:7e7b6a5891444d9d13256202319f5be6e70addb81f0b0c077e5294c63529503b |
| schemas/run-manifest.schema.json | sha256:62851f48df41a1dc270a525b44a8ef01eab660af9d5b60030d6c0a8776e196f2 |
| schemas/scheduler-attempt.schema.json | sha256:91984a9fb1f7f9150f7799dc337807bd14b93b50908a56e0e230391546c9c4ac |
| schemas/submission-inputs.schema.json | sha256:741c31071d945861c98abaec766ef5533e6a35cd338306e1d0eff9fb8c8ea845 |
| schemas/workload-catalog.schema.json | sha256:4039ead3f77c0949db2a701dae90461788ed6856838075a1f223f3d4b853fa06 |

## Known limitations

- 2 of 12 compute profiles are provisioned: cpu-32vcpu, gpu-1xa10g. Every other profile is priced and dated but carries provisioned: false, so resolve_compute_profile_for_execution refuses it. Phase 0 proves pricing and classification for all of them and that nothing can run is no longer true of the whole catalog.
- Team bindings are empty. OrganizationInventory.team_bindings.teams is an empty tuple, so no submitter or lead is bound to a team. Every team-scoped rule is therefore either deferred or unenforceable today.
- Approval scope is organization. Any team lead may approve any member's routine submission. Check D1 in the negative-case matrix is deferred for this reason.
- Cross-team attribution is implemented but cannot reject anything yet. Every decision records the claimed team and a team_verified flag, and a submitter naming a team they do not belong to is denied as soon as team bindings exist. With bindings empty, every shipped decision records team_verified: false, which is the audit record's way of saying the attribution was accepted unchecked. Check 9 is deferred for this reason: no test can show a shipped rejection that the shipped configuration cannot produce.
- The secret scan applied to this bundle masks its own content digests before scanning. A 64-character hexadecimal sha256 digest and a 40-character hexadecimal commit SHA both match the generic long-credential patterns in evidence.py, so the two exact token shapes this bundle emits are replaced with placeholders and everything else is scanned unchanged. No other exemption is applied.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py, tests/test_phase5_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`, which is the command this bundle asks the reviewer to run.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded in the bundle index identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a contract changes. Re-run `uv run python tools/build_phase0_proof.py` and read the diff before accepting a phase gate. The recorded fixture digests are the one part that fails loudly on its own when it goes stale.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
