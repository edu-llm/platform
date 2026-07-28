# Phase 3 schema compatibility report

The eighteen contract models Phase 3 added. The structural digest is `sha256` over the model's JSON schema with sorted keys, so it changes when a field is added, removed, retyped or reconstrained, and does not change when unrelated code moves.

Phase 3 also exported six models that Phase 0 defined and nothing had ever constructed: `LogicalRun`, `SchedulerAttempt`, `LifecycleEvent`, `CheckpointManifest`, `ResultManifest` and `BatchJobBinding`. They are not repeated here -- the repository-wide inventory is in `proof/phase-0/schema-compatibility.md`, and a second copy is a copy that goes stale -- but the export is what makes them reviewable by somebody who does not read Python.

| model | module | kind | schema_version | exported | structural digest |
| --- | --- | --- | --- | --- | --- |
| BatchJobBinding | edullm_platform.contracts.execution | record | 1 | yes | sha256:e42641ace737ac344e31cfe56152e7b41bf2fff4d888cd8a3741c3c7b805ba1a |
| ExecutionTarget | edullm_platform.contracts.execution | record | unversioned | no | sha256:5b237400b5a505d9c8fd5b4b81a4a4d01b7c65cfc97b05023cf7b8c3c152ec9c |
| ExecutionTargetBinding | edullm_platform.contracts.execution | record | unversioned | no | sha256:011f8640d8c895318e86bae11e62d59972b5b5ef67a1c50e77cb0f9b74ad0aa2 |
| ExecutionTargetCatalog | edullm_platform.contracts.execution | record | 1 | no | sha256:baad4dd4cda8e9519685011cd20f97f36338e0e2e54d96488f7a897243688baa |
| ImageScanException | edullm_platform.contracts.image_scan | record | unversioned | yes | sha256:f2aca5a4ed2373862ed4f22eef0a78dd2063d2db159e2e70670c4e6375249eb6 |
| ImageScanExceptionRegistry | edullm_platform.contracts.image_scan | record | 1 | yes | sha256:08fa8dc6ee43fe2d52ff8dbca543e2080943fafdd08bab531c781164919069fc |
| ImageScanPolicy | edullm_platform.contracts.image_scan | record | unversioned | yes | sha256:a995523c781b979c5400ab756aa90b610775a5f797641b38b7530418f283c192 |
| ImageScanSummary | edullm_platform.contracts.image_scan | record | 1 | no | sha256:4ae0dbc073e6e33a52d8caf9213d1b8344b8ceb49d60d6475a766702cb6b2f30 |
| AccountMeasurements | edullm_platform.phase3_evidence | record | 1 | no | sha256:5b7c36029e25ad4004faea605604a6a1ea4c0a41c51ca0821661559cc356c611 |
| ActionVerdict | edullm_platform.phase3_evidence | record | unversioned | no | sha256:f52eda88bcd1ab4b8229db05097df2adf8120549ac32c8d9c5dedeff17b3a83f |
| AuthorizationControl | edullm_platform.phase3_evidence | record | unversioned | no | sha256:30fab671ec96cf057ef841200b009f492cb19d3d023dbc0fc0cf28837103cb23 |
| BatchInventory | edullm_platform.phase3_evidence | record | unversioned | no | sha256:c7a9bf1aa4905b1ba9991f59ff32e66417fa672cc298d18557e6156b627f9c06 |
| NetworkPlacement | edullm_platform.phase3_evidence | record | unversioned | no | sha256:0496bd671bdc88534cbbb467b95abcad1456cafb8ae2c1b771a77041afe6eb24 |
| RegionAuthorization | edullm_platform.phase3_evidence | record | unversioned | no | sha256:c68439705e4f321d2950f16ba1764e01647ade1cadbe42aa0f4800368acfad60 |
| ServiceLinkedRoleRecord | edullm_platform.phase3_evidence | record | unversioned | no | sha256:84e62d941950110a6fee3edd6b455c3e377e8a6bb12234ff8eb9ff448d861402 |
| SubnetOffering | edullm_platform.phase3_evidence | record | unversioned | no | sha256:ab6f5c0b1a06a0a71328ecc73605f226a33c407a626d0cddd55fd337eccb8575 |
| VpcQuotaRecord | edullm_platform.phase3_evidence | record | unversioned | no | sha256:43cd7c39a0e005ea23e5682e8e39b63473756d4c4cb05dcf7f24dc4b114bb994 |
| Phase3GateReport | edullm_platform.phase3_gate | record | unversioned | no | sha256:001d51e15a835edd5a5f33d0978ffe25256b81d0f3677396ea7e28e0d6ef9917 |
