"""The four things the lane refuses, and the reason each survives an ungated route.

THE STANDING DIRECTION THIS FILE IS WRITTEN AGAINST. The owner's position is that the lane is
ungated by design and that a gate leaking into it is a defect rather than a trade-off. So the
question for every candidate refusal is not "would this be nice to catch" but "is this a
permission". A refusal that says a destination is misspelled is not a permission: nothing is
being withheld, and the same command works the moment the spelling is right.

tests/test_lane_verdicts.py is the other half and is the one with teeth. This file says what the
lane does refuse; that one says the submission path's refusals cannot reach it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.cli.configuration import (
    ConfigurationUnreadableError,
    ReviewedConfiguration,
    load_reviewed_configuration,
)
from edullm_platform.cli.lane import (
    LaneRequest,
    instance_type_for,
    lane_refusals,
    placement_warning,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"


def configuration() -> ReviewedConfiguration:
    return load_reviewed_configuration(CONFIG_DIR)


def request(**overrides: str) -> LaneRequest:
    fields = {
        "project": "mixlaw",
        "team": "memory-split",
        "person": "caiiris",
        "compute_profile": "gpu-1xt4",
    }
    fields.update(overrides)
    return LaneRequest(**fields)


def codes(**overrides: str) -> list[str]:
    return [
        refusal.code
        for refusal in lane_refusals(request(**overrides), configuration=configuration())
    ]


def test_a_clean_lane_request_is_refused_nothing() -> None:
    """THE CASE THE WHOLE SLICE IS FOR.
    Mutation: refuse anything at all on a well-formed ask.

    A researcher standing in any directory, naming a project and a machine, gets the machine.
    """
    assert codes() == []


def test_a_machine_the_catalog_does_not_price_is_refused() -> None:
    """Mutation: pass an unknown name through to run-instances.

    The catalog is where an instance type comes from, and it is also the allow-list the
    researcher role enforces at launch. A name the catalog does not carry produces no instance
    type at all, so the alternative to refusing here is an aws CLI usage error about a missing
    parameter.
    """
    assert codes(compute_profile="gpu-9000") == ["unknown_machine"]


def test_a_machine_the_catalog_prices_but_does_not_provision_is_allowed() -> None:
    """THE ONE THE SUBMISSION PATH REFUSES AND THIS ONE MUST NOT.
    Mutation: call resolve_compute_profile_for_execution, which is what check calls.

    "Provisioned" is a statement about a Batch queue existing. A lane machine is not a Batch job,
    so a profile with no queue is a shape a researcher may legitimately start, and refusing it
    here would import the submission path's meaning of a word into a route that has no queues at
    all. gpu-1xh100 is priced, unprovisioned, and p5.4xlarge is on the role's allow-list.
    """
    assert codes(compute_profile="gpu-1xh100") == []


def test_a_shape_that_does_not_place_is_warned_about_rather_than_refused() -> None:
    """Mutation: turn the warning into a refusal.

    system-overview.md, "The machines", records that the compile step warns rather than refuses
    on a shape config/capacity.yaml says may not place, and gives the reason: the submitter is
    told and the choice stays theirs. The lane inherits the sentence and not a new rule.
    """
    warned = placement_warning(configuration(), "gpu-1xh100")

    assert warned is not None
    assert codes(compute_profile="gpu-1xh100") == []


def test_a_shape_that_places_reliably_is_said_nothing_about() -> None:
    """Mutation: warn on every shape.

    A line printed before every machine is a line nobody reads by their fifth run, and the one
    it would drown is the expiry. gpu-1xt4 places reliably, which is the case that has to be
    silent for the warning above to mean anything.
    """
    assert placement_warning(configuration(), "gpu-1xt4") is None


def test_the_warning_names_the_instrument_that_measured_it() -> None:
    """Mutation: print the verdict and drop measured_by.

    placement.py's header records why the two are not the same strength of claim: a queue that
    asked repeatedly and never got a machine has settled it, and a single instant probe has been
    overturned eight times in that same direction. A researcher deciding whether to wait needs
    to know which one they are reading.
    """
    warned = placement_warning(configuration(), "gpu-1xh100")

    assert warned is not None
    assert "queue" in warned


def test_the_warning_is_a_terminal_sentence_and_carries_no_markdown() -> None:
    """Mutation: reuse placement.placement_warning, which is the compile step's.

    That one is written for a pull request comment and carries **bold** and backticked paths,
    which in a terminal are literal asterisks somebody has to read past. It is also five
    sentences, printed above the one line about the expiry that most has to be seen. Same file,
    same verdicts, different reader.
    """
    warned = placement_warning(configuration(), "gpu-1xh100")

    assert warned is not None
    assert "**" not in warned
    assert "`" not in warned


def test_a_capacity_file_that_will_not_parse_is_an_unusable_install_and_not_a_traceback(
    tmp_path: Path,
) -> None:
    """Mutation: let UnreadableCapacityError out.

    read_capacity raises rather than defaulting to "everything places", which is right and is
    the decision its own docstring records. What must not happen is that reaching a terminal:
    ConfigurationUnreadableError is the class main() already turns into exit 2, and a researcher
    who meets a traceback on their first command learns the tool is broken.
    """
    directory = tmp_path / "config"
    directory.mkdir()
    for name in CONFIG_DIR.glob("*.yaml"):
        (directory / name.name).write_text(name.read_text(encoding="utf-8"), encoding="utf-8")
    (directory / "capacity.yaml").write_text("profiles: not-a-list\n", encoding="utf-8")

    with pytest.raises(ConfigurationUnreadableError):
        placement_warning(load_reviewed_configuration(directory), "gpu-1xh100")


def test_a_team_the_roster_does_not_declare_is_refused() -> None:
    """Mutation: accept whatever was typed.

    Team is the first segment of the working prefix. A team nothing declares creates a prefix no
    listing of any group's work will ever include, so the files are not lost and are unfindable,
    which is worse. This is a spelling check on a destination rather than a permission: nobody is
    being told they may not have a machine.
    """
    assert codes(team="memroy-split") == ["unknown_team"]


def test_a_project_that_is_empty_is_refused() -> None:
    """Mutation: default it to the team, or to "default".

    The project names the working prefix's last segment, tags the instance and the volume, and is
    the value the researcher role's condition holds the launch tag equal to. A default would put
    two unrelated pieces of work in one prefix and under one cost tag, and the person who picked
    neither would be the one who could not tell them apart.
    """
    assert codes(project="") == ["no_project"]


def test_a_caller_who_is_already_in_the_lane_is_told_what_to_do() -> None:
    """Mutation: fall back to the session name.

    person_from_caller_arn returns None for a lane session because the person is not in the ARN.
    The verb passes that through as an empty person, and the refusal has to name the remedy
    rather than the field: run the verb from your ordinary session, which is the one thing the
    caller can do.
    """
    refusals = lane_refusals(request(person=""), configuration=configuration())

    assert [refusal.code for refusal in refusals] == ["cannot_tell_who_you_are"]
    assert "ordinary session" in refusals[0].detail


def test_every_refusal_carries_a_remedy_and_not_only_a_code() -> None:
    """Mutation: ship a code with an empty detail.

    decisions.md settles this under "Notification decisions": the code is what a skill and a test
    match on, the text is what a person reads. A refusal carrying only a code sends a first-week
    researcher somewhere unhelpful, which is what adarsh-rajesh-first-run.md records.
    """
    every = [
        *lane_refusals(request(compute_profile="gpu-9000"), configuration=configuration()),
        *lane_refusals(request(team="memroy-split"), configuration=configuration()),
        *lane_refusals(request(project=""), configuration=configuration()),
        *lane_refusals(request(person=""), configuration=configuration()),
    ]

    assert len(every) == 4
    for refusal in every:
        assert len(refusal.detail.split()) > 15


def test_the_lane_refuses_four_things_and_the_list_is_closed() -> None:
    """**THE ASSERTION THAT MAKES A FIFTH REFUSAL A DELIBERATE ACT.**
    Mutation: add a refusal for anything at all.

    Every gate that ever leaked into an ungated route did so one reasonable-looking refusal at a
    time, and each one was defensible on its own. Pinning the set is what makes the next one
    arrive as a failing test with tests/test_lane_verdicts.py's ruling beside it, rather than as
    a line in a diff nobody weighed against "is this a permission".
    """
    everything_wrong = LaneRequest(project="", team="", person="", compute_profile="")

    refused = {
        refusal.code for refusal in lane_refusals(everything_wrong, configuration=configuration())
    }

    assert refused == {
        "cannot_tell_who_you_are",
        "no_project",
        "unknown_team",
        "unknown_machine",
    }


def test_the_instance_type_comes_from_the_catalog_and_not_from_a_table_here() -> None:
    """Mutation: keep a profile-to-type map in lane.py.

    config/workload-catalog.yaml is where an instance type lives, and the researcher role's
    allow-list is generated from the same file, so a second table here would be the one that
    disagrees the first time a shape is added.
    """
    assert instance_type_for(configuration(), "gpu-8xt4") == "g4dn.metal"
    assert instance_type_for(configuration(), "gpu-9000") is None
