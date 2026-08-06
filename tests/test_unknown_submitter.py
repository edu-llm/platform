"""What ``check`` says when it cannot tell who you are, which used to be less than usual.

**THE FAILURE THIS IS WRITTEN ABOUT IS A CHECK THAT RELAXES WHEN ITS INPUT IS MISSING.**
Measured on 2026-08-05 in this repository: ``edullm check --json`` with ``GH_CONFIG_DIR``
pointed at an empty directory answered ``"submitter": null`` and one refusal, where the same
command with a login answered two. The one it dropped was ``team_is_ambiguous``, and it did not
gain ``submitter_unknown`` in exchange -- because an unresolved team short-circuits
``_preflight`` before ``run_preflight`` runs, so the refusal that exists to say "I cannot tell
who you are" was never reached. A broken login made the tool quieter and more permissive, which
is backwards, and it is why the Windows configuration-directory bug stayed invisible: the
symptom of not being found is silence.

**THREE PLACES CONSULT THE SUBMITTER AND ALL THREE ARE HELD HERE.** ``_check_identity`` asks
the roster, ``resolve_team`` asks which group the roster puts you in, and ``_check_team`` asks
whether the claimed group is one of them. Each is asserted to name the unknown submitter rather
than to answer as though the question did not arise. Two of the three returned an empty answer
before this module existed, and an empty answer from a check is indistinguishable from a pass.

**The property, rather than the three cases, is the point.**
:func:`test_an_unknown_submitter_is_never_quieter_than_a_known_one` runs one submission twice
over identical inputs and requires the unknown run's refusal codes to cover the known run's
wherever the known run's depend on identity. A case-by-case suite would go on passing after
somebody added a fourth place that reads the submitter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.cli.configuration import load_reviewed_configuration
from edullm_platform.cli.main import EXIT_REFUSED
from edullm_platform.cli.preflight import (
    SUBMITTER_UNKNOWN,
    Refusal,
    SubmissionRequest,
    _check_identity,
    _check_team,
    resolve_team,
    run_preflight,
)
from tests.cli_support import (
    CONFIG_DIR,
    SUBMITTER,
    SUBMITTER_ON_TWO_TEAMS,
    SUBMITTER_TEAM,
    FakeRunner,
    git_answers,
    invoke,
    write_spec,
)

#: A declared group the roster does not put :data:`SUBMITTER` on, which is what makes
#: ``submitter_not_in_claimed_team`` reachable.
NOT_MY_TEAM = "pre-training"

#: Every place in ``preflight`` that consults the login, as a callable taking the reviewed
#: configuration and answering with whatever that place says when there is no login. Written
#: out by name rather than discovered, so that adding a fourth reader of the submitter is an
#: edit here: a site nobody listed is a site nobody holds to anything.
READS_THE_SUBMITTER: dict[str, Callable[[Any], Sequence[Refusal]]] = {
    "_check_identity": lambda config: _check_identity(None, config),
    "_check_team": lambda config: _check_team(_request(), config, None),
    "resolve_team": lambda config: [
        refusal
        for refusal in (resolve_team(config, submitter=None, default=None)[2],)
        if refusal is not None
    ],
}

CHECK = ("check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment")


def checkout(tmp_path: Path) -> tuple[Path, FakeRunner]:
    write_spec(tmp_path)
    return tmp_path, FakeRunner(git_answers(tmp_path))


def refusal_codes(out: str) -> set[str]:
    """Every code the refusal block printed, read off the rendered output.

    Read off the output a person sees rather than out of a ``Preflight``, because the failure
    was that a refusal existed in one arrangement and reached nobody in another. A test
    reaching past the rendering could not have seen it.
    """
    return {
        line.split("refused", 1)[1].split()[0]
        for line in out.splitlines()
        if "refused" in line and line.split("refused", 1)[1].split()
    }


@pytest.fixture(scope="module")
def configuration() -> object:
    return load_reviewed_configuration(CONFIG_DIR)


# --------------------------------------------------------------------------------------
# The property
# --------------------------------------------------------------------------------------


def test_an_unknown_submitter_is_never_quieter_than_a_known_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One submission, run twice, and the unknown run may not lose a refusal.

    Mutation: read the login and, where there is none, carry on with everything that depends
    on it skipped. That is what this did, and it is the shape worth naming rather than the
    single refusal it dropped: a check whose answer gets more permissive as its inputs get
    worse is one nobody can rely on precisely when something is wrong.
    """
    root, runner = checkout(tmp_path)

    _, known, _ = invoke(
        list(CHECK), runner=runner, cwd=root, monkeypatch=monkeypatch,
        login=SUBMITTER_ON_TWO_TEAMS,
    )
    root_two, runner_two = checkout(tmp_path / "second")
    _, unknown, _ = invoke(
        list(CHECK), runner=runner_two, cwd=root_two, monkeypatch=monkeypatch, login=None
    )

    assert len(refusal_codes(unknown)) >= len(refusal_codes(known)), (
        f"a known submitter is refused {sorted(refusal_codes(known))} and an unknown one "
        f"{sorted(refusal_codes(unknown))}, so not being able to tell who you are makes this "
        "check more permissive"
    )


