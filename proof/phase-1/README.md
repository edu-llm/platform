# Phase 1 proof bundle

Phase: phase-1
Bundle schema version: 1
Source commit: 7f822e3577edaf373a3e9221f76f4cbd4db53888
Generated: 2026-07-30T20:42:17+00:00

This bundle exists so that a reviewer can decide whether Phase 1 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase1_proof.py` at generation time. Every criterion is covered and the gate is green, which is the state in which a bundle is most worth reading carefully: the Known limitations below say what each criterion does not cover, and `open-decisions.md` says what this phase surfaced and did not settle.

## Contents

- `negative-case-matrix.md` — each of the eight Phase 1 acceptance criteria mapped to the tests cited for it, by node id, with every gap stated. Read this one first.
- `publisher-denial-matrix.md` — the run this phase turns on, the five refusals the publisher session met with the CloudTrail event id of each, how every probe is aimed so that being permitted would change nothing, and what choosing a probe has cost so far.
- `image-rebuild-comparison.md` — the same commit built four times from the same pinned base, field by field, and the four causes that account for every difference.
- `open-decisions.md` — questions this phase surfaced and did not answer. One so far: whether a scan result may block a publish.
- `deployed-role-drift.md` — how a role in the account is compared to the template that claims to describe it, what the comparison cannot see, and what it found. Phase 0 has no counterpart: it deployed nothing.
- `unit-test-report.md` — summarised pass and fail counts, per module and for the whole suite, with the commands to reproduce them.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of what each committed role template grants, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — the contract models the modules behind this bundle define, with the structural digest of each and what makes one move.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 3695 |
| suite tests executed | 3529 |
| suite passed | 3529 |
| suite failed | 0 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 423 |
| matrix node ids passed | 423 |
| matrix node ids failed | 0 |
| phase criteria | 8 |
| criteria COVERED | 8 (1, 2, 3, 4, 5, 6, 7, 8) |
| criteria DEFERRED | 0 |
| criteria GAP (each one fails the gate) | 0 |
| roles compared to their template | 2 |
| role drift findings | 0 |
| role templates with recorded digests | 2 |
| publish runs captured | 1 |
| actions the publisher session was refused | 5 |
| image configurations compared | 5 |
| open decisions recorded | 0 |
| contract models in schema-compatibility.md | 19 |

## Verification commands

Run these from the repository root.

```
uv run pytest -q
uv run ruff check .
uv run mypy
uv run python tools/export_schemas.py
uv run python tools/validate_phase1.py
uv run python tools/build_phase1_proof.py
```

`tools/validate_phase1.py` exits 0 against this tree: every phase criterion is covered or explicitly deferred.

## Inputs measured

Digests of the files this bundle was generated from, so a reviewer can confirm the bundle describes the tree in front of them. Verify with `shasum -a 256 <file>`.

| file | digest |
| --- | --- |
| .github/workflows/build-research-image.yml | sha256:7c19d25c5f18e9040305cc8124891e8fbe222dbf7b59b79648b9d6ecf170970e |
| .github/workflows/deploy-phase1-ecr.yml | sha256:0bea8d5868e5382e6a61b5b799085bddf5a03e500cf38ba604e2026226583862 |
| config/repositories.yaml | sha256:2ebf3fc8d091d88c62555e432059c59e208a5f26f2d0bdd8f7cf50133fdcd384 |
| fixtures/evidence/phase-1/rebuild/local-rebuild-comparison.json | sha256:91966d61ec214e5c66a6ed801ed9a3271b834ff10a110afa600cf66981d7a33d |
| fixtures/evidence/phase-1/roles/sbsandbox-intern-edullm-ecr-publisher.sanitized.json | sha256:ffa2f5e2f9fb77aa9a045e17080dbada30aeb7f0f1ea35ad7e0ff9fa19d8851d |
| fixtures/evidence/phase-1/roles/sbsandbox-intern-edullm-infra-deployer.sanitized.json | sha256:2fd6db3df56ec02cee13a01437094b2d993fc71514c0338facc9247cb13477a2 |
| fixtures/evidence/phase-1/run/denials/batch-SubmitJob.sanitized.json | sha256:795febb8aa042ec85ab966e734c075ca2764f7d54da2da12819436dea4829654 |
| fixtures/evidence/phase-1/run/denials/batch-UpdateComputeEnvironment.sanitized.json | sha256:b2af615431c6913856a402dd99961aacee2925fd3d1ed484e216d86cf2126e0d |
| fixtures/evidence/phase-1/run/denials/ecr-DeleteRepository.sanitized.json | sha256:049d2fa4daeb7fdcfe1aae751d7f9c7829a9a9e256eae28d5605843430159481 |
| fixtures/evidence/phase-1/run/denials/iam-CreateRole.sanitized.json | sha256:bf8680cbb4cd6864b43621d8333e4390feb2f4ffbda745aa6aa659b9ef544a5d |
| fixtures/evidence/phase-1/run/denials/s3-ListAllMyBuckets.sanitized.json | sha256:b160fb0e4a47e8bfe52e9b828cfe8b4667ebdc5ed9311062b86ee50affab6498 |
| fixtures/evidence/phase-1/run/ecr-image.sanitized.json | sha256:542f734d6d010a00383d7a54a30e1921ed8fb5d35ddd73f0007034be808a014f |
| fixtures/evidence/phase-1/run/ecr-repository.sanitized.json | sha256:52b2a084a21ebde05d809bb97a07bc596c2b43526ed97092d6904ebe0a93359d |
| fixtures/evidence/phase-1/run/image-scan.sanitized.json | sha256:fdd2aa3793eff1fe61c94898db00389b6310e782e36fbe322a441140d591c611 |
| fixtures/evidence/phase-1/run/immutable-tag-refusal.sanitized.json | sha256:57182a350d4fd584e59652ff1f73b45305d92d0700f0d412c8f4852f98d8921b |
| fixtures/evidence/phase-1/run/publisher-session.sanitized.json | sha256:dac2929a79f4712fdb6536e1ba50eddf38083263eb095161d061dfa9949ea095 |
| infra/ecr-repositories.yaml | sha256:fc4e3348b0c23ac616db74d29bfa0abfac0e2b526482d2715555ce66d5d97d24 |
| infra/iam/ecr-publisher-role.yaml | sha256:0bb8c9357ccc329951132aa3d591f2a6f6427624314c858638530d828a1b42d3 |
| infra/iam/infra-deployer-role.yaml | sha256:596abb25126c0f10d734cbecd01bec08495cac63b19a81ab46870318504774ac |

## Known limitations

- Whether an image scan result should be able to block a publish is an open question and this bundle does not answer it. The published image scanned four critical and eight high findings, all of them inherited from the base image this repository pins, and blocked nothing, because nothing is wired to the scan. That is harmless while nothing runs a Phase 1 image and stops being harmless the day something does. See `open-decisions.md`; it is recorded there rather than settled here.
- One run, one commit, one repository. Everything the live half of this phase claims comes from a single publish of one branch commit, and check 1 is covered on the strength of it. Nothing here says the next commit will publish, and nothing here is a claim about any repository other than the one config/repositories.yaml registers.
- The second push that ECR refused was made by hand from a laptop, under an identity that is not the publisher role, which is why check 7 is covered on a narrower observation than a reader might assume. Tag immutability belongs to the repository rather than to the caller, so the refusal stands; what was not observed is the publisher role meeting it, and the publish workflow deliberately cannot produce that, because its pre-flight lookup resumes rather than pushing again.
- The S3 half of check 6 is narrower than the criterion's words. The probe is ListBuckets, an account-level call with no bucket to be absent, so a refusal proves the role holds no account-wide S3 permission rather than that it cannot read a dataset. Closing that difference needs a bucket this project owns and an object in it that exists, and no such bucket is deployed.
- The rebuild comparison behind check 2 was made locally rather than by the workflow, on one builder and one platform, both recorded in the record it reads. The workflow cannot produce it: a re-run of the same commit resumes to the published digest instead of building. A different BuildKit could produce a different answer.
- A capture is a statement about one moment. The records under `fixtures/evidence/phase-1/roles/` stop loading thirty days after they were observed — sbsandbox-intern-edullm-ecr-publisher on 2026-08-28, sbsandbox-intern-edullm-infra-deployer on 2026-08-27 — and every claim resting on them is a gap again from that date. Nothing renews it, and nothing should.
- The records of the publish run under `fixtures/evidence/phase-1/run/` expire the same way and it means something different. They stop loading on 2026-08-25, and checks 1, 6 and 7 revert to gaps on that date. Nothing about the run will have changed — the image, its scan, the session and the five refusals are all still in the registry and in CloudTrail — but nobody will have confirmed lately that the repository is still immutable, the role is still refused, and the tag still resolves to this digest. Re-capturing costs a read of the account rather than another publish.
- The drift comparison does not reason about IAM wildcards. A deployed resource of `repository/*` against a template's `repository/x` is reported as one resource gained and one lost, not as one being wider than the other.
- The secret scan applied to this bundle masks its own content digests before scanning. A 64-character hexadecimal sha256 digest and a 40-character hexadecimal commit SHA both match the generic long-credential patterns in evidence.py, so the two exact token shapes this bundle emits are replaced with placeholders and everything else is scanned unchanged. No other exemption is applied.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py, tests/test_phase2_proof.py, tests/test_phase3_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded in the bundle index identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a template changes. Re-run `uv run python tools/build_phase1_proof.py` and read the diff before accepting a phase gate. The recorded role digests are the one part that fails loudly on its own when it goes stale.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
