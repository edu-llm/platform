"""Machinery every phase's proof bundle is built out of.

A proof bundle exists so a reviewer can decide whether a phase is done without reading
the test suite. That only works if the bundle cannot say something the gate does not, so
the parts that decide what a bundle may claim live here rather than in a generator: a
second phase with its own copy could relax its own copy.

:func:`contradicting_status_claims` is the sharpest of them. Tables in a bundle are
rendered from the recorded status and cannot disagree with it; a hand-written sentence
can, and once did. This reads every status word a document ascribes to a numbered check
and refuses the bundle when one disagrees with the criteria definition. It is a blunt
reader by design — it does not understand negation, so ``Check 9 is not covered`` reads
as a claim of ``covered`` and is meant to. A claim a machine cannot check does not belong
in a document whose whole purpose is being trustworthy without verification.

Everything else here is the shared plumbing: the golden-digest tripwire and its
regeneration discipline, the nested pytest runs a generator uses to verify the tree it is
describing, the secret scan applied to every document before it is written, the contract
inventory, and the markdown helpers. A phase's generator supplies what is specific to it:
which artifacts have goldens, which documents the bundle contains, and what the prose
says.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import pkgutil
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, get_args
from xml.etree import ElementTree

import edullm_platform
from edullm_platform.contracts.base import ContractModel
from edullm_platform.criteria import CriterionSpec, CriterionStatus
from edullm_platform.evidence import redact_content_digests, scan_for_secrets

__all__ = [
    "CITATION_LEGEND",
    "GENERATOR_TEST_PATHS",
    "STATUS_LEGEND",
    "GoldenDigestDriftError",
    "GoldenDrift",
    "MissingTestNodeError",
    "ModelRecord",
    "ProofBundleError",
    "RecordedGolden",
    "SchemaFileRecord",
    "SuiteOutcome",
    "assert_secret_free",
    "bullets",
    "collect_node_ids",
    "command_block",
    "contradicting_status_claims",
    "count_naming",
    "describe_drift",
    "file_digest",
    "full_suite_child_runs",
    "golden_drift",
    "golden_drift_guidance",
    "load_recorded_goldens",
    "model_records",
    "pytest_environment",
    "redact_own_digests",
    "render_check_detail",
    "render_goldens_document",
    "run_full_suite",
    "run_test_selection",
    "schema_file_records",
    "source_commit_sha",
    "status_claims",
    "status_label",
    "table",
]

BUNDLE_SCHEMA_VERSION: Final = 1

#: Every test module that builds a proof bundle. Each generator excludes all of them from
#: its verification run rather than only its own; see :func:`run_full_suite`.
GENERATOR_TEST_PATHS: Final = ("tests/test_phase0_proof.py", "tests/test_phase1_proof.py")

# Prose that names a check and gives it a status. A sentence is the unit, because a
# status word further away than that is talking about something else.
CHECK_REFERENCE: Final = re.compile(
    r"\b(?:check|criterion|criteria)\s+(?P<number>D?[0-9]+)\b", re.IGNORECASE
)
STATUS_CLAIM: Final = re.compile(r"\b(?P<word>covered|deferred|gaps?)\b", re.IGNORECASE)
CLAUSE_BREAK: Final = re.compile(r"[.;|\n]")
STATUS_BY_WORD: Final = {
    "covered": CriterionStatus.COVERED,
    "deferred": CriterionStatus.DEFERRED,
    "gap": CriterionStatus.GAP,
    "gaps": CriterionStatus.GAP,
}
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


class ProofBundleError(RuntimeError):
    pass


class GoldenDigestDriftError(ProofBundleError):
    pass


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
    environment = dict(os.environ)
    environment[nested_env] = "1"
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


def collect_node_ids(repo_root: Path, *, nested_env: str) -> tuple[str, ...]:
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


def _clause_around(text: str, reference: re.Match[str]) -> tuple[str, str]:
    return (
        CLAUSE_BREAK.split(text[: reference.start()])[-1],
        CLAUSE_BREAK.split(text[reference.end() :], maxsplit=1)[0],
    )


def status_claims(text: str) -> tuple[tuple[str, CriterionStatus], ...]:
    """Every ``(check number, status)`` pair this prose asserts.

    A status word after the reference wins over one before it, because that is how a
    status is ascribed: ``Check 9 is deferred``. Only the first word in the clause counts,
    so ``deferred rather than covered`` claims one status and not two. No attempt is made
    to read a negation: ``Check 9 is not covered`` reads here as a claim of COVERED, and
    is meant to, because a claim a machine cannot check does not belong in this bundle.
    """
    claims: list[tuple[str, CriterionStatus]] = []
    for reference in CHECK_REFERENCE.finditer(text):
        before, after = _clause_around(text, reference)
        claimed = STATUS_CLAIM.search(after) or STATUS_CLAIM.search(before)
        if claimed is None:
            continue
        claims.append((reference.group("number"), STATUS_BY_WORD[claimed.group().lower()]))
    return tuple(claims)


def contradicting_status_claims(
    documents: Mapping[str, str],
    checks: Sequence[CriterionSpec],
) -> tuple[str, ...]:
    """Prose in a bundle that gives a check a status the criteria definition does not.

    The bundle exists so a reviewer can trust it without reading the suite, so a sentence
    that disagrees with the gate is the one defect it cannot survive. Tables are rendered
    from the computed status and cannot disagree; a hand-written sentence can, and did.
    """
    recorded = {check.number: check.status for check in checks}
    problems: list[str] = []
    for filename, text in sorted(documents.items()):
        for number, claimed in status_claims(text):
            actual = recorded.get(number)
            if actual is None:
                problems.append(
                    f"{filename} calls check {number} {claimed.value}, and no criterion of "
                    "this phase carries that number"
                )
            elif actual is not claimed:
                problems.append(
                    f"{filename} calls check {number} {claimed.value}; the criteria "
                    f"definition records it {actual.value}"
                )
    return tuple(problems)


def recorded_status(checks: Sequence[CriterionSpec], number: str) -> CriterionStatus:
    for check in checks:
        if check.number == number:
            return check.status
    raise ProofBundleError(
        f"a known limitation names check {number}, which the criteria definition does not record"
    )
