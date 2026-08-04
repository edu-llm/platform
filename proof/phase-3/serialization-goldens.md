# Phase 3 golden canonical digests

The canonical JSON digest of each of the four roles Phase 3 adds, taken over the projection the drift comparison acts on rather than over the file.

A comment, a reordered key or a whitespace change alters the file and not the projection, and does not land here. A statement that grants one more action alters the projection whatever it does to the file, and does. The digest is `sha256` over `canonical_json_bytes(TemplateRole)`, the same function that produces manifest digests in lineage records.

This tripwire is worth more in Phase 3 than it was in Phase 1, for a reason particular to this moment: none of these roles is deployed, so there is no capture to compare any of them against and the drift comparison has nothing to run on. Until the laptop deploy lands, the recorded digest is the only thing standing between a template widened in the meantime and nobody noticing.

| role | template | canonical bytes | digest |
| --- | --- | --- | --- |
| sbsandbox-intern-edullm-batch-execution | infra/iam/batch-roles.yaml | 2156 | sha256:e787c6b2bc7fc54c7932cefd858f7ef5828543008514026073a0b86db43a5f8a |
| sbsandbox-intern-edullm-batch-workload | infra/iam/batch-roles.yaml | 1740 | sha256:b0b7adec52febf7124a90ce0aa0f3352087febde504105f6ce45aa85468cc100 |
| sbsandbox-intern-edullm-batch-instance | infra/iam/batch-roles.yaml | 2382 | sha256:036bb85baec47faeaca22f89c683a12237e19f38b59eb2742e0140db166badf8 |
| sbsandbox-intern-edullm-lifecycle-lambda | infra/iam/lifecycle-lambda-role.yaml | 2004 | sha256:60c744aa4376c4d015989fb2c2a1896e1daf196bc4f44dc2133448d32879ad7e |

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
