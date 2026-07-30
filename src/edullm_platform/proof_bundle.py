"""Machinery every phase's proof bundle is built out of.

A proof bundle exists so a reviewer can decide whether a phase is done without reading
the test suite. That only works if the bundle cannot say something the gate does not, so
the parts that decide what a bundle may claim live here rather than in a generator: a
second phase with its own copy could relax its own copy.

:func:`contradicting_status_claims` is the sharpest of them, and it is imported from
``edullm_platform.status_prose`` rather than written here: the same reader is run over a
gate's own note, which is where this class of defect survived once. Tables in a bundle are
rendered from the recorded status and cannot disagree with it; a hand-written sentence can,
and twice did.

Everything else here is the shared plumbing: the golden-digest tripwire and its
regeneration discipline, the nested pytest runs a generator uses to verify the tree it is
describing, the secret scan applied to every document before it is written, the contract
inventory together with the tripwire that reads it back out of a published bundle, and the
markdown helpers. A phase's generator supplies what is specific to it: which artifacts have
goldens, which documents the bundle contains, and what the prose says.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import pkgutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, get_args
from xml.etree import ElementTree

import edullm_platform
from edullm_platform.contracts.base import ContractModel
from edullm_platform.criteria import CriterionSpec, CriterionStatus
from edullm_platform.evidence import redact_content_digests, scan_for_secrets
from edullm_platform.status_prose import (
    contradicting_status_claims,
    status_claims,
    status_count_claims,
)

__all__ = [
    "CITATION_LEGEND",
    "GENERATOR_NESTED_ENV_VARS",
    "GENERATOR_TEST_PATHS",
    "SCOPE_IS_NOT_AUTHORSHIP",
    "STATUS_LEGEND",
    "BundleWaitingOnADeployError",
    "GoldenDigestDriftError",
    "GoldenDrift",
    "InventoryDrift",
    "MissingTestNodeError",
    "ModelRecord",
    "ProofBundleError",
    "RecordedGolden",
    "RecordedModel",
    "RecordedSchemaFile",
    "SchemaFileRecord",
    "SuiteOutcome",
    "assert_secret_free",
    "bullets",
    "collect_node_ids",
    "collection_child_runs",
    "command_block",
    "contradicting_status_claims",
    "count_naming",
    "describe_drift",
    "describe_inventory_drift",
    "file_digest",
    "full_suite_child_runs",
    "golden_drift",
    "golden_drift_guidance",
    "inventory_drift",
    "inventory_drift_guidance",
    "load_recorded_goldens",
    "markdown_tables",
    "model_records",
    "pytest_environment",
    "recorded_models",
    "recorded_schema_files",
    "redact_own_digests",
    "render_check_detail",
    "render_goldens_document",
    "run_full_suite",
    "run_test_selection",
    "schema_file_drift",
    "schema_file_records",
    "source_commit_sha",
    "status_claims",
    "status_count_claims",
    "status_label",
    "table",
]

BUNDLE_SCHEMA_VERSION: Final = 1

#: Every test module that builds a proof bundle. Each generator excludes all of them from
#: its verification run rather than only its own; see :func:`run_full_suite`.
GENERATOR_TEST_PATHS: Final = (
    "tests/test_phase0_proof.py",
    "tests/test_phase1_proof.py",
    "tests/test_phase2_proof.py",
    "tests/test_phase3_proof.py",
)

#: Every generator's recursion guard, all of which are set on every nested run rather than
#: only the one belonging to the generator that started it; see :func:`pytest_environment`.
#: A generator that adds one adds it here, and ``test_verification_reuse.py`` fails until
#: it does.
GENERATOR_NESTED_ENV_VARS: Final = (
    "EDULLM_PHASE0_PROOF_NESTED",
    "EDULLM_PHASE1_PROOF_NESTED",
    "EDULLM_PHASE2_PROOF_NESTED",
    "EDULLM_PHASE3_PROOF_NESTED",
)

STATUS_PROSE: Final = {
    CriterionStatus.COVERED: "covered",
    CriterionStatus.DEFERRED: "deferred",
    CriterionStatus.GAP: "a gap",
}
STATUS_LABEL: Final = {
    CriterionStatus.COVERED: "COVERED",
    CriterionStatus.DEFERRED: "DEFERRED",
    CriterionStatus.GAP: "GAP",
}

GOLDEN_DRIFT_GUIDANCE: Final = """{fixture} ({contract}) no longer serializes to its recorded canonical digest.
  recorded: {recorded}
  live:     {live}

