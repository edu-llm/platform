# Phase 0 schema compatibility report

109 contract models. The structural digest is `sha256` over the model's JSON schema with sorted keys, so it changes when a field is added, removed, retyped, or reconstrained, and does not change when unrelated code moves. Comparing this table between phases answers whether a schema changed.

The kind column separates a `record`, which some payload is validated against, from a `base`, which exists only for other models to inherit from and which no payload names directly.

## Repository-configuration contracts

35 models are reachable from the sixteen root models exported to `schemas/`. These describe what the repository declares: who is in the organization, what compute exists, what policy applies, and what a submission looks like. They are versioned by the checked-in JSON Schema files below rather than by a `schema_version` field, except for RunManifest, which carries both.

| model | module | kind | schema_version | structural digest |
| --- | --- | --- | --- | --- |
| DecisionRecord | edullm_platform.contracts.admission | record | 1 | sha256:9482e02ae58fdbe3a8876cf2a10d13b2c383bcd3d724386497c52c5f278611de |
| IntentRecord | edullm_platform.contracts.admission | record | 1 | sha256:ea5ee7114ae524a14e28691db4445c53a582ee8eef65f299653034cd0db58b58 |
| AuthorizationDecision | edullm_platform.contracts.authorization | record | unversioned | sha256:9d83f1667db4782d3a712900981dba1b9b3c0fcb3bc74da53b727d553c21dee5 |
| AttributionTag | edullm_platform.contracts.bindings | record | unversioned | sha256:aec053fb5315e407f4f0ab603a320efbe59520de5654c8a7f1823a0ee4867655 |
| RepositoryBinding | edullm_platform.contracts.bindings | record | unversioned | sha256:acfb98fa6a3ecffb8258ab1ea4f150e721c0f1c067e067a701e61837a9fcfd6c |
| TeamBinding | edullm_platform.contracts.bindings | record | unversioned | sha256:ecf980a6ccba443b8f7d96455d0baf114886ddebe9d0c1e8540a1725f55b845e |
| TeamBindingCatalog | edullm_platform.contracts.bindings | record | unversioned | sha256:722d7cd12fe48c2a07bf055fd5a5574005a041ad6ff196bf066a042ee29ffcb7 |
| DatasetRegistry | edullm_platform.contracts.dataset_registry | record | 1 | sha256:dc482fdd7a0e7d510f2f41c4a4765971d248785466baa083a58b47b4ec41449c |
| RegisteredDatasetRelease | edullm_platform.contracts.dataset_registry | record | unversioned | sha256:db34cb36a36150d433a41cb0668abc50d67d0882644ef4d279d459fb64040666 |
| BatchJobBinding | edullm_platform.contracts.execution | record | 1 | sha256:e42641ace737ac344e31cfe56152e7b41bf2fff4d888cd8a3741c3c7b805ba1a |
| GitHubWorkflowRunReference | edullm_platform.contracts.image | record | unversioned | sha256:a80e5dc8c40056fbc75557716cac82f11e5f4d80ec840dcb76d33ad56c57604f |
| ImageScanException | edullm_platform.contracts.image_scan | record | unversioned | sha256:f2aca5a4ed2373862ed4f22eef0a78dd2063d2db159e2e70670c4e6375249eb6 |
| ImageScanExceptionRegistry | edullm_platform.contracts.image_scan | record | 1 | sha256:08fa8dc6ee43fe2d52ff8dbca543e2080943fafdd08bab531c781164919069fc |
| ImageScanPolicy | edullm_platform.contracts.image_scan | record | unversioned | sha256:a995523c781b979c5400ab756aa90b610775a5f797641b38b7530418f283c192 |
| OrganizationInventory | edullm_platform.contracts.inventory | record | unversioned | sha256:950a43db0b18777147c8dc8bbbc8f19a388c1768d671360906d101a2f39705ce |
| PersonRef | edullm_platform.contracts.inventory | record | unversioned | sha256:3fd6419368a4098e6f5792779e2bc5fd0bce1975846b53e2492654d2ce7a7305 |
| CheckpointRef | edullm_platform.contracts.lifecycle | record | unversioned | sha256:74d6aea6cf08b2b0c2151d0ff6c1ef8d804405869774911f8e0e73f94ccaa4e3 |
| LifecycleEvent | edullm_platform.contracts.lifecycle | record | 1 | sha256:1fcdc66ee1a799f18bcc37fa146aa8c9597304c459120840d0afe2f7f58f01cf |
| LogicalRun | edullm_platform.contracts.lifecycle | record | 1 | sha256:fffce378b3f982237b891b5cd4302001302cc2277a5a3bbf9329dd26e18c95db |
| SchedulerAttempt | edullm_platform.contracts.lifecycle | record | 1 | sha256:4cf54ad2ac3a0c40f5ad8bcb792daa837fc8abad32de5223f3f680d9d890a688 |
| FanOut | edullm_platform.contracts.manifest | record | unversioned | sha256:86de4fcb96a84d6753317c71207aecc11838a96b7713a33fd29c3d97e3c5c870 |
| RunManifest | edullm_platform.contracts.manifest | record | 1 | sha256:819ed6a07eb28bf235d73b8df36fdc5fbc16e391bcfe26ae7c0abd40b862df02 |
| ApprovalPolicy | edullm_platform.contracts.policy | record | unversioned | sha256:10b38d3b612f94f3ca2d06d6c004c183de2a6e5ed99b267a7d66c339d1cfb473 |
| PolicyThresholds | edullm_platform.contracts.policy | record | unversioned | sha256:7e11224790f5297718e233801ed7ee9fc8ef40405b8b0abdf709e70625a98a00 |
| RegisteredRepository | edullm_platform.contracts.repository_registry | record | unversioned | sha256:6061c9afa770d4335d829aa1d6ed781cea6bfb492719843918d3bd0e364b2fae |
| RepositoryRegistry | edullm_platform.contracts.repository_registry | record | unversioned | sha256:d69819572c1f37964e8bf2553d0c66b65b234d221039d95a8085150812ed7429 |
| CheckpointManifest | edullm_platform.contracts.results | record | 1 | sha256:4a27e4581c4d888b09d99e81d2236353fd5cf012198b4b520e95eb0406077237 |
| ResultManifest | edullm_platform.contracts.results | record | 1 | sha256:3bc34ee47a6dab8f04d777a05418940e3616a22d2c5c165f294991972e8cef59 |
| WandbRunRef | edullm_platform.contracts.results | record | unversioned | sha256:cba8ceb21dd7d198dfbe0976bf225d5a837782d343708a4a251195e8a7aaef97 |
| CheckpointContract | edullm_platform.contracts.workload | record | unversioned | sha256:97160a720340044f91d3707d703a2a424ff8c3d5479c10c8f6a48e68e34ad9f0 |
| ComputeProfile | edullm_platform.contracts.workload | record | unversioned | sha256:980b84356011d721f565c7d3fdaa7c852ef286c429d1f2b796a6d4ae163ede20 |
| CostInputs | edullm_platform.contracts.workload | record | unversioned | sha256:42d9b8e66cb97787e2c46e55b6d2254a8c7bab7930cc53653cc14d9b0740d424 |
| WorkloadCatalog | edullm_platform.contracts.workload | record | unversioned | sha256:d5eb8b6f40addd387722837c9d0f52bfada42e7d30087589d9f9b43f21497312 |
| WorkloadProfile | edullm_platform.contracts.workload | record | unversioned | sha256:e5a748fc939a27a220a1da81ad515c719678d757d29b42b008158217baaa10a7 |
| SubmissionInputs | edullm_platform.submission | record | unversioned | sha256:6b0b672d4f9b8f743e263decdab00f7bea318fce7aa8d616dddc5e9846ffc451 |

