# Phase 1 schema compatibility report

The 28 contract models Phase 1 added. The structural digest is `sha256` over the model's JSON schema with sorted keys, so it changes when a field is added, removed, retyped or reconstrained, and does not change when unrelated code moves.

None of these is exported to `schemas/`. Those files describe what a human authors — the organization, the workload catalog, the policy, a run manifest — and nobody writes an evidence record by hand. The repository-wide inventory, including every Phase 0 contract, is in `proof/phase-0/schema-compatibility.md`; it is not repeated here, because a second copy is a copy that goes stale.

| model | module | kind | schema_version | structural digest |
| --- | --- | --- | --- | --- |
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
| Phase1GateReport | edullm_platform.phase1_gate | record | unversioned | sha256:272f0f0197f14c273859796b1e7c563015d86233b0e15870c9de39618ef1de44 |
| AttemptedDenial | edullm_platform.publisher_denials | record | unversioned | sha256:f0b497787467fde6f343ddec8552ead542f393a7ed111e59cc7b74041107fe69 |
| PublisherDenialMatrix | edullm_platform.publisher_denials | record | 1 | sha256:66bcc2645e9e044e23cd10338e2041e7a236528989f5ad2013f0d1d292d354da |
| ConfigurationField | edullm_platform.rebuild_comparison | record | unversioned | sha256:04c684f0cfe10bf5d4afbc0a8885fc89150cf49966d76690a30f104c132478c1 |
| LocalRebuildComparison | edullm_platform.rebuild_comparison | record | 1 | sha256:8585e161aa3a3b8608869b06434ae517694bf094d196b8fb7da55dda51dd2c6b |
| RebuiltImage | edullm_platform.rebuild_comparison | record | unversioned | sha256:ba0d657955630da264698dfbb7b85dc6dd70cc0401cbaf3d8f23db75cba16d4e |
| RoleDriftFinding | edullm_platform.role_drift | record | unversioned | sha256:4572586fb60d4c8b381ac0680119aa19c5b3009343767fb6d3c1b301a35cb5d0 |
| RoleDriftReport | edullm_platform.role_drift | record | unversioned | sha256:f0e5b2e3ec53486f5dfa7a880c32d594f002bba71f1b1ff71285673a6cf5fe27 |
| TemplateRole | edullm_platform.role_drift | record | unversioned | sha256:5e12a30b611a50e621b89faa4c18987f91b429d710bd748f4342fae2821a8e9a |