This is a serialization tripwire, not a formatting check. A change to field ordering, to a
serializer, to a default value, or to the fixture itself lands here and nowhere else.

Do exactly one of these, deliberately:

  1. The change was intended. Re-record with
       {command} --regenerate-goldens
     and review the digest diff in the same commit as the change that caused it, so the new
     digest is approved by a human rather than absorbed silently.

  2. The change was not intended. This is a regression: fix it instead of re-recording.
     Every digest already written into a proof bundle, a run manifest reference, or a
     lineage record disagrees with this build until you do."""

INVENTORY_DRIFT_GUIDANCE: Final = """{subject}: the recorded {field} does not describe this tree.
  recorded: {recorded}
  live:     {live}

This is a contract-inventory tripwire, not a formatting check. The table it guards is a
published claim about this repository's contracts, and until it agrees with the tree the
bundle describes a repository that is not the one in front of you.

Which field moved decides what it means, and they do not mean the same thing:

  * a structural digest means a field was added, removed, retyped or reconstrained. Every
    payload already written against the old shape is one the new shape may now refuse;
  * a module means the contract did not change and the code moved. It costs a reviewer
    nothing and still has to be re-recorded, because a table naming a file the model is
    not in is a table nobody can check by hand;
  * a presence means a contract was added to the tree or taken out of it.

Do exactly one of these, deliberately:

  1. The change was intended. Re-record with
       {command}
     and review the table diff in the same commit as the change that caused it, so the new
     table is approved by a human rather than absorbed silently.

  2. The change was not intended. This is a regression: fix it instead of re-recording."""

STATUS_LEGEND: Final = (
    "Three statuses exist and no more. **COVERED** means one or more cited tests prove the "
    "criterion as stated against the shipped configuration and all of them pass; the gate "
    "passes it. **DEFERRED** means an explicit recorded decision not to satisfy it yet, which "
    "requires both a written reason and a written trigger describing what makes it live again; "
    "the gate passes it. **GAP** is everything else, and the gate fails it. There is no "
    "in-between status, because an in-between status is what lets a gate be green and wrong at "
    "the same time."
)

#: Why a phase bundle's contract table stopped claiming the phase added what it lists.
#: Printed by all three phase generators rather than written into each, because a phase
#: that kept its own copy could quietly go back to claiming authorship.
SCOPE_IS_NOT_AUTHORSHIP: Final = (
    "What scopes this table is where code sits today, not a record of what the phase "
    "delivered. It was introduced for a long time as the contract models the phase added, "
    "which is a question it cannot answer: the only thing it knows about a model is which "
    "module the model is in now, so moving one to another file changed the count without "
    "any phase having delivered anything different. It is a compatibility view over the "
    "complete inventory in `proof/phase-0/schema-compatibility.md`, and "
    "`tests/test_schema_compatibility.py` fails when either table stops describing the tree."
)

CITATION_LEGEND: Final = (
    "`proving` tests prove the criterion as stated against the shipped configuration; only a "
    "COVERED criterion may cite one. `supporting` tests are cited evidence that does not amount "
    "to proof — either because they exercise the code path under a synthetic configuration that "
    "is not what ships, or because they prove only part of the claim. Both kinds are executed. "
    "A supporting citation that is renamed or deleted still fails the criterion."
)


class ProofBundleError(RuntimeError):
    pass


class GoldenDigestDriftError(ProofBundleError):
    pass


class BundleWaitingOnADeployError(ProofBundleError):
    """A committed template amendment has not reached the account yet.

    Its own class rather than a message a caller matches on, because two different callers
    have to tell this refusal apart from every other one and prose is the wrong thing for
    either to key on. A case that expects some *other* refusal has to be able to say it
    did not get this one; a case that skips while a deploy is outstanding has to skip on
    this and on nothing else, or an expired capture would take the same branch.

    It is still a :class:`ProofBundleError`, and the bundle is still not written. The
    account is behind the committed template, the criteria the bundle would print rest on
    a capture of the account, and a bundle written now would state a status the gate does
    not reach. See :mod:`edullm_platform.pending_amendments`.
    """


class MissingTestNodeError(ProofBundleError):
    pass


# --------------------------------------------------------------------------------------
# Golden canonical digests
# --------------------------------------------------------------------------------------


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


def render_goldens_document(
    goldens: Sequence[RecordedGolden],
    *,
    phase: str,
) -> str:
    payload = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "phase": phase,
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
    return tuple(
        RecordedGolden(
            fixture=entry["fixture"],
            relative_path=entry["relative_path"],
            contract=entry["contract"],
            canonical_json_bytes=entry["canonical_json_bytes"],
            digest=entry["digest"],
        )
        for entry in payload["fixtures"]
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


def golden_drift_guidance(*, command: str) -> str:
    """The guidance with this generator's own command in it, and the fields left open."""
    return GOLDEN_DRIFT_GUIDANCE.replace("{command}", command)


