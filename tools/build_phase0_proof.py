from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy, ApprovalScope
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.criteria import (
    REENTRANT_TEST_MODULES,
    CriteriaDefinitionError,
    CriterionSpec,
    CriterionStatus,
)

# The criterion-to-test mapping is defined once, in the library, and imported here and
# by the acceptance gate. This module must never grow its own copy: the matrix below and
# the gate's verdict have to be the same claim, or the bundle is decoration.
from edullm_platform.phase0_criteria import (
    FixtureReference,
    discover_fixtures,
    phase0_criteria,
    recorded_checks,
    related_deferrals,
)
from edullm_platform.proof_bundle import (
    CITATION_LEGEND,
    GENERATOR_TEST_PATHS,
    STATUS_LEGEND,
    STATUS_PROSE,
    GoldenDigestDriftError,
    MissingTestNodeError,
    ModelRecord,
    ProofBundleError,
    RecordedGolden,
    SchemaFileRecord,
    SuiteOutcome,
    assert_secret_free,
    bullets,
    collect_node_ids,
    command_block,
    contradicting_status_claims,
    count_naming,
    describe_drift,
    file_digest,
    golden_drift,
    golden_drift_guidance,
    load_recorded_goldens,
    model_records,
    recorded_status,
    render_check_detail,
    render_goldens_document,
    run_full_suite,
    run_test_selection,
    schema_file_records,
    source_commit_sha,
    status_label,
    table,
)
from edullm_platform.status_prose import spell

PHASE: Final = "phase-0"
BUNDLE_SCHEMA_VERSION: Final = 1
BUNDLE_RELATIVE_DIR: Final = Path("proof") / PHASE
GOLDENS_FILENAME: Final = "serialization-goldens.json"
BUNDLE_FILENAMES: Final = (
    "README.md",
    "negative-case-matrix.md",
    "schema-compatibility.md",
    GOLDENS_FILENAME,
    "serialization-goldens.md",
    "unit-test-report.md",
)
NESTED_RUN_ENV: Final = "EDULLM_PHASE0_PROOF_NESTED"
GENERATOR_TEST_PATH: Final = "tests/test_phase0_proof.py"
GENERATOR_COMMAND: Final = "uv run python tools/build_phase0_proof.py"

CONFIG_INPUTS: Final = (
    "config/organization.yaml",
    "config/policy.yaml",
    "config/workload-catalog.yaml",
)

VERIFICATION_COMMANDS: Final = (
    "uv run pytest -q",
    "uv run ruff check .",
    "uv run mypy src",
    "uv run python tools/export_schemas.py",
    "uv run python tools/validate_phase0.py",
    GENERATOR_COMMAND,
)
GOLDENS_MISSING_GUIDANCE: Final = (
    "No recorded canonical digests were found at {path}. The Phase 0 proof bundle is the "
    "source of this tripwire; generate it with `uv run python tools/build_phase0_proof.py` "
    "and commit the result."
)
@dataclass(frozen=True)
class FixtureCoverage:
    fixture: str
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class Verification:
    collected_node_ids: tuple[str, ...]
    selected_node_ids: tuple[str, ...]
    failed_node_ids: tuple[str, ...]
    selected: SuiteOutcome
    full_suite: SuiteOutcome
    fixture_coverage: tuple[FixtureCoverage, ...]
def default_output_dir(repo_root: Path) -> Path:
    return repo_root / BUNDLE_RELATIVE_DIR


def goldens_path(output_dir: Path) -> Path:
    return output_dir / GOLDENS_FILENAME


def load_fixture(repo_root: Path, reference: FixtureReference) -> ContractModel:
    return load_yaml(repo_root / reference.relative_path, reference.model_type)


def fixture_digest(repo_root: Path, reference: FixtureReference) -> str:
    return sha256_digest(load_fixture(repo_root, reference))


def fixture_canonical_length(repo_root: Path, reference: FixtureReference) -> int:
    return len(canonical_json_bytes(load_fixture(repo_root, reference)))


def compute_goldens(
    repo_root: Path,
    references: Sequence[FixtureReference],
) -> tuple[RecordedGolden, ...]:
    return tuple(
        RecordedGolden(
            fixture=reference.fixture,
            relative_path=reference.relative_path,
            contract=reference.contract,
            canonical_json_bytes=fixture_canonical_length(repo_root, reference),
            digest=fixture_digest(repo_root, reference),
        )
        for reference in references
    )
