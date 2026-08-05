"""Whether a committed fixture names anything the reviewed configuration no longer has.

**The failure this is written about.** ``olmo-core-train-1gpu`` and ``olmo-core-train-4gpu``
were collapsed into ``olmo-core-train`` when the catalog stopped naming a machine in a
workload name. Ten submission fixtures across four ``edu-llm/OLMo-core`` branches went on
naming ``olmo-core-train-4gpu``, and every one of them was refused at that field and nowhere
else. Four pre-training workstreams could not submit, and nothing anywhere was red: the
catalog was internally consistent, the fixtures were valid JSON, and no test compared the
two. This module is that comparison.

**The subject is derived, not listed, on both sides.** Which documents are fixtures is
decided by shape -- a mapping carrying a ``workload_profile`` key -- rather than by path, so
a fixture parked outside ``fixtures/manifests/`` is covered on the day it is committed. What
names are valid is read out of ``config/`` at the moment the test runs, so retiring an entry
makes this red without an edit here. A list of either side written in Python is the shape of
mistake being closed rather than a way of closing it: the fixtures were already a set
somebody had written down, and what nobody had written down was that the set had to agree
with the catalog.

**It reads what is committed rather than what is on disk**, because that is the difference
that mattered. Nathan's fixtures were correct on the branch they were written on and wrong
once the catalog moved underneath them; a checkout can carry an uncommitted correction and a
push carries only the commit. ``git ls-files`` is therefore the enumeration, which is what
makes this a subprocess test.

**Five fields and not one.** The incident was a workload profile, and a compute profile, a
dataset release, a team or a repository going the same way costs the same and is refused the
same. ``config/`` answers all five, so checking one would have been a decision to be
surprised by the next four.

**WHAT IS DELIBERATELY EXCLUDED, AND IT IS NOT A CONVENIENCE.**
``fixtures/evidence/`` holds records of runs that were submitted, and three of them name
``olmo-core-cpu-smoke`` and ``olmo-core-train-smoke`` -- workloads that were renamed after
those runs happened. ``config/workload-catalog.yaml`` sets out at length why they were not
rewritten: a lineage record states what a run *was submitted as* and carries a
``manifest_sha256`` over its own bytes, so editing the name inside one would both falsify
the record and break its digest. A record of the past is not a fixture anybody can submit,
and a test that failed on one would be asking for history to be forged. The exclusion is one
directory, named here rather than pattern-matched, so adding a second is a decision somebody
makes in this file instead of a glob quietly widening.

**A test that cannot fail is worth nothing, which this repository has already been caught
by.** ``tests/test_workload_dataset_reach.py`` records two assertions found green over the
exact state they existed to refuse. So :func:`test_a_retired_name_is_actually_caught` plants
the real retired name into a real committed fixture's payload and requires this checker to
report it, and :func:`test_every_field_is_actually_checked` does the same once per field.
Without those, a checker that discovered no documents at all would pass.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
CONFIG_DIR: Final = PROJECT_ROOT / "config"

#: The one directory whose documents record what was submitted rather than what may be.
#: The module docstring carries the argument; this is a prefix match on the repository-
#: relative path, so the whole tree under it is out.
RECORDED_HISTORY: Final = "fixtures/evidence/"

#: The key that makes a committed mapping a submission fixture. Every form this platform
#: accepts carries it -- ``SubmissionInputs``, ``RunManifest`` and the intent record all
#: declare it required -- and nothing else committed here does except the catalog that
#: defines it and the source that reads it, neither of which parses as a mapping with it at
#: the top level.
FIXTURE_KEY: Final = "workload_profile"

#: Suffixes worth parsing. A fixture is data, and this platform writes data in exactly these
#: two formats: the submission forms are JSON and the representative manifests are YAML.
DATA_SUFFIXES: Final = (".json", ".yaml", ".yml")

#: A dataset release naming no corpus, which is a real answer on the form rather than an
#: absent field. ``DatasetRegistry.is_registered`` already accepts it, so this constant is
#: not consulted here; it is named so a reader wondering whether ``none`` is handled does
#: not have to go and find out.
NO_DATASET: Final = "none"


@dataclass(frozen=True)
class Finding:
    """One committed fixture naming one thing the reviewed configuration does not have."""

    path: str
    field: str
    value: str
    offered: str

    def __str__(self) -> str:
        return (
            f"{self.path}: {self.field} is {self.value!r}, which config/ does not have. "
            f"Offered: {self.offered}"
        )


@dataclass(frozen=True)
class ReviewedNames:
    """Every name a fixture may use, read from ``config/`` rather than written here."""

    workload_profiles: frozenset[str]
    compute_profiles: frozenset[str]
    teams: frozenset[str]
    repositories: frozenset[str]
    datasets: DatasetRegistry

    @classmethod
    def read(cls, config_dir: Path) -> ReviewedNames:
        catalog = load_yaml(config_dir / "workload-catalog.yaml", WorkloadCatalog)
        inventory = load_yaml(config_dir / "organization.yaml", OrganizationInventory)
        registry = load_yaml(config_dir / "repositories.yaml", RepositoryRegistry)
        return cls(
            workload_profiles=frozenset(entry.name for entry in catalog.workloads),
            compute_profiles=frozenset(entry.name for entry in catalog.compute_profiles),
            teams=frozenset(team.team_id for team in inventory.team_bindings.teams),
            repositories=frozenset(entry.repository for entry in registry.repositories),
            datasets=load_yaml(config_dir / "datasets.yaml", DatasetRegistry),
        )

    def findings_in(self, path: str, payload: dict[str, object]) -> list[Finding]:
        """Every field of this fixture that names something not in the configuration.

        Every field rather than the first, for the reason
        ``edullm_platform.cli.preflight.run_preflight`` collects refusals rather than
        stopping: somebody fixing three names one test run at a time is three runs, and
        the second and third were visible the first time.
        """
        found: list[Finding] = []
        for field, known in (
            ("workload_profile", self.workload_profiles),
            ("compute_profile", self.compute_profiles),
            ("team", self.teams),
            ("repository", self.repositories),
        ):
            value = payload.get(field)
            if isinstance(value, str) and value not in known:
                found.append(
                    Finding(path, field, value, ", ".join(sorted(known)))
                )
        release = payload.get("dataset_release")
        if isinstance(release, str) and not self.datasets.is_registered(release):
            found.append(
                Finding(path, "dataset_release", release, "see config/datasets.yaml")
            )
        return found


def committed_data_files(root: Path) -> tuple[str, ...]:
    """Every committed JSON or YAML path, repository-relative, history excluded."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return tuple(
        path
        for path in listing.split("\0")
        if path.endswith(DATA_SUFFIXES) and not path.startswith(RECORDED_HISTORY)
    )


