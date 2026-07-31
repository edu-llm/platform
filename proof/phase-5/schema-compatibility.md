# Phase 5 schema compatibility report

The five contract models defined by the modules this bundle's evidence is built from, so that a reviewer can check a shape without reading the whole inventory. The structural digest is `sha256` over the model's JSON schema with sorted keys, so it changes when a field is added, removed, retyped or reconstrained, and does not change when unrelated code moves.

What scopes this table is where code sits today, not a record of what the phase delivered. It was introduced for a long time as the contract models the phase added, which is a question it cannot answer: the only thing it knows about a model is which module the model is in now, so moving one to another file changed the count without any phase having delivered anything different. It is a compatibility view over the complete inventory in `proof/phase-0/schema-compatibility.md`, and `tests/test_schema_compatibility.py` fails when either table stops describing the tree.

`AdmittedRunEvidence` is the one worth reading. It is the only record in this repository that spans two systems -- the lineage store and the scheduler -- and it does so because the central Phase 5 claim is a comparison between them. Splitting it would put the two halves of one assertion into two records that nothing requires to be about the same run.

| model | module | kind | schema_version | exported | structural digest |
| --- | --- | --- | --- | --- | --- |
| AdmittedRunEvidence | edullm_platform.phase5_evidence | record | unversioned | no | sha256:be8cc9a1a6d7d999e2deb1eacc3aedc973a46f5cb0cdffa590c91ca572c74104 |
| BranchProtectionEvidence | edullm_platform.phase5_evidence | record | unversioned | no | sha256:844ce60279a4834229d3fafed970f0b1e57efba02023995eacc35a84d2ecef21 |
| PublishedImageEvidence | edullm_platform.phase5_evidence | record | unversioned | no | sha256:edd8d7f244bd221cad534be10b7841232a22d7f1d6a3683fbddad2dac5d2734d |
| RunAuthorizationEvidence | edullm_platform.phase5_evidence | record | unversioned | no | sha256:916693013bebeece8892ac0b0b4aad36f6609d41bf86ee14fddd43541aceafcf |
| Phase5GateReport | edullm_platform.phase5_gate | record | unversioned | no | sha256:2bb7873a1f36eff0a655097499ece02ffabed6c538ddf87f47c7f8d60031e15f |
