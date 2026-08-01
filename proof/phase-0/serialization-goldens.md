# Phase 0 golden canonical digests

The canonical JSON digest of every one of the 9 shipped fixtures, recorded so that a later build can be compared against this one.

The digest is `sha256` over `canonical_json_bytes(model)`: the validated contract dumped in JSON mode with aliases, null fields kept, keys sorted, and compact separators. It is the same function that produces manifest digests in lineage records, so a drift here is a drift there.

| fixture | contract | canonical bytes | digest |
| --- | --- | --- | --- |
| fixtures/authorization/admin-exception.yaml | AuthorizationScenario | 646 | sha256:d8651178b3bccf203aca41453ad69fffb46f1e931042627e83990749232f9166 |
| fixtures/authorization/lead-self-authorization.yaml | AuthorizationScenario | 593 | sha256:d7f4783cffc2ab6f14fc60e1c797fc462a705bbcd5f69fe6093b37b37f335553 |
| fixtures/authorization/member-approval.yaml | AuthorizationScenario | 646 | sha256:36ae0b620598e58ecad16c936c6d74f5f837a1588d9aac391a3d18f4a39a32b5 |
| fixtures/manifests/cpu-routine.yaml | RunManifest | 630 | sha256:abddcbad40510042b36d9a482f27c2d96776125f0edcd2050635d136ffee9c13 |
| fixtures/manifests/gpu-exception.yaml | RunManifest | 632 | sha256:e9b232825543128bc4e86d0bfe3460ea5f2e94930f89033be4c2cdd514d066ec |
| fixtures/manifests/gpu-routine.yaml | RunManifest | 619 | sha256:006b3d8316e154e8462458d98c609d24fa20174ef503eed7f27cc726bb9f0a2f |
| fixtures/manifests/multiseed-routine.yaml | RunManifest | 694 | sha256:108ae519bfcf00355b90b6f8faa73f83654db2e1e10a1b916863ba4d084ba87d |
| fixtures/manifests/olmo-branch-routine.yaml | RunManifest | 637 | sha256:96a5d23d8044bde8e840743e005e6d8f796f9de19240bdffe2ceba8e7ab1b683 |
| fixtures/manifests/sagemaker-routine.yaml | RunManifest | 629 | sha256:e94d6e83ec7c81baf5aa7f0ef910a0b5f8da0bcc41bb8dbddc55f768253d6d3b |

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
