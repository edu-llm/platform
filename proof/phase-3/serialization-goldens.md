# Phase 3 golden canonical digests

The canonical JSON digest of each of the four roles Phase 3 adds, taken over the projection the drift comparison acts on rather than over the file.

A comment, a reordered key or a whitespace change alters the file and not the projection, and does not land here. A statement that grants one more action alters the projection whatever it does to the file, and does. The digest is `sha256` over `canonical_json_bytes(TemplateRole)`, the same function that produces manifest digests in lineage records.

This tripwire is worth more in Phase 3 than it was in Phase 1, for a reason particular to this moment: none of these roles is deployed, so there is no capture to compare any of them against and the drift comparison has nothing to run on. Until the laptop deploy lands, the recorded digest is the only thing standing between a template widened in the meantime and nobody noticing.

| role | template | canonical bytes | digest |
| --- | --- | --- | --- |
| sbsandbox-intern-edullm-batch-execution | infra/iam/batch-roles.yaml | 1716 | sha256:fd41e2321e4fe42b9639ee69b1867fdc2d90a7c35194599fd3a65c940fbeff79 |
| sbsandbox-intern-edullm-batch-workload | infra/iam/batch-roles.yaml | 1151 | sha256:c8db5c5373fe66028d398ff1d0c4650db78203516cf10d761e8f937c2eed4ea6 |
| sbsandbox-intern-edullm-batch-instance | infra/iam/batch-roles.yaml | 1942 | sha256:e9eb886361d48c844ba027eebe0562fcc54a7afe9c5462cf539f21dab3190cf5 |
| sbsandbox-intern-edullm-lifecycle-lambda | infra/iam/lifecycle-lambda-role.yaml | 1684 | sha256:818390fae8b9ff8160acf7b7611a1be49355a66ce5ac888ee7d43f9083953be1 |

## How this fails

`serialization-goldens.json` in this directory is the machine-readable copy. `tests/test_phase3_golden.py` reprojects each template, recomputes its digest and compares it to the recorded value, one test per role so a failure names the role rather than the batch.

`uv run python tools/build_phase3_proof.py` refuses to overwrite a drifted digest. Re-recording requires `--regenerate-goldens`, so a change to what a role may do cannot be absorbed by re-running the generator.

```
<role> (<contract>) no longer serializes to its recorded canonical digest.
  recorded: <recorded digest>
  live:     <live digest>

This is a serialization tripwire, not a formatting check. A change to field ordering, to a
serializer, to a default value, or to the fixture itself lands here and nowhere else.

Do exactly one of these, deliberately:

  1. The change was intended. Re-record with
       uv run python tools/build_phase3_proof.py --regenerate-goldens
     and review the digest diff in the same commit as the change that caused it, so the new
     digest is approved by a human rather than absorbed silently.

  2. The change was not intended. This is a regression: fix it instead of re-recording.
     Every digest already written into a proof bundle, a run manifest reference, or a
     lineage record disagrees with this build until you do.
```
