# Phase 0 schema compatibility report

149 contract models. The structural digest is `sha256` over the model's JSON schema with sorted keys, so it changes when a field is added, removed, retyped, or reconstrained, and does not change when unrelated code moves. Comparing this table between phases answers whether a schema changed.

This is the complete inventory: every contract model in the repository is below, whichever phase wrote it and whichever module it has since moved to. The three phase bundles carry a scoped view of the same digests and none of them carries a digest that is not here. `tests/test_schema_compatibility.py` recomputes every row of every one of those tables against the tree and fails when one stops describing it.

The kind column separates a `record`, which some payload is validated against, from a `base`, which exists only for other models to inherit from and which no payload names directly.

## Repository-configuration contracts

38 models are reachable from the sixteen root models exported to `schemas/`. These describe what the repository declares: who is in the organization, what compute exists, what policy applies, and what a submission looks like. They are versioned by the checked-in JSON Schema files below rather than by a `schema_version` field, except for RunManifest, which carries both.

| model | module | kind | schema_version | structural digest |
| --- | --- | --- | --- | --- |
| DecisionRecord | edullm_platform.contracts.admission | record | 1 | sha256:873aca75cf16b78097bfa3bc91155a39141fd2b5f20c32ce025596c5fa954383 |
| IntentRecord | edullm_platform.contracts.admission | record | 1 | sha256:658a63bf0a0090de921c874829fa52a685a7d6ca974e4231e0320b430189811e |
| AuthorizationDecision | edullm_platform.contracts.authorization | record | unversioned | sha256:8b8a278b005f2baa3791625a371426215b627fa5a63d2166dca067020149d64d |
| AttributionTag | edullm_platform.contracts.bindings | record | unversioned | sha256:aec053fb5315e407f4f0ab603a320efbe59520de5654c8a7f1823a0ee4867655 |
| RepositoryBinding | edullm_platform.contracts.bindings | record | unversioned | sha256:acfb98fa6a3ecffb8258ab1ea4f150e721c0f1c067e067a701e61837a9fcfd6c |
| TeamBinding | edullm_platform.contracts.bindings | record | unversioned | sha256:59ced5000c7a439b3fc063ab0685a9339aa82289365181ccce4365c11623cae7 |
| TeamBindingCatalog | edullm_platform.contracts.bindings | record | unversioned | sha256:ad706a267df31f57c1bd7e64195cd855cfd95be85a32e741bde1429f318852ad |
| DatasetRegistry | edullm_platform.contracts.dataset_registry | record | 1 | sha256:2eb0a2e62fd359c2fb927c1f5060546cd2093d8b6d7a599b77a4a328313653ae |
| PublishedDatasetReference | edullm_platform.contracts.dataset_registry | record | unversioned | sha256:281fc1b6cf010f71cde18e94c44d8f6400cb9d0685d7e21ded46247478aeb41c |
| RegisteredDatasetRelease | edullm_platform.contracts.dataset_registry | record | unversioned | sha256:ae83ea63439eaeb2627954783bb33f3289051e4cfbc3918f30503dffe6869d49 |
| BatchJobBinding | edullm_platform.contracts.execution | record | 1 | sha256:e42641ace737ac344e31cfe56152e7b41bf2fff4d888cd8a3741c3c7b805ba1a |
| GitHubWorkflowRunReference | edullm_platform.contracts.image | record | unversioned | sha256:a80e5dc8c40056fbc75557716cac82f11e5f4d80ec840dcb76d33ad56c57604f |
| ImageScanException | edullm_platform.contracts.image_scan | record | unversioned | sha256:f2aca5a4ed2373862ed4f22eef0a78dd2063d2db159e2e70670c4e6375249eb6 |
| ImageScanExceptionRegistry | edullm_platform.contracts.image_scan | record | 1 | sha256:446b067f64b593aa2c7b35275bed21c02f547c1c1d801b58aaf36813b6754ce2 |
| ImageScanPolicy | edullm_platform.contracts.image_scan | record | unversioned | sha256:a995523c781b979c5400ab756aa90b610775a5f797641b38b7530418f283c192 |
| ReviewedVulnerability | edullm_platform.contracts.image_scan | record | unversioned | sha256:e1bab292e41b2667ab935a3ab6c45d7f85fc265fbe4038866ed4fbaa60d0a7e1 |
| OrganizationInventory | edullm_platform.contracts.inventory | record | unversioned | sha256:290459e16dc7a20904900733cb19dcf1f957b148cc70f33e329c6cdd1a4904e4 |
| PersonRef | edullm_platform.contracts.inventory | record | unversioned | sha256:ff2cded7d21f82656b42896ffa1f04a04225c0e9758a8f795c0d52a0e702e662 |
| CheckpointRef | edullm_platform.contracts.lifecycle | record | unversioned | sha256:74d6aea6cf08b2b0c2151d0ff6c1ef8d804405869774911f8e0e73f94ccaa4e3 |
| LifecycleEvent | edullm_platform.contracts.lifecycle | record | 1 | sha256:1fcdc66ee1a799f18bcc37fa146aa8c9597304c459120840d0afe2f7f58f01cf |
| LogicalRun | edullm_platform.contracts.lifecycle | record | 1 | sha256:fffce378b3f982237b891b5cd4302001302cc2277a5a3bbf9329dd26e18c95db |
| SchedulerAttempt | edullm_platform.contracts.lifecycle | record | 1 | sha256:4cf54ad2ac3a0c40f5ad8bcb792daa837fc8abad32de5223f3f680d9d890a688 |
| FanOut | edullm_platform.contracts.manifest | record | unversioned | sha256:46456341d85064909fda54d1662ceaeb950686be4a8054d2ff1769e668582ccb |
| RunManifest | edullm_platform.contracts.manifest | record | 1 | sha256:1f6d150b79887bbc6f2962f71964b08180ea13894329c783907e5309df48cedc |
| ApprovalPolicy | edullm_platform.contracts.policy | record | unversioned | sha256:05d9f749d85b945e788a7222bccfa558f77e4920261ae7cb75fd83efe121e442 |
| PolicyThresholds | edullm_platform.contracts.policy | record | unversioned | sha256:f772cd00d6ae1c97fd494f7a268074d4119e522f25366ee66e5a4e0b8fa51ea1 |
| RegisteredRepository | edullm_platform.contracts.repository_registry | record | unversioned | sha256:6061c9afa770d4335d829aa1d6ed781cea6bfb492719843918d3bd0e364b2fae |
| RepositoryRegistry | edullm_platform.contracts.repository_registry | record | unversioned | sha256:d69819572c1f37964e8bf2553d0c66b65b234d221039d95a8085150812ed7429 |
| CheckpointManifest | edullm_platform.contracts.results | record | 1 | sha256:4a27e4581c4d888b09d99e81d2236353fd5cf012198b4b520e95eb0406077237 |
| CheckpointSurvey | edullm_platform.contracts.results | record | 1 | sha256:eb628ae8c4baad97da4aa1321ba454b01ee5f8cf99f8b70d5c7058f4debb7230 |
| ResultManifest | edullm_platform.contracts.results | record | 1 | sha256:47ba07791dc06f0b05512ffdcc18ad5b953a650c88e68b30a7b40d93ac1f39e4 |
| WandbRunRef | edullm_platform.contracts.results | record | unversioned | sha256:cba8ceb21dd7d198dfbe0976bf225d5a837782d343708a4a251195e8a7aaef97 |
| CheckpointContract | edullm_platform.contracts.workload | record | unversioned | sha256:97160a720340044f91d3707d703a2a424ff8c3d5479c10c8f6a48e68e34ad9f0 |
| ComputeProfile | edullm_platform.contracts.workload | record | unversioned | sha256:980b84356011d721f565c7d3fdaa7c852ef286c429d1f2b796a6d4ae163ede20 |
| CostInputs | edullm_platform.contracts.workload | record | unversioned | sha256:42d9b8e66cb97787e2c46e55b6d2254a8c7bab7930cc53653cc14d9b0740d424 |
| WorkloadCatalog | edullm_platform.contracts.workload | record | unversioned | sha256:626769fbb943492d6101b90e8cdfe8209635d669172569571d4fa134635a05d1 |
| WorkloadProfile | edullm_platform.contracts.workload | record | unversioned | sha256:82e29b6a8169249896bbcfb883e1e5aff2ade8251d9b52729c58259f59018c4a |
| SubmissionInputs | edullm_platform.submission | record | unversioned | sha256:fffbede3d3965964d19731fef60955b4a4e5ec3412585c9ba2e2f6e4751e4ca7 |

