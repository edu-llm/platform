# Phase 0 schema compatibility report

41 contract models. The structural digest is `sha256` over the model's JSON schema with sorted keys, so it changes when a field is added, removed, retyped, or reconstrained, and does not change when unrelated code moves. Comparing this table between phases answers whether a schema changed.

The kind column separates a `record`, which some payload is validated against, from a `base`, which exists only for other models to inherit from and which no payload names directly.

## Repository-configuration contracts

14 models are reachable from the four root models exported to `schemas/`. These describe what the repository declares: who is in the organization, what compute exists, what policy applies, and what a submission looks like. They are versioned by the checked-in JSON Schema files below rather than by a `schema_version` field, except for RunManifest, which carries both.

| model | module | kind | schema_version | structural digest |
| --- | --- | --- | --- | --- |
| AttributionTag | edullm_platform.contracts.bindings | record | unversioned | sha256:aec053fb5315e407f4f0ab603a320efbe59520de5654c8a7f1823a0ee4867655 |
| RepositoryBinding | edullm_platform.contracts.bindings | record | unversioned | sha256:acfb98fa6a3ecffb8258ab1ea4f150e721c0f1c067e067a701e61837a9fcfd6c |
| TeamBinding | edullm_platform.contracts.bindings | record | unversioned | sha256:ecf980a6ccba443b8f7d96455d0baf114886ddebe9d0c1e8540a1725f55b845e |
| TeamBindingCatalog | edullm_platform.contracts.bindings | record | unversioned | sha256:722d7cd12fe48c2a07bf055fd5a5574005a041ad6ff196bf066a042ee29ffcb7 |
| OrganizationInventory | edullm_platform.contracts.inventory | record | unversioned | sha256:950a43db0b18777147c8dc8bbbc8f19a388c1768d671360906d101a2f39705ce |
| PersonRef | edullm_platform.contracts.inventory | record | unversioned | sha256:3fd6419368a4098e6f5792779e2bc5fd0bce1975846b53e2492654d2ce7a7305 |
| FanOut | edullm_platform.contracts.manifest | record | unversioned | sha256:86de4fcb96a84d6753317c71207aecc11838a96b7713a33fd29c3d97e3c5c870 |
| RunManifest | edullm_platform.contracts.manifest | record | 1 | sha256:819ed6a07eb28bf235d73b8df36fdc5fbc16e391bcfe26ae7c0abd40b862df02 |
| ApprovalPolicy | edullm_platform.contracts.policy | record | unversioned | sha256:1552cbd63788a09e6adda5b22253a29651fff9263dcb619d9c2060c8d9ede9d9 |
| PolicyThresholds | edullm_platform.contracts.policy | record | unversioned | sha256:7e11224790f5297718e233801ed7ee9fc8ef40405b8b0abdf709e70625a98a00 |
| CheckpointContract | edullm_platform.contracts.workload | record | unversioned | sha256:97160a720340044f91d3707d703a2a424ff8c3d5479c10c8f6a48e68e34ad9f0 |
| ComputeProfile | edullm_platform.contracts.workload | record | unversioned | sha256:980b84356011d721f565c7d3fdaa7c852ef286c429d1f2b796a6d4ae163ede20 |
| WorkloadCatalog | edullm_platform.contracts.workload | record | unversioned | sha256:d5eb8b6f40addd387722837c9d0f52bfada42e7d30087589d9f9b43f21497312 |
| WorkloadProfile | edullm_platform.contracts.workload | record | unversioned | sha256:e5a748fc939a27a220a1da81ad515c719678d757d29b42b008158217baaa10a7 |

## Runtime records

27 models are not exported to `schemas/`. These are produced while work runs or while a decision is made: lineage, results, datasets, authorization outcomes, operational evidence, and gate results. They carry a `schema_version` field where they are persisted, and they are deliberately not published as repository configuration, because no human authors them by hand.