def test_a_check_that_cannot_tell_who_you_are_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal a researcher needs, which is the one that names ``gh auth login``.

    Mutation: resolve the team to nothing and return no refusal with it. ``_preflight`` stops
    on an unresolved team, so a silent one takes every later check down with it -- including
    the refusal that would have explained the silence.
    """
    root, runner = checkout(tmp_path)

    code, out, err = invoke(
        list(CHECK), runner=runner, cwd=root, monkeypatch=monkeypatch, login=None
    )

    assert code == EXIT_REFUSED, out + err
    assert SUBMITTER_UNKNOWN.code in refusal_codes(out)
    assert "gh auth login" in out


def test_naming_a_team_does_not_buy_past_an_unknown_submitter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--team`` answers which group, and it does not answer who.

    Mutation: treat a named team as making identity unnecessary. It is the arrangement that
    reaches furthest into ``run_preflight`` with no login, so it is the one where a missing
    identity check goes unnoticed longest.
    """
    root, runner = checkout(tmp_path)

    code, out, err = invoke(
        [*CHECK, "--team", SUBMITTER_TEAM],
        runner=runner, cwd=root, monkeypatch=monkeypatch, login=None,
    )

    assert code == EXIT_REFUSED, out + err
    assert SUBMITTER_UNKNOWN.code in refusal_codes(out)


def test_the_refusal_is_said_once_rather_than_once_per_check_that_wanted_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three checks want the submitter and a reader needs the sentence once.

    Mutation: let each site append its own copy. The remedy is one command and repeating it
    three times reads as three problems, which is the same argument ``run_preflight`` already
    makes where it drops a denied-outright condition it has already said in words.
    """
    root, runner = checkout(tmp_path)

    _, out, _ = invoke(
        [*CHECK, "--team", SUBMITTER_TEAM],
        runner=runner, cwd=root, monkeypatch=monkeypatch, login=None,
    )

    assert out.count(f"refused  {SUBMITTER_UNKNOWN.code}") == 1


# --------------------------------------------------------------------------------------
# The three places that consult the submitter, each held to naming it
# --------------------------------------------------------------------------------------


def test_resolve_team_refuses_rather_than_answering_nothing(configuration: object) -> None:
    """The drop the research found. Mutation: return the team as ``None`` with no refusal.

    ``None`` and ``None`` are the two halves of one answer here, and the caller reads only
    the first: an unresolved team stops ``_preflight``, so a team resolved to nothing without
    a reason is a check that ends early and reports nothing about why.
    """
    ambiguous, _, refusal = resolve_team(
        configuration, submitter=SUBMITTER_ON_TWO_TEAMS, default=None
    )
    unknown, _, unknown_refusal = resolve_team(configuration, submitter=None, default=None)

    assert ambiguous is None and refusal is not None
    assert unknown is None
    assert unknown_refusal is not None, (
        "the roster cannot name a group for somebody it cannot name, and saying so is the "
        "whole difference between a refusal and a check that stops"
    )
    assert unknown_refusal.code == SUBMITTER_UNKNOWN.code


def test_every_refusal_a_known_submitter_can_earn_is_answered_for_an_unknown_one(
    configuration: object,
) -> None:
    """``run_preflight`` over one request, with the submitter varied and nothing else.

    Mutation: read the submitter and let a check that wanted it fall through. This is the
    aggregate, and it is asserted alongside the per-site case below rather than instead of
    it, for a reason found by mutating: with ``_check_team`` returning nothing this stayed
    green, because ``_check_identity`` had already put ``submitter_unknown`` in the list and
    the counts came out equal. An aggregate cannot see a silent site that another site
    happens to cover.
    """
    known = run_preflight(_request(), configuration=configuration, submitter=SUBMITTER)
    unknown = run_preflight(_request(), configuration=configuration, submitter=None)

    identity_bound = {"submitter_not_in_claimed_team", "submitter_not_on_roster"}
    lost = {refusal.code for refusal in known.refusals} - {
        refusal.code for refusal in unknown.refusals
    }
    assert lost <= identity_bound, f"{sorted(lost)} is refused for a known submitter only"
    assert SUBMITTER_UNKNOWN.code in {refusal.code for refusal in unknown.refusals}
    assert len(unknown.refusals) >= len(known.refusals)


@pytest.mark.parametrize("site", sorted(READS_THE_SUBMITTER))
def test_no_site_that_wanted_the_submitter_answers_with_nothing(
    site: str, configuration: object
) -> None:
    """Each of the three, asked with nobody to ask about, and required to say something.

    Mutation: return the empty list from any one of them. Per site and by name, because the
    property is per site: a check that cannot make its check must not answer as though it
    made it, and whether some other check happens to be loud in the same run is not what
    makes this one correct.

    The private two are reached directly, which a test in this repository does not usually
    do. It is done here because the aggregate above provably cannot see them -- it was green
    over the exact state it exists to refuse -- and a property that only holds by coincidence
    of ordering is one the next edit removes.
    """
    answered = READS_THE_SUBMITTER[site](configuration)
    assert answered, f"{site} answers nothing when it cannot tell who the submitter is"
    assert SUBMITTER_UNKNOWN.code in {refusal.code for refusal in answered}


def _request() -> SubmissionRequest:
    """One submission claiming a group the roster does not put :data:`SUBMITTER` on."""
    return SubmissionRequest(
        repository="OLMo-core",
        commit_sha="a" * 40,
        workload_profile="olmo-core-check",
        compute_profile="cpu-small",
        dataset_release="none",
        team=NOT_MY_TEAM,
        experiment="an-experiment",
        wandb_project=NOT_MY_TEAM,
        command=("python", "-c", "pass"),
    )