## Runtime records

111 models are not exported to `schemas/`. These are produced while work runs or while a decision is made: lineage, results, datasets, authorization outcomes, operational evidence, and gate results. They carry a `schema_version` field where they are persisted, and they are deliberately not published as repository configuration, because no human authors them by hand.

| model | module | kind | schema_version | structural digest |
| --- | --- | --- | --- | --- |
| AdmissionDenialMatrix | edullm_platform.admission_denials | record | 1 | sha256:fe33252d54657119ea0f49b18d8134909df709834f70af1d1ed4fc1c10c40b11 |
| BatchDenialMatrix | edullm_platform.batch_denials | record | 1 | sha256:ae6f2f8dceddb4b601b9fe988fa8ebf2190a1fa584ff1af939de93dafd493e80 |
| DatasetAccessPolicy | edullm_platform.contracts.dataset | record | unversioned | sha256:290f1aa4dc4828d1b25c686a896e64a7086f74e257765e524c0d2b759576c036 |
| DatasetObject | edullm_platform.contracts.dataset | record | unversioned | sha256:48028f7a41435bcec6d43186a456188f46f3d49b09fa29c73c67e95440577646 |
| DatasetRelease | edullm_platform.contracts.dataset | record | 1 | sha256:b01afe271ecd1ac6260da0b7f7063fa0562aa16becb10b3edfecb2ea9c32f14d |
| DatasetSchemaRef | edullm_platform.contracts.dataset | record | unversioned | sha256:428524feed425756f7183ef5952bef3bce25b6f1a4e7998926ab1882cf2899a1 |
| AuthorizationScenario | edullm_platform.contracts.decision_matrix | record | 1 | sha256:f10e3bdf85532a82faf51aa33b7422fa912c173f43de797d5fb84115e57f4975 |
| ExpectedAuthorization | edullm_platform.contracts.decision_matrix | record | unversioned | sha256:9993c58e9a50ed17c86e0702fadf6e5af38a9e8002c533be7647b333d9eb42e0 |
| ScenarioActor | edullm_platform.contracts.decision_matrix | record | unversioned | sha256:6a7342c0b0639b375aaa9c008760cc47826abab381bce2a9c37b3aea6e9b828f |
| ExecutionTarget | edullm_platform.contracts.execution | record | unversioned | sha256:5b237400b5a505d9c8fd5b4b81a4a4d01b7c65cfc97b05023cf7b8c3c152ec9c |
| ExecutionTargetBinding | edullm_platform.contracts.execution | record | unversioned | sha256:011f8640d8c895318e86bae11e62d59972b5b5ef67a1c50e77cb0f9b74ad0aa2 |
| ExecutionTargetCatalog | edullm_platform.contracts.execution | record | 1 | sha256:baad4dd4cda8e9519685011cd20f97f36338e0e2e54d96488f7a897243688baa |
| ImageProvenance | edullm_platform.contracts.image | record | 1 | sha256:102aa35cb3107bfc48c8d448b8047b6a897fa0939cc8de34a63d1987d5b601c4 |
| ImageScanSummary | edullm_platform.contracts.image_scan | record | 1 | sha256:4ae0dbc073e6e33a52d8caf9213d1b8344b8ceb49d60d6475a766702cb6b2f30 |
| ScanFinding | edullm_platform.contracts.image_scan | record | unversioned | sha256:07e6f38f7190b357b088220a80499f36683d42270fa428292bb67cb1112e9ffd |
| RequestFacts | edullm_platform.contracts.policy | record | unversioned | sha256:485e40ae5aadececec2074170641563a8246726c1067fdee8148c6d72a2645b5 |
| SourceIdentity | edullm_platform.contracts.source_identity | record | 1 | sha256:c785066e238f71471c7cab1aaaca9f2fd53f3b9eb5653abe1f444d18dca1efa1 |
| CriterionResult | edullm_platform.criteria | record | unversioned | sha256:b45d908a731eeb75ddc20bf1abb24357fe952d605a399dc7773bb1e4a4bbf11c |
| PilotVerdict | edullm_platform.criteria | record | unversioned | sha256:ca942afc4f5bc9cd57d1f215ddd65aa166fd6d572c26e88f9f1d81d3a5074488 |
| BatchQuotaRecord | edullm_platform.evidence | record | unversioned | sha256:b315f8a70fe1fa3933ca365ae89f2af41e0ac4a4470c176f7b80890a19ead92c |
| CapturedServiceQuotasEvidence | edullm_platform.evidence | record | unversioned | sha256:2e3c2d6b13fa402c242534f5edbcac96eee3dd8fba2431787b5800051b63ba99 |
| FreshEvidenceModel | edullm_platform.evidence | base | unversioned | sha256:8c0c624f5d3cfe3da33c28f804dc2e62b086b3af56a6a43de2f624c0498eed8b |
| GitHubPlanEvidence | edullm_platform.evidence | record | unversioned | sha256:16df1c81fffe080590c4606d9ebf0bd5fe2e9dd388a75f0f2ba3736cbeb83b9a |
| QuotaRecord | edullm_platform.evidence | record | unversioned | sha256:1d12f0f55e61c12b0d042871513637406b2d9bfeb8f8735fbd6d245ef5553af9 |
| RecordedEventModel | edullm_platform.evidence | base | unversioned | sha256:7ab65f3d99fa7da1d47424a7b7314f24eb2c8d62dac4462ebccde0a68b086f81 |
| ServiceQuotasEvidence | edullm_platform.evidence | base | unversioned | sha256:3b532f5c59cab1982631ea3f179c60b1fbcbab791546565deb4b8bdcdc0d1d42 |
| IamActionMatch | edullm_platform.iam_documents | record | unversioned | sha256:32aa0008c3c9785f841033729aa36af5c7a47cf8f9a6b8800a049fd6ed469f95 |
| IamAttachedPolicy | edullm_platform.iam_documents | record | unversioned | sha256:094af69b7d05c460ebcd252a48262f88f2f310affebe3b8f732285eacc366d07 |
| IamConditionEntry | edullm_platform.iam_documents | record | unversioned | sha256:c4c18750fa294a432ba76e5630bbcd994317c00095125d4df76a15ab0c23ee7c |
| IamInlinePolicy | edullm_platform.iam_documents | record | unversioned | sha256:3ff0f0333b6453872a293ec86f69475be987f91d8bd536e6e9411fa4528665df |
| IamPermissionStatement | edullm_platform.iam_documents | record | unversioned | sha256:1e59eb9cb85b179c9ed242b18c23a719594fe9652de1c2f35c7e6d9196d452a8 |
| IamPrincipal | edullm_platform.iam_documents | record | unversioned | sha256:40b28ec7e64f877a2ab184b0b3940a352974763ca73ef892f85fdff36f1f34f2 |
| IamPrincipalMatch | edullm_platform.iam_documents | record | unversioned | sha256:541fb03a83c5ffc7d924fe32d2c635399343f4622517e15d3177f3cf9feb96b1 |
| IamResourceMatch | edullm_platform.iam_documents | record | unversioned | sha256:3375154e58184c2766a905e660500457145848cb27da71aa974fdcce1ed49c12 |
| IamTrustStatement | edullm_platform.iam_documents | record | unversioned | sha256:c980d45a581830325ab30cc75fe332c79315db0b62acefb48e121bd3db3dea23 |
| GateCheck | edullm_platform.phase0_gate | record | unversioned | sha256:3dbb91b49e418557346c57de4ddf6fbfc2ecbd44056b3767df7d897e4a35f487 |
| Phase0GateReport | edullm_platform.phase0_gate | record | unversioned | sha256:2e045fc7fd5ffd738c8977378e6532c736c147b589d5794881500e93b583a59d |
| Phase0GateResult | edullm_platform.phase0_gate | record | unversioned | sha256:bb8b182761dc0a67cb9e455e030f4d3f23545ca49b09caa877f9ec01357a5f5c |
| BuildProvenanceEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:79ab5763ecdaa73fa042f2f5b3793c67ca9441389c07bb636cca28c3778e82e3 |
| DenialEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:d51a5c45e15361a25928c9ae8fdaf41d688118e48509ee3e5cd69e8a830748b0 |
| DeployedRoleEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:dff6ce8ed5758c6580a28dec55607daabfa0e8d65a029507909e2a3ac540cc37 |
| EcrImageEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:fb1eb1dbeec4765672ed2624a08a2dfd074a7cd0e54d225a5369bf21bf5880de |
| EcrLifecycleRule | edullm_platform.phase1_evidence | record | unversioned | sha256:7ffd8226788f5ca6478b6c4d81fa14b8a4360c56b0fcfbd7cd22aa00d8809ee6 |
| EcrRepositoryEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:0c5261ba9d7585898d3377fa6af37cc0c10222a22b69312c43a62a745757f7bf |
| ImageScanEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:fe319ebea128d53ad24f38b5a6ba1cb9a1d7113e2517e2be4f4e1fc1ccaa9adc |
| ImageScanFindingCounts | edullm_platform.phase1_evidence | record | unversioned | sha256:3ecbfd6c0d498de0074f167970f5124624b7821b7270d2d049d48bd6e182d07c |
| ImmutableTagRefusalEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:94005586a8fd7f73ecb15f07702063229cb43518cd9dbee032210b79d330eb70 |
| OidcSessionEvidence | edullm_platform.phase1_evidence | record | unversioned | sha256:ac5da5197bfab87802a8940e85cef7c2a98b22bba5fa1603e9e3d4b157f47856 |
| Phase1GateReport | edullm_platform.phase1_gate | record | unversioned | sha256:37e350f2a31013f33e1617a8c7d08a7f48da838e5cf37d24a793d80a3747ca0f |
| AdmissionExecution | edullm_platform.phase2_evidence | record | unversioned | sha256:b713e6adbbef4826795d41aa6a4c48780f973639cc1aa2413aca7b627c3b5d63 |
| AdmissionExecutionInventory | edullm_platform.phase2_evidence | record | unversioned | sha256:8193d6d263e3e888b068b9aa36bcaf2dfe5b88ee7d5cbeb70f32481e3de076e1 |
| EnvironmentInventory | edullm_platform.phase2_evidence | record | unversioned | sha256:a496a853cb96ec9f456ebc6fd2c62bca2cbd09221f5e63e1099b13966e9f06d0 |
| EnvironmentReviewer | edullm_platform.phase2_evidence | record | unversioned | sha256:2d2d86e7f72a582de3961f38e82b5bdb4e88cc262c5a95deeab864c5ce3417f6 |
| LeadTeamMembership | edullm_platform.phase2_evidence | record | unversioned | sha256:3b0ba7c9b7d6aa5639b6a96b13b758325661fd4fa3e341b68664d42efde91ac1 |
| LineageInventory | edullm_platform.phase2_evidence | record | unversioned | sha256:44fdfa6f1ce14ce085bf18add6364911b6e4ddb95d2915f865c545319c7526b3 |
| LineageObject | edullm_platform.phase2_evidence | record | unversioned | sha256:e8b3a5a7c45fa505cd8a7a2f3d2a8df3cf482684a693d8b96404488ac9773d06 |
| ProtectedEnvironment | edullm_platform.phase2_evidence | record | unversioned | sha256:3d60181d51ce862aa4b65d8d7cbe3da760a50552e10328d96ef5894bc735f692 |
| ResearchTeamInventory | edullm_platform.phase2_evidence | record | unversioned | sha256:e998bfbe106f60ba57bf3e84004d250f17254e287c3885f2f7b477a1e831e5f8 |
| ResearchTeamMembership | edullm_platform.phase2_evidence | record | unversioned | sha256:05c6073d33ec3aae676d1ebfe47985a5b401e48886f1a35882b2b0a6061ab6f0 |
| SecretInventory | edullm_platform.phase2_evidence | record | unversioned | sha256:ba3cec7c6b95f1761756f3938cfd445b3ba081f34d7cd5fb33e6f3c9754dad39 |
| Phase2GateReport | edullm_platform.phase2_gate | record | unversioned | sha256:237063f6f507f5fc06a78659bb898512bfaf472ad6c8c641d8cf840d049e3dbf |
| AccountMeasurements | edullm_platform.phase3_evidence | record | 1 | sha256:5b7c36029e25ad4004faea605604a6a1ea4c0a41c51ca0821661559cc356c611 |
| ActionVerdict | edullm_platform.phase3_evidence | record | unversioned | sha256:f52eda88bcd1ab4b8229db05097df2adf8120549ac32c8d9c5dedeff17b3a83f |
| AuthorizationControl | edullm_platform.phase3_evidence | record | unversioned | sha256:30fab671ec96cf057ef841200b009f492cb19d3d023dbc0fc0cf28837103cb23 |
| BatchInventory | edullm_platform.phase3_evidence | record | unversioned | sha256:c7a9bf1aa4905b1ba9991f59ff32e66417fa672cc298d18557e6156b627f9c06 |
| BatchJobEvidence | edullm_platform.phase3_evidence | record | unversioned | sha256:0b612a13e37736e59976ce179caa61b94338fa7ca03db34329fe4061728198ee |
| ComputeEnvironmentEvidence | edullm_platform.phase3_evidence | record | unversioned | sha256:2a4eb0f281994e83435ac37deefe844683608dee04f531d8b7edc1af7f823639 |
| LineageObjectAttestation | edullm_platform.phase3_evidence | record | unversioned | sha256:e0803e81aadaa05ab566ea5dbe5adfe12022721a80fe3df45154a54ccb8d8f23 |
| LogStreamEvidence | edullm_platform.phase3_evidence | record | unversioned | sha256:f7501f77b8ff258d880fe942207bf73880518870642703c6cb41a7e98c2cbd26 |
| NetworkPlacement | edullm_platform.phase3_evidence | record | unversioned | sha256:0496bd671bdc88534cbbb467b95abcad1456cafb8ae2c1b771a77041afe6eb24 |
| RefusedRunEvidence | edullm_platform.phase3_evidence | record | unversioned | sha256:79763c54cb9c9ff620675ae391fa65216ebfc61417d078a22ce36e7dafce2ee8 |
| RegionAuthorization | edullm_platform.phase3_evidence | record | unversioned | sha256:c68439705e4f321d2950f16ba1764e01647ade1cadbe42aa0f4800368acfad60 |
| RunLineageAttestation | edullm_platform.phase3_evidence | record | unversioned | sha256:cab19b085a23ba42f91f173ff76b0c8506d3982f90c0c5d2468148232075d27a |
| ServiceLinkedRoleRecord | edullm_platform.phase3_evidence | record | unversioned | sha256:84e62d941950110a6fee3edd6b455c3e377e8a6bb12234ff8eb9ff448d861402 |
| SubnetOffering | edullm_platform.phase3_evidence | record | unversioned | sha256:ab6f5c0b1a06a0a71328ecc73605f226a33c407a626d0cddd55fd337eccb8575 |
| VpcQuotaRecord | edullm_platform.phase3_evidence | record | unversioned | sha256:43cd7c39a0e005ea23e5682e8e39b63473756d4c4cb05dcf7f24dc4b114bb994 |
| Phase3GateReport | edullm_platform.phase3_gate | record | unversioned | sha256:9cb9a4ce3e638eb11b415a6e006b229adff35b919100caa6caf0eb565d336648 |
| CheckpointObservation | edullm_platform.phase4_evidence | record | unversioned | sha256:75a9326923285e8e5fdb3fc78a3e6cdf4b82503fee8c1219f7ad71e5cc131736 |
| ContainerVariable | edullm_platform.phase4_evidence | record | unversioned | sha256:a14df17755c20204d9ddbf482b1fe1e34edc795699b5aa6e15a722bf420eb851 |
| CorpusReadEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:8eb1efd6fc0db2a1f3c4581132c2b57eda590251300815fa4fc8c74676871828 |
| GpuCapabilityEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:3045fb93c5f1197a81095cf1da65d1d67cd4e666ff7097c2428cd2dfde3d6966 |
| GpuComputeEnvironmentEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:a4839a5d4d881dd67e137a64821ddaced0b340466179ba05fd1eb4dd15b6a200 |
| GpuJobEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:33d7f935aaa4e55c72cfbbe55862d481ccead69818aa621d1a06f5e8c37a8bbe |
| InstanceTypeOffering | edullm_platform.phase4_evidence | record | unversioned | sha256:152e8f5d436183f1a86854e2e0f1bc2fe0408f1308375a32cd2efec5f6278cab |
| InstanceTypeOfferingEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:aa619a8e63e530f18f9ae3b3f34b9b673f06b81e4fb4bfe25623916d75f2e7b1 |
| IsolationEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:27d2318ae24565019ee808a90da295dfa724e98a8113a00847ffe56efbd5afdd |
| OutputObject | edullm_platform.phase4_evidence | record | unversioned | sha256:c192781749f07310ed5d8ae5a6ee7c836c6cb117f56e0f384c5f993c9a314df7 |
| OutputPrefixEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:2ce39e867aae6ad02d833068aa0e68aa7498016be9cb18c0bbebe6822768483d |
| ResumeEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:8ad8f5f65f6a1f7aef5d36d049c4a1d09269a0bb217fa2ed5f092a9e220ad96d |
| SecretDeliveryEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:1b4eb420671c5194307b71dcdd37ec700d2768b7c97a5c5103cdfd110a421eff |
| TrainingSummaryEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:2e97f2b0ed364df6afaf8139f6cebfbd8137094ce7828ad8f2af146924d4fb91 |
| WorkloadRoleScopeEvidence | edullm_platform.phase4_evidence | record | unversioned | sha256:c82e1f0f32998e0cea2afa17647b446eda89170c573d76dbbbcd76b17f08b7e7 |
| Phase4GateReport | edullm_platform.phase4_gate | record | unversioned | sha256:57cbe195d2a7a6ba2b5117519165f41302c7fcac042472c7931a25831159878b |
| AdmittedRunEvidence | edullm_platform.phase5_evidence | record | unversioned | sha256:be8cc9a1a6d7d999e2deb1eacc3aedc973a46f5cb0cdffa590c91ca572c74104 |
| BranchProtectionEvidence | edullm_platform.phase5_evidence | record | unversioned | sha256:844ce60279a4834229d3fafed970f0b1e57efba02023995eacc35a84d2ecef21 |
| PublishedImageEvidence | edullm_platform.phase5_evidence | record | unversioned | sha256:edd8d7f244bd221cad534be10b7841232a22d7f1d6a3683fbddad2dac5d2734d |
| RunAuthorizationEvidence | edullm_platform.phase5_evidence | record | unversioned | sha256:916693013bebeece8892ac0b0b4aad36f6609d41bf86ee14fddd43541aceafcf |
| Phase5GateReport | edullm_platform.phase5_gate | record | unversioned | sha256:2bb7873a1f36eff0a655097499ece02ffabed6c538ddf87f47c7f8d60031e15f |
| PhaseGateReport | edullm_platform.phase_gate | base | unversioned | sha256:c3ed9d4e03917d577f70d05028d8a450ea36034d606ba54701f671c65da4ca0c |
| AttemptedDenial | edullm_platform.publisher_denials | record | unversioned | sha256:f0b497787467fde6f343ddec8552ead542f393a7ed111e59cc7b74041107fe69 |
| PublisherDenialMatrix | edullm_platform.publisher_denials | record | 1 | sha256:66bcc2645e9e044e23cd10338e2041e7a236528989f5ad2013f0d1d292d354da |
| ConfigurationField | edullm_platform.rebuild_comparison | record | unversioned | sha256:04c684f0cfe10bf5d4afbc0a8885fc89150cf49966d76690a30f104c132478c1 |
| LocalRebuildComparison | edullm_platform.rebuild_comparison | record | 1 | sha256:8585e161aa3a3b8608869b06434ae517694bf094d196b8fb7da55dda51dd2c6b |
| RebuiltImage | edullm_platform.rebuild_comparison | record | unversioned | sha256:ba0d657955630da264698dfbb7b85dc6dd70cc0401cbaf3d8f23db75cba16d4e |
| RoleDriftFinding | edullm_platform.role_drift | record | unversioned | sha256:4572586fb60d4c8b381ac0680119aa19c5b3009343767fb6d3c1b301a35cb5d0 |
| RoleDriftReport | edullm_platform.role_drift | record | unversioned | sha256:f0e5b2e3ec53486f5dfa7a880c32d594f002bba71f1b1ff71285673a6cf5fe27 |
| TemplateRole | edullm_platform.role_drift | record | unversioned | sha256:5e12a30b611a50e621b89faa4c18987f91b429d710bd748f4342fae2821a8e9a |
| ComparedField | edullm_platform.run_comparison | record | unversioned | sha256:25693216d0652deffbf2bf769ad65433205dc1d93170d61d77c86878efb0dda9 |
| RecordField | edullm_platform.run_comparison | record | unversioned | sha256:85ed7e23a9932d370881ca8c98bf0226dde5f926d00f5fd32ac84f1091d7136f |
| RecordedRun | edullm_platform.run_comparison | record | unversioned | sha256:33c8621f4396a4f96fb63fd05689875253b4f2133180ab57006aee9b80e97630 |
| TwoRunComparison | edullm_platform.run_comparison | record | 1 | sha256:f68980a6ae7a884289de94fea97afe355ed1f5f00edebd4ce45cfc6b0c36cddb |

