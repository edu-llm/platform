# Phase 0 proof bundle

Phase: phase-0
Bundle schema version: 1
Source commit: 1bfbacb839a315a5e95529287dc10c938bb19756
Generated: 2026-08-02T15:03:51+00:00

This bundle exists so that a reviewer can decide whether Phase 0 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase0_proof.py` at generation time.

## Contents

- `unit-test-report.md` — summarised pass and fail counts, per fixture and for the whole suite, with the commands to reproduce them.
- `negative-case-matrix.md` — each of the thirteen Phase 0 acceptance criteria mapped to the tests cited for it, by node id, with every gap and every deferral stated. Read this one first.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of every fixture, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — every contract model, its schema version, and its structural digest, split into repository configuration and runtime records.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 4500 |
| suite tests executed | 4307 |
| suite passed | 4305 |
| suite failed | 2 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 255 |
| matrix node ids passed | 255 |
| matrix node ids failed | 0 |
| phase criteria | 13 |
| criteria COVERED | 12 (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13) |
| criteria DEFERRED | 1 (10) |
| criteria GAP (each one fails the gate) | 0 |
| related recorded deferrals | 1 (D1) |
| fixtures with recorded digests | 9 |
| contract models inventoried | 144 |
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
| config/organization.yaml | sha256:4b3d6cbdc0c080dc01b36918401f720f9cfc87d821a6cfa61787fdc7020d80cf |
| config/policy.yaml | sha256:9217d17abbb34aa85812d4796220288e1043f1e9ca1482b93a517da0687e6e51 |
| config/workload-catalog.yaml | sha256:8a2c8c6b6880c0719d51859b53e64ec004234aeb276568d8cf4539186dbe7b97 |
| fixtures/authorization/admin-exception.yaml | sha256:34aa5778205f2d40629c8ea769e443984ab0cda901711d8ce943da4a08adeb0c |
| fixtures/authorization/lead-self-authorization.yaml | sha256:7616de94f8a4ffb338e358163fef4240ed99bb9e59f90d5c5c566ed62bd4f2b0 |
| fixtures/authorization/member-approval.yaml | sha256:d6da4e22145165f4233c7c150789d2eea7fb9a7d8cf8546e72812bb971bd4dc4 |
| fixtures/manifests/cpu-routine.yaml | sha256:38bb87c5171d9bb9eb6ba74172d07cd975477c96cdbf6493c17a1ca1d3628467 |
| fixtures/manifests/gpu-exception.yaml | sha256:c49687a49020b47f8e5de88fd736cb0dc49672df471c7d345d1d70089edb47be |
| fixtures/manifests/gpu-routine.yaml | sha256:e83601657638685d58a590ac6a272777672ad184a39f9edc37a319c8e415fbdf |
| fixtures/manifests/multiseed-routine.yaml | sha256:e4775ba478e52ecdb710d2d7a70cfc75177acfb3724e2b8f7d787a4f2223202d |
| fixtures/manifests/olmo-branch-routine.yaml | sha256:f552e7a8e2b8abb8ec9b87da5333dc2e61ec7e24150cfa7d2c7f45dfff0fab1f |
| fixtures/manifests/sagemaker-routine.yaml | sha256:c1cee5e54c60a700ac0ab0260b3249fbe1f37e807cf52ac381d58b5691cb0248 |
| schemas/batch-job-binding.schema.json | sha256:41a20845192e959dd91b32f87d57da0cf3b04e5a2177e0200f27aec2cece901c |
| schemas/checkpoint-manifest.schema.json | sha256:f51cc8c500c81cd1d286dc86c59ff5f24b09a640127034f1527494251d76a07e |
| schemas/datasets.schema.json | sha256:12bd81126ee022c25cc0b8bc3b8e35b446f3c672a840cf13afcc88e4e690823c |
| schemas/decision-record.schema.json | sha256:852ee127b92bc2a592f5da86972e32ed50e949548951bd66fed94b237b6ce821 |
| schemas/image-exceptions.schema.json | sha256:e57e7388a87f331a31c9f1e871065306421772bf1da3a043b46f121bde1ffb17 |
| schemas/intent-record.schema.json | sha256:39b40c1375c470efe47179c52e898562a7623c1a79f429de8f078f46cf3ddc8d |
| schemas/lifecycle-event.schema.json | sha256:f747e330743b4f471021b38e161d26e24ecda8fb47ab02b08939ee298a1921ab |
| schemas/logical-run.schema.json | sha256:898f1d6b338ea810a75c0614035a49e0812147aef7816037c97447a602d37688 |
| schemas/organization.schema.json | sha256:37c30582f008b541fe11a1403f5311026ae908d98e8821b4ed6842c3d4365e66 |
| schemas/policy.schema.json | sha256:e4030f6190b01c200a16bf998da52f4e25951685fc3b1df5e81874a3ebd97722 |
| schemas/repositories.schema.json | sha256:ee5ef9172b9ab89aa0965cefda9d86fda855c4cd3f0eeda41ab50551327ff68e |
| schemas/result-manifest.schema.json | sha256:5bd07bde5ac6c86323878915dd684dafa2e838fa20b53d78554074159563cb26 |
| schemas/run-manifest.schema.json | sha256:62851f48df41a1dc270a525b44a8ef01eab660af9d5b60030d6c0a8776e196f2 |
| schemas/scheduler-attempt.schema.json | sha256:91984a9fb1f7f9150f7799dc337807bd14b93b50908a56e0e230391546c9c4ac |
| schemas/submission-inputs.schema.json | sha256:741c31071d945861c98abaec766ef5533e6a35cd338306e1d0eff9fb8c8ea845 |
| schemas/workload-catalog.schema.json | sha256:4039ead3f77c0949db2a701dae90461788ed6856838075a1f223f3d4b853fa06 |

## Known limitations

- 11 of 13 compute profiles are provisioned: cpu-32vcpu, gpu-1xa10g, gpu-1xl4, gpu-1xt4, gpu-4xa10g, gpu-4xl4, gpu-4xl40s, gpu-4xt4, gpu-8xa100, gpu-8xa10g, gpu-8xh100. Every other profile is priced and dated but carries provisioned: false, so resolve_compute_profile_for_execution refuses it. Phase 0 proves pricing and classification for all of them and that nothing can run is no longer true of the whole catalog.
- Approval scope is organization. Any team lead may approve any member's routine submission. Check D1 in the negative-case matrix is deferred for this reason.
- The secret scan applied to this bundle masks its own content digests before scanning. A 64-character hexadecimal sha256 digest and a 40-character hexadecimal commit SHA both match the generic long-credential patterns in evidence.py, so the two exact token shapes this bundle emits are replaced with placeholders and everything else is scanned unchanged. No other exemption is applied.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py, tests/test_phase5_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`, which is the command this bundle asks the reviewer to run.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded in the bundle index identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a contract changes. Re-run `uv run python tools/build_phase0_proof.py` and read the diff before accepting a phase gate. The recorded fixture digests are the one part that fails loudly on its own when it goes stale.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