def fixture_scoped_node_ids(
    collected: Sequence[str],
    references: Sequence[FixtureReference],
) -> tuple[FixtureCoverage, ...]:
    return tuple(
        FixtureCoverage(
            fixture=reference.fixture,
            node_ids=tuple(
                node_id for node_id in collected if f"[{reference.fixture}]" in node_id
            ),
        )
        for reference in references
    )


def verify_repository(
    repo_root: Path,
    references: Sequence[FixtureReference] | None = None,
) -> Verification:
    fixtures = discover_fixtures(repo_root) if references is None else tuple(references)
    collected = collect_node_ids(repo_root, nested_env=NESTED_RUN_ENV)
    checks = recorded_checks(fixtures)
    cited = {node_id for check in checks for node_id in check.cited_node_ids}
    missing = sorted(cited - set(collected))
    if missing:
        raise MissingTestNodeError(
            "the negative-case matrix cites test node ids that pytest does not collect; "
            "a matrix may not claim coverage it cannot run:\n  " + "\n  ".join(missing)
        )
    coverage = fixture_scoped_node_ids(collected, fixtures)
    selected = tuple(
        sorted(cited | {node_id for entry in coverage for node_id in entry.node_ids})
    )
    reentrant = sorted(
        node_id
        for node_id in selected
        if node_id.split("::", 1)[0] in REENTRANT_TEST_MODULES
    )
    if reentrant:
        raise ProofBundleError(
            "the proof generator must not select a test that invokes the generator or the "
            "acceptance gate, which would recurse:\n  " + "\n  ".join(reentrant)
        )
    outcome, failed = run_test_selection(repo_root, selected, nested_env=NESTED_RUN_ENV)
    return Verification(
        collected_node_ids=collected,
        selected_node_ids=selected,
        failed_node_ids=failed,
        selected=outcome,
        full_suite=run_full_suite(repo_root, nested_env=NESTED_RUN_ENV),
        fixture_coverage=coverage,
    )
def known_limitations(repo_root: Path, checks: Sequence[CriterionSpec]) -> tuple[str, ...]:
    """What this bundle does not establish, read off the tree rather than remembered.

    Every entry is either conditional on a configuration value that can change, or true
    by construction. No entry states a criterion status of its own: where one names a
    check, the status word comes from ``checks``, so a limitation cannot disagree with the
    verdict the gate reached. ``contradicting_status_claims`` refuses the bundle if one
    ever does.
    """

    def status_of(number: str) -> str:
        return STATUS_PROSE[recorded_status(checks, number)]

    catalog = load_yaml(repo_root / "config" / "workload-catalog.yaml", WorkloadCatalog)
    inventory = load_yaml(repo_root / "config" / "organization.yaml", OrganizationInventory)
    policy = load_yaml(repo_root / "config" / "policy.yaml", ApprovalPolicy)
    limitations: list[str] = []
    if not any(profile.provisioned for profile in catalog.compute_profiles):
        limitations.append(
            f"No compute profile is provisioned. All {len(catalog.compute_profiles)} profiles in "
            "the workload catalog are priced and dated but carry provisioned: false, so "
            "resolve_compute_profile_for_execution refuses every one of them. Phase 0 proves "
            "pricing and classification, not that anything can run."
        )
    if not inventory.team_bindings.teams:
        limitations.append(
            "Team bindings are empty. OrganizationInventory.team_bindings.teams is an empty "
            "tuple, so no submitter or lead is bound to a team. Every team-scoped rule is "
            "therefore either deferred or unenforceable today."
        )
    if policy.approval_scope is not ApprovalScope.TEAM:
        limitations.append(
            f"Approval scope is {policy.approval_scope.value}. Any team lead may approve any "
            f"member's routine submission. Check D1 in the negative-case matrix is "
            f"{status_of('D1')} for this reason."
        )
    if not inventory.team_bindings.teams:
        limitations.append(
            "Cross-team attribution is implemented but cannot reject anything yet. Every "
            "decision records the claimed team and a team_verified flag, and a submitter "
            "naming a team they do not belong to is denied as soon as team bindings exist. "
            "With bindings empty, every shipped decision records team_verified: false, which "
            "is the audit record's way of saying the attribution was accepted unchecked. "
            f"Check 9 is {status_of('9')} for this reason: no test can show a shipped "
            "rejection that the shipped configuration cannot produce."
        )
    # Everything below holds by construction rather than by configuration. Each one names
    # something this generator always does, so there is no condition left to read off the
    # tree, and none of them states a criterion status.
    limitations.append(
        "The secret scan applied to this bundle masks its own content digests before scanning. "
        "A 64-character hexadecimal sha256 digest and a 40-character hexadecimal commit SHA "
        "both match the generic long-credential patterns in evidence.py, so the two exact token "
        "shapes this bundle emits are replaced with placeholders and everything else is scanned "
        "unchanged. No other exemption is applied."
    )
    limitations.append(
        "The nested verification run excludes every test module that builds a proof bundle "
        f"({', '.join(GENERATOR_TEST_PATHS)}), because those tests invoke a generator and would "
        "recurse. They run in the reviewer's own `uv run pytest -q`, which is the command this "
        "bundle asks the reviewer to run."
    )
    limitations.append(
        "This bundle describes the working tree at generation time, which may differ from the "
        "commit named above. The input digests recorded in the bundle index identify exactly "
        "what was measured."
    )
    limitations.append(
        "Nothing forces this bundle to stay current. It is a snapshot, and its counts go stale "
        f"as soon as a test is added or a contract changes. Re-run `{GENERATOR_COMMAND}` and "
        "read the diff before accepting a phase gate. The recorded fixture digests are the one "
        "part that fails loudly on its own when it goes stale."
    )
    return tuple(limitations)
