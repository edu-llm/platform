# Phase 2 schema compatibility report

The fifteen contract models defined by the modules this bundle's evidence is built from, so that a reviewer can check a shape without reading the whole inventory. The structural digest is `sha256` over the model's JSON schema with sorted keys, so it changes when a field is added, removed, retyped or reconstrained, and does not change when unrelated code moves.

What scopes this table is where code sits today, not a record of what the phase delivered. It was introduced for a long time as the contract models the phase added, which is a question it cannot answer: the only thing it knows about a model is which module the model is in now, so moving one to another file changed the count without any phase having delivered anything different. It is a compatibility view over the complete inventory in `proof/phase-0/schema-compatibility.md`, and `tests/test_schema_compatibility.py` fails when either table stops describing the tree.

| model | module | kind | schema_version | exported | structural digest |
| --- | --- | --- | --- | --- | --- |
| AdmissionDenialMatrix | edullm_platform.admission_denials | record | 1 | no | sha256:fe33252d54657119ea0f49b18d8134909df709834f70af1d1ed4fc1c10c40b11 |
| DecisionRecord | edullm_platform.contracts.admission | record | 1 | yes | sha256:9482e02ae58fdbe3a8876cf2a10d13b2c383bcd3d724386497c52c5f278611de |
| IntentRecord | edullm_platform.contracts.admission | record | 1 | yes | sha256:ea5ee7114ae524a14e28691db4445c53a582ee8eef65f299653034cd0db58b58 |
| AdmissionExecution | edullm_platform.phase2_evidence | record | unversioned | no | sha256:b713e6adbbef4826795d41aa6a4c48780f973639cc1aa2413aca7b627c3b5d63 |
| AdmissionExecutionInventory | edullm_platform.phase2_evidence | record | unversioned | no | sha256:8193d6d263e3e888b068b9aa36bcaf2dfe5b88ee7d5cbeb70f32481e3de076e1 |
| EnvironmentInventory | edullm_platform.phase2_evidence | record | unversioned | no | sha256:a496a853cb96ec9f456ebc6fd2c62bca2cbd09221f5e63e1099b13966e9f06d0 |
| EnvironmentReviewer | edullm_platform.phase2_evidence | record | unversioned | no | sha256:2d2d86e7f72a582de3961f38e82b5bdb4e88cc262c5a95deeab864c5ce3417f6 |
| LeadTeamMembership | edullm_platform.phase2_evidence | record | unversioned | no | sha256:fcf1ab4981bb9d42b15966eebdd656a42617c7bec19816df63a6fff58158c8cc |
| LineageInventory | edullm_platform.phase2_evidence | record | unversioned | no | sha256:44fdfa6f1ce14ce085bf18add6364911b6e4ddb95d2915f865c545319c7526b3 |
| LineageObject | edullm_platform.phase2_evidence | record | unversioned | no | sha256:e8b3a5a7c45fa505cd8a7a2f3d2a8df3cf482684a693d8b96404488ac9773d06 |
| ProtectedEnvironment | edullm_platform.phase2_evidence | record | unversioned | no | sha256:3d60181d51ce862aa4b65d8d7cbe3da760a50552e10328d96ef5894bc735f692 |
| ResearchTeamInventory | edullm_platform.phase2_evidence | record | unversioned | no | sha256:e998bfbe106f60ba57bf3e84004d250f17254e287c3885f2f7b477a1e831e5f8 |
| ResearchTeamMembership | edullm_platform.phase2_evidence | record | unversioned | no | sha256:05c6073d33ec3aae676d1ebfe47985a5b401e48886f1a35882b2b0a6061ab6f0 |
| SecretInventory | edullm_platform.phase2_evidence | record | unversioned | no | sha256:ba3cec7c6b95f1761756f3938cfd445b3ba081f34d7cd5fb33e6f3c9754dad39 |
| Phase2GateReport | edullm_platform.phase2_gate | record | unversioned | no | sha256:237063f6f507f5fc06a78659bb898512bfaf472ad6c8c641d8cf840d049e3dbf |

`IntentRecord` and `DecisionRecord` are the two a reviewer should read closely: they are the audit trail, they are written once and never rewritten, and a field retyped after a record is in the store is a field the store's older objects no longer satisfy. That is not hypothetical here -- `CostInputs` had to be taught to accept a recorded total, because `maximum_compute_cost_usd` is computed and pydantic refused every decision record in the store on the way back in.
