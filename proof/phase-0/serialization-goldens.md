# Phase 0 golden canonical digests

The canonical JSON digest of every one of the 9 shipped fixtures, recorded so that a later build can be compared against this one.

The digest is `sha256` over `canonical_json_bytes(model)`: the validated contract dumped in JSON mode with aliases, null fields kept, keys sorted, and compact separators. It is the same function that produces manifest digests in lineage records, so a drift here is a drift there.

| fixture | contract | canonical bytes | digest |
| --- | --- | --- | --- |
| fixtures/authorization/admin-exception.yaml | AuthorizationScenario | 619 | sha256:397e747f9183f3e06af807a0667cb604c7ed46d11eb723c1eb39c5ebaa32941b |
| fixtures/authorization/lead-self-authorization.yaml | AuthorizationScenario | 566 | sha256:ee4e732bc5f188a4e8e0b952406fec7da4ffcd6459a8a90e82a2ea520b4d273f |
| fixtures/authorization/member-approval.yaml | AuthorizationScenario | 619 | sha256:cd310ce8607856afe52366786265538f36937c7a7a3cca9e26dc95ca0509b185 |
| fixtures/manifests/cpu-routine.yaml | RunManifest | 614 | sha256:cb8eb2f73f14707478e457a31ca30a58b954b2109c86a444c841adc6661cf277 |
| fixtures/manifests/gpu-exception.yaml | RunManifest | 633 | sha256:1bbe9c4c3cc84cc16ded6c9076b5940244f4388658833bd60fffec2f74733db8 |
| fixtures/manifests/gpu-routine.yaml | RunManifest | 620 | sha256:69fd7d9a19741789dae24fa843eb831cf6a968cffb0bca3890b08761e84b9f90 |
| fixtures/manifests/multiseed-routine.yaml | RunManifest | 695 | sha256:47b1c40ec11529eb9b798a0ee1094ef42befa1c20d4626bf919b5e58d5d52fa0 |
| fixtures/manifests/olmo-branch-routine.yaml | RunManifest | 638 | sha256:b82bb9812dd3b7239457b9a3a2e51885f065e455f73e39f4a8933a05d6dc27b2 |
| fixtures/manifests/sagemaker-routine.yaml | RunManifest | 630 | sha256:dd9dc83aa569762a8844bef532bdf11a5bff4e3531f0db867de735bef25a1626 |

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
