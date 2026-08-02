# Phase 2 golden canonical digests

The canonical JSON digest of each of the three roles Phase 2 adds, taken over the projection the drift comparison acts on rather than over the file.

A comment, a reordered key or a whitespace change alters the file and not the projection, and does not land here. A statement that grants one more action alters the projection whatever it does to the file, and does. The digest is `sha256` over `canonical_json_bytes(TemplateRole)`, the same function that produces manifest digests in lineage records.

This tripwire is doing more work in Phase 2 than in Phase 1, and it is worth understanding why. All three roles are deployed -- they were created from a laptop on 2026-07-27 -- and none of them has been captured, so the comparison that would catch one widened in the console has nothing to run on. Until a capture lands, the recorded digest catches a template that changed and says nothing at all about the account.

| role | template | canonical bytes | digest |
| --- | --- | --- | --- |
| sbsandbox-intern-edullm-admission | infra/iam/admission-role.yaml | 2124 | sha256:e3842507a5b258bfda00b2526fdfdebc88f97bcdc4ad6c2829dcd60e55a453c6 |
| sbsandbox-intern-edullm-admission-states | infra/iam/admission-service-roles.yaml | 11138 | sha256:117ead92b27b72bb1156f17bcd70df0e6da01e86fb6cc9af205d7e1feb9fd21e |
| sbsandbox-intern-edullm-admission-lambda | infra/iam/admission-service-roles.yaml | 918 | sha256:2eca3d6d95954cabaf13148f99c380a90d4fe6049c254c195928adee1f81ec4c |

## How this fails

`serialization-goldens.json` in this directory is the machine-readable copy. `tests/test_phase2_proof.py` reprojects each template, recomputes its digest and compares it to the recorded value.

`uv run python tools/build_phase2_proof.py` refuses to overwrite a drifted digest. Re-recording requires `--regenerate-goldens`, so a change to what a role may do cannot be absorbed by re-running the generator.

```
<role> (<contract>) no longer serializes to its recorded canonical digest.
  recorded: <recorded digest>
  live:     <live digest>

This is a serialization tripwire, not a formatting check. A change to field ordering, to a
serializer, to a default value, or to the fixture itself lands here and nowhere else.

Do exactly one of these, deliberately:

  1. The change was intended. Re-record with
       uv run python tools/build_phase2_proof.py --regenerate-goldens
     and review the digest diff in the same commit as the change that caused it, so the new
     digest is approved by a human rather than absorbed silently.

  2. The change was not intended. This is a regression: fix it instead of re-recording.
     Every digest already written into a proof bundle, a run manifest reference, or a
     lineage record disagrees with this build until you do.
```