def render_unit_test_report(verification: Verification) -> str:
    full = verification.full_suite
    selected = verification.selected
    rows = [
        [
            entry.fixture,
            str(len(entry.node_ids)),
            "pass" if selected.green else "see failures below",
        ]
        for entry in verification.fixture_coverage
    ]
    sections = [
        "# Phase 0 unit-test report",
        "",
        ("Summarised counts only. Raw pytest output is not copied here; the commands below "
        "reproduce it in full."),
        "",
        "## Commands a reviewer can re-run",
        "",
        command_block(VERIFICATION_COMMANDS),
        "",
        "## Whole suite",
        "",
        table(
            ["measure", "count"],
            [
                ["collected by pytest", str(len(verification.collected_node_ids))],
                [f"executed (excluding {', '.join(GENERATOR_TEST_PATHS)})", str(full.tests)],
                ["passed", str(full.passed)],
                ["failed", str(full.failures)],
                ["errored", str(full.errors)],
                ["skipped", str(full.skipped)],
                ["pytest exit code", str(full.exit_code)],
            ],
        ),
        "",
        "## Targeted verification run",
        "",
        (f"Every test node id cited by the negative-case matrix, plus every test parametrised "
        f"over one of the {spell(len(verification.fixture_coverage))} fixtures, executed as one "
        "selection."),
        "",
        table(
            ["measure", "count"],
            [
                ["selected node ids", str(len(verification.selected_node_ids))],
                ["executed", str(selected.tests)],
                ["passed", str(selected.passed)],
                ["failed", str(selected.failures)],
                ["errored", str(selected.errors)],
                ["skipped", str(selected.skipped)],
                ["pytest exit code", str(selected.exit_code)],
            ],
        ),
        "",
        "## Per-fixture coverage",
        "",
        ("Tests parametrised over each fixture by name. A fixture with no parametrised tests "
        "would show zero here."),
        "",
        table(["fixture", "parametrised tests", "result"], rows),
    ]
    if verification.failed_node_ids:
        sections.extend(
            [
                "",
                "## Failures",
                "",
                bullets(verification.failed_node_ids),
            ]
        )
    return "\n".join(sections) + "\n"