def committed_fixtures(root: Path) -> Iterator[tuple[str, dict[str, object]]]:
    """Each committed submission fixture, as its path and its parsed mapping.

    A document that will not parse is skipped rather than failed on. Whether every
    committed YAML is well formed is a question other tests already ask of the files they
    own, and answering it again here would make this module fail for a reason that has
    nothing to do with the names in it.
    """
    for path in committed_data_files(root):
        text = (root / path).read_text(encoding="utf-8")
        try:
            payload = (
                json.loads(text) if path.endswith(".json") else yaml.safe_load(text)
            )
        except (json.JSONDecodeError, yaml.YAMLError):
            continue
        if isinstance(payload, dict) and FIXTURE_KEY in payload:
            yield path, payload


@pytest.fixture(scope="module")
def reviewed() -> ReviewedNames:
    return ReviewedNames.read(CONFIG_DIR)


@pytest.fixture(scope="module")
def fixtures() -> tuple[tuple[str, dict[str, object]], ...]:
    return tuple(committed_fixtures(PROJECT_ROOT))


@pytest.mark.slow
def test_no_committed_fixture_names_anything_the_configuration_lacks(
    reviewed: ReviewedNames,
    fixtures: tuple[tuple[str, dict[str, object]], ...],
) -> None:
    findings = [
        finding
        for path, payload in fixtures
        for finding in reviewed.findings_in(path, payload)
    ]
    assert not findings, "\n".join(str(finding) for finding in findings)


@pytest.mark.slow
def test_the_discovery_finds_the_fixtures_this_repository_commits(
    fixtures: tuple[tuple[str, dict[str, object]], ...],
) -> None:
    """The six representative manifests, at least, or the check above proved nothing.

    Named as a floor rather than as an exact set: a seventh fixture is a thing somebody
    should be able to commit without editing a test, and the assertion that matters is
    that discovery by shape reaches the fixtures already here. Discovering nothing is the
    way this module fails silently, and it is the only way, so it is asserted separately
    from the names.
    """
    discovered = {path for path, _ in fixtures}
    representative = {
        f"fixtures/manifests/{path.name}"
        for path in (PROJECT_ROOT / "fixtures" / "manifests").glob("*.yaml")
    }
    assert representative
    assert representative <= discovered


@pytest.mark.slow
def test_recorded_history_is_excluded_and_would_otherwise_fire(
    reviewed: ReviewedNames,
) -> None:
    """The exclusion is load-bearing, so it is asserted rather than assumed.

    If the committed lineage records ever stop naming a retired workload, this fails and
    the exclusion above should be reconsidered rather than kept out of habit.
    """
    evidence = PROJECT_ROOT / "fixtures" / "evidence"
    recorded = {
        payload["workload_profile"]
        for path in evidence.rglob("*.json")
        if isinstance(payload := _parsed(path), dict) and FIXTURE_KEY in payload
    }
    assert recorded, "no committed record names a workload profile at all"
    retired = recorded - reviewed.workload_profiles
    assert retired, (
        "every committed record names a live workload profile, so "
        f"{RECORDED_HISTORY} no longer needs excluding: {sorted(recorded)}"
    )


def _parsed(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


@pytest.mark.slow
def test_a_retired_name_is_actually_caught(
    reviewed: ReviewedNames,
    fixtures: tuple[tuple[str, dict[str, object]], ...],
) -> None:
    """The real name from the real incident, planted in a real committed fixture."""
    path, payload = fixtures[0]
    findings = reviewed.findings_in(
        path, {**payload, "workload_profile": "olmo-core-train-4gpu"}
    )
    assert [finding.field for finding in findings] == ["workload_profile"]
    assert "olmo-core-train-4gpu" in str(findings[0])
    assert "olmo-core-train" in findings[0].offered


@pytest.mark.slow
@pytest.mark.parametrize(
    "field",
    ["workload_profile", "compute_profile", "team", "repository", "dataset_release"],
)
def test_every_field_is_actually_checked(
    field: str,
    reviewed: ReviewedNames,
    fixtures: tuple[tuple[str, dict[str, object]], ...],
) -> None:
    """One planted name per field, so a field silently dropped from the loop is red.

    ``findings_in`` reads the five out of two structures rather than one, and the
    dataset asks a registry rather than a set, so the loop is the kind of thing an edit
    can shorten without anything noticing.
    """
    path, payload = fixtures[0]
    findings = reviewed.findings_in(path, {**payload, field: "not-a-name-anything-has"})
    assert field in {finding.field for finding in findings}
