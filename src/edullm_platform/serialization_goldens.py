"""The recorded canonical digest of everything this repository must not change silently.

Three sets of committed artifacts carry a digest that is checked on every run, and each of
them fails for a reason nothing else in the suite would notice.

**Contract fixtures.** The nine files under ``fixtures/manifests/`` and
``fixtures/authorization/`` are digested over the validated model rather than over the
bytes, so a change to field ordering, to a serializer, or to a default value lands here and
nowhere else. Reindenting a fixture is not drift; a field changing value is.

**IAM role templates.** The nine roles declared under ``infra/iam/`` are digested over the
projection the drift comparison acts on rather than over the file, so this fails when a role
gains or loses a permission and not when somebody rewrites a comment. Seven of the nine have
no capture to compare against, so for those the recorded digest is the only thing between a
template widened in the meantime and nobody noticing.

**Admitted run captures.** The three records under ``fixtures/evidence/phase-5/runs/`` are
the only evidence that two people who did not build the platform used it. They cannot be
re-taken once the account moves on, and they name workload profiles that have since been
retired, so a re-take would change what they say.

Re-recording is deliberate and is a person's decision. ``tools/record_goldens.py`` writes
the recorded files, and a moved digest is either a change somebody meant, in which case the
diff is reviewed in the same commit, or a regression, in which case re-recording is the
wrong repair.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.decision_matrix import AuthorizationScenario
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.phase2_evidence import PHASE2_ROLE_TEMPLATES
from edullm_platform.phase5_capture import admitted_runs
from edullm_platform.phase5_evidence import AdmittedRunEvidence
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    PHASE3_ROLE_TEMPLATES,
    TemplateRole,
    load_template_roles,
)

__all__ = [
    "FIXTURE_DIRECTORIES",
    "GOLDENS_DIR",
    "GOLDEN_SETS",
    "ROLE_TEMPLATES",
    "FixtureReference",
    "GoldenDrift",
    "GoldenSet",
    "GoldensError",
    "RecordedGolden",
    "admitted_run_goldens",
    "committed_role",
    "contract_fixture_goldens",
    "discover_fixtures",
    "fixture_canonical_length",
    "fixture_digest",
    "golden_drift",
    "golden_drift_guidance",
    "load_recorded_goldens",
    "recorded_path",
    "render_goldens_document",
    "role_template_goldens",
]

GOLDENS_SCHEMA_VERSION: Final = 1

#: Where the recorded digests are committed, relative to the repository root.
GOLDENS_DIR: Final = Path("fixtures") / "goldens"

RECORD_COMMAND: Final = "uv run python tools/record_goldens.py"

GOLDEN_DRIFT_GUIDANCE: Final = """{fixture} ({contract}) no longer serializes to its recorded canonical digest.
  recorded: {recorded}
  live:     {live}

This is a serialization tripwire, not a formatting check. A change to field ordering, to a
serializer, to a default value, or to the artifact itself lands here and nowhere else.