def describe_drift(drift: Sequence[GoldenDrift], *, command: str) -> str:
    guidance = golden_drift_guidance(command=command)
    return "\n\n".join(
        guidance.format(
            fixture=entry.fixture,
            contract=entry.contract,
            recorded=entry.recorded,
            live=entry.live,
        )
        for entry in drift
    )


# --------------------------------------------------------------------------------------
# Verifying the tree the bundle describes
# --------------------------------------------------------------------------------------


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


def pytest_environment(nested_env: str) -> dict[str, str]:
    """The child's environment, with every generator's guard set rather than just one.

    Setting only the caller's own guard left a Phase 0 child and a Phase 1 child differing
    in one variable while running the same command over the same tree, which is a
    difference with no consequence and one cost: two children that cannot be recognised as
    the same measurement. Setting all of them makes them byte-identical, which is what
    lets :func:`run_full_suite` and :func:`collect_node_ids` answer both from one run.

    It tightens the guard rather than loosening it. Every generator now refuses inside any
    nested run, not only inside its own, which is the answer a generator should give
    anywhere below a verification.
    """
    environment = dict(os.environ)
    for variable in (*GENERATOR_NESTED_ENV_VARS, nested_env):
        environment[variable] = "1"
    return environment


def run_pytest(
    repo_root: Path,
    arguments: Sequence[str],
    *,
    nested_env: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *arguments],
        cwd=repo_root,
        env=pytest_environment(nested_env),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


#: What this process has already collected, keyed by resolved repository root. Memory
#: only, for the reason given in :func:`run_full_suite`.
_COLLECTION_CACHE: dict[Path, tuple[str, ...]] = {}

#: How many collection children this process has actually started; see
#: :func:`full_suite_child_runs` for what reads the pair of these.
_collection_child_runs = 0


def collection_child_runs() -> int:
    """The number of collection pytest children started in this process so far."""
    return _collection_child_runs


