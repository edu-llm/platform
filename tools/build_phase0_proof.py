from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import pkgutil
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, get_args
from xml.etree import ElementTree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import edullm_platform
from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.criteria import (
    REENTRANT_TEST_MODULES,
    CriteriaDefinitionError,
    CriterionSpec,
    CriterionStatus,
)
from edullm_platform.evidence import redact_content_digests, scan_for_secrets

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

SHA256_DIGEST_TOKEN: Final = re.compile(r"sha256:[0-9a-f]{64}")
GIT_COMMIT_SHA_TOKEN: Final = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")

GOLDEN_DRIFT_GUIDANCE: Final = """{fixture} ({contract}) no longer serializes to its recorded canonical digest.
  recorded: {recorded}
  live:     {live}

This is a serialization tripwire, not a formatting check. A change to field ordering, to a
serializer, to a default value, or to the fixture itself lands here and nowhere else.

Do exactly one of these, deliberately:

  1. The change was intended. Re-record with
       uv run python tools/build_phase0_proof.py --regenerate-goldens
     and review the digest diff in the same commit as the change that caused it, so the new
     digest is approved by a human rather than absorbed silently.

  2. The change was not intended. This is a regression: fix it instead of re-recording.
     Every digest already written into a proof bundle, a run manifest reference, or a
     lineage record disagrees with this build until you do."""

GOLDENS_MISSING_GUIDANCE: Final = (
    "No recorded canonical digests were found at {path}. The Phase 0 proof bundle is the "
    "source of this tripwire; generate it with `uv run python tools/build_phase0_proof.py` "
    "and commit the result."
)


class ProofBundleError(RuntimeError):
    pass


class GoldenDigestDriftError(ProofBundleError):
    pass


class MissingTestNodeError(ProofBundleError):
    pass


@dataclass(frozen=True)
class RecordedGolden:
    fixture: str
    relative_path: str
    contract: str
    canonical_json_bytes: int
    digest: str


@dataclass(frozen=True)
class GoldenDrift:
    fixture: str
    contract: str
    recorded: str
    live: str


@dataclass(frozen=True)
class SuiteOutcome:
    tests: int
    failures: int
    errors: int
    skipped: int
    exit_code: int

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped

    @property
    def green(self) -> bool:
        return self.exit_code == 0 and self.failures == 0 and self.errors == 0


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


@dataclass(frozen=True)
class ModelRecord:
    module: str
    name: str
    schema_version: str
    exported: bool
    base: bool
    structural_digest: str


@dataclass(frozen=True)
class SchemaFileRecord:
    filename: str
    model: str
    file_digest: str


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