Do exactly one of these, deliberately:

  1. The change was intended. Re-record with
       {command}
     and review the digest diff in the same commit as the change that caused it, so the new
     digest is approved by a human rather than absorbed silently.

  2. The change was not intended. This is a regression: fix it instead of re-recording."""

GOLDENS_MISSING_GUIDANCE: Final = (
    "No recorded canonical digests were found at {path}. Generate them with "
    f"`{RECORD_COMMAND}` and commit the result."
)


class GoldensError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------
# What a recorded digest is
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


def golden_drift_guidance(*, command: str = RECORD_COMMAND) -> str:
    """The guidance with the re-recording command in it, and the fields left open."""
    return GOLDEN_DRIFT_GUIDANCE.replace("{command}", command)


def render_goldens_document(goldens: Sequence[RecordedGolden], *, subject: str) -> str:
    payload = {
        "schema_version": GOLDENS_SCHEMA_VERSION,
        "subject": subject,
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


# --------------------------------------------------------------------------------------
# The committed contract fixtures
# --------------------------------------------------------------------------------------

FIXTURE_DIRECTORIES: Final[tuple[tuple[str, type[ContractModel]], ...]] = (
    ("fixtures/manifests", RunManifest),
    ("fixtures/authorization", AuthorizationScenario),
)


@dataclass(frozen=True)
class FixtureReference:
    fixture: str
    relative_path: str
    model_type: type[ContractModel]

    @property
    def contract(self) -> str:
        return self.model_type.__name__


def discover_fixtures(repo_root: Path) -> tuple[FixtureReference, ...]:
    references: list[FixtureReference] = []
    for directory, model_type in FIXTURE_DIRECTORIES:
        for path in sorted((repo_root / directory).glob("*.yaml")):
            references.append(
                FixtureReference(
                    fixture=path.name,
                    relative_path=f"{directory}/{path.name}",
                    model_type=model_type,
                )
            )
    names = [reference.fixture for reference in references]
    if len(set(names)) != len(names):
        raise GoldensError("fixture file names must be unique across fixture directories")
    return tuple(sorted(references, key=lambda reference: reference.relative_path))


def load_fixture(repo_root: Path, reference: FixtureReference) -> ContractModel:
    return load_yaml(repo_root / reference.relative_path, reference.model_type)


def fixture_digest(repo_root: Path, reference: FixtureReference) -> str:
    return sha256_digest(load_fixture(repo_root, reference))


def fixture_canonical_length(repo_root: Path, reference: FixtureReference) -> int:
    return len(canonical_json_bytes(load_fixture(repo_root, reference)))


def contract_fixture_goldens(repo_root: Path) -> tuple[RecordedGolden, ...]:
    return tuple(
        RecordedGolden(
            fixture=reference.fixture,
            relative_path=reference.relative_path,
            contract=reference.contract,
            canonical_json_bytes=fixture_canonical_length(repo_root, reference),
            digest=fixture_digest(repo_root, reference),
        )
        for reference in discover_fixtures(repo_root)
    )


# --------------------------------------------------------------------------------------
# The committed IAM role templates
# --------------------------------------------------------------------------------------

#: Every role this repository commits a template for. The three registries stay separate
#: where they are declared, because each is the subject of a different capture and a
#: different freshness window; what is shared is only the digest taken over them.
ROLE_TEMPLATES: Final[tuple[tuple[str, str], ...]] = (
    *COMMITTED_ROLE_TEMPLATES,
    *PHASE2_ROLE_TEMPLATES,
    *PHASE3_ROLE_TEMPLATES,
)


def committed_role(repo_root: Path, *, role_name: str, relative_path: str) -> TemplateRole:
    roles = load_template_roles(repo_root / relative_path)
    matching = [role for role in roles if role.role_name == role_name]
    if len(matching) != 1:
        raise GoldensError(f"{relative_path} does not declare exactly one {role_name}")
    return matching[0]


def role_template_goldens(repo_root: Path) -> tuple[RecordedGolden, ...]:
    """One digest per committed role, over its projection rather than over the file.

    A drift here is a change to what a role may do, and re-recording it is the same moment
    somebody has to go and re-capture the account, because a role that compared clean
    against the old projection has not been compared against this one.
    """
    return tuple(
        RecordedGolden(
            fixture=role_name,
            relative_path=relative_path,
            contract=TemplateRole.__name__,
            canonical_json_bytes=len(
                canonical_json_bytes(
                    committed_role(repo_root, role_name=role_name, relative_path=relative_path)
                )
            ),
            digest=sha256_digest(
                committed_role(repo_root, role_name=role_name, relative_path=relative_path)
            ),
        )
        for role_name, relative_path in ROLE_TEMPLATES
    )


# --------------------------------------------------------------------------------------
# The committed pilot run captures
# --------------------------------------------------------------------------------------

ADMITTED_RUN_CAPTURE_DIR: Final = "fixtures/evidence/phase-5/runs"


def admitted_run_goldens(repo_root: Path) -> tuple[RecordedGolden, ...]:
    """One digest per committed capture, over the parsed record rather than over the file.

    Over the parsed model rather than the file bytes, so reindenting a record is not drift
    and a field changing value is.
    """
    return tuple(
        RecordedGolden(
            fixture=run.run_id,
            relative_path=f"{ADMITTED_RUN_CAPTURE_DIR}/{run.run_id}/admitted-run.sanitized.json",
            contract=AdmittedRunEvidence.__name__,
            canonical_json_bytes=len(canonical_json_bytes(run.record)),
            digest=sha256_digest(run.record),
        )
        for run in admitted_runs(repo_root / "fixtures" / "evidence" / "phase-5")
    )


# --------------------------------------------------------------------------------------
# The three sets, and where each is recorded
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldenSet:
    """One subject, the file its digests are recorded in, and how to recompute them."""

    subject: str
    filename: str
    live: Callable[[Path], tuple[RecordedGolden, ...]]


GOLDEN_SETS: Final[tuple[GoldenSet, ...]] = (
    GoldenSet(
        subject="contract-fixtures",
        filename="contract-fixtures.json",
        live=contract_fixture_goldens,
    ),
    GoldenSet(
        subject="iam-role-templates",
        filename="iam-role-templates.json",
        live=role_template_goldens,
    ),
    GoldenSet(
        subject="admitted-runs",
        filename="admitted-runs.json",
        live=admitted_run_goldens,
    ),
)


def recorded_path(repo_root: Path, golden_set: GoldenSet) -> Path:
    return repo_root / GOLDENS_DIR / golden_set.filename