def execute_collection(repo_root: Path, *, nested_env: str) -> tuple[str, ...]:
    """Ask a pytest child what it can collect in this tree. No reuse, no memory."""
    global _collection_child_runs
    _collection_child_runs += 1
    completed = run_pytest(
        repo_root, ["--collect-only", "-q", "--no-header"], nested_env=nested_env
    )
    if completed.returncode != 0:
        raise ProofBundleError(
            "pytest could not collect the test suite:\n"
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    node_ids = tuple(line.strip() for line in completed.stdout.splitlines() if "::" in line)
    if not node_ids:
        raise ProofBundleError("pytest collected no test node ids")
    return node_ids


def collect_node_ids(repo_root: Path, *, nested_env: str) -> tuple[str, ...]:
    """Every node id pytest collects in this tree, collected once per process.

    Kept on the same terms as :func:`run_full_suite`, and safe for the same reasons: the
    memory is process-local and never written to disk, and the key is the resolved
    repository root, so a different tree collects for itself. What every generator gets
    is one honest listing of the tree in front of them rather than three of it.
    """
    key = repo_root.resolve()
    remembered = _COLLECTION_CACHE.get(key)
    if remembered is not None:
        return remembered
    node_ids = execute_collection(repo_root, nested_env=nested_env)
    _COLLECTION_CACHE[key] = node_ids
    return node_ids


def read_junit_outcome(path: Path, exit_code: int) -> SuiteOutcome:
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


def failed_node_ids(path: Path) -> tuple[str, ...]:
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
    *,
    nested_env: str,
) -> tuple[SuiteOutcome, tuple[str, ...]]:
    with tempfile.TemporaryDirectory() as workspace:
        report = Path(workspace) / "selection.xml"
        completed = run_pytest(
            repo_root,
            ["-q", "--no-header", "--tb=no", f"--junitxml={report}", *node_ids],
            nested_env=nested_env,
        )
        if not report.exists():
            raise ProofBundleError(
                "pytest did not run the selected node ids:\n"
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        return read_junit_outcome(report, completed.returncode), failed_node_ids(report)


#: What this process has already measured, keyed by resolved repository root and ignore
#: list. Memory only, and deliberately: a verification written to disk would be found
#: again after the tree it described had changed, and a bundle would report a pass for a
#: suite that never ran against what it describes. See :func:`run_full_suite`.
_FULL_SUITE_CACHE: dict[tuple[Path, tuple[str, ...]], SuiteOutcome] = {}

#: How many full-suite children this process has actually started. Read by the session
#: budget in ``tests/test_suite_budget.py``, which is what stops a later phase quietly
#: reintroducing the multiplier this cache removed.
_full_suite_child_runs = 0


def full_suite_child_runs() -> int:
    """The number of full-suite pytest children started in this process so far."""
    return _full_suite_child_runs


def execute_full_suite(
    repo_root: Path,
    *,
    nested_env: str,
    ignore: Sequence[str],
) -> SuiteOutcome:
    """Start a pytest child and measure the tree with it. No reuse, no memory."""
    global _full_suite_child_runs
    _full_suite_child_runs += 1
    with tempfile.TemporaryDirectory() as workspace:
        report = Path(workspace) / "suite.xml"
        completed = run_pytest(
            repo_root,
            [
                "-q",
                "--no-header",
                "--tb=no",
                *(f"--ignore={path}" for path in ignore),
                f"--junitxml={report}",
            ],
            nested_env=nested_env,
        )
        if not report.exists():
            raise ProofBundleError(
                "pytest did not run the full suite:\n"
                + (completed.stderr.strip() or completed.stdout.strip())
            )
        return read_junit_outcome(report, completed.returncode)


def run_full_suite(
    repo_root: Path,
    *,
    nested_env: str,
    ignore: Sequence[str] = GENERATOR_TEST_PATHS,
) -> SuiteOutcome:
    """The whole suite, minus the modules that build a bundle, measured once per tree.

    Every generator's test module is excluded, not just this generator's own. A generator
    test builds a bundle, which runs the suite, which would run the other generator's
    tests, which build another bundle: bounded, because each build excludes its own tests,
    and quadratic in wall-clock time for no added assurance. Those modules run in the
    reviewer's own `uv run pytest -q`, which is the command every bundle asks for.

    Several generators verify the same unchanged tree in one session and every one of them
    asks the same question, so the answer is kept against the tree it was measured on. A
    bundle still reports a full suite that genuinely ran in this session; it reports the
    run that happened rather than the third repetition of it.

    Two properties stop that becoming a way to claim a verification nobody performed. The
    memory is process-local and never written to disk, so a new process measures rather
    than remembers and a pass recorded before a change cannot be found after it. And the
    key is the resolved root with the ignore list, so a different tree — including every
    temporary one a test builds — misses and measures for itself.
    """
    key = (repo_root.resolve(), tuple(ignore))
    remembered = _FULL_SUITE_CACHE.get(key)
    if remembered is not None:
        return remembered
    outcome = execute_full_suite(repo_root, nested_env=nested_env, ignore=ignore)
    _FULL_SUITE_CACHE[key] = outcome
    return outcome


# --------------------------------------------------------------------------------------
# What may be written
# --------------------------------------------------------------------------------------


def redact_own_digests(text: str) -> str:
    return redact_content_digests(text)


def assert_secret_free(filename: str, text: str) -> None:
    try:
        scan_for_secrets(redact_own_digests(text))
    except ValueError as error:
        raise ProofBundleError(
            f"{filename} did not pass the evidence secret scan: {error}"
        ) from error


# --------------------------------------------------------------------------------------
# Contract inventory
# --------------------------------------------------------------------------------------


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


def discover_contract_models() -> tuple[type[ContractModel], ...]:
    models: dict[str, type[ContractModel]] = {}
    for module_info in pkgutil.walk_packages(edullm_platform.__path__, prefix="edullm_platform."):
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


# --------------------------------------------------------------------------------------
# Reading a recorded contract inventory back
# --------------------------------------------------------------------------------------
#
# A bundle's `schema-compatibility.md` is a recorded-digest artifact exactly as
# `serialization-goldens.json` is, and for a long time only one of the two could be
# checked. The goldens were written as JSON and had a tripwire; the contract tables were
# written as markdown, read by nobody, and drifted — Phase 1's table went on naming a
# module nine of its models had left, and recorded a digest for DeployedRoleEvidence that
# the tree had stopped computing, and nothing failed.
#
# What is parsed is the published document rather than a machine-readable sidecar. A
# sidecar would be a second copy of the same table, which is what every one of these
# bundles warns against in its own prose, and it would leave the document a reviewer
# actually reads as the unchecked one.


#: The first four columns of every contract table a bundle writes. Phase 0's tables carry
#: these five columns and phases 1 to 3 insert an ``exported`` column before the digest,
#: so the digest is read from the last cell rather than from a fixed index.
MODEL_TABLE_HEADER: Final = ("model", "module", "kind", "schema_version")

#: The exported-schema table, which records the digest of a file rather than of a shape.
SCHEMA_FILE_TABLE_HEADER: Final = ("file", "root model", "file digest")


@dataclass(frozen=True)
class RecordedModel:
    name: str
    module: str
    kind: str
    schema_version: str
    digest: str


@dataclass(frozen=True)
class RecordedSchemaFile:
    path: str
    model: str
    digest: str


@dataclass(frozen=True)
class InventoryDrift:
    subject: str
    field: str
    recorded: str
    live: str


def split_markdown_row(line: str) -> tuple[str, ...] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return tuple(cell.strip() for cell in stripped[1:-1].split("|"))


def markdown_tables(text: str) -> Iterator[tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]]:
    """Every pipe table in a document, as a header and its rows.

    Written here rather than depended on, because the only tables it has to read are the
    ones :func:`table` writes and those are one shape: a header line, a rule, then rows
    until the first line that is not one.
    """
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        header = split_markdown_row(lines[index])
        rule = split_markdown_row(lines[index + 1]) if index + 1 < len(lines) else None
        if header is None or rule is None or set(rule) != {"---"}:
            index += 1
            continue
        index += 2
        rows: list[tuple[str, ...]] = []
        while index < len(lines):
            row = split_markdown_row(lines[index])
            if row is None:
                break
            rows.append(row)
            index += 1
        yield header, tuple(rows)