def render_goldens_report(goldens: Sequence[RecordedGolden]) -> str:
    rows = [
        [
            record.relative_path,
            record.contract,
            str(record.canonical_json_bytes),
            record.digest,
        ]
        for record in goldens
    ]
    return (
        "\n".join(
            [
                "# Phase 0 golden canonical digests",
                "",
                (f"The canonical JSON digest of every one of the {len(goldens)} shipped fixtures, "
                "recorded so that a later build can be compared against this one."),
                "",
                ("The digest is `sha256` over `canonical_json_bytes(model)`: the validated "
                "contract dumped in JSON mode with aliases, null fields kept, keys sorted, and "
                "compact separators. It is the same function that produces manifest digests in "
                "lineage records, so a drift here is a drift there."),
                "",
                table(
                    ["fixture", "contract", "canonical bytes", "digest"],
                    rows,
                ),
                "",
                "## How this fails",
                "",
                (f"`{GOLDENS_FILENAME}` in this directory is the machine-readable copy. "
                "`tests/test_phase0_golden.py` reloads every fixture, recomputes its digest, and "
                "compares it to the recorded value, one test per fixture so a failure names the "
                "fixture rather than the batch. A change to field ordering, to a serializer, or "
                "to a default value fails there by name."),
                "",
                (f"`{GENERATOR_COMMAND}` refuses to overwrite a drifted digest. Re-recording is a "
                "deliberate act that requires `--regenerate-goldens`, so a regression cannot be "
                "absorbed by re-running the generator."),
                "",
                ("The failure message tells the reader which of the two situations they are in "
                "and what to do about each:"),
                "",
                "```",
                golden_drift_guidance(command=GENERATOR_COMMAND).format(
                    fixture="<fixture>",
                    contract="<contract>",
                    recorded="<recorded digest>",
                    live="<live digest>",
                ),
                "```",
            ]
        )
        + "\n"
    )


def render_schema_report(
    models: Sequence[ModelRecord],
    schema_files: Sequence[SchemaFileRecord],
) -> str:
    exported = [record for record in models if record.exported]
    runtime = [record for record in models if not record.exported]
    versioned = [record for record in models if record.schema_version != "unversioned"]

    def rows(records: Sequence[ModelRecord]) -> list[list[str]]:
        return [
            [
                record.name,
                record.module,
                "base" if record.base else "record",
                record.schema_version,
                record.structural_digest,
            ]
            for record in records
        ]

    headers = ["model", "module", "kind", "schema_version", "structural digest"]
    return (
        "\n".join(
            [
                "# Phase 0 schema compatibility report",
                "",
                (f"{len(models)} contract models. The structural digest is `sha256` over the "
                "model's JSON schema with sorted keys, so it changes when a field is added, "
                "removed, retyped, or reconstrained, and does not change when unrelated code "
                "moves. Comparing this table between phases answers whether a schema changed."),
                "",
                ("The kind column separates a `record`, which some payload is validated against, "
                "from a `base`, which exists only for other models to inherit from and which no "
                "payload names directly."),
                "",
                "## Repository-configuration contracts",
                "",
                (f"{len(exported)} models are reachable from the {spell(len(schema_files))} "
                "root models exported to "
                "`schemas/`. These describe what the repository declares: who is in the "
                "organization, what compute exists, what policy applies, and what a submission "
                "looks like. They are versioned by the checked-in JSON Schema files below rather "
                "than by a `schema_version` field, except for RunManifest, which carries both."),
                "",
                table(headers, rows(exported)),
                "",
                "## Runtime records",
                "",
                (f"{len(runtime)} models are not exported to `schemas/`. These are produced while "
                "work runs or while a decision is made: lineage, results, datasets, "
                "authorization outcomes, operational evidence, and gate results. They carry a "
                "`schema_version` field where they are persisted, and they are deliberately not "
                "published as repository configuration, because no human authors them by hand."),
                "",
                table(headers, rows(runtime)),
                "",
                "## Exported JSON Schema files",
                "",
                ("The checked-in schemas under `schemas/`, with the digest of each file as "
                "generated. `tests/test_schema_export.py::test_checked_in_schemas_match_contract_"
                "models` fails if a file drifts from its model."),
                "",
                table(
                    ["file", "root model", "file digest"],
                    [
                        [f"schemas/{record.filename}", record.model, record.file_digest]
                        for record in schema_files
                    ],
                ),
                "",
                ("Regenerate with `uv run python tools/export_schemas.py`. Verify a file by hand "
                "with `shasum -a 256 schemas/<file>`."),
                "",
                "## Declared contract versions",
                "",
                table(
                    ["model", "schema_version"],
                    [
                        [record.name, record.schema_version]
                        for record in sorted(versioned, key=lambda record: record.name)
                    ],
                ),
            ]
        )
        + "\n"
    )
