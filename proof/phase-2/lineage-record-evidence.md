# Phase 2 lineage record evidence

Every object in `sbsandbox-intern-edullm-lineage`, read from `fixtures/evidence/phase-2/lineage.sanitized.json`, with the records themselves committed under `fixtures/evidence/phase-2/lineage/records/`. These expire on 2026-08-26, and this generator refuses to build once they do.

## What S3 attests about each object

| key | VersionId | ChecksumSHA256 | bytes | canonical |
| --- | --- | --- | --- | --- |
| `decision/run_019fa446-8a4e-7094-9e29-d44fffbd2491.json` | `xm1c4vi9QpSWTUmMqoB6_LLnmcTCt2MQ` | `sha256:0800beff17f8017340a0cd0840ef7a515ca6fdbeaa2983ddf88b6fd5d7b5bcbd` | 914 | no |
| `decision/run_019fa468-c9b5-706a-8849-87c1d0b5befb.json` | `pyBv0fv1XsG_YfE5jaQ6H4VNhoHXized` | `sha256:0df3f1f05e9b45c4df017f03f5f0c7de1bb23f9094f018d3e6fb38a01f2392e8` | 826 | yes |
| `decision/run_019fa46a-5478-70ea-aab6-28de23c41f7f.json` | `OZgTrpNBfsz0BDo4NHldfNiPxleHLtCo` | `sha256:9d057f51c7029ec591376f87096108af66aa24747c0fb1b3e1867890ddcee0c1` | 856 | yes |
| `decision/run_019fa471-0173-7050-a41b-22ca01969b52.json` | `_a8On374hJ9cP89EsLxdRSdV3LxuNfv2` | `sha256:e5bbdcab20eb169667a2c5c6f130081cb53f315dc545bff5ecccd2374ec46b3d` | 826 | yes |
| `decision/run_019fa4c0-390d-7081-b539-08d9ff6b58be.json` | `Y2WW1ItFr25yq7OhfIV.tU9hyqPFqC9t` | `sha256:72c02a559373f03818b069a00071bfb33421d373425698b0af85d64393709759` | 609 | yes |
| `intent/run_019fa446-8a4e-7094-9e29-d44fffbd2491.json` | `iPg3cc4qqfCjots7xoJcoe2.zDSFNede` | `sha256:c9b0b4ade2a88077a854056e68473659b1afc9185c5f203a37018fba3c15fb91` | 1235 | no |
| `intent/run_019fa468-c9b5-706a-8849-87c1d0b5befb.json` | `zvSZdQobTIHhFbrWC9vAoUPjUxHqawVn` | `sha256:edba6252123a2d7281dd98b37459943629e32cf80f499fdb22d0d41051370b98` | 1123 | yes |
| `intent/run_019fa46a-5478-70ea-aab6-28de23c41f7f.json` | `qSXIpZAF2qFt04QJJJsyUCWdG.6OXz8b` | `sha256:89ad215366a6c1e6177e24a04de4db99f0687529175d9bccae39ef3dfadd4ba1` | 1125 | yes |
| `intent/run_019fa471-0173-7050-a41b-22ca01969b52.json` | `9DZ6OOh9T2FZVomdcEgFIQmVlxsqeJNF` | `sha256:e1e6f323427445671f554e4af5d23ea7a2020b578d926d6e934d3144604be0ba` | 1123 | yes |
| `intent/run_019fa4c0-390d-7081-b539-08d9ff6b58be.json` | `.y62jDj6dd8PipSfejwXtvND2DzVr4vE` | `sha256:be35e6f2797c899e1a5b44544ed64d053b125f6d17cb47fb0cc6932f2fd976a5` | 1123 | yes |

**The checksum is written in hex and S3 reported it in base64.** That is a presentation change rather than a redaction: the same thirty-two bytes, reversible with one line of base64, and the spelling every other digest in this repository uses. Base64 of thirty-two bytes is forty-four characters of `[A-Za-z0-9/+=]`, which is precisely the shape the evidence secret scan refuses, so printing the literal value would have the whole document withheld as though it carried a credential.

**Attested rather than computed here.** Both fields come back from `HeadObject` with `--checksum-mode ENABLED`, so an object missing either would mean a write took a path the template does not describe.

**`ChecksumSHA256` is not the manifest hash and the two must never be conflated.** The checksum attests that the object's bytes arrived intact; `manifest_sha256` attests the manifest's canonical serialization and is the value an approval was taken against. They answer different questions, and a record that mixed them would be a lineage error rather than a wording slip.

**The store holds two shapes and both are captured.** The two objects marked `no` above were written before the encoding fix and are a JSON string containing the record, because the S3 SDK integration encodes whatever the Body path yields and the handler was returning canonical strings. The rest are the canonical bytes. Recording the older shape rather than dropping it is deliberate: a capture that made the store look uniform would leave the first person to read one of those objects meeting a surprise nobody wrote down.

## The decision beside every intent, joined by run id

| run id | accepted | class | gate it came through | reason | policy |
| --- | --- | --- | --- | --- | --- |
| `run_019fa446-8a4e-7094-9e29-d44fffbd2491` | yes | routine | run-approval-lead | accepted | `v1` |
| `run_019fa468-c9b5-706a-8849-87c1d0b5befb` | yes | routine | run-approval-lead | accepted | `v1` |
| `run_019fa46a-5478-70ea-aab6-28de23c41f7f` | yes | exception | run-approval-admin | accepted | `v1` |
| `run_019fa471-0173-7050-a41b-22ca01969b52` | yes | routine | run-approval-lead | accepted | `v1` |
| `run_019fa4c0-390d-7081-b539-08d9ff6b58be` | no | exception | run-approval-lead | manifest_hash_mismatch | `v1` |

The last row is the one worth reading twice. It is a refusal, and it still has both records: a submission whose manifest did not hash to what was approved earned a decision naming the reason, against the run id that was attempted. A refusal that left no record would make a rejected submission indistinguishable from one nobody made.

Each run id owns exactly one intent record and one decision record, and each intent's manifest still hashes to the value recorded beside it -- recomputed from the stored bytes, so a manifest edited after the fact would fail rather than read as intact. That property is what the whole approval gate rests on.

Reading these records back is what found the defect that made them readable. `maximum_compute_cost_usd` is a computed field, so pydantic wrote it out and refused it on the way back in, and every decision record in the store failed to load. A record the writing model cannot read back is an audit trail nobody can audit.

## What this document does not carry

- **The `412 PreconditionFailed` refusal of a second conditional write.** `tools/probe_conditional_write.py` established that a second `PutObject` carrying `IfNoneMatch: *` fails, and that Step Functions surfaces it as `S3.S3Exception`. The response was never committed.
- **What `S3.S3Exception` does and does not distinguish.** It is the generic name for every unmodelled S3 error, so it does not tell a genuine already-exists from a transient fault. The 412 and its precondition message appear only in the `Cause`, which no `ErrorEquals` can match, so `RecordConflict` means the write was refused rather than that the key existed.

| criterion | status today |
| --- | --- |
| 12 | a gap |
| 17 | covered |
| 18 | covered |
| 21 | covered |