def recorded_models(document: str) -> tuple[RecordedModel, ...]:
    """Every contract row a bundle's schema-compatibility document records."""
    return tuple(
        RecordedModel(
            name=row[0],
            module=row[1],
            kind=row[2],
            schema_version=row[3],
            digest=row[-1],
        )
        for header, rows in markdown_tables(document)
        if header[: len(MODEL_TABLE_HEADER)] == MODEL_TABLE_HEADER
        for row in rows
    )


def recorded_schema_files(document: str) -> tuple[RecordedSchemaFile, ...]:
    return tuple(
        RecordedSchemaFile(path=row[0], model=row[1], digest=row[2])
        for header, rows in markdown_tables(document)
        if header == SCHEMA_FILE_TABLE_HEADER
        for row in rows
    )


def inventory_drift(
    recorded: Sequence[RecordedModel],
    live: Sequence[ModelRecord],
) -> tuple[InventoryDrift, ...]:
    """What a recorded contract table and the tree disagree about, keyed by model name.

    Keyed by name and not by ``module.name``, which is the point rather than a detail. A
    model that moves between modules is the same contract in a different file; a tripwire
    keyed on where it lives would report one model vanishing and an unrelated one
    appearing, and leave the reviewer to notice for themselves that the two had the same
    digest. Keyed on the name, the same move reports what it is: one relocation, nothing
    about the shape.
    """
    recorded_by_name = {record.name: record for record in recorded}
    live_by_name = {record.name: record for record in live}
    drift: list[InventoryDrift] = []
    for name in sorted(set(recorded_by_name) | set(live_by_name)):
        before = recorded_by_name.get(name)
        after = live_by_name.get(name)
        if before is not None and after is not None:
            drift.extend(
                InventoryDrift(subject=name, field=field, recorded=was, live=now)
                for field, was, now in (
                    ("module", before.module, after.module),
                    ("kind", before.kind, "base" if after.base else "record"),
                    ("schema_version", before.schema_version, after.schema_version),
                    ("structural digest", before.digest, after.structural_digest),
                )
                if was != now
            )
        elif before is None and after is not None:
            drift.append(
                InventoryDrift(
                    subject=name,
                    field="presence",
                    recorded="not recorded",
                    live=f"defined in {after.module}",
                )
            )
        elif before is not None:
            drift.append(
                InventoryDrift(
                    subject=name,
                    field="presence",
                    recorded=f"recorded in {before.module}",
                    live="no longer in this bundle's scope",
                )
            )
    return tuple(drift)