## Runtime records

74 models are not exported to `schemas/`. These are produced while work runs or while a decision is made: lineage, results, datasets, authorization outcomes, operational evidence, and gate results. They carry a `schema_version` field where they are persisted, and they are deliberately not published as repository configuration, because no human authors them by hand.

| model | module | kind | schema_version | structural digest |
| --- | --- | --- | --- | --- |
| AdmissionDenialMatrix | edullm_platform.admission_denials | record | 1 | sha256:fe33252d54657119ea0f49b18d8134909df709834f70af1d1ed4fc1c10c40b11 |
| BatchDenialMatrix | edullm_platform.batch_denials | record | 1 | sha256:ae6f2f8dceddb4b601b9fe988fa8ebf2190a1fa584ff1af939de93dafd493e80 |
| DatasetAccessPolicy | edullm_platform.contracts.dataset | record | unversioned | sha256:290f1aa4dc4828d1b25c686a896e64a7086f74e257765e524c0d2b759576c036 |
| DatasetObject | edullm_platform.contracts.dataset | record | unversioned | sha256:48028f7a41435bcec6d43186a456188f46f3d49b09fa29c73c67e95440577646 |
| DatasetRelease | edullm_platform.contracts.dataset | record | 1 | sha256:b01afe271ecd1ac6260da0b7f7063fa0562aa16becb10b3edfecb2ea9c32f14d |
| DatasetSchemaRef | edullm_platform.contracts.dataset | record | unversioned | sha256:428524feed425756f7183ef5952bef3bce25b6f1a4e7998926ab1882cf2899a1 |
| AuthorizationScenario | edullm_platform.contracts.decision_matrix | record | 1 | sha256:0c7409b810840c4024bc9979c3a57af21aa2c1be58b486c2e0eadcb5ab3cf687 |
| ExpectedAuthorization | edullm_platform.contracts.decision_matrix | record | unversioned | sha256:44224e7839f149446ecf5fd8a011becf303fa4fcb14fe3e48471cfd1f4f8caaf |
| ScenarioActor | edullm_platform.contracts.decision_matrix | record | unversioned | sha256:6a7342c0b0639b375aaa9c008760cc47826abab381bce2a9c37b3aea6e9b828f |
| ExecutionTarget | edullm_platform.contracts.execution | record | unversioned | sha256:5b237400b5a505d9c8fd5b4b81a4a4d01b7c65cfc97b05023cf7b8c3c152ec9c |
| ExecutionTargetBinding | edullm_platform.contracts.execution | record | unversioned | sha256:011f8640d8c895318e86bae11e62d59972b5b5ef67a1c50e77cb0f9b74ad0aa2 |
| ExecutionTargetCatalog | edullm_platform.contracts.execution | record | 1 | sha256:baad4dd4cda8e9519685011cd20f97f36338e0e2e54d96488f7a897243688baa |
| ImageProvenance | edullm_platform.contracts.image | record | 1 | sha256:102aa35cb3107bfc48c8d448b8047b6a897fa0939cc8de34a63d1987d5b601c4 |
| ImageScanSummary | edullm_platform.contracts.image_scan | record | 1 | sha256:4ae0dbc073e6e33a52d8caf9213d1b8344b8ceb49d60d6475a766702cb6b2f30 |
| RequestFacts | edullm_platform.contracts.policy | record | unversioned | sha256:966ee518a0ef0f15641cb8d0579d63c9534080783452153f8fcc01a6dcffd9d2 |
| SourceIdentity | edullm_platform.contracts.source_identity | record | 1 | sha256:c785066e238f71471c7cab1aaaca9f2fd53f3b9eb5653abe1f444d18dca1efa1 |
| CriterionResult | edullm_platform.criteria | record | unversioned | sha256:b45d908a731eeb75ddc20bf1abb24357fe952d605a399dc7773bb1e4a4bbf11c |
| PilotVerdict | edullm_platform.criteria | record | unversioned | sha256:ca942afc4f5bc9cd57d1f215ddd65aa166fd6d572c26e88f9f1d81d3a5074488 |
| BatchQuotaRecord | edullm_platform.evidence | record | unversioned | sha256:b315f8a70fe1fa3933ca365ae89f2af41e0ac4a4470c176f7b80890a19ead92c |
| CapturedServiceQuotasEvidence | edullm_platform.evidence | record | unversioned | sha256:2e3c2d6b13fa402c242534f5edbcac96eee3dd8fba2431787b5800051b63ba99 |
| FreshEvidenceModel | edullm_platform.evidence | base | unversioned | sha256:7c123a5ee3ee892e28cf3aa1cc32ae98ac83cec63944475bb8a4830d24e02549 |
| GitHubPlanEvidence | edullm_platform.evidence | record | unversioned | sha256:16df1c81fffe080590c4606d9ebf0bd5fe2e9dd388a75f0f2ba3736cbeb83b9a |
| QuotaRecord | edullm_platform.evidence | record | unversioned | sha256:1d12f0f55e61c12b0d042871513637406b2d9bfeb8f8735fbd6d245ef5553af9 |
| ServiceQuotasEvidence | edullm_platform.evidence | base | unversioned | sha256:3b532f5c59cab1982631ea3f179c60b1fbcbab791546565deb4b8bdcdc0d1d42 |
| GateCheck | edullm_platform.phase0_gate | record | unversioned | sha256:3dbb91b49e418557346c57de4ddf6fbfc2ecbd44056b3767df7d897e4a35f487 |
| Phase0GateReport | edullm_platform.phase0_gate | record | unversioned | sha256:2e045fc7fd5ffd738c8977378e6532c736c147b589d5794881500e93b583a59d |
| Phase0GateResult | edullm_platform.phase0_gate | record | unversioned | sha256:bb8b182761dc0a67cb9e455e030f4d3f23545ca49b09caa877f9ec01357a5f5c |
| BuildProvenanceEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:79ab5763ecdaa73fa042f2f5b3793c67ca9441389c07bb636cca28c3778e82e3 |
| DenialEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:d51a5c45e15361a25928c9ae8fdaf41d688118e48509ee3e5cd69e8a830748b0 |
| DeployedRoleEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:7a289194821b3508dc7dccc9cc3c107b0a073fa439af309a6277929641611a7d |
| EcrImageEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:fb1eb1dbeec4765672ed2624a08a2dfd074a7cd0e54d225a5369bf21bf5880de |
| EcrLifecycleRule | edullm_platform.phase1_evidence | record | unversioned | sha256:7ffd8226788f5ca6478b6c4d81fa14b8a4360c56b0fcfbd7cd22aa00d8809ee6 |
| EcrRepositoryEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:0c5261ba9d7585898d3377fa6af37cc0c10222a22b69312c43a62a745757f7bf |
| IamActionMatch | edullm_platform.phase1_evidence | record | unversioned | sha256:32aa0008c3c9785f841033729aa36af5c7a47cf8f9a6b8800a049fd6ed469f95 |
| IamAttachedPolicy | edullm_platform.phase1_evidence | record | unversioned | sha256:094af69b7d05c460ebcd252a48262f88f2f310affebe3b8f732285eacc366d07 |
| IamConditionEntry | edullm_platform.phase1_evidence | record | unversioned | sha256:c4c18750fa294a432ba76e5630bbcd994317c00095125d4df76a15ab0c23ee7c |
| IamInlinePolicy | edullm_platform.phase1_evidence | record | unversioned | sha256:3ff0f0333b6453872a293ec86f69475be987f91d8bd536e6e9411fa4528665df |
| IamPermissionStatement | edullm_platform.phase1_evidence | record | unversioned | sha256:1e59eb9cb85b179c9ed242b18c23a719594fe9652de1c2f35c7e6d9196d452a8 |
| IamPrincipal | edullm_platform.phase1_evidence | record | unversioned | sha256:40b28ec7e64f877a2ab184b0b3940a352974763ca73ef892f85fdff36f1f34f2 |
| IamPrincipalMatch | edullm_platform.phase1_evidence | record | unversioned | sha256:541fb03a83c5ffc7d924fe32d2c635399343f4622517e15d3177f3cf9feb96b1 |
| IamResourceMatch | edullm_platform.phase1_evidence | record | unversioned | sha256:3375154e58184c2766a905e660500457145848cb27da71aa974fdcce1ed49c12 |
| IamTrustStatement | edullm_platform.phase1_evidence | record | unversioned | sha256:c980d45a581830325ab30cc75fe332c79315db0b62acefb48e121bd3db3dea23 |
| ImageScanEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:fe319ebea128d53ad24f38b5a6ba1cb9a1d7113e2517e2be4f4e1fc1ccaa9adc |
| ImageScanFindingCounts | edullm_platform.phase1_evidence | record | unversioned | sha256:3ecbfd6c0d498de0074f167970f5124624b7821b7270d2d049d48bd6e182d07c |
| ImmutableTagRefusalEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:94005586a8fd7f73ecb15f07702063229cb43518cd9dbee032210b79d330eb70 |
| OidcSessionEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:ac5da5197bfab87802a8940e85cef7c2a98b22bba5fa1603e9e3d4b157f47856 |
| Phase1GateReport | edullm_platform.phase1_gate | record | unversioned | sha256:37e350f2a31013f33e1617a8c7d08a7f48da838e5cf37d24a793d80a3747ca0f |
| AdmissionExecution | edullm_platform.phase2_evidence | record | unversioned | sha256:b713e6adbbef4826795d41aa6a4c48780f973639cc1aa2413aca7b627c3b5d63 |
| AdmissionExecutionInventory | edullm_platform.phase2_evidence | record | unversioned | sha256:8193d6d263e3e888b068b9aa36bcaf2dfe5b88ee7d5cbeb70f32481e3de076e1 |
| EnvironmentInventory | edullm_platform.phase2_evidence | record | unversioned | sha256:a496a853cb96ec9f456ebc6fd2c62bca2cbd09221f5e63e1099b13966e9f06d0 |
| EnvironmentReviewer | edullm_platform.phase2_evidence | record | unversioned | sha256:2d2d86e7f72a582de3961f38e82b5bdb4e88cc262c5a95deeab864c5ce3417f6 |
| LineageInventory | edullm_platform.phase2_evidence | record | unversioned | sha256:44fdfa6f1ce14ce085bf18add6364911b6e4ddb95d2915f865c545319c7526b3 |
| LineageObject | edullm_platform.phase2_evidence | record | unversioned | sha256:e8b3a5a7c45fa505cd8a7a2f3d2a8df3cf482684a693d8b96404488ac9773d06 |
| ProtectedEnvironment | edullm_platform.phase2_evidence | record | unversioned | sha256:3d60181d51ce862aa4b65d8d7cbe3da760a50552e10328d96ef5894bc735f692 |
| SecretInventory | edullm_platform.phase2_evidence | record | unversioned | sha256:ba3cec7c6b95f1761756f3938cfd445b3ba081f34d7cd5fb33e6f3c9754dad39 |
| Phase2GateReport | edullm_platform.phase2_gate | record | unversioned | sha256:237063f6f507f5fc06a78659bb898512bfaf472ad6c8c641d8cf840d049e3dbf |
| AccountMeasurements | edullm_platform.phase3_evidence | record | 1 | sha256:5b7c36029e25ad4004faea605604a6a1ea4c0a41c51ca0821661559cc356c611 |
| ActionVerdict | edullm_platform.phase3_evidence | record | unversioned | sha256:f52eda88bcd1ab4b8229db05097df2adf8120549ac32c8d9c5dedeff17b3a83f |
| AuthorizationControl | edullm_platform.phase3_evidence | record | unversioned | sha256:30fab671ec96cf057ef841200b009f492cb19d3d023dbc0fc0cf28837103cb23 |
| BatchInventory | edullm_platform.phase3_evidence | record | unversioned | sha256:c7a9bf1aa4905b1ba9991f59ff32e66417fa672cc298d18557e6156b627f9c06 |
| NetworkPlacement | edullm_platform.phase3_evidence | record | unversioned | sha256:0496bd671bdc88534cbbb467b95abcad1456cafb8ae2c1b771a77041afe6eb24 |
| RegionAuthorization | edullm_platform.phase3_evidence | record | unversioned | sha256:c68439705e4f321d2950f16ba1764e01647ade1cadbe42aa0f4800368acfad60 |
| ServiceLinkedRoleRecord | edullm_platform.phase3_evidence | record | unversioned | sha256:84e62d941950110a6fee3edd6b455c3e377e8a6bb12234ff8eb9ff448d861402 |
| SubnetOffering | edullm_platform.phase3_evidence | record | unversioned | sha256:ab6f5c0b1a06a0a71328ecc73605f226a33c407a626d0cddd55fd337eccb8575 |
| VpcQuotaRecord | edullm_platform.phase3_evidence | record | unversioned | sha256:43cd7c39a0e005ea23e5682e8e39b63473756d4c4cb05dcf7f24dc4b114bb994 |
| Phase3GateReport | edullm_platform.phase3_gate | record | unversioned | sha256:afae329619a08322914bc407920ae1991006d4a48547dc46f71bc21f742c7af5 |
| AttemptedDenial | edullm_platform.publisher_denials | record | unversioned | sha256:f0b497787467fde6f343ddec8552ead542f393a7ed111e59cc7b74041107fe69 |
| PublisherDenialMatrix | edullm_platform.publisher_denials | record | 1 | sha256:66bcc2645e9e044e23cd10338e2041e7a236528989f5ad2013f0d1d292d354da |
| ConfigurationField | edullm_platform.rebuild_comparison | record | unversioned | sha256:04c684f0cfe10bf5d4afbc0a8885fc89150cf49966d76690a30f104c132478c1 |
| LocalRebuildComparison | edullm_platform.rebuild_comparison | record | 1 | sha256:8585e161aa3a3b8608869b06434ae517694bf094d196b8fb7da55dda51dd2c6b |
| RebuiltImage | edullm_platform.rebuild_comparison | record | unversioned | sha256:ba0d657955630da264698dfbb7b85dc6dd70cc0401cbaf3d8f23db75cba16d4e |
| RoleDriftFinding | edullm_platform.role_drift | record | unversioned | sha256:4572586fb60d4c8b381ac0680119aa19c5b3009343767fb6d3c1b301a35cb5d0 |
| RoleDriftReport | edullm_platform.role_drift | record | unversioned | sha256:f0e5b2e3ec53486f5dfa7a880c32d594f002bba71f1b1ff71285673a6cf5fe27 |
| TemplateRole | edullm_platform.role_drift | record | unversioned | sha256:5e12a30b611a50e621b89faa4c18987f91b429d710bd748f4342fae2821a8e9a |