def render_matrix(
    criteria: Sequence[CriterionSpec],
    deferrals: Sequence[CriterionSpec],
    verification: Verification,
) -> str:
    checks = tuple(criteria) + tuple(deferrals)
    summary_rows = [
        [
            check.number,
            status_label(check),
            str(len(check.proving_node_ids)),
            str(len(check.supporting_node_ids)),
            check.statement,
        ]
        for check in checks
    ]
    gaps = [check for check in checks if check.status is CriterionStatus.GAP]
    deferred = [check for check in checks if check.status is CriterionStatus.DEFERRED]
    sections = [
        "# Phase 0 negative-case matrix",
        "",
        (f"The {len(criteria)} Phase 0 acceptance criteria, mapped to the tests cited for each "
        "one by node id. Each cited node id was collected and executed by this generator before "
        "the bundle was written; a citation pytest cannot collect aborts generation rather than "
        "being printed."),
        "",
        ("This mapping is defined once, in `src/edullm_platform/phase0_criteria.py`. The "
        "acceptance gate reads the same definition and executes the same node ids, so this "
        "matrix and `tools/validate_phase0.py` cannot disagree."),
        "",
        (f"Verification run: {verification.selected.tests} tests executed, "
        f"{verification.selected.passed} passed, {verification.selected.failures} failed, "
        f"{verification.selected.errors} errored, pytest exit code "
        f"{verification.selected.exit_code}."),
        "",
        STATUS_LEGEND,
        "",
        CITATION_LEGEND,
        "",
        table(
            ["#", "status", "proving", "supporting", "check"],
            summary_rows,
        ),
        "",
        (f"Rows numbered 1 to {len(criteria)} are the phase criteria. Rows numbered D-something "
        "are recorded decisions adjacent to a criterion; they are shown so the decision is "
        "visible, and they are not counted as phase criteria by the gate."),
        "",
    ]
    if gaps:
        sections.extend(
            [
                "## Gaps",
                "",
                ("Read these first. A matrix that overstates coverage is worse than no matrix. "
                "A gap is a claim nobody decided to postpone and nobody proved; it is not the "
                "same thing as a deferral, which follows. Every gap here fails the acceptance "
                "gate."),
                "",
            ]
        )
        for check in gaps:
            sections.extend(
                [
                    f"### Check {check.number} (GAP) — {check.statement}",
                    "",
                    bullets(check.gaps),
                    "",
                ]
            )
    if deferred:
        sections.extend(
            [
                "## Deferred by explicit decision",
                "",
                ("These wait on sub-team assignments. They are recorded here rather than "
                "omitted, no test in this bundle claims them as proved, and each one states "
                "the condition that makes it live again."),
                "",
            ]
        )
        for check in deferred:
            sections.extend(
                [
                    f"### Check {check.number} (DEFERRED) — {check.statement}",
                    "",
                    check.deferral_reason or "",
                    "",
                    f"Live again when: {check.deferral_trigger}",
                    "",
                ]
            )
    sections.extend(["## Checks", ""])
    for check in checks:
        sections.extend(render_check_detail(check))
    return "\n".join(sections).rstrip() + "\n"


def _gate_verdict(gap_numbers: Sequence[str]) -> str:
    if not gap_numbers:
        return (
            "`tools/validate_phase0.py` exits 0 against this tree: every phase criterion is "
            "covered or explicitly deferred, and every operational inventory check passes."
        )
    if len(gap_numbers) == 1:
        subject = f"criterion {gap_numbers[0]} is a GAP"
    else:
        subject = f"criteria {', '.join(gap_numbers)} are GAPs"
    return (
        f"`tools/validate_phase0.py` exits 1 against this tree. Phase 0 is not accepted: "
        f"{subject}. That is the honest state of the phase, not a broken gate. Read the Gaps "
        "section of `negative-case-matrix.md` for what closes it."
    )


