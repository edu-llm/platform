# Phase 0 proof bundle

Phase: phase-0
Bundle schema version: 1
Source commit: b73cdd73bcc73964de71ebfa8d527da469d1ba6a
Generated: 2026-08-04T23:45:05+00:00

This bundle exists so that a reviewer can decide whether Phase 0 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase0_proof.py` at generation time.

## Contents

- `unit-test-report.md` — summarised pass and fail counts, per fixture and for the whole suite, with the commands to reproduce them.
- `negative-case-matrix.md` — each of the thirteen Phase 0 acceptance criteria mapped to the tests cited for it, by node id, with every gap and every deferral stated. Read this one first.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of every fixture, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — every contract model, its schema version, and its structural digest, split into repository configuration and runtime records.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 5040 |
| suite tests executed | 4847 |
| suite passed | 4847 |
| suite failed | 0 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 254 |
| matrix node ids passed | 254 |
| matrix node ids failed | 0 |
| phase criteria | 13 |
| criteria COVERED | 12 (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13) |
| criteria DEFERRED | 1 (10) |
| criteria GAP (each one fails the gate) | 0 |
| related recorded deferrals | 1 (D1) |
| fixtures with recorded digests | 9 |
| contract models inventoried | 149 |
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
| CheckpointSurvey | 1 |
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
| TwoRunComparison | 1 |

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
| config/organization.yaml | sha256:93dbb74ea3f44c5b1bf9d00d505acfa888a6e78bf456550d0202989571d9c56a |
| config/policy.yaml | sha256:093eb2bc7e52c1e452538928f3579bde26e6dd425018516b1211268c944247ad |
| config/workload-catalog.yaml | sha256:e3a5fd4ab03d74866a9d7c5307a14e36639d30aea3553205d569a06989ae7e9a |
| fixtures/authorization/admin-exception.yaml | sha256:c49db36e999df3cefd9d3e95127fe947479d121ca6b6b450839e9f0c8ae8144f |
| fixtures/authorization/lead-self-authorization.yaml | sha256:4879d23dffb1ae9c3d81cdb35e382a1636b0642f75b7cc2afe0f8a54cebc562b |
| fixtures/authorization/member-approval.yaml | sha256:d6da4e22145165f4233c7c150789d2eea7fb9a7d8cf8546e72812bb971bd4dc4 |
| fixtures/manifests/cpu-routine.yaml | sha256:d862931f6b4bcbb2eb58ac5279d619d333353c693cf79129de0020cdf40fd66d |
| fixtures/manifests/gpu-exception.yaml | sha256:a2b15bb3ee4bbd76734b50209a507303c94c7fed5146be5d423dcb66bdf41165 |
| fixtures/manifests/gpu-routine.yaml | sha256:9ab17092987083a2f4ffabe42f5ad1bb938e0178b6e333f7cb2d9c3aa0036de5 |
| fixtures/manifests/multiseed-routine.yaml | sha256:c176bbf3e41e9645270e735ca3704e994a8839a1b5063ee195cefadfed69198d |
| fixtures/manifests/olmo-branch-routine.yaml | sha256:4ca3591efed038502e454184f05e9660659c89e48eb0a9283ac0a2fffdd90c45 |
| fixtures/manifests/sagemaker-routine.yaml | sha256:aaeda5bc0ebb3804e8b79a9f4da750e25faad8dc4c52d22c49c55ce0561abe7e |
| schemas/batch-job-binding.schema.json | sha256:41a20845192e959dd91b32f87d57da0cf3b04e5a2177e0200f27aec2cece901c |
| schemas/checkpoint-manifest.schema.json | sha256:f51cc8c500c81cd1d286dc86c59ff5f24b09a640127034f1527494251d76a07e |
| schemas/datasets.schema.json | sha256:12bd81126ee022c25cc0b8bc3b8e35b446f3c672a840cf13afcc88e4e690823c |
| schemas/decision-record.schema.json | sha256:f0290fb71a610aa1cc133600c28caf55e388535ddf1153ae62cffcd606499cd5 |
| schemas/image-exceptions.schema.json | sha256:e57e7388a87f331a31c9f1e871065306421772bf1da3a043b46f121bde1ffb17 |
| schemas/intent-record.schema.json | sha256:17ffacfd445b5ec8b2ebc585e994b2a3362e231a4215c09a2b030cac93ea2b12 |
| schemas/lifecycle-event.schema.json | sha256:f747e330743b4f471021b38e161d26e24ecda8fb47ab02b08939ee298a1921ab |
| schemas/logical-run.schema.json | sha256:898f1d6b338ea810a75c0614035a49e0812147aef7816037c97447a602d37688 |
| schemas/organization.schema.json | sha256:37c30582f008b541fe11a1403f5311026ae908d98e8821b4ed6842c3d4365e66 |
| schemas/policy.schema.json | sha256:8a99f30cbfad406c46853fc115e3cdba9e380f793bf0b34bc7bc231df8eff6ff |
| schemas/repositories.schema.json | sha256:ee5ef9172b9ab89aa0965cefda9d86fda855c4cd3f0eeda41ab50551327ff68e |
| schemas/result-manifest.schema.json | sha256:38421f82a8c861df341fcfd514e9a1bb621f7ced55258b20061d310d0c61b7f9 |
| schemas/run-manifest.schema.json | sha256:7f6795c9a7a246b2670bc181f19f07ef16086b233b6d14d1d8def41971b04769 |
| schemas/scheduler-attempt.schema.json | sha256:91984a9fb1f7f9150f7799dc337807bd14b93b50908a56e0e230391546c9c4ac |
| schemas/submission-inputs.schema.json | sha256:02585ab3655cb95c5cf574c9e4906f6709ad54973db5cf353b868cd747880fe7 |
| schemas/workload-catalog.schema.json | sha256:2345ae1bd3a2985fa55bdb2c7b7e8bba4127c925ba43f086929f443fa32d1384 |

## Known limitations

- 14 of 17 compute profiles are provisioned: cpu-32vcpu, gpu-1xa10g, gpu-1xl4, gpu-1xl40s, gpu-1xt4, gpu-4xa10g, gpu-4xl4, gpu-4xl40s, gpu-4xt4, gpu-8xa100, gpu-8xa10g, gpu-8xl4, gpu-8xl40s, gpu-8xt4. Every other profile is priced and dated but carries provisioned: false, so resolve_compute_profile_for_execution refuses it. Phase 0 proves pricing and classification for all of them and that nothing can run is no longer true of the whole catalog.
- Approval scope is organization. Any team lead may approve any member's routine submission. Check D1 in the negative-case matrix is deferred for this reason.
- The secret scan applied to this bundle masks its own content digests before scanning. A 64-character hexadecimal sha256 digest and a 40-character hexadecimal commit SHA both match the generic long-credential patterns in evidence.py, so the two exact token shapes this bundle emits are replaced with placeholders and everything else is scanned unchanged. No other exemption is applied.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py, tests/test_phase5_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`, which is the command this bundle asks the reviewer to run.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded in the bundle index identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a contract changes. Re-run `uv run python tools/build_phase0_proof.py` and read the diff before accepting a phase gate. The recorded fixture digests are the one part that fails loudly on its own when it goes stale.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