def schema_file_drift(
    recorded: Sequence[RecordedSchemaFile],
    live: Sequence[SchemaFileRecord],
) -> tuple[InventoryDrift, ...]:
    """The same check for the exported-schema digests recorded beside the contract tables."""
    recorded_by_path = {record.path: record for record in recorded}
    live_by_path = {f"schemas/{record.filename}": record for record in live}
    drift: list[InventoryDrift] = []
    for path in sorted(set(recorded_by_path) | set(live_by_path)):
        before = recorded_by_path.get(path)
        after = live_by_path.get(path)
        if before is not None and after is not None:
            drift.extend(
                InventoryDrift(subject=path, field=field, recorded=was, live=now)
                for field, was, now in (
                    ("root model", before.model, after.model),
                    ("file digest", before.digest, after.file_digest),
                )
                if was != now
            )
        elif before is None and after is not None:
            drift.append(
                InventoryDrift(
                    subject=path,
                    field="presence",
                    recorded="not recorded",
                    live=f"exported from {after.model}",
                )
            )
        elif before is not None:
            drift.append(
                InventoryDrift(
                    subject=path,
                    field="presence",
                    recorded=f"recorded for {before.model}",
                    live="no longer exported",
                )
            )
    return tuple(drift)


def inventory_drift_guidance(*, command: str) -> str:
    """The guidance with this bundle's own regeneration command in it, fields left open."""
    return INVENTORY_DRIFT_GUIDANCE.replace("{command}", command)


def describe_inventory_drift(drift: Sequence[InventoryDrift], *, command: str) -> str:
    guidance = inventory_drift_guidance(command=command)
    return "\n\n".join(
        guidance.format(
            subject=entry.subject,
            field=entry.field,
            recorded=entry.recorded,
            live=entry.live,
        )
        for entry in drift
    )


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


# --------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def command_block(commands: Sequence[str]) -> str:
    return "```\n" + "\n".join(commands) + "\n```"


def count_naming(numbers: Sequence[str]) -> str:
    if not numbers:
        return "0"
    return f"{len(numbers)} ({', '.join(numbers)})"


# --------------------------------------------------------------------------------------
# What a bundle may say about a criterion
# --------------------------------------------------------------------------------------


def status_label(check: CriterionSpec) -> str:
    return STATUS_LABEL[check.status]


def render_check_detail(check: CriterionSpec) -> list[str]:
    sections = [
        f"### Check {check.number} — {check.statement}",
        "",
        f"**Status: {status_label(check)}**",
        "",
    ]
    if check.deferral_reason:
        sections.extend(["Deferred because:", "", check.deferral_reason, ""])
    if check.deferral_trigger:
        sections.extend(["Live again when:", "", check.deferral_trigger, ""])
    if check.gaps:
        sections.extend(["Gap:", "", bullets(check.gaps), ""])
    if check.scope_limits:
        sections.extend(["Scope:", "", bullets(check.scope_limits), ""])
    if check.proving_node_ids:
        sections.extend(
            [
                f"Proving tests ({len(check.proving_node_ids)}), all executed and passing:",
                "",
                bullets([f"`{node_id}`" for node_id in check.proving_node_ids]),
                "",
            ]
        )
    else:
        sections.extend(["No test proves this check.", ""])
    if check.supporting_node_ids:
        sections.extend(
            [
                (
                    f"Supporting tests ({len(check.supporting_node_ids)}), all executed and "
                    "passing, cited as evidence rather than as proof:"
                ),
                "",
                bullets([f"`{node_id}`" for node_id in check.supporting_node_ids]),
                "",
            ]
        )
    return sections


def recorded_status(checks: Sequence[CriterionSpec], number: str) -> CriterionStatus:
    for check in checks:
        if check.number == number:
            return check.status
    raise ProofBundleError(
        f"a known limitation names check {number}, which the criteria definition does not record"
    )
