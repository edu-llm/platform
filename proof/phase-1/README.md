# Phase 1 proof bundle

Phase: phase-1
Bundle schema version: 1
Source commit: b48eb91ee91ba7296d0f794c5c63dd4801e277d7
Generated: 2026-07-26T21:57:11+00:00

This bundle exists so that a reviewer can decide whether Phase 1 is done without reading the test suite. Everything it claims was executed by `uv run python tools/build_phase1_proof.py` at generation time. It is not done, and the Result table below says by how much.

## Contents

- `negative-case-matrix.md` — each of the eight Phase 1 acceptance criteria mapped to the tests cited for it, by node id, with every gap stated. Read this one first.
- `deployed-role-drift.md` — how a role in the account is compared to the template that claims to describe it, what the comparison cannot see, and what it found. Phase 0 has no counterpart: it deployed nothing.
- `unit-test-report.md` — summarised pass and fail counts, per module and for the whole suite, with the commands to reproduce them.
- `serialization-goldens.md` and `serialization-goldens.json` — the recorded canonical digest of what each committed role template grants, and the tripwire that fails when one drifts.
- `schema-compatibility.md` — the contract models Phase 1 added, with their structural digests.

## Result

| measure | value |
| --- | --- |
| suite tests collected | 2072 |
| suite tests executed | 1997 |
| suite passed | 1997 |
| suite failed | 0 |
| suite errored | 0 |
| suite skipped | 0 |
| matrix node ids executed | 324 |
| matrix node ids passed | 324 |
| matrix node ids failed | 0 |
| phase criteria | 8 |
| criteria COVERED | 4 (3, 4, 5, 8) |
| criteria DEFERRED | 0 |
| criteria GAP (each one fails the gate) | 4 (1, 2, 6, 7) |
| roles compared to their template | 2 |
| role drift findings | 0 |
| role templates with recorded digests | 2 |
| contract models added by this phase | 24 |

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

`tools/validate_phase1.py` exits 1 against this tree. Phase 1 is not accepted: criteria 1, 2, 6, 7 are GAPs. That is the honest state of the phase, not a broken gate. Read the Gaps section of `negative-case-matrix.md` for what closes it.

## Inputs measured

Digests of the files this bundle was generated from, so a reviewer can confirm the bundle describes the tree in front of them. Verify with `shasum -a 256 <file>`.

| file | digest |
| --- | --- |
| .github/workflows/build-research-image.yml | sha256:2797be4b88d3569c87fbc929cc9de25b484b42207987f139de5592707a3a23d8 |
| .github/workflows/deploy-phase1-ecr.yml | sha256:8320eda8dcf143695ffbed148efadf9aceb8052d5e4b2c3578aeb92fb97cdf4a |
| config/repositories.yaml | sha256:607b4e0db31f0f9e119f233ba019896b8ff3866bca50a048ea7d44d9d10e23d4 |
| fixtures/evidence/phase-1/roles/sbsandbox-intern-edullm-ecr-publisher.sanitized.json | sha256:9705f3eb935a2f86171142d4268a77c6b0a0be89c02f7bae66b6181a9502eb56 |
| fixtures/evidence/phase-1/roles/sbsandbox-intern-edullm-infra-deployer.sanitized.json | sha256:86affd607e2ab42a751de397e8060192c99c143303f8719d93e1c2c3ef2fe875 |
| infra/ecr-repositories.yaml | sha256:e376f3c0be68510e2c195c410738125cf67165d18b4a5e4289d0205bbb2547d9 |
| infra/iam/ecr-publisher-role.yaml | sha256:9f117cb0262e2da221bacfce14251add3bc80596aac8fd36145355aacf72b5cb |
| infra/iam/infra-deployer-role.yaml | sha256:17b8cf8656dee8b9a3c961c81db7a59cfa21b6faed7423992436cff69fc40552 |

## Known limitations

- The role comparison says the deployed roles are what their templates declare. It does not say what either role was refused: no session has been issued to the publisher role and nothing it attempted has been denied, which is why check 6 is a gap even though the account half of it now holds.
- Nothing else in Phase 1 has run against the account. No image has been built, no digest returned, no session issued and no call refused, which is why 4 of the 8 criteria fail the gate; the matrix names them.
- A capture is a statement about one moment. The records under `fixtures/evidence/phase-1/roles/` stop loading thirty days after they were observed — sbsandbox-intern-edullm-ecr-publisher on 2026-08-25, sbsandbox-intern-edullm-infra-deployer on 2026-08-25 — and every claim resting on them is a gap again from that date. Nothing renews it, and nothing should.
- The drift comparison does not reason about IAM wildcards. A deployed resource of `repository/*` against a template's `repository/x` is reported as one resource gained and one lost, not as one being wider than the other.
- The secret scan applied to this bundle masks its own content digests before scanning. A 64-character hexadecimal sha256 digest and a 40-character hexadecimal commit SHA both match the generic long-credential patterns in evidence.py, so the two exact token shapes this bundle emits are replaced with placeholders and everything else is scanned unchanged. No other exemption is applied.
- The nested verification run excludes every test module that builds a proof bundle (tests/test_phase0_proof.py, tests/test_phase1_proof.py), because those tests invoke a generator and would recurse. They run in the reviewer's own `uv run pytest -q`.
- This bundle describes the working tree at generation time, which may differ from the commit named above. The input digests recorded in the bundle index identify exactly what was measured.
- Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale as soon as a test is added or a template changes. Re-run `uv run python tools/build_phase1_proof.py` and read the diff before accepting a phase gate. The recorded role digests are the one part that fails loudly on its own when it goes stale.

## Reviewer sign-off

Reviewed by: ______________________  Date: ______________  Accept / Reject: ______________