| model | module | kind | schema_version | structural digest |
| --- | --- | --- | --- | --- |
| AuthorizationDecision | edullm_platform.contracts.authorization | record | unversioned | sha256:9d83f1667db4782d3a712900981dba1b9b3c0fcb3bc74da53b727d553c21dee5 |
| DatasetAccessPolicy | edullm_platform.contracts.dataset | record | unversioned | sha256:290f1aa4dc4828d1b25c686a896e64a7086f74e257765e524c0d2b759576c036 |
| DatasetObject | edullm_platform.contracts.dataset | record | unversioned | sha256:48028f7a41435bcec6d43186a456188f46f3d49b09fa29c73c67e95440577646 |
| DatasetRelease | edullm_platform.contracts.dataset | record | 1 | sha256:b01afe271ecd1ac6260da0b7f7063fa0562aa16becb10b3edfecb2ea9c32f14d |
| DatasetSchemaRef | edullm_platform.contracts.dataset | record | unversioned | sha256:428524feed425756f7183ef5952bef3bce25b6f1a4e7998926ab1882cf2899a1 |
| AuthorizationScenario | edullm_platform.contracts.decision_matrix | record | 1 | sha256:fbb7bcf63817b7c8384ed1bdffae58e76709358151931a3ab7486bed88f533ff |
| ExpectedAuthorization | edullm_platform.contracts.decision_matrix | record | unversioned | sha256:44224e7839f149446ecf5fd8a011becf303fa4fcb14fe3e48471cfd1f4f8caaf |
| ScenarioActor | edullm_platform.contracts.decision_matrix | record | unversioned | sha256:6a7342c0b0639b375aaa9c008760cc47826abab381bce2a9c37b3aea6e9b828f |
| CheckpointRef | edullm_platform.contracts.lifecycle | record | unversioned | sha256:74d6aea6cf08b2b0c2151d0ff6c1ef8d804405869774911f8e0e73f94ccaa4e3 |
| LifecycleEvent | edullm_platform.contracts.lifecycle | record | 1 | sha256:1fcdc66ee1a799f18bcc37fa146aa8c9597304c459120840d0afe2f7f58f01cf |
| LogicalRun | edullm_platform.contracts.lifecycle | record | 1 | sha256:fffce378b3f982237b891b5cd4302001302cc2277a5a3bbf9329dd26e18c95db |
| SchedulerAttempt | edullm_platform.contracts.lifecycle | record | 1 | sha256:4cf54ad2ac3a0c40f5ad8bcb792daa837fc8abad32de5223f3f680d9d890a688 |
| RequestFacts | edullm_platform.contracts.policy | record | unversioned | sha256:189d4875f637bfd748915498f0c6bd2e740d0ea723bf03bd1cb97a6601dabad4 |
| CheckpointManifest | edullm_platform.contracts.results | record | 1 | sha256:4a27e4581c4d888b09d99e81d2236353fd5cf012198b4b520e95eb0406077237 |
| ResultManifest | edullm_platform.contracts.results | record | 1 | sha256:3bc34ee47a6dab8f04d777a05418940e3616a22d2c5c165f294991972e8cef59 |
| WandbRunRef | edullm_platform.contracts.results | record | unversioned | sha256:cba8ceb21dd7d198dfbe0976bf225d5a837782d343708a4a251195e8a7aaef97 |
| CostInputs | edullm_platform.contracts.workload | record | unversioned | sha256:42d9b8e66cb97787e2c46e55b6d2254a8c7bab7930cc53653cc14d9b0740d424 |
| BatchQuotaRecord | edullm_platform.evidence | record | unversioned | sha256:b315f8a70fe1fa3933ca365ae89f2af41e0ac4a4470c176f7b80890a19ead92c |
| CapturedServiceQuotasEvidence | edullm_platform.evidence | record | unversioned | sha256:2e3c2d6b13fa402c242534f5edbcac96eee3dd8fba2431787b5800051b63ba99 |
| FreshEvidenceModel | edullm_platform.evidence | base | unversioned | sha256:7c123a5ee3ee892e28cf3aa1cc32ae98ac83cec63944475bb8a4830d24e02549 |
| GitHubPlanEvidence | edullm_platform.evidence | record | unversioned | sha256:16df1c81fffe080590c4606d9ebf0bd5fe2e9dd388a75f0f2ba3736cbeb83b9a |
| QuotaRecord | edullm_platform.evidence | record | unversioned | sha256:1d12f0f55e61c12b0d042871513637406b2d9bfeb8f8735fbd6d245ef5553af9 |
| ServiceQuotasEvidence | edullm_platform.evidence | base | unversioned | sha256:3b532f5c59cab1982631ea3f179c60b1fbcbab791546565deb4b8bdcdc0d1d42 |
| CriterionResult | edullm_platform.phase0_gate | record | unversioned | sha256:aad07eae4bd7a45913d87cbffaa6c829d473949d9a67e566bcbbb56bbeccd004 |
| GateCheck | edullm_platform.phase0_gate | record | unversioned | sha256:3dbb91b49e418557346c57de4ddf6fbfc2ecbd44056b3767df7d897e4a35f487 |
| Phase0GateReport | edullm_platform.phase0_gate | record | unversioned | sha256:02eb9d875d971b229069b345e83a5646deca38af71d787a32cbe4be81b3d3883 |
| Phase0GateResult | edullm_platform.phase0_gate | record | unversioned | sha256:bb8b182761dc0a67cb9e455e030f4d3f23545ca49b09caa877f9ec01357a5f5c |

## Exported JSON Schema files

The checked-in schemas under `schemas/`, with the digest of each file as generated. `tests/test_schema_export.py::test_checked_in_schemas_match_contract_models` fails if a file drifts from its model.

| file | root model | file digest |
| --- | --- | --- |
| schemas/organization.schema.json | OrganizationInventory | sha256:5caadb560ced32562f2673591717ce836f1831292cab16a4f9c3a22ba3c0c1f1 |
| schemas/policy.schema.json | ApprovalPolicy | sha256:1ddace2bcdeac29fb6bc686756fac59ecac93f318637b76d0dcc7d28bc394341 |
| schemas/run-manifest.schema.json | RunManifest | sha256:62851f48df41a1dc270a525b44a8ef01eab660af9d5b60030d6c0a8776e196f2 |
| schemas/workload-catalog.schema.json | WorkloadCatalog | sha256:4039ead3f77c0949db2a701dae90461788ed6856838075a1f223f3d4b853fa06 |

Regenerate with `uv run python tools/export_schemas.py`. Verify a file by hand with `shasum -a 256 schemas/<file>`.

## Declared contract versions

| model | schema_version |
| --- | --- |
| AuthorizationScenario | 1 |
| CheckpointManifest | 1 |
| DatasetRelease | 1 |
| LifecycleEvent | 1 |
| LogicalRun | 1 |
| ResultManifest | 1 |
| RunManifest | 1 |
| SchedulerAttempt | 1 |