def render_goldens_document(goldens: Sequence[RecordedGolden]) -> str:
    payload = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "phase": PHASE,
        "fixtures": [
            {
                "fixture": record.fixture,
                "relative_path": record.relative_path,
                "contract": record.contract,
                "canonical_json_bytes": record.canonical_json_bytes,
                "digest": record.digest,
            }
            for record in goldens
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def load_recorded_goldens(path: Path) -> tuple[RecordedGolden, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload["fixtures"]
    return tuple(
        RecordedGolden(
            fixture=entry["fixture"],
            relative_path=entry["relative_path"],
            contract=entry["contract"],
            canonical_json_bytes=entry["canonical_json_bytes"],
            digest=entry["digest"],
        )
        for entry in entries
    )


def golden_drift(
    recorded: Sequence[RecordedGolden],
    live: Sequence[RecordedGolden],
) -> tuple[GoldenDrift, ...]:
    recorded_by_fixture = {record.fixture: record for record in recorded}
    live_by_fixture = {record.fixture: record for record in live}
    drift: list[GoldenDrift] = []
    for fixture in sorted(set(recorded_by_fixture) | set(live_by_fixture)):
        before = recorded_by_fixture.get(fixture)
        after = live_by_fixture.get(fixture)
        if before is not None and after is not None:
            if before.digest != after.digest or before.contract != after.contract:
                drift.append(
                    GoldenDrift(
                        fixture=fixture,
                        contract=after.contract,
                        recorded=before.digest,
                        live=after.digest,
                    )
                )
        elif before is None and after is not None:
            drift.append(
                GoldenDrift(
                    fixture=fixture,
                    contract=after.contract,
                    recorded="not recorded",
                    live=after.digest,
                )
            )
        elif before is not None:
            drift.append(
                GoldenDrift(
                    fixture=fixture,
                    contract=before.contract,
                    recorded=before.digest,
                    live="fixture no longer present",
                )
            )
    return tuple(drift)


def describe_drift(drift: Sequence[GoldenDrift]) -> str:
    return "\n\n".join(
        GOLDEN_DRIFT_GUIDANCE.format(
            fixture=entry.fixture,
            contract=entry.contract,
            recorded=entry.recorded,
            live=entry.live,
        )
        for entry in drift
    )


def _pytest_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment[NESTED_RUN_ENV] = "1"
    return environment


def _run_pytest(repo_root: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *arguments],
        cwd=repo_root,
        env=_pytest_environment(),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def collect_node_ids(repo_root: Path) -> tuple[str, ...]:
    completed = _run_pytest(repo_root, ["--collect-only", "-q", "--no-header"])
    if completed.returncode != 0:
        raise ProofBundleError(
            "pytest could not collect the test suite:\n"
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    node_ids = tuple(line.strip() for line in completed.stdout.splitlines() if "::" in line)
    if not node_ids:
        raise ProofBundleError("pytest collected no test node ids")
    return node_ids


def _read_junit_outcome(path: Path, exit_code: int) -> SuiteOutcome:
    root = ElementTree.parse(path).getroot()
    suite = root.find("testsuite") if root.tag == "testsuites" else root
    if suite is None:
        raise ProofBundleError("pytest did not emit a JUnit test suite element")
    return SuiteOutcome(
        tests=int(suite.get("tests", "0")),
        failures=int(suite.get("failures", "0")),
        errors=int(suite.get("errors", "0")),
        skipped=int(suite.get("skipped", "0")),
        exit_code=exit_code,
    )


def _failed_node_ids(path: Path) -> tuple[str, ...]:
    root = ElementTree.parse(path).getroot()
    failed: list[str] = []
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        module = case.get("classname", "").replace(".", "/")
        failed.append(f"{module}.py::{case.get('name', '')}")
    return tuple(sorted(failed))


def run_test_selection(
    repo_root: Path,
    node_ids: Sequence[str],
) -> tuple[SuiteOutcome, tuple[str, ...]]:
    with tempfile.TemporaryDirectory() as workspace:
        report = Path(workspace) / "selection.xml"
        completed = _run_pytest(
            repo_root,
            ["-q", "--no-header", "--tb=no", f"--junitxml={report}", *node_ids],
        )
        if not report.exists():
            raise ProofBundleError(
                "pytest did not run the selected node ids:\n"
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        return _read_junit_outcome(report, completed.returncode), _failed_node_ids(report)


def run_full_suite(repo_root: Path) -> SuiteOutcome:
    with tempfile.TemporaryDirectory() as workspace:
        report = Path(workspace) / "suite.xml"
        completed = _run_pytest(
            repo_root,
            [
                "-q",
                "--no-header",
                "--tb=no",
                f"--ignore={GENERATOR_TEST_PATH}",
                f"--junitxml={report}",
            ],
        )
        if not report.exists():
            raise ProofBundleError(
                "pytest did not run the full suite:\n"
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        return _read_junit_outcome(report, completed.returncode)


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
    collected = collect_node_ids(repo_root)
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
    outcome, failed = run_test_selection(repo_root, selected)
    return Verification(
        collected_node_ids=collected,
        selected_node_ids=selected,
        failed_node_ids=failed,
        selected=outcome,
        full_suite=run_full_suite(repo_root),
        fixture_coverage=coverage,
    )


def discover_contract_models() -> tuple[type[ContractModel], ...]:
    models: dict[str, type[ContractModel]] = {}
    for module_info in pkgutil.walk_packages(
        edullm_platform.__path__, prefix="edullm_platform."
    ):
        module = importlib.import_module(module_info.name)
        for attribute in vars(module).values():
            if not isinstance(attribute, type) or not issubclass(attribute, ContractModel):
                continue
            if attribute is ContractModel or attribute.__module__ != module_info.name:
                continue
            models[f"{attribute.__module__}.{attribute.__name__}"] = attribute
    return tuple(models[key] for key in sorted(models))


def structural_digest(model: type[ContractModel]) -> str:
    encoded = json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def declared_schema_version(model: type[ContractModel]) -> str:
    field = model.model_fields.get("schema_version")
    if field is None:
        return "unversioned"
    arguments = get_args(field.annotation)
    if len(arguments) != 1:
        return "unversioned"
    return str(arguments[0])


def exported_model_names(repo_root: Path) -> frozenset[str]:
    names: set[str] = set()
    for record in schema_file_records(repo_root):
        payload = json.loads((repo_root / "schemas" / record.filename).read_text(encoding="utf-8"))
        names.add(record.model)
        names.update(payload.get("$defs", {}))
    return frozenset(names)


def model_records(repo_root: Path) -> tuple[ModelRecord, ...]:
    exported = exported_model_names(repo_root)
    models = discover_contract_models()
    bases = {
        ancestor
        for model in models
        for ancestor in model.__mro__[1:]
        if ancestor is not ContractModel
    }
    return tuple(
        ModelRecord(
            module=model.__module__,
            name=model.__name__,
            schema_version=declared_schema_version(model),
            exported=model.__name__ in exported,
            base=model in bases,
            structural_digest=structural_digest(model),
        )
        for model in models
    )


def file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def schema_file_records(repo_root: Path) -> tuple[SchemaFileRecord, ...]:
    records: list[SchemaFileRecord] = []
    for path in sorted((repo_root / "schemas").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        title = payload.get("title")
        if not isinstance(title, str):
            raise ProofBundleError(f"{path.name} does not name the model it was rendered from")
        records.append(
            SchemaFileRecord(filename=path.name, model=title, file_digest=file_digest(path))
        )
    return tuple(records)


def source_commit_sha(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        return "unavailable (not a git checkout)"
    return completed.stdout.strip()


def known_limitations(repo_root: Path) -> tuple[str, ...]:
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
    limitations.append(
        f"Approval scope is {policy.approval_scope.value}. Any team lead may approve any "
        "member's routine submission. Check D1 in the negative-case matrix is deferred for "
        "this reason."
    )
    limitations.append(
        "Cross-team attribution is implemented but cannot reject anything yet. Every decision "
        "records the claimed team and a team_verified flag, and a submitter naming a team they "
        "do not belong to is denied as soon as team bindings exist. With bindings empty, every "
        "shipped decision records team_verified: false, which is the audit record's way of "
        "saying the attribution was accepted unchecked. Check 9 is deferred for this reason "
        "rather than covered."
    )
    limitations.append(
        "Source-order independence is not proved for the three AuthorizationScenario fixtures. "
        "Check 2 is a gap for this reason and the acceptance gate fails on it. This is "
        "unfinished work with no recorded decision behind it, which is exactly the difference "
        "between a gap and a deferral."
    )
    limitations.append(
        "The secret scan applied to this bundle masks its own content digests before scanning. "
        "A 64-character hexadecimal sha256 digest and a 40-character hexadecimal commit SHA "
        "both match the generic long-credential patterns in evidence.py, so the two exact token "
        "shapes this bundle emits are replaced with placeholders and everything else is scanned "
        "unchanged. No other exemption is applied."
    )
    limitations.append(
        f"The nested verification run excludes {GENERATOR_TEST_PATH}, because those tests invoke "
        "this generator and would recurse. They run in the reviewer's own `uv run pytest -q`, "
        "which is the command this bundle asks the reviewer to run."
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


def redact_own_digests(text: str) -> str:
    return redact_content_digests(text)


def assert_secret_free(filename: str, text: str) -> None:
    try:
        scan_for_secrets(redact_own_digests(text))
    except ValueError as error:
        raise ProofBundleError(
            f"{filename} did not pass the evidence secret scan: {error}"
        ) from error


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _command_block(commands: Sequence[str]) -> str:
    return "```\n" + "\n".join(commands) + "\n```"


def _count_naming(numbers: Sequence[str]) -> str:
    if not numbers:
        return "0"
    return f"{len(numbers)} ({', '.join(numbers)})"


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
        _command_block(VERIFICATION_COMMANDS),
        "",
        "## Whole suite",
        "",
        _table(
            ["measure", "count"],
            [
                ["collected by pytest", str(len(verification.collected_node_ids))],
                [f"executed (excluding {GENERATOR_TEST_PATH})", str(full.tests)],
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
        ("Every test node id cited by the negative-case matrix, plus every test parametrised "
        "over one of the nine fixtures, executed as one selection."),
        "",
        _table(
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
        _table(["fixture", "parametrised tests", "result"], rows),
    ]
    if verification.failed_node_ids:
        sections.extend(
            [
                "",
                "## Failures",
                "",
                _bullets(verification.failed_node_ids),
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
                _table(
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
                GOLDEN_DRIFT_GUIDANCE.format(
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
                (f"{len(exported)} models are reachable from the four root models exported to "
                "`schemas/`. These describe what the repository declares: who is in the "
                "organization, what compute exists, what policy applies, and what a submission "
                "looks like. They are versioned by the checked-in JSON Schema files below rather "
                "than by a `schema_version` field, except for RunManifest, which carries both."),
                "",
                _table(headers, rows(exported)),
                "",
                "## Runtime records",
                "",
                (f"{len(runtime)} models are not exported to `schemas/`. These are produced while "
                "work runs or while a decision is made: lineage, results, datasets, "
                "authorization outcomes, operational evidence, and gate results. They carry a "
                "`schema_version` field where they are persisted, and they are deliberately not "
                "published as repository configuration, because no human authors them by hand."),
                "",
                _table(headers, rows(runtime)),
                "",
                "## Exported JSON Schema files",
                "",
                ("The checked-in schemas under `schemas/`, with the digest of each file as "
                "generated. `tests/test_schema_export.py::test_checked_in_schemas_match_contract_"
                "models` fails if a file drifts from its model."),
                "",
                _table(
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
                _table(
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


def _status_label(check: CriterionSpec) -> str:
    return {
        CriterionStatus.COVERED: "COVERED",
        CriterionStatus.DEFERRED: "DEFERRED",
        CriterionStatus.GAP: "GAP",
    }[check.status]


STATUS_LEGEND: Final = (
    "Three statuses exist and no more. **COVERED** means one or more cited tests prove the "
    "criterion as stated against the shipped configuration and all of them pass; the gate "
    "passes it. **DEFERRED** means an explicit recorded decision not to satisfy it yet, which "
    "requires both a written reason and a written trigger describing what makes it live again; "
    "the gate passes it. **GAP** is everything else, and the gate fails it. There is no "
    "in-between status, because an in-between status is what lets a gate be green and wrong at "
    "the same time."
)

CITATION_LEGEND: Final = (
    "`proving` tests prove the criterion as stated against the shipped configuration; only a "
    "COVERED criterion may cite one. `supporting` tests are cited evidence that does not amount "
    "to proof — either because they exercise the code path under a synthetic configuration that "
    "is not what ships, or because they prove only part of the claim. Both kinds are executed. "
    "A supporting citation that is renamed or deleted still fails the criterion."
)


def _render_check_detail(check: CriterionSpec) -> list[str]:
    sections = [
        f"### Check {check.number} — {check.statement}",
        "",
        f"**Status: {_status_label(check)}**",
        "",
    ]
    if check.deferral_reason:
        sections.extend(["Deferred because:", "", check.deferral_reason, ""])
    if check.deferral_trigger:
        sections.extend(["Live again when:", "", check.deferral_trigger, ""])
    if check.gaps:
        sections.extend(["Gap:", "", _bullets(check.gaps), ""])
    if check.scope_limits:
        sections.extend(["Scope:", "", _bullets(check.scope_limits), ""])
    if check.proving_node_ids:
        sections.extend(
            [
                f"Proving tests ({len(check.proving_node_ids)}), all executed and passing:",
                "",
                _bullets([f"`{node_id}`" for node_id in check.proving_node_ids]),
                "",
            ]
        )
    else:
        sections.extend(["No test proves this check.", ""])
    if check.supporting_node_ids:
        sections.extend(
            [
                (f"Supporting tests ({len(check.supporting_node_ids)}), all executed and "
                "passing, cited as evidence rather than as proof:"),
                "",
                _bullets([f"`{node_id}`" for node_id in check.supporting_node_ids]),
                "",
            ]
        )
    return sections


def render_matrix(
    criteria: Sequence[CriterionSpec],
    deferrals: Sequence[CriterionSpec],
    verification: Verification,
) -> str:
    checks = tuple(criteria) + tuple(deferrals)
    summary_rows = [
        [
            check.number,
            _status_label(check),
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
        _table(
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
                    _bullets(check.gaps),
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
        sections.extend(_render_check_detail(check))
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
                _bullets(
                    [
                        ("`unit-test-report.md` — summarised pass and fail counts, per fixture "
                        "and for the whole suite, with the commands to reproduce them."),
                        ("`negative-case-matrix.md` — each of the thirteen Phase 0 acceptance "
                        "criteria mapped to the tests cited for it, by node id, with every gap "
                        "and every deferral stated. Read this one first."),
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
                _table(
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
                        ["criteria COVERED", _count_naming(covered_numbers)],
                        ["criteria DEFERRED", _count_naming(deferred_numbers)],
                        ["criteria GAP (each one fails the gate)", _count_naming(gap_numbers)],
                        ["related recorded deferrals", _count_naming(related_numbers)],
                        ["fixtures with recorded digests", str(len(goldens))],
                        ["contract models inventoried", str(len(models))],
                        ["JSON Schema files exported", str(len(schema_files))],
                    ],
                ),
                "",
                "## Contract versions",
                "",
                _table(
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
                _command_block(VERIFICATION_COMMANDS),
                "",
                _gate_verdict(gap_numbers),
                "",
                "## Inputs measured",
                "",
                ("Digests of the files this bundle was generated from, so a reviewer can confirm "
                "the bundle describes the tree in front of them. Verify with "
                "`shasum -a 256 <file>`."),
                "",
                _table(
                    ["file", "digest"],
                    [[path, digest] for path, digest in input_digests],
                ),
                "",
                "## Known limitations",
                "",
                _bullets(limitations),
                "",
                "## Reviewer sign-off",
                "",
                ("Reviewed by: ______________________  Date: ______________  "
                "Accept / Reject: ______________"),
            ]
        )
        + "\n"
    )


def input_digest_table(
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
            raise GoldenDigestDriftError(describe_drift(drift))

    output_dir.mkdir(parents=True, exist_ok=True)
    goldens_document = render_goldens_document(goldens)
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
            input_digests=input_digest_table(repo_root, goldens, schema_files),
            limitations=known_limitations(repo_root),
        ),
    }
    if set(documents) | {GOLDENS_FILENAME} != set(BUNDLE_FILENAMES):
        raise ProofBundleError("the bundle wrote a different file set than it declares")
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
