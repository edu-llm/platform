"""The one flag the lane answers for the researcher, and the one it still will not.

WHAT MOVED AND WHY IT IS NOT THE THIN END OF DEFAULTING THE PROJECT. The plan's argument
against a defaulted project holds and is held below: a project tags the instance and the
volume, it is the last segment of the working prefix, and two unrelated pieces of work under
one name is two unrelated pieces of work on one bill that nobody can afterwards separate. It
is a name only the person has.

A compute profile is not that. It is a price, it is declared in reviewed configuration, and
the platform learned to choose one it can defend: ``cli/scaffold.py`` reads "cheapest" over
the shapes whose cards can actually run what a trainer defaults to, because the cheapest GPU
in the catalog is a Turing card with no bfloat16 and the pair priced at the cheapest rate was
a run that died on the first kernel after the machine was billed. The lane takes that ordering
and not that function, which is keyed on a workload profile the lane has none of.

WHAT THE PLAN'S AUTHOR WOULD OBJECT TO, ANSWERED IN THE CASES BELOW. That a default which
starts a machine spends money nobody asked for. But ``edullm run`` starts a machine on every
reading -- the question was only which one -- and the alternative to choosing is an uninformed
guess by somebody who does not know the catalog, whose most likely guess is the shape that
reads cheapest and is the trap. So the default is the least expensive machine that works, it
is announced with its rate before anything starts, and the expiry bounds it either way.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

from edullm_platform.cli.configuration import ReviewedConfiguration, load_reviewed_configuration
from edullm_platform.cli.lane import (
    LaneRequest,
    default_compute_profile,
    lane_refusals,
    placement_said,
    placement_verdict,
)
from edullm_platform.contracts.base import serialize_decimal
from edullm_platform.precision import gpu_of

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"


def configuration() -> ReviewedConfiguration:
    return load_reviewed_configuration(CONFIG_DIR)


def profile(named: str, *, of: ReviewedConfiguration | None = None):  # type: ignore[no-untyped-def]
    found = next(
        entry for entry in (of or configuration()).catalog.compute_profiles if entry.name == named
    )
    return found


def with_only(names: set[str]) -> ReviewedConfiguration:
    """The real installation with its catalog narrowed, so the fall-throughs are reachable.

    The directory stays the real one because ``config/capacity.yaml`` is read off it, and the
    catalog is a frozen contract model, so this is a copy rather than an edit.
    """
    original = configuration()
    kept = [entry for entry in original.catalog.compute_profiles if entry.name in names]
    return dataclasses.replace(
        original, catalog=original.catalog.model_copy(update={"compute_profiles": kept})
    )


def with_capacity(said: dict[str, str], tmp_path: Path) -> ReviewedConfiguration:
    """The real installation with some verdicts overwritten, in a directory of its own."""
    directory = tmp_path / "config"
    directory.mkdir()
    for name in CONFIG_DIR.glob("*.yaml"):
        (directory / name.name).write_text(name.read_text(encoding="utf-8"), encoding="utf-8")
    capacity = yaml.safe_load((CONFIG_DIR / "capacity.yaml").read_text(encoding="utf-8"))
    for record in capacity["profiles"]:
        if record["profile"] in said:
            record["places"] = said[record["profile"]]
            record.pop("wait", None)
    (directory / "capacity.yaml").write_text(yaml.safe_dump(capacity), encoding="utf-8")
    return load_reviewed_configuration(directory)


def test_the_lane_answers_the_compute_flag_for_somebody_who_named_no_shape() -> None:
    """THE FIRST COMMAND ANYBODY TYPES.
    Mutation: return None and leave --compute required.

    Four flags before a researcher sees a GPU was the friction this removes, and the platform
    holds every fact the choice needs: the catalog prices the shapes, precision.py knows which
    cards have bfloat16, and capacity.yaml records which ones EC2 will sell.
    """
    chosen = default_compute_profile(configuration())

    assert chosen is not None
    assert profile(chosen.profile).accelerator == "gpu"


def test_the_default_is_not_the_cheapest_gpu_because_the_cheapest_cannot_run_the_work() -> None:
    """**THE MUTATION THIS FUNCTION EXISTS AGAINST.**
    Mutation: take the cheapest GPU shape by rate, full stop.

    That is ``gpu-1xt4``, and its T4 is Turing, which has no bfloat16 arithmetic in the
    hardware. A trainer that asks for the format dies on the first kernel needing it, after the
    machine has been obtained and billed, and ``torch.cuda.is_bf16_supported()`` returns true on
    the card so nothing before the device says otherwise. Handing that shape to somebody who
    named no shape at all is the platform making the mistake on their behalf.
    """
    chosen = default_compute_profile(configuration())
    assert chosen is not None
    cheapest = min(
        (entry for entry in configuration().catalog.compute_profiles if entry.accelerator == "gpu"),
        key=lambda entry: entry.hourly_rate_usd,
    )

    assert chosen.profile != cheapest.name
    assert (card := gpu_of(cheapest)) is not None and not card.architecture.supports_bfloat16
    assert (chose := gpu_of(profile(chosen.profile))) is not None
    assert chose.architecture.supports_bfloat16


def test_nothing_that_can_run_the_work_and_places_is_cheaper_than_what_was_chosen() -> None:
    """Mutation: pick by name, or take the first, or take the largest.

    Read over the catalog rather than over the function's own shortlist, so it is a statement
    about the answer and not a restatement of how it was reached. "Cheapest that works" is the
    whole ordering, and a default that quietly cost more than it had to would be the surprise
    the required flag was protecting people from.
    """
    chosen = default_compute_profile(configuration())
    assert chosen is not None
    rate = profile(chosen.profile).hourly_rate_usd

    cheaper = [
        entry.name
        for entry in configuration().catalog.compute_profiles
        if entry.accelerator == "gpu"
        and entry.hourly_rate_usd < rate
        and (card := gpu_of(entry)) is not None
        and card.architecture.supports_bfloat16
        and placement_said(placement_verdict(configuration(), entry.name)) is None
    ]

    assert cheaper == []


def test_the_default_passes_over_a_shape_the_account_may_not_be_able_to_get(
    tmp_path: Path,
) -> None:
    """Mutation: drop the capacity filter.

    Invisible against the shipped configuration, where the cheapest capable shape also places,
    which is exactly why it needs a case that makes it visible. A default is the one choice
    nobody reviews before it runs, so pointing it at a shape ``config/capacity.yaml`` records as
    not placing would hang somebody's first command on a machine that may never arrive -- for
    the sake of a difference in cents against the next one along.
    """
    narrowed = with_capacity({"gpu-1xl4": "unreliably"}, tmp_path)

    chosen = default_compute_profile(narrowed)
    ordinary = default_compute_profile(configuration())

    assert ordinary is not None and ordinary.profile == "gpu-1xl4"
    assert chosen is not None and chosen.profile != "gpu-1xl4"
    assert placement_said(placement_verdict(narrowed, chosen.profile)) is None


def test_a_catalog_where_nothing_places_still_hands_over_a_machine(tmp_path: Path) -> None:
    """Mutation: refuse, or return None, when no shape passes a filter.

    A default that can refuse is worse than the required flag it replaced: the researcher is
    now stopped by a decision they did not make and cannot see. Every filter falls through, and
    the sentence says which one it could not honour rather than the choice disappearing.
    """
    nothing_places = with_capacity(
        {
            record.profile: "unreliably"
            for record in [
                placement_verdict(configuration(), entry.name)
                for entry in configuration().catalog.compute_profiles
            ]
            if record is not None
        },
        tmp_path,
    )

    chosen = default_compute_profile(nothing_places)

    assert chosen is not None
    assert profile(chosen.profile, of=nothing_places).accelerator == "gpu"


def test_a_catalog_where_no_card_has_bfloat16_says_so_by_not_claiming_it() -> None:
    """Mutation: write the reason out once as a constant beside the choice.

    The line quotes why this shape and not a cheaper one, and both filters above fall through,
    so a reason written once is a claim that stops being true the first time the list it
    describes changes underneath it. That is the same disease as an expiry that is not the tag:
    correct on the day it was typed and unfalsifiable afterwards.
    """
    turing_only = with_only({"gpu-1xt4", "gpu-4xt4", "gpu-8xt4"})

    chosen = default_compute_profile(turing_only)

    assert chosen is not None
    assert chosen.profile == "gpu-1xt4"
    assert "bfloat16" not in chosen.said
    assert "bfloat16" in (default_compute_profile(configuration()) or chosen).said


def test_the_line_quotes_the_shape_it_chose_and_that_shape_s_rate() -> None:
    """Mutation: quote the catalog's cheapest rate beside the shape that was actually picked.

    The same rule the expiry now keeps: the thing done and the thing said are produced together
    out of one value, so they cannot part company. A researcher who is told a rate is going to
    plan against it, and the rate of a shape nobody started is worse than no rate at all.
    """
    chosen = default_compute_profile(configuration())
    assert chosen is not None
    entry = profile(chosen.profile)

    assert chosen.profile in chosen.said
    assert entry.instance_type in chosen.said
    assert f"${serialize_decimal(entry.hourly_rate_usd)}/hour" in chosen.said
    assert "--compute" in chosen.said


def test_a_catalog_with_no_gpu_at_all_leaves_the_flag_to_the_researcher() -> None:
    """Mutation: fall back to a CPU profile.

    ``edullm run`` and ``edullm shell`` are how somebody gets a card. Handing over a CPU box to
    a person who asked for a machine and named none would be a default that answered a question
    nobody asked, and the failure would arrive as a program that cannot find a device.
    """
    assert default_compute_profile(with_only({"cpu-32vcpu"})) is None


def test_the_two_ways_to_have_no_machine_do_not_read_the_same() -> None:
    """Mutation: keep one detail for both causes of unknown_machine.

    A shape that was named and is not priced is a misspelling and the remedy is the list. No
    shape at all means the flag was omitted and there was nothing to pick, which is a broken
    installation -- and quoting an empty name back at somebody who typed no name sends them
    looking for a typo they did not make.
    """
    misspelled = lane_refusals(
        LaneRequest(project="mixlaw", person="caiiris", compute_profile="gpu-9000"),
        configuration=configuration(),
    )
    named_none = lane_refusals(
        LaneRequest(project="mixlaw", person="caiiris", compute_profile=""),
        configuration=configuration(),
    )

    assert (
        [one.code for one in misspelled] == [one.code for one in named_none] == ["unknown_machine"]
    )
    assert misspelled[0].detail != named_none[0].detail
    assert "--compute" in named_none[0].detail


def test_defaulting_the_machine_did_not_default_the_project() -> None:
    """**THE ARGUMENT THIS CHANGE HAD TO NOT OVERTURN, PINNED WHERE THE CHANGE IS.**
    Mutation: give the project the same treatment, from the person or from "default".

    The two flags are different in kind and this is the fence on the difference. A machine is a
    price the platform can weigh and the researcher can see in the first line of nvidia-smi. A
    project is a name only they hold: it tags the instance and the volume, it is the last
    segment of the working prefix, and a wrong one puts two unrelated pieces of work under one
    bill in a directory nobody chose, with nothing afterwards able to tell them apart.
    """
    refused = lane_refusals(
        LaneRequest(project="", person="caiiris", compute_profile="gpu-1xl4"),
        configuration=configuration(),
    )

    assert [one.code for one in refused] == ["no_project"]
    assert "no default" in refused[0].detail
