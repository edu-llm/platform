# Phase 0 golden canonical digests

The canonical JSON digest of every one of the 9 shipped fixtures, recorded so that a later build can be compared against this one.

The digest is `sha256` over `canonical_json_bytes(model)`: the validated contract dumped in JSON mode with aliases, null fields kept, keys sorted, and compact separators. It is the same function that produces manifest digests in lineage records, so a drift here is a drift there.

| fixture | contract | canonical bytes | digest |
| --- | --- | --- | --- |
| fixtures/authorization/admin-exception.yaml | AuthorizationScenario | 675 | sha256:b081f8d1e067939aac57abb9afdaf09fc43a623957330fac9f6765aefbdad836 |
| fixtures/authorization/lead-self-authorization.yaml | AuthorizationScenario | 622 | sha256:9aebc09ec8548896997f7efdfa4921448012d66ef6610b2c7fb58536daddb288 |
| fixtures/authorization/member-approval.yaml | AuthorizationScenario | 675 | sha256:36601ad56b263ac3c9e463983b74689aad775a0c06252f811221ef27456b7d00 |
| fixtures/manifests/cpu-routine.yaml | RunManifest | 626 | sha256:44f5da44d110a82934b8876529caf2eed09ac03fc5ac0aafb5b3b87694283352 |
| fixtures/manifests/gpu-exception.yaml | RunManifest | 628 | sha256:9f98a7b409acaebe5633a8291e1b25cb6ff98d962942779402049c95b08c1a36 |
| fixtures/manifests/gpu-routine.yaml | RunManifest | 618 | sha256:e95cc487f936490f236b6a01e1dd526c3dcdfda2a80f12320744b6ed0ba7941a |
| fixtures/manifests/multiseed-routine.yaml | RunManifest | 672 | sha256:ee03afcb6d2d07a40c21a5de43c9edd160fa74afa74ed00d87292cadb635c49a |
| fixtures/manifests/olmo-branch-routine.yaml | RunManifest | 634 | sha256:117f312a6453eba2851e5d875082cdf08500170b4eab2c9563695b06407a1643 |
| fixtures/manifests/sagemaker-routine.yaml | RunManifest | 628 | sha256:21dacc0c6c5018242bc7be920fb84f7eb4a1c53b11401c5d445e01b80a6320c2 |

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
