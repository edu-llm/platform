# Phase 5 proof bundle

Phase: phase-5
Bundle schema version: 1
Source commit: 5744b5dbce09cbe3d3a1397b42afe7525bcdeda9
Generated: 2026-08-01T04:16:05+00:00

This bundle exists so that a reviewer can decide whether Phase 5 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase5_proof.py` at generation time. No criterion is a gap and the gate is green, and criterion 6 is deferred rather than covered -- a recorded decision not to satisfy it yet, which passes the gate without anybody having observed it. Read that entry in `negative-case-matrix.md` first: it carries the reason and the trigger, and the Known limitations below say what this phase does not establish.

**Read this first.** Phase 5's claim is not about a mechanism. Every phase before it proved that something works; this one adds no capability at all and asks whether the capabilities already built are reachable by somebody who did not build them. The answer arrived on one day: three runs were submitted by a researcher who is not the author, every one of them was released by a lead who is not the submitter, and the decision records carry `routine_approved_by_lead_or_admin` -- the reason code the entire two-person approval design exists to produce, and which had never been written in twenty-five prior dispatches.

**What this bundle does not establish is larger than its one outstanding check, and the gate being green does not shrink it.** Every one of the three runs went to the CPU profile carrying a print statement, so nothing here was trained, no checkpoint was written and no GPU was touched. What was established is that the two-person path completes, which is what the phase is named after and what had never happened. It is not evidence that this platform carries a research workload for somebody who did not build it.

The one check that is outstanding is a different kind of open from every other in this repository. The others are captures nobody has taken. This one wants a GPU run claiming a team other than `platform` and writing a checkpoint, and each of those three works and has been exercised separately -- so it closes on one submission rather than on any work. Its observation moved to Phase 6's closeout on 2026-07-31 and still closes this phase's gate rather than that one's, which is why the verdict below is green while the phase is not finished. The Result table says which check, and `negative-case-matrix.md` carries the reason and the trigger.

## Contents

- `negative-case-matrix.md` — each of the fifteen Phase 5 acceptance criteria mapped to the tests cited for it, by node id, with every gap and every deferral stated. Read this one first.
- `second-person-evidence.md` — who submitted, who released, what each run did, and why the two that failed are committed. This is the document the phase exists for.
- `image-provenance-evidence.md` — the commit, the tag, the digest and the container, and the two-entry allowlist that used to stand between a freshly built image and a run.
- `access-control-evidence.md` — how `main` is protected, what a code owner owns, and who may start a deployment. The containment that had to land in the same change as the write grant.
- `open-decisions.md` — what this phase surfaced and did not settle, and why the judgements it took are argued where they apply rather than collected here.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of every committed pilot-run capture, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — the contract models the modules behind this bundle define, with the structural digest of each.
- `unit-test-report.md` — summarised pass and fail counts, per module and for the whole suite, with the commands to reproduce them.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 3879 |
| suite tests executed | 3686 |
| suite passed | 3686 |
| suite failed | 0 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 92 |
| matrix node ids passed | 92 |
| matrix node ids failed | 0 |
| phase criteria | 15 |
| criteria COVERED | 14 (1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15) |
| criteria DEFERRED | 1 (6) |
| criteria GAP (each one fails the gate) | 0 |
| criteria pilot-blocking | 10 (1, 2, 3, 4, 5, 7, 8, 12, 14, 15) |
| pilot-blocking criteria unmet | 0 |
| pilot runs captured | 3 |
| pilot runs released by another person | 3 |
| capture digests recorded | 3 |
| contract models in schema-compatibility.md | 5 |

## Verification commands

Run these from the repository root.

```
uv run pytest -q
uv run ruff check .
uv run mypy
uv run python tools/export_schemas.py
uv run python tools/validate_phase5.py
uv run python tools/build_phase5_proof.py
```

`tools/validate_phase5.py` exits 0 against this tree: every phase criterion is covered or explicitly deferred.

## Inputs measured

Digests of the files this bundle was generated from, so a reviewer can confirm the bundle describes the tree in front of them. Verify with `shasum -a 256 <file>`.

| file | digest |
| --- | --- |
| .github/CODEOWNERS | sha256:defc3ec7e43f5dc70f137ff21566d23e3d961355fbc871467e3676c9ab651df4 |
| .github/workflows/build-research-image.yml | sha256:f39972c4da20a63b3fd0f725ccaf521dd893d94c4d72d6f376456e081223e122 |
| .github/workflows/submit-run.yml | sha256:ef281641bd92ab24a4767009847a26d89f68bacee15cd31cf9ebdf3c56dd1c99 |
| README.md | sha256:51c34da1f6d766423d1779c989930cefb96d01809a1659fef1de305940f29f4d |
| config/image-exceptions.yaml | sha256:0828f4203385bbc3adbd8521f62768e7f4eb46f56382bc6223d37b89aae7a49c |
| config/organization.yaml | sha256:cea2d2123d2fb5794614e6a4b8a362c8aaa33626f20ae28a13cb673fadf4ac78 |
| fixtures/evidence/phase-5/branch-protection.sanitized.json | sha256:99a949fb3cae169e5b77cc53661e660410928552213615314bab97ec83100ef5 |
| fixtures/evidence/phase-5/published-image.sanitized.json | sha256:65f9b7ef2f121541a121944641c51d5e3548675b9da58f15e5aa2dee2d313d37 |
| src/edullm_platform/image_resolution.py | sha256:0bd11cece57c91d6d82680dbffb1e959dade2ea9f60094a5a1a747accbf554fd |

## Known limitations

- Everything about people here rests on three runs by one submitter on one day. That is enough to establish that the two-person path completes, which is the thing that had never been established; it is not a sample from which anything about how the platform behaves for a second, third or tenth person follows.
- The cohort is three and two of them are leads, who authorize their own routine runs by design. So the only person in it whose submission needs releasing by somebody else at all is the one non-lead, and check 2 -- covered -- rests entirely on him. If he had dropped out the phase would have lost its point rather than a participant, and the correct response would have been to seat another non-lead rather than to record the criterion closed by self-authorization.
- Check 6 is deferred, which passes the gate and proves nothing. All three runs went to the CPU profile carrying a print statement and two W&B calls, so no pilot run has trained anything, written a checkpoint, or touched a GPU -- and that is the largest thing this bundle does not establish. The deferral moved the observation to Phase 6's closeout, where it still closes this phase's gate; it did not make the observation less necessary, and a reader who takes the green verdict for a research workload having run is reading it wrong.
- No CPU run could reach Weights and Biases while these three ran. `CONTAINER_SHAPES['cpu-32vcpu']` declared `secrets=()` while `gpu-1xa10g` named the W&B secret, so the third pilot run's command failed on `No API key configured`. Check 8 is covered on what the submitter is told, which was honest and, on that profile and that day, pointed at a project nothing could write to. The gap is closed -- both profiles now carry the same secrets -- so what these runs demonstrate about W&B is the defect rather than the remedy.
- The result manifest names no W&B run for any of these, because `lifecycle_projection` hardcodes `wandb_run=None` on every one it writes. That is Phase 7 item 7.4 and it is asserted rather than worked around, so the day it changes a test fails and this sentence gets reread.
- Check 7 is covered against the workflow rather than against a refusal somebody received, which is one step weaker. No pilot submission has been refused on its merits: the two failed dispatches were a tool invoked without a required argument and a container that could not start, and neither is a refusal. What is asserted is what the workflow does with a refusal it is given.
- The branch-protection record expires thirty days after it was observed, and the cited tests fail once it does. Nothing about the runs will have changed on that date -- every lineage object is in a write-once store -- and what will have lapsed is anybody's knowledge of how the repository is configured. That is the window working rather than a defect.
- `enforce_admins` is off, so the three admins may merge a workflow change without a code-owner review. Check 10 says `a member` for that reason and the captured record carries the field, but a reader should not leave this bundle believing the control binds everybody.
- The image scan behind check 3 is BASIC, which reads the operating system package database and does not look at Python distributions. About three gigabytes of installed Python in the pilot image was scanned by nothing.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py, tests/test_phase5_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded below identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a capture is retaken. Re-run `uv run python tools/build_phase5_proof.py` and read the diff before accepting a phase gate.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