## Exported JSON Schema files

The checked-in schemas under `schemas/`, with the digest of each file as generated. `tests/test_schema_export.py::test_checked_in_schemas_match_contract_models` fails if a file drifts from its model.

| file | root model | file digest |
| --- | --- | --- |
| schemas/batch-job-binding.schema.json | BatchJobBinding | sha256:41a20845192e959dd91b32f87d57da0cf3b04e5a2177e0200f27aec2cece901c |
| schemas/checkpoint-manifest.schema.json | CheckpointManifest | sha256:f51cc8c500c81cd1d286dc86c59ff5f24b09a640127034f1527494251d76a07e |
| schemas/datasets.schema.json | DatasetRegistry | sha256:3f175ffd729d92eaba728bc459ca455a538bb5b5131840c11986f1548579ef9b |
| schemas/decision-record.schema.json | DecisionRecord | sha256:852ee127b92bc2a592f5da86972e32ed50e949548951bd66fed94b237b6ce821 |
| schemas/image-exceptions.schema.json | ImageScanExceptionRegistry | sha256:0c2c63980225a5c6e1097356b98f2fe535b8885e8da9ad1618e93697548da862 |
| schemas/intent-record.schema.json | IntentRecord | sha256:39b40c1375c470efe47179c52e898562a7623c1a79f429de8f078f46cf3ddc8d |
| schemas/lifecycle-event.schema.json | LifecycleEvent | sha256:f747e330743b4f471021b38e161d26e24ecda8fb47ab02b08939ee298a1921ab |
| schemas/logical-run.schema.json | LogicalRun | sha256:898f1d6b338ea810a75c0614035a49e0812147aef7816037c97447a602d37688 |
| schemas/organization.schema.json | OrganizationInventory | sha256:5caadb560ced32562f2673591717ce836f1831292cab16a4f9c3a22ba3c0c1f1 |
| schemas/policy.schema.json | ApprovalPolicy | sha256:e57443df3ebf18a1b1858a441f99aec2d41121a3f2110d05be30b530ff2b7f67 |
| schemas/repositories.schema.json | RepositoryRegistry | sha256:ee5ef9172b9ab89aa0965cefda9d86fda855c4cd3f0eeda41ab50551327ff68e |
| schemas/result-manifest.schema.json | ResultManifest | sha256:7e7b6a5891444d9d13256202319f5be6e70addb81f0b0c077e5294c63529503b |
| schemas/run-manifest.schema.json | RunManifest | sha256:62851f48df41a1dc270a525b44a8ef01eab660af9d5b60030d6c0a8776e196f2 |
| schemas/scheduler-attempt.schema.json | SchedulerAttempt | sha256:91984a9fb1f7f9150f7799dc337807bd14b93b50908a56e0e230391546c9c4ac |
| schemas/submission-inputs.schema.json | SubmissionInputs | sha256:0cbe9a1bc42474266c0d217cf4dab4031fb3720408f1fe347a12e62e4abc1578 |
| schemas/workload-catalog.schema.json | WorkloadCatalog | sha256:4039ead3f77c0949db2a701dae90461788ed6856838075a1f223f3d4b853fa06 |

Regenerate with `uv run python tools/export_schemas.py`. Verify a file by hand with `shasum -a 256 schemas/<file>`.

## Declared contract versions

| model | schema_version |
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
