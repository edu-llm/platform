# Phase 0 proof bundle

Phase: phase-0
Bundle schema version: 1
Source commit: 782b4ea69339407acfd4281dd9a00206ae35459b
Generated: 2026-07-27T05:09:00+00:00

This bundle exists so that a reviewer can decide whether Phase 0 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase0_proof.py` at generation time.

## Contents

- `unit-test-report.md` — summarised pass and fail counts, per fixture and for the whole suite, with the commands to reproduce them.
- `negative-case-matrix.md` — each of the thirteen Phase 0 acceptance criteria mapped to the tests cited for it, by node id, with every gap and every deferral stated. Read this one first.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of every fixture, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — every contract model, its schema version, and its structural digest, split into repository configuration and runtime records.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 2705 |
| suite tests executed | 2620 |
| suite passed | 2620 |
| suite failed | 0 |
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
| contract models inventoried | 80 |
| JSON Schema files exported | 9 |

## Contract versions

| contract | schema_version |
| --- | --- |
| AdmissionDenialMatrix | 1 |
| AuthorizationScenario | 1 |
| CheckpointManifest | 1 |
| DatasetRegistry | 1 |
| DatasetRelease | 1 |
| DecisionRecord | 1 |
| ImageProvenance | 1 |
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
| config/organization.yaml | sha256:6236a7d673c3c2a534229e81a77c4fa25f2d93ef820239afd6b705b7896326bf |
| config/policy.yaml | sha256:92eaa1184e11dfce0bbd7e9aaddd168f8bc15a7075f2d9270b5ad8f32ff0148d |
| config/workload-catalog.yaml | sha256:31eacaa510964426782f8e5f8c7be431880538739ea3c5c7a94cc66340621ca9 |
| fixtures/authorization/admin-exception.yaml | sha256:b7e70ff952819e51c4c033e7655db488fee51b0f0ea08c8a7a3b478c7b3efece |
| fixtures/authorization/lead-self-authorization.yaml | sha256:2571d0f48577986f6bf7c7e0491ca791ff6e9eb7bac0444fbee2751bc1959ad2 |
| fixtures/authorization/member-approval.yaml | sha256:55c4233cf0e037acc74e75a02943484a5df47c0bd5a24fd423eb95b415997db5 |
| fixtures/manifests/cpu-routine.yaml | sha256:57df534749b3ef621c67d7b7abd9d5d6822848ec2776953aa2f6c28f9a29b2fc |
| fixtures/manifests/gpu-exception.yaml | sha256:ac493344c543e7ef49ac5368bb19b9f391afedf8675aeb29650ebd2b7f207db9 |
| fixtures/manifests/gpu-routine.yaml | sha256:c32d8c289c1f2c6b568bda5cc43a8a35b3b52ff0c435a214b4cefd6f82d1dd6f |
| fixtures/manifests/multiseed-routine.yaml | sha256:0241fc78bc6e165b4f06c0e0223607f39f4f1a4fee4700f56348b488619743bd |
| fixtures/manifests/olmo-branch-routine.yaml | sha256:111be4025328f27d4f9d6d4a8b204bc5c26322cf2e4b66f4207a4ecc70d3db86 |
| fixtures/manifests/sagemaker-routine.yaml | sha256:3a7277b614e990f9e1a827f272ff917603b8059f831abae1d4342d15be12956a |
| schemas/datasets.schema.json | sha256:3f175ffd729d92eaba728bc459ca455a538bb5b5131840c11986f1548579ef9b |
| schemas/decision-record.schema.json | sha256:344b5620a7dfda70671857e64e578895edaabc4f1f3556c84eb0432d2b7e449c |
| schemas/intent-record.schema.json | sha256:39b40c1375c470efe47179c52e898562a7623c1a79f429de8f078f46cf3ddc8d |
| schemas/organization.schema.json | sha256:5caadb560ced32562f2673591717ce836f1831292cab16a4f9c3a22ba3c0c1f1 |
| schemas/policy.schema.json | sha256:29cc11db46a87f328c77f113179765c6aa2a873f10cb9922f2a28b9a1dc29f61 |
| schemas/repositories.schema.json | sha256:ee5ef9172b9ab89aa0965cefda9d86fda855c4cd3f0eeda41ab50551327ff68e |
| schemas/run-manifest.schema.json | sha256:62851f48df41a1dc270a525b44a8ef01eab660af9d5b60030d6c0a8776e196f2 |
| schemas/submission-inputs.schema.json | sha256:0cbe9a1bc42474266c0d217cf4dab4031fb3720408f1fe347a12e62e4abc1578 |
| schemas/workload-catalog.schema.json | sha256:4039ead3f77c0949db2a701dae90461788ed6856838075a1f223f3d4b853fa06 |

## Known limitations

- No compute profile is provisioned. All 12 profiles in the workload catalog are priced and dated but carry provisioned: false, so resolve_compute_profile_for_execution refuses every one of them. Phase 0 proves pricing and classification, not that anything can run.
- Team bindings are empty. OrganizationInventory.team_bindings.teams is an empty tuple, so no submitter or lead is bound to a team. Every team-scoped rule is therefore either deferred or unenforceable today.
- Approval scope is organization. Any team lead may approve any member's routine submission. Check D1 in the negative-case matrix is deferred for this reason.
- Cross-team attribution is implemented but cannot reject anything yet. Every decision records the claimed team and a team_verified flag, and a submitter naming a team they do not belong to is denied as soon as team bindings exist. With bindings empty, every shipped decision records team_verified: false, which is the audit record's way of saying the attribution was accepted unchecked. Check 9 is deferred for this reason: no test can show a shipped rejection that the shipped configuration cannot produce.
- The secret scan applied to this bundle masks its own content digests before scanning. A 64-character hexadecimal sha256 digest and a 40-character hexadecimal commit SHA both match the generic long-credential patterns in evidence.py, so the two exact token shapes this bundle emits are replaced with placeholders and everything else is scanned unchanged. No other exemption is applied.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`, which is the command this bundle asks the reviewer to run.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded in the bundle index identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a contract changes. Re-run `uv run python tools/build_phase0_proof.py` and read the diff before accepting a phase gate. The recorded fixture digests are the one part that fails loudly on its own when it goes stale.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