def render_index(
    *,
    generated_at: datetime,
    commit_sha: str,
    criteria: Sequence[CriterionSpec],
    deferrals: Sequence[CriterionSpec],
    verification: Verification,
    goldens: Sequence[RecordedGolden],
    models: Sequence[ModelRecord],
    schema_files: Sequence[SchemaFileRecord],
    input_digests: Sequence[tuple[str, str]],
    limitations: Sequence[str],
) -> str:
    versioned = [record for record in models if record.schema_version != "unversioned"]
    covered_numbers = [
        check.number for check in criteria if check.status is CriterionStatus.COVERED
    ]
    deferred_numbers = [
        check.number for check in criteria if check.status is CriterionStatus.DEFERRED
    ]
    gap_numbers = [check.number for check in criteria if check.status is CriterionStatus.GAP]
    related_numbers = [check.number for check in deferrals]
    return (
        "\n".join(
            [
                "# Phase 0 proof bundle",
                "",
                f"Phase: {PHASE}",
                f"Bundle schema version: {BUNDLE_SCHEMA_VERSION}",
                f"Source commit: {commit_sha}",
                f"Generated: {generated_at.astimezone(UTC).isoformat(timespec='seconds')}",
                "",
                ("This bundle exists so that a reviewer can decide whether Phase 0 is done "
                "without reading the test suite. Everything it claims was executed by "
                f"`{GENERATOR_COMMAND}` at generation time."),
                "",
                "## Contents",
                "",
                bullets(
                    [
                        ("`unit-test-report.md` — summarised pass and fail counts, per fixture "
                        "and for the whole suite, with the commands to reproduce them."),
                        (f"`negative-case-matrix.md` — each of the {spell(len(criteria))} Phase 0 "
                        "acceptance criteria mapped to the tests cited for it, by node id, with "
                        "every gap and every deferral stated. Read this one first."),
                        "`serialization-goldens.md` and `"
                        + GOLDENS_FILENAME
                        + "` — the recorded canonical digest of every fixture, and the "
                        "tripwire that fails when one drifts.",
                        ("`schema-compatibility.md` — every contract model, its schema version, "
                        "and its structural digest, split into repository configuration and "
                        "runtime records."),
                    ]
                ),
                "",
                "## Result",
                "",
                table(
                    ["measure", "value"],
                    [
                        [
                            "suite tests collected",
                            str(len(verification.collected_node_ids)),
                        ],
                        [
                            "suite tests executed",
                            str(verification.full_suite.tests),
                        ],
                        ["suite passed", str(verification.full_suite.passed)],
                        ["suite failed", str(verification.full_suite.failures)],
                        ["suite errored", str(verification.full_suite.errors)],
                        ["suite skipped", str(verification.full_suite.skipped)],
                        [
                            "matrix node ids executed",
                            str(verification.selected.tests),
                        ],
                        ["matrix node ids passed", str(verification.selected.passed)],
                        ["matrix node ids failed", str(verification.selected.failures)],
                        ["phase criteria", str(len(criteria))],
                        ["criteria COVERED", count_naming(covered_numbers)],
                        ["criteria DEFERRED", count_naming(deferred_numbers)],
                        ["criteria GAP (each one fails the gate)", count_naming(gap_numbers)],
                        ["related recorded deferrals", count_naming(related_numbers)],
                        ["fixtures with recorded digests", str(len(goldens))],
                        ["contract models inventoried", str(len(models))],
                        ["JSON Schema files exported", str(len(schema_files))],
                    ],
                ),
                "",
                "## Contract versions",
                "",
                table(
                    ["contract", "schema_version"],
                    [
                        [record.name, record.schema_version]
                        for record in sorted(versioned, key=lambda record: record.name)
                    ],
                ),
                "",
                ("Repository-configuration contracts are versioned by their exported JSON Schema "
                "rather than by a field. See `schema-compatibility.md`."),
                "",
                "## Verification commands",
                "",
                "Run these from the repository root.",
                "",
                command_block(VERIFICATION_COMMANDS),
                "",
                _gate_verdict(gap_numbers),
                "",
                "## Inputs measured",
                "",
                ("Digests of the files this bundle was generated from, so a reviewer can confirm "
                "the bundle describes the tree in front of them. Verify with "
                "`shasum -a 256 <file>`."),
                "",
                table(
                    ["file", "digest"],
                    [[path, digest] for path, digest in input_digests],
                ),
                "",
                "## Known limitations",
                "",
                bullets(limitations),
                "",
                "## Reviewer sign-off",
                "",
                ("Reviewed by: ______________________  Date: ______________  "
                "Accept / Reject: ______________"),
            ]
        )
        + "\n"
    )


