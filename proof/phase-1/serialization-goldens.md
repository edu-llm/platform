# Phase 1 golden canonical digests

The canonical JSON digest of each of the 2 committed role templates, taken over the projection the drift comparison acts on rather than over the file.

That is the difference worth understanding. A comment, a reordered key or a whitespace change alters the file and not the projection, and does not land here. A statement that grants one more action alters the projection whatever it does to the file, and does. The digest is `sha256` over `canonical_json_bytes(TemplateRole)`, the same function that produces manifest digests in lineage records.

| role | template | canonical bytes | digest |
| --- | --- | --- | --- |
| sbsandbox-intern-edullm-ecr-publisher | infra/iam/ecr-publisher-role.yaml | 2034 | sha256:a25031110ebe139885faaa6aa1ca3479f699ab46a597439031fb8db203e85f5d |
| sbsandbox-intern-edullm-infra-deployer | infra/iam/infra-deployer-role.yaml | 6069 | sha256:195a00ec8d75c46d70e42ed13f8b68d969ffb6ddbbd91ecc6583713123a513ac |

## How this fails

`serialization-goldens.json` in this directory is the machine-readable copy. `tests/test_phase1_golden.py` reprojects each template, recomputes its digest and compares it to the recorded value, one test per role so a failure names the role rather than the batch.

`uv run python tools/build_phase1_proof.py` refuses to overwrite a drifted digest. Re-recording requires `--regenerate-goldens`, so a change to what a role may do cannot be absorbed by re-running the generator.

Re-recording is also the moment to re-capture. A role that compared clean against the old projection has not been compared against the new one, so any drift report in this bundle is about a template that no longer exists.

```
<role> (<contract>) no longer serializes to its recorded canonical digest.
  recorded: <recorded digest>
  live:     <live digest>

This is a serialization tripwire, not a formatting check. A change to field ordering, to a
serializer, to a default value, or to the fixture itself lands here and nowhere else.

Do exactly one of these, deliberately:

  1. The change was intended. Re-record with
       uv run python tools/build_phase1_proof.py --regenerate-goldens
     and review the digest diff in the same commit as the change that caused it, so the new
     digest is approved by a human rather than absorbed silently.

  2. The change was not intended. This is a regression: fix it instead of re-recording.
     Every digest already written into a proof bundle, a run manifest reference, or a
     lineage record disagrees with this build until you do.
```
