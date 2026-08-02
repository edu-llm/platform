# Phase 0 golden canonical digests

The canonical JSON digest of every one of the 9 shipped fixtures, recorded so that a later build can be compared against this one.

The digest is `sha256` over `canonical_json_bytes(model)`: the validated contract dumped in JSON mode with aliases, null fields kept, keys sorted, and compact separators. It is the same function that produces manifest digests in lineage records, so a drift here is a drift there.

| fixture | contract | canonical bytes | digest |
| --- | --- | --- | --- |
| fixtures/authorization/admin-exception.yaml | AuthorizationScenario | 675 | sha256:285430eaad152bbadeb0bddf0462add88cb17cb0d6a29150c94045d1c76ecbe6 |
| fixtures/authorization/lead-self-authorization.yaml | AuthorizationScenario | 622 | sha256:8d5f66bca66e494144cf1604a4ac57027b363409e50596bb9e1ce5e4c19738ac |
| fixtures/authorization/member-approval.yaml | AuthorizationScenario | 675 | sha256:36601ad56b263ac3c9e463983b74689aad775a0c06252f811221ef27456b7d00 |
| fixtures/manifests/cpu-routine.yaml | RunManifest | 630 | sha256:abddcbad40510042b36d9a482f27c2d96776125f0edcd2050635d136ffee9c13 |
| fixtures/manifests/gpu-exception.yaml | RunManifest | 633 | sha256:883f37fed958b43f808d35f094c7db35d30b186fbd7007d1ef4f5334070a298c |
| fixtures/manifests/gpu-routine.yaml | RunManifest | 623 | sha256:93104f1cdc56c72b9130801d043c368214f7bd2ab5ae3bae30796219a6feadbd |
| fixtures/manifests/multiseed-routine.yaml | RunManifest | 694 | sha256:108ae519bfcf00355b90b6f8faa73f83654db2e1e10a1b916863ba4d084ba87d |
| fixtures/manifests/olmo-branch-routine.yaml | RunManifest | 639 | sha256:b99191c1de896a919b31dfaadd8e89305daf22de6f46fdee1c1bb3116031d265 |
| fixtures/manifests/sagemaker-routine.yaml | RunManifest | 633 | sha256:0dc978d125e14726c244b1dd08fb273ec1aa56cbc99c7a17c1886be0a1ec84d9 |

## How this fails

`serialization-goldens.json` in this directory is the machine-readable copy. `tests/test_phase0_golden.py` reloads every fixture, recomputes its digest, and compares it to the recorded value, one test per fixture so a failure names the fixture rather than the batch. A change to field ordering, to a serializer, or to a default value fails there by name.

`uv run python tools/build_phase0_proof.py` refuses to overwrite a drifted digest. Re-recording is a deliberate act that requires `--regenerate-goldens`, so a regression cannot be absorbed by re-running the generator.

The failure message tells the reader which of the two situations they are in and what to do about each:

```
<fixture> (<contract>) no longer serializes to its recorded canonical digest.
  recorded: <recorded digest>
  live:     <live digest>

This is a serialization tripwire, not a formatting check. A change to field ordering, to a
serializer, to a default value, or to the fixture itself lands here and nowhere else.

Do exactly one of these, deliberately:

  1. The change was intended. Re-record with
       uv run python tools/build_phase0_proof.py --regenerate-goldens
     and review the digest diff in the same commit as the change that caused it, so the new
     digest is approved by a human rather than absorbed silently.

  2. The change was not intended. This is a regression: fix it instead of re-recording.
     Every digest already written into a proof bundle, a run manifest reference, or a
     lineage record disagrees with this build until you do.
```
