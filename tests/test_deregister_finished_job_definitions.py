"""The sweeper that retires per-run job definitions, and the two ways it could be dangerous.

Both are asserted rather than reasoned about. It could match the wrong names, in which case
it either does nothing for ever or deregisters something somebody else owns. And it could
retire a definition underneath a job that is still running.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load() -> Any:
    """Import the tool by path, the way the other tool tests here do.

    Registered in ``sys.modules`` before execution, because a module that imports itself
    indirectly during execution would otherwise get a second, half-initialised copy -- the
    defect ``stop-the-loader-clobbering-an-import`` fixed for the other loaders.
    """
    name = "deregister_finished_job_definitions"
    if name in sys.modules:
        return sys.modules[name]
    path = PROJECT_ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TOOL = _load()

RUN_A = "run_019fc322-a474-7090-b471-97ddbeea0899"
RUN_B = "run_019fb3d0-e94e-7057-826c-2965e344e5ac"


class FakeBatch:
    def __init__(self, names: list[str], *, pages: int = 1) -> None:
        self.names = names
        self.pages = pages
        self.deregistered: list[str] = []
        self.refuse: set[str] = set()

    def describe_job_definitions(self, **arguments: Any) -> Any:
        token = arguments.get("nextToken")
        index = int(token) if token else 0
        chunk = self.names[index :: self.pages] if self.pages > 1 else self.names
        answer: dict[str, Any] = {
            "jobDefinitions": [
                {"jobDefinitionName": name, "revision": 1, "jobDefinitionArn": f"arn::{name}"}
                for name in chunk
            ]
        }
        if index + 1 < self.pages:
            answer["nextToken"] = str(index + 1)
        return answer

    def deregister_job_definition(self, **arguments: Any) -> Any:
        target = str(arguments["jobDefinition"])
        if target.split(":", maxsplit=1)[0] in self.refuse:
            raise RuntimeError("ClientError: JobDefinitionNotFoundException")
        self.deregistered.append(target)
        return {}


class FakeLister:
    def __init__(self, run_ids: list[str]) -> None:
        self.run_ids = run_ids

    def list_objects_v2(self, **arguments: Any) -> Any:
        return {
            "Contents": [{"Key": f"result/{run_id}.json"} for run_id in self.run_ids],
            "IsTruncated": False,
        }


def definition_for(run_id: str) -> str:
    from edullm_platform.execution import job_definition_name

    return job_definition_name(run_id)


def test_the_name_it_matches_is_the_one_the_submitter_registers() -> None:
    """THE CORRECTION THE WHOLE TOOL TURNS ON. Mutation: match ``run_`` at the start.

    The build dispatch describes these as ``run_<uuid>``. Nothing in the account is called
    that; measured on 2026-08-02, all 58 are ``sbsandbox-intern-edullm-run_<uuid>``. A
    sweeper written against the documented name matches nothing, reports a clean account
    every night, and the backlog grows behind a green check -- which is strictly worse than
    never having written it, because it also answers the question.

    Asserted against ``job_definition_name`` rather than against a literal, so the prefix
    here and the name the submitter mints cannot drift apart.
    """
    assert definition_for(RUN_A).startswith(TOOL.DEFINITION_PREFIX)
    assert not definition_for(RUN_A).startswith("run_")
    assert TOOL.DEFINITION_PREFIX == "sbsandbox-intern-edullm-run_"


def test_a_definition_whose_run_has_not_finished_is_left_alone() -> None:
    """Mutation: retire everything matching the name.

    Batch documents that a running job survives its definition being deregistered, and this
    does not lean on that. A result record is this platform's own written statement that a
    run reached a terminal state, and it is the only thing that makes a definition
    retirable. A run still going, and a run whose recorder never fired, both stay.
    """
    definitions = [
        TOOL.Definition(name=definition_for(RUN_A), revision=1, arn="a"),
        TOOL.Definition(name=definition_for(RUN_B), revision=1, arn="b"),
    ]
    retire, keep = TOOL.retirable(definitions, finished={RUN_A})

    assert [entry.run_id for entry in retire] == [RUN_A]
    assert [entry.run_id for entry in keep] == [RUN_B]


def test_definitions_this_platform_did_not_register_are_never_touched() -> None:
    """Mutation: drop the prefix filter and sweep every ACTIVE definition.

    The shared per-shape definitions from ``config/execution-targets.yaml`` are what every
    run is submitted against, and ``edullm-validator`` and friends belong to work outside
    this platform. ``edullm-validator`` alone carries ten live ACTIVE revisions, so a
    sweeper that retired by age or by count rather than by name would take them first.
    """
    batch = FakeBatch(
        [
            definition_for(RUN_A),
            "sbsandbox-intern-edullm-gpu-1xh100-run",
            "sbsandbox-intern-edullm-cpu-run",
            "edullm-validator",
            "edullm-reservoir-ingest",
        ]
    )

    assert [entry.name for entry in TOOL.active_run_definitions(batch)] == [definition_for(RUN_A)]


def test_the_listing_is_read_to_the_end() -> None:
    """Mutation: take the first page.

    ``describe_job_definitions`` answers a hundred at a time and the account already holds
    114 revisions in total, so a single call describes a prefix of the list. The failure
    shape is the same as the name one: a partial sweep reports success and the tail
    accumulates.
    """
    names = [definition_for(f"run_019fc322-a474-7090-b471-97ddbeea08{index:02d}") for index in range(6)]
    batch = FakeBatch(names, pages=3)

    assert len(TOOL.active_run_definitions(batch)) == len(names)


def test_one_refusal_does_not_abandon_the_rest_of_the_backlog() -> None:
    """Mutation: let the exception out.

    A definition already retired between the listing and the call is an ordinary race, not
    an error: the listing asks for ACTIVE and deregistration is the only thing that changes
    that, so this is idempotent by construction. Stopping on the first would leave 57 in
    place for the sake of one.
    """
    first, second = definition_for(RUN_A), definition_for(RUN_B)
    batch = FakeBatch([first, second])
    batch.refuse = {first}
    definitions = [
        TOOL.Definition(name=first, revision=1, arn="a"),
        TOOL.Definition(name=second, revision=1, arn="b"),
    ]

    outcomes = dict(TOOL.deregister(batch, definitions))

    assert outcomes[first].startswith("refused")
    assert outcomes[second] == "deregistered"
    assert batch.deregistered == [f"{second}:1"]


def test_nothing_is_deregistered_without_being_asked() -> None:
    """Mutation: make ``--apply`` the default.

    Every tool here that can reach the account reports first. The first thing anybody does
    with a sweeper is look at what it would take, and a default that acted would make that
    reading impossible to do safely once.
    """
    batch = FakeBatch([definition_for(RUN_A)])
    lister = FakeLister([RUN_A])

    definitions = TOOL.active_run_definitions(batch)
    finished = TOOL.runs_with_a_result(lister, lineage_bucket="sbsandbox-intern-edullm-lineage")
    retire, keep = TOOL.retirable(definitions, finished=finished)
    report = TOOL.render(retire, keep, applied=False)

    assert batch.deregistered == []
    assert "Would deregister" in report
    assert RUN_A in report


def test_the_report_names_what_it_left_as_well_as_what_it_took() -> None:
    """Mutation: print only the retired list.

    "58 definitions, 51 retirable" and "58 definitions, 0 retirable" are different accounts
    and the second is a recorder that stopped projecting terminal events. A report showing
    only the first list reads identically in both.
    """
    retire = [TOOL.Definition(name=definition_for(RUN_A), revision=1, arn="a")]
    keep = [TOOL.Definition(name=definition_for(RUN_B), revision=1, arn="b")]

    report = TOOL.render(retire, keep, applied=True)

    assert "Deregistered" in report
    assert "## Left alone" in report
    assert RUN_B in report


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("sbsandbox-intern-edullm-lineage", "sbsandbox-intern-edullm-lineage"),
        ("s3://sbsandbox-intern-edullm-lineage", "sbsandbox-intern-edullm-lineage"),
        ("s3://sbsandbox-intern-edullm-lineage/result/", "sbsandbox-intern-edullm-lineage"),
    ],
)
def test_a_bucket_may_be_named_either_way(given: str, expected: str) -> None:
    assert TOOL._lineage_bucket(given) == expected


def test_a_bucket_that_is_a_path_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(TOOL.ReportInputError):
        TOOL._lineage_bucket("some/path")