## Exported JSON Schema files

The checked-in schemas under `schemas/`, with the digest of each file as generated. `tests/test_schema_export.py::test_checked_in_schemas_match_contract_models` fails if a file drifts from its model.

| file | root model | file digest |
| --- | --- | --- |
| schemas/batch-job-binding.schema.json | BatchJobBinding | sha256:41a20845192e959dd91b32f87d57da0cf3b04e5a2177e0200f27aec2cece901c |
| schemas/checkpoint-manifest.schema.json | CheckpointManifest | sha256:f51cc8c500c81cd1d286dc86c59ff5f24b09a640127034f1527494251d76a07e |
| schemas/datasets.schema.json | DatasetRegistry | sha256:12bd81126ee022c25cc0b8bc3b8e35b446f3c672a840cf13afcc88e4e690823c |
| schemas/decision-record.schema.json | DecisionRecord | sha256:f0290fb71a610aa1cc133600c28caf55e388535ddf1153ae62cffcd606499cd5 |
| schemas/image-exceptions.schema.json | ImageScanExceptionRegistry | sha256:e57e7388a87f331a31c9f1e871065306421772bf1da3a043b46f121bde1ffb17 |
| schemas/intent-record.schema.json | IntentRecord | sha256:17ffacfd445b5ec8b2ebc585e994b2a3362e231a4215c09a2b030cac93ea2b12 |
| schemas/lifecycle-event.schema.json | LifecycleEvent | sha256:f747e330743b4f471021b38e161d26e24ecda8fb47ab02b08939ee298a1921ab |
| schemas/logical-run.schema.json | LogicalRun | sha256:898f1d6b338ea810a75c0614035a49e0812147aef7816037c97447a602d37688 |
| schemas/organization.schema.json | OrganizationInventory | sha256:37c30582f008b541fe11a1403f5311026ae908d98e8821b4ed6842c3d4365e66 |
| schemas/policy.schema.json | ApprovalPolicy | sha256:8a99f30cbfad406c46853fc115e3cdba9e380f793bf0b34bc7bc231df8eff6ff |
| schemas/repositories.schema.json | RepositoryRegistry | sha256:ee5ef9172b9ab89aa0965cefda9d86fda855c4cd3f0eeda41ab50551327ff68e |
| schemas/result-manifest.schema.json | ResultManifest | sha256:38421f82a8c861df341fcfd514e9a1bb621f7ced55258b20061d310d0c61b7f9 |
| schemas/run-manifest.schema.json | RunManifest | sha256:7f6795c9a7a246b2670bc181f19f07ef16086b233b6d14d1d8def41971b04769 |
| schemas/scheduler-attempt.schema.json | SchedulerAttempt | sha256:91984a9fb1f7f9150f7799dc337807bd14b93b50908a56e0e230391546c9c4ac |
| schemas/submission-inputs.schema.json | SubmissionInputs | sha256:02585ab3655cb95c5cf574c9e4906f6709ad54973db5cf353b868cd747880fe7 |
| schemas/workload-catalog.schema.json | WorkloadCatalog | sha256:2345ae1bd3a2985fa55bdb2c7b7e8bba4127c925ba43f086929f443fa32d1384 |

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