def input_digesttable(
    repo_root: Path,
    goldens: Sequence[RecordedGolden],
    schema_files: Sequence[SchemaFileRecord],
) -> tuple[tuple[str, str], ...]:
    paths = [
        *CONFIG_INPUTS,
        *(record.relative_path for record in goldens),
        *(f"schemas/{record.filename}" for record in schema_files),
    ]
    return tuple((path, file_digest(repo_root / path)) for path in sorted(paths))


def build_bundle(
    repo_root: Path,
    output_dir: Path,
    *,
    generated_at: datetime,
    regenerate_goldens: bool = False,
    verification: Verification | None = None,
) -> tuple[Path, ...]:
    fixtures = discover_fixtures(repo_root)
    goldens = compute_goldens(repo_root, fixtures)
    criteria = phase0_criteria(fixtures)
    deferrals = related_deferrals(fixtures)

    goldens_file = goldens_path(output_dir)
    if goldens_file.exists() and not regenerate_goldens:
        drift = golden_drift(load_recorded_goldens(goldens_file), goldens)
        if drift:
            raise GoldenDigestDriftError(describe_drift(drift, command=GENERATOR_COMMAND))

    output_dir.mkdir(parents=True, exist_ok=True)
    goldens_document = render_goldens_document(goldens, phase=PHASE)
    assert_secret_free(GOLDENS_FILENAME, goldens_document)
    goldens_file.write_text(goldens_document, encoding="utf-8")

    resolved = verify_repository(repo_root, fixtures) if verification is None else verification
    models = model_records(repo_root)
    schema_files = schema_file_records(repo_root)
    documents = {
        "unit-test-report.md": render_unit_test_report(resolved),
        "negative-case-matrix.md": render_matrix(criteria, deferrals, resolved),
        "serialization-goldens.md": render_goldens_report(goldens),
        "schema-compatibility.md": render_schema_report(models, schema_files),
        "README.md": render_index(
            generated_at=generated_at,
            commit_sha=source_commit_sha(repo_root),
            criteria=criteria,
            deferrals=deferrals,
            verification=resolved,
            goldens=goldens,
            models=models,
            schema_files=schema_files,
            input_digests=input_digesttable(repo_root, goldens, schema_files),
            limitations=known_limitations(repo_root, criteria + deferrals),
        ),
    }
    if set(documents) | {GOLDENS_FILENAME} != set(BUNDLE_FILENAMES):
        raise ProofBundleError("the bundle wrote a different file set than it declares")
    contradictions = contradicting_status_claims(documents, criteria + deferrals)
    if contradictions:
        raise ProofBundleError(
            "the bundle states a criterion status the acceptance gate did not reach; a "
            "reviewer who trusts this bundle without reading the suite would be misled:\n  "
            + "\n  ".join(contradictions)
        )
    for filename, text in sorted(documents.items()):
        assert_secret_free(filename, text)
    written = [goldens_file]
    for filename, text in sorted(documents.items()):
        path = output_dir / filename
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return tuple(sorted(written))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the Phase 0 proof bundle under proof/phase-0/."
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--regenerate-goldens", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if os.environ.get(NESTED_RUN_ENV):
        print(
            "refusing to build the proof bundle from inside its own verification run",
            file=sys.stderr,
        )
        return 2
    args = parse_args(argv)
    repo_root = PROJECT_ROOT
    output_dir = (
        default_output_dir(repo_root) if args.output_dir is None else Path(args.output_dir)
    )
    generated_at = (
        datetime.now(tz=UTC)
        if args.generated_at is None
        else datetime.fromisoformat(args.generated_at)
    )
    try:
        written = build_bundle(
            repo_root,
            output_dir,
            generated_at=generated_at,
            regenerate_goldens=args.regenerate_goldens,
        )
    except (ProofBundleError, CriteriaDefinitionError) as error:
        print(str(error), file=sys.stderr)
        return 1
    for path in written:
        print(path.relative_to(repo_root) if path.is_relative_to(repo_root) else path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
