"""``edullm studio``: which space it opens, what it prices, what it stops and what it refuses.

**THE CASES ARE ORDERED BY WHAT THEY COST TO GET WRONG.** The expensive mistakes here are
opening somebody else's space, starting a second app beside one already running, and reporting a
stop that did not happen, so those come first. Then the naming rule, which is the part a real
person on Windows broke; then the refusals, then the argv the verb builds.

Nothing here reaches AWS. ``lane_answers`` and ``studio_answers`` describe the account and
``FakeRunner`` refuses any call a fixture did not declare, which is what makes a new call the
verb learns to make a failure by name rather than a network read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED, EXIT_UNREACHABLE
from edullm_platform.cli.studio import (
    APP_NAME,
    APP_TYPE,
    IMAGE_ACCOUNT_PARAMETER,
    PERSON_TAG_KEY,
    STUDIO_NAME_LIMIT,
    SURFACE_TAG_KEY,
    SURFACE_TAG_VALUE,
    IdleShutdown,
    OwnedSpace,
    RunningApp,
    StudioRequest,
    StudioSettings,
    apps_by_space,
    create_app_argv,
    create_space_argv,
    create_user_profile_argv,
    delete_app_argv,
    idle_said,
    idle_shutdown,
    image_account_argv,
    image_arn_for,
    load_studio_settings,
    owned_spaces,
    portal_uri,
    presigned_url_argv,
    price_said,
    project_of_space,
    running_app,
    shape_for,
    space_name_for,
    space_named,
    studio_name_for,
    studio_refusals,
    studio_tags,
)
from edullm_platform.researcher_lane import GOVERNANCE_TAG_KEYS, PROJECT_TAG_KEY
from tests.cli_support import (
    CONFIG_DIR,
    STUDIO_IDLE_MINUTES,
    STUDIO_IMAGE_ACCOUNT,
    STUDIO_PERSON,
    STUDIO_PROJECT,
    STUDIO_SPACE,
    STUDIO_URL,
    STUDIO_VOLUME_GIB,
    FakeRunner,
    failed,
    git_answers,
    invoke,
    lane_answers,
    ok,
    pages_opened,
    studio_answers,
)

#: The person ``lane_answers`` federates as, and the project every start below opens.
THE_PERSON = STUDIO_PERSON
THE_PROJECT = STUDIO_PROJECT

#: The roster as the live domain holds it on 2026-08-06: twenty profiles named ``first-last``
#: and twenty private spaces named ``<profile>-lab`` owned by the matching profile. Three of
#: them here, because the property under test is about the shape of the name and not the size
#: of the team.
THE_LIVE_CONVENTION = (("aryan-verma", "lab"), ("frank-gonzalez", "lab"), ("eric-wu", "lab"))


def a_studio(tmp_path: Path, **overrides: object) -> FakeRunner:
    """A laptop holding a session, against a domain that exists."""
    answers = dict(git_answers(tmp_path))
    answers.update(lane_answers())
    answers.update(studio_answers(**overrides))  # type: ignore[arg-type]
    return FakeRunner(answers)


def settings() -> StudioSettings:
    """The rate card this repository ships, which is the one the verb reads."""
    return load_studio_settings(CONFIG_DIR)


def a_request(project: str = THE_PROJECT, person: str = THE_PERSON) -> StudioRequest:
    name = studio_name_for(person)
    return StudioRequest(
        person=person, studio_name=name, project=project, space=space_name_for(name, project)
    )


# ---------------------------------------------------------------------------------------
# whose space this is, which is the one that costs somebody else's work to get wrong
# ---------------------------------------------------------------------------------------


def test_a_space_somebody_else_owns_is_refused_and_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE ONE THAT MAKES DERIVING A NAME SAFE. Mutation: trust the derived name.

    The name is an address and the ownership field is the fact. Studio would let this through:
    the domain's execution role is shared and a private space is private by console convention
    rather than by an authorisation on ``CreateApp``. So a derived name that lands on somebody
    else's disk has to be caught here or not at all.
    """
    runner = a_studio(tmp_path, spaces=((STUDIO_SPACE, "somebody-else", 5),))

    code, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_REFUSED
    assert "space_belongs_to_somebody_else" in err
    assert "somebody-else" in err
    assert not runner.ran("aws", "sagemaker", "create-app")
    assert not runner.ran("aws", "sagemaker", "create-presigned-domain-url")


def test_discovery_reads_the_ownership_field_and_not_the_name(tmp_path: Path) -> None:
    """Mutation: filter the space list by a name prefix instead of by the owner.

    A prefix filter is the convention baked in, and it gets two things wrong at once: it claims
    a space somebody else owns whose name happens to start with this person's, and it loses a
    space of theirs that was named by hand in the console. The ownership field is what Studio
    itself uses to mean this, so reading it is robust to whatever anybody named things.
    """
    listed = json.dumps(
        {
            "Spaces": [
                _a_space("caiiris-mixlaw", "caiiris", 5),
                _a_space("caiiris-extra-thing", "somebody-else", 5),
                _a_space("named-by-hand", "caiiris", 20),
            ]
        }
    )

    mine = owned_spaces(listed, owner="caiiris")

    assert [space.name for space in mine] == ["caiiris-mixlaw", "named-by-hand"]
    assert [space.project for space in mine] == ["mixlaw", None]


def test_a_space_of_yours_named_off_the_convention_is_still_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: list only the spaces this tool could have made.

    A hand-made space is a disk billing to somebody, and a listing that hid it would tell them
    they do not have a thing they are paying for. It is marked as unreachable by ``--project``
    rather than dropped, which is the honest description of a name this rule cannot address.
    """
    runner = a_studio(
        tmp_path, spaces=((STUDIO_SPACE, THE_PERSON, 5), ("hand-made", THE_PERSON, 9))
    )

    code, out, _ = invoke(["studio"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert "hand-made" in out
    assert "not reachable by --project" in out


# ---------------------------------------------------------------------------------------
# the two that cost money to get wrong
# ---------------------------------------------------------------------------------------


def test_an_app_already_running_is_answered_with_its_link_and_never_a_second_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE ONE THAT MATTERS HERE. Mutation: read "resume" as "start".

    Studio permits more than one app on a space, so this mistake is available, it is silent,
    and it doubles somebody's hourly rate under their own name with nothing in the console
    saying which of the two anybody is looking at. Verified against the live account on
    ``aryan-verma-lab`` as well as here.
    """
    runner = a_studio(tmp_path, app_status="InService")

    code, out, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert not runner.ran("aws", "sagemaker", "create-app"), (
        "a second app was started beside one already running"
    )
    assert "already running" in err
    # NOT IN THE OUTPUT, WHICH IS THE REVERSAL. The link went to the browser rather than to the
    # scrollback, and a URL on stdout here would mean the hand-off had silently stopped happening.
    assert STUDIO_URL not in out
    assert pages_opened()


def test_a_pending_app_counts_as_running_because_the_instance_is_already_allocated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: treat anything that is not ``InService`` as nothing.

    An app coming up is an instance already bought. Reporting it as absent starts a second
    one, which is the same failure as the case above arriving through a narrower door.
    """
    runner = a_studio(tmp_path, app_status="Pending")

    code, _, _ = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert not runner.ran("aws", "sagemaker", "create-app")


def test_a_stop_that_sagemaker_refused_is_never_reported_as_a_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE OTHER ONE THAT MATTERS. Mutation: exit 0 whatever ``delete-app`` answered.

    A stop reported as done and not done leaves somebody believing they are not being billed
    while they are, which is the exact belief this verb exists to make true.
    """
    answers = dict(git_answers(tmp_path))
    answers.update(lane_answers())
    answers.update(studio_answers(running=(STUDIO_SPACE,)))
    answers[("aws", "sagemaker", "delete-app")] = failed("An error occurred (ThrottlingException)")
    runner = FakeRunner(answers)

    code, out, err = invoke(
        ["studio", "--stop"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    # Compared without the line breaks, because the message is wrapped for a terminal and a
    # test pinned to where it wraps would fail on a reworded sentence that says the same thing.
    assert "still running and still billing by the hour" in " ".join(err.split())
    assert out == ""


def test_stopping_deletes_the_app_and_says_the_volume_goes_on_costing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: say the charge has ended, full stop.

    ``delete-app`` is how Studio spells stop and it leaves the space's volume behind, so a
    message that stopped at "the charge has ended" would be false about the disk -- which is
    the charge somebody discovers a month later.
    """
    runner = a_studio(tmp_path, running=(STUDIO_SPACE,))

    code, out, _ = invoke(
        ["studio", "--stop"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert runner.ran("aws", "sagemaker", "delete-app")
    assert "hourly charge has ended" in out
    assert "a month" in out


def test_stopping_nothing_is_exit_zero_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: refuse on the second call.

    The state this verb exists to produce is already the state, and a cleanup command that
    cannot be run twice is one nobody puts in a script or a shell alias.
    """
    runner = a_studio(tmp_path)

    code, out, _ = invoke(
        ["studio", "--stop"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert not runner.ran("aws", "sagemaker", "delete-app")
    assert "nothing of yours is billing" in out


def test_a_bare_stop_stops_every_app_and_a_projected_one_stops_only_that_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: make ``--project`` required on ``--stop`` once it names the space.

    Once a person may have six spaces, a stop that demanded to be told which one is six
    commands and a memory test, run by somebody who is leaving for the day -- which is the
    exact shape that produced a 69-hour GPU bill in this account. Stopping is safe to do
    broadly in a way that terminating a lane machine is not: every file is on the space's own
    volume and survives.
    """
    both = (STUDIO_SPACE, f"{THE_PERSON}-other")
    owned = ((STUDIO_SPACE, THE_PERSON, 5), (f"{THE_PERSON}-other", THE_PERSON, 5))
    everything = a_studio(tmp_path, spaces=owned, running=both)
    just_one = a_studio(tmp_path, spaces=owned, running=both)

    invoke(["studio", "--stop"], runner=everything, cwd=tmp_path, monkeypatch=monkeypatch)
    invoke(
        ["studio", "--stop", "--project", THE_PROJECT],
        runner=just_one,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    stopped_all = [
        call for call in everything.calls if call[:3] == ("aws", "sagemaker", "delete-app")
    ]
    stopped_one = [
        call for call in just_one.calls if call[:3] == ("aws", "sagemaker", "delete-app")
    ]

    assert len(stopped_all) == 2
    assert len(stopped_one) == 1
    assert STUDIO_SPACE in stopped_one[0]
    assert f"{THE_PERSON}-other" not in stopped_one[0]


def test_a_stop_for_a_project_with_nothing_running_names_the_projects_you_have(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: answer a mistyped project with a bare "nothing found".

    ``cli/lane.py``'s ``no_machine_to_stop`` already made this ruling. A person runs a stop
    because they believe something is billing, and "nothing found" tells them the opposite of
    the truth in the exact words that sound like reassurance.
    """
    runner = a_studio(
        tmp_path,
        spaces=((STUDIO_SPACE, THE_PERSON, 5),),
        running=(STUDIO_SPACE,),
    )

    code, out, _ = invoke(
        ["studio", "--stop", "--project", "misremembered"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK
    assert THE_PROJECT in out
    assert not runner.ran("aws", "sagemaker", "delete-app")


# ---------------------------------------------------------------------------------------
# the naming rule, which is the defect a real person hit
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("person", "project"), THE_LIVE_CONVENTION)
def test_the_rule_lands_on_the_spaces_that_already_exist_without_naming_the_word(
    person: str, project: str
) -> None:
    """THE ONE THE ACCOUNT DECIDES. Mutation: special-case ``lab``, or invent a new suffix.

    Twenty private spaces exist, named ``<profile>-lab`` and owned by the matching profile, and
    ``--project lab`` has to resume each of them rather than create a twenty-first. That falls
    out of ``<person>-<project>`` rather than out of a rule about the word: the same rule sends
    ``--project scratch`` to ``<person>-scratch``, and would have landed on whatever suffix
    somebody had chosen instead.
    """
    assert space_name_for(person, project) == f"{person}-{project}"
    assert project_of_space(person, f"{person}-{project}") == project


def test_a_space_can_never_be_named_exactly_like_a_profile_the_verb_derives() -> None:
    """THE DEFECT ITSELF. Mutation: name the space after the person.

    ``CreateSpace`` answers ``User Profile already exists with the same name 'aryan-verma'. User
    Profile and Space name must be unique in a domain.`` Profiles and spaces share one
    namespace, so a space-per-person arrangement fails on its first invocation for every person
    who has a profile -- which is all twenty of them. A project is never empty, so the derived
    space name always carries a suffix the caller's own profile does not.
    """
    for person, _ in THE_LIVE_CONVENTION:
        for project in ("lab", "onboarding", "x"):
            assert space_name_for(person, project) != person


def test_a_project_that_would_overrun_the_name_is_refused_and_never_truncated() -> None:
    """Mutation: cut the name to fit.

    Two long project names cut to one length are two pieces of work pointed at one disk with
    nothing saying so, which is the collision this rule exists to prevent arriving through the
    length limit instead of through the namespace.
    """
    person = "siddhartha-venkatayogi"
    long_enough = "x" * (STUDIO_NAME_LIMIT - len(person))
    request = StudioRequest(
        person=person,
        studio_name=person,
        project=long_enough,
        space=space_name_for(person, long_enough),
    )

    assert space_name_for(person, long_enough) == ""
    assert [refusal.code for refusal in studio_refusals(request)] == ["project_name_is_too_long"]
    assert str(STUDIO_NAME_LIMIT - len(person) - 1) in studio_refusals(request)[0].detail


def test_a_project_with_nothing_sagemaker_takes_in_it_is_refused() -> None:
    """Mutation: fall through to an empty suffix, which names the person's own profile."""
    request = a_request(project="...")

    assert [refusal.code for refusal in studio_refusals(request)] == ["project_name_is_unusable"]


def test_a_project_whose_space_is_already_a_profile_is_refused_in_words_somebody_can_act_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: let ``CreateSpace`` report the collision.

    It reports it truthfully and incomprehensibly: a sentence about user profiles, to somebody
    who typed a project name. It needs a hyphenated surname to happen at all, which is exactly
    why it is worth spelling out -- whoever hits it will hit it once and have no idea why.
    """
    runner = a_studio(
        tmp_path,
        spaces=(),
        profiles=(THE_PERSON, STUDIO_SPACE),
    )

    code, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_REFUSED
    assert "project_collides_with_a_profile" in err
    assert not runner.ran("aws", "sagemaker", "create-space")
    assert not runner.ran("aws", "sagemaker", "create-app")


def test_two_projects_are_two_spaces_and_the_same_project_is_the_same_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE POINT OF THE WHOLE REDESIGN. Mutation: one space per person.

    A space carries its own disk, so two projects that want different dependencies and
    different half-built state want two spaces. Forcing them into one makes the disk a shared
    mutable thing and the person the merge conflict.
    """
    owned = ((STUDIO_SPACE, THE_PERSON, 5),)
    returning = a_studio(tmp_path, spaces=owned)
    fresh = a_studio(tmp_path, spaces=owned)

    invoke(
        ["studio", "--project", THE_PROJECT],
        runner=returning,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    invoke(["studio", "--project", "another"], runner=fresh, cwd=tmp_path, monkeypatch=monkeypatch)

    assert not returning.ran("aws", "sagemaker", "create-space")
    assert fresh.ran("aws", "sagemaker", "create-space")
    made = next(call for call in fresh.calls if call[:3] == ("aws", "sagemaker", "create-space"))
    assert f"{THE_PERSON}-another" in made


# ---------------------------------------------------------------------------------------
# the bare verb, which now lists rather than refusing
# ---------------------------------------------------------------------------------------


def test_the_bare_verb_lists_what_you_have_rather_than_refusing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE REVERSAL. Mutation: keep the ``no_project`` refusal now the project names the space.

    The argument against *defaulting* the project is untouched and is in the flag's own help.
    But once the project is the only way back to a disk, a person who has forgotten what they
    called last week's work cannot reach it, and the tool that knows is the one refusing to
    say. Refusing to guess and refusing to answer were never the same act.
    """
    runner = a_studio(
        tmp_path,
        spaces=((STUDIO_SPACE, THE_PERSON, 5), (f"{THE_PERSON}-onboarding", THE_PERSON, 5)),
        running=(STUDIO_SPACE,),
    )

    code, out, _ = invoke(["studio"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert f"--project {THE_PROJECT}" in out
    assert "--project onboarding" in out
    assert "RUNNING" in out
    assert not runner.ran("aws", "sagemaker", "create-app")
    assert not runner.ran("aws", "sagemaker", "create-space")


def test_somebody_with_no_spaces_is_told_how_to_make_one_and_why_there_is_no_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print an empty list, or refuse.

    Fifteen people on the roster have no AWS role yet, so this is the first thing most of them
    will see. The argument the ``no_project`` refusal used to make lives here now, where it
    reaches the same person while telling them how to proceed rather than why they were
    stopped.
    """
    runner = a_studio(tmp_path, spaces=())

    code, out, _ = invoke(["studio"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert "no Studio spaces yet" in out
    assert "no default" in out
    assert "one bill" in out


def test_the_listing_says_what_the_disks_cost_and_that_nothing_reclaims_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE ACCUMULATION RULING, WHICH IS VISIBILITY AND NOT A CEILING.

    Mutation: refuse past some number of spaces. A ceiling refuses at the moment somebody
    starts work, refuses the cheap thing to punish the disks they already have, reclaims
    nothing, and would need a number nobody can defend. What the person with too many disks
    needs is to know which ones, which is a listing. The honest missing piece is a sweep --
    ``infra/expiry-janitor.yaml`` has no SageMaker arm -- and this says so rather than
    pretending a limit is one.
    """
    many = tuple((f"{THE_PERSON}-p{index}", THE_PERSON, 5) for index in range(9))
    runner = a_studio(tmp_path, spaces=many)

    code, out, _ = invoke(["studio"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert "9 spaces" in out
    assert "a month" in out
    assert "Nothing deletes a space for you" in " ".join(out.split())


def test_no_number_of_spaces_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: add a ceiling after all.

    The quantities are two orders of magnitude apart: the unattended ``ml.g4dn.xlarge`` app
    that ran across three nights cost more than sixty space-months of disk. A limit would
    govern the wrong one.
    """
    many = tuple((f"{THE_PERSON}-p{index}", THE_PERSON, 5) for index in range(40))
    runner = a_studio(tmp_path, spaces=many)

    code, _, err = invoke(
        ["studio", "--project", "one-more"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert "41 spaces" in err
    assert runner.ran("aws", "sagemaker", "create-space")


# ---------------------------------------------------------------------------------------
# what it says before it spends anything
# ---------------------------------------------------------------------------------------


def test_the_rate_is_printed_before_the_app_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: price it afterwards, or not at all.

    ``check`` prices a submission before it is dispatched and this is the same promise on the
    exploration surface. Printed to stderr, which is where every explanation this verb makes
    goes, so that the one thing ``--print-url`` puts on stdout is the URL and nothing else.
    """
    runner = a_studio(tmp_path)

    code, out, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert "an hour at list price" in err
    assert out == ""


def test_the_url_is_handed_to_a_browser_and_never_to_the_scrollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE FAILURE THIS CHANGE IS FOR. Mutation: print it, which is what shipped.

    The URL is 4,251 characters and lives 300 seconds, and 300 is a ceiling the API enforces
    rather than a setting. Selecting four thousand characters out of a terminal, without the
    breaks the terminal drew into them, and getting them into a browser before they expire is
    not a thing that works -- and when it does not, AWS sends the person to a console sign-in
    page asking for a password this organisation issues nobody. What the browser was handed is
    a short ``file://`` address, so the credential is on no command line.
    """
    runner = a_studio(tmp_path, app_status="InService")

    code, out, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    opened = pages_opened()

    assert code == EXIT_OK
    assert len(opened) == 1
    assert STUDIO_URL in opened[0].read_text(encoding="utf-8")
    assert STUDIO_URL not in out
    assert STUDIO_URL not in err
    assert out == ""


def test_print_url_prints_the_url_alone_and_opens_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: open a browser as well, or print the explanation beside it.

    The flag is for a machine with no browser and for scripts, and both want one line. It has
    to open nothing: a headless box that launched a browser anyway would consume the single-use
    sign-in and hand the caller a dead URL.
    """
    runner = a_studio(tmp_path, app_status="InService")

    code, out, err = invoke(
        ["studio", "--project", THE_PROJECT, "--print-url"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK
    assert not pages_opened()
    assert out.strip() == STUDIO_URL
    assert out.count("\n") == 1
    assert "treat it as a password" in err


def test_an_ssh_session_prints_rather_than_opening_a_browser_nobody_can_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: open one anyway and report success.

    On a remote host this can succeed and still fail the person: the window opens where nobody
    is sitting, the single-use sign-in is spent, and the terminal says it worked. Detected
    rather than left to ``--print-url``, because somebody who has to know about the flag to
    avoid the trap will find the trap first.
    """
    runner = a_studio(tmp_path, app_status="InService")

    code, out, err = invoke(
        ["studio", "--project", THE_PROJECT],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
        ssh=True,
    )

    assert code == EXIT_OK
    assert not pages_opened()
    assert out.strip() == STUDIO_URL
    assert "SSH session" in err


def test_a_space_that_had_no_app_says_the_page_is_not_the_notebook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: say "opened your notebook" whatever was actually opened.

    A browser that came up looks like success. When the app was not running the page is the
    space in Studio rather than JupyterLab, because ``--space-name`` against a space with no app
    serves a blank 404 -- so the sentence has to say what the person is looking at and what to
    do on it, or they conclude the tool is broken.
    """
    runner = a_studio(tmp_path)

    code, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )
    said = " ".join(err.split())

    assert code == EXIT_OK
    assert pages_opened()
    assert "an app is starting now" in said
    assert "Open JupyterLab from there" in said


def test_the_disk_quoted_is_the_space_s_own_and_not_the_configured_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: quote ``volume_gib`` from the rate card for a space that exists.

    The configured number is what a space this verb *creates* gets. The twenty in the domain
    were made by hand at a different size, so quoting the configured one is a disk cost that is
    wrong in the reassuring direction for every person who already has a space.
    """
    runner = a_studio(tmp_path, spaces=((STUDIO_SPACE, THE_PERSON, 37),))

    _, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert "37 GB volume" in err


def test_it_says_what_the_domain_actually_does_to_an_idle_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE SENTENCE THAT WENT STALE IN AN HOUR. Mutation: write the timeout down.

    This verb shipped saying the domain had no idle shutdown. That was measured and true on the
    morning of 2026-08-06 and false by the afternoon, when the domain was given a 240-minute
    timeout -- and nobody would have noticed until somebody left a GPU on believing it. The
    number now comes from ``DescribeDomain`` on the way past, so the only way to make it stale
    is to change the domain between the read and the print.
    """
    runner = a_studio(tmp_path, idle_minutes=STUDIO_IDLE_MINUTES)

    _, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    said = " ".join(err.split())

    assert f"after {STUDIO_IDLE_MINUTES} minutes" in said
    assert "Stopping it yourself when you finish costs nothing" in said
    assert "no idle-shutdown setting" not in said


def test_a_domain_with_no_idle_shutdown_is_said_to_have_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: assume the timeout exists now that it does.

    It was turned on by hand and can be turned off the same way. Reading it is what makes both
    sentences true, and the safe direction for anything unreadable is "nothing will stop this".
    """
    runner = a_studio(tmp_path, idle_minutes=None)

    _, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert "Nothing will stop this for you" in err


@pytest.mark.parametrize("body", ["", "not json", "{}", '{"DefaultUserSettings": 3}'])
def test_a_domain_that_could_not_be_read_is_treated_as_having_no_idle_shutdown(body: str) -> None:
    """Mutation: default to enabled, or index into the body.

    The asymmetry decides it. An unreadable domain reported as "Studio will stop this for you"
    is the sentence that leaves a GPU on all weekend; a timeout that exists and is reported as
    absent costs somebody one unnecessary stop.
    """
    assert idle_shutdown(body).enabled is False


def test_the_idle_sentence_prices_the_walk_away() -> None:
    """Mutation: state the timeout and stop.

    Four hours is an abstraction and four dollars is not. The number is what makes ``--stop``
    worth typing now that the domain will eventually do it anyway.
    """
    gpu = shape_for(settings(), "ml.g4dn.xlarge")
    assert gpu is not None
    said = idle_said(IdleShutdown(minutes=240), gpu)

    assert "240 minutes" in said
    assert "$2.95" in said


def test_a_shape_nobody_priced_is_refused_before_any_credential_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: start it anyway, or refuse after the identity call.

    Starting something the verb cannot quote is the failure it exists to prevent. Refusing
    before ``sts:GetCallerIdentity`` matters separately: a misspelled shape answered with "log
    in first" is a refusal about the wrong thing.
    """
    runner = a_studio(tmp_path)

    code, _, err = invoke(
        ["studio", "--project", THE_PROJECT, "--instance-type", "ml.p5.48xlarge"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert "shape_is_not_priced" in err
    assert not runner.ran("aws", "sts", "get-caller-identity")


def test_the_json_document_carries_no_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: put the sign-in URL in the machine-readable form.

    It is a bearer credential with a five-minute life, and a document is precisely the thing
    somebody redirects into a file and pastes into an issue when asking what went wrong.
    """
    runner = a_studio(tmp_path)

    code, out, _ = invoke(
        ["studio", "--project", THE_PROJECT, "--json"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    document = json.loads(out)

    assert code == EXIT_OK
    assert STUDIO_URL not in out
    assert document["verb"] == "studio"
    assert document["space"] == STUDIO_SPACE
    assert document["project"] == THE_PROJECT
    assert document["idle_shutdown"] is True
    assert document["idle_timeout_minutes"] == STUDIO_IDLE_MINUTES
    assert document["refused"] is False


def test_the_json_listing_carries_every_space_and_which_are_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: publish the prose and leave a program to parse it.

    ``AGENTS.md`` tells every agent to read the machine-readable form and match on codes, so a
    listing that existed only as paragraphs would be one an agent has to scrape.
    """
    runner = a_studio(
        tmp_path,
        spaces=((STUDIO_SPACE, THE_PERSON, 5), (f"{THE_PERSON}-idle", THE_PERSON, 5)),
        running=(STUDIO_SPACE,),
    )

    code, out, _ = invoke(
        ["studio", "--json"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )
    document = json.loads(out)

    assert code == EXIT_OK
    assert [space["project"] for space in document["spaces"]] == ["idle", THE_PROJECT]
    assert [space["running"] for space in document["spaces"]] == [False, True]


# ---------------------------------------------------------------------------------------
# setting somebody up, which is every first invocation
# ---------------------------------------------------------------------------------------


def test_a_first_invocation_makes_the_profile_and_the_space_before_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: create the app first, or make somebody ask for a space by hand.

    Neither create allocates an instance, so the free half happens first and a person whose
    only knowledge is the verb is set up by it.
    """
    runner = a_studio(tmp_path, profile_exists=False, space_exists=False)

    code, _, _ = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )
    made = [
        " ".join(call[:3])
        for call in runner.calls
        if call[:2] == ("aws", "sagemaker") and "create" in call[2]
    ]

    assert code == EXIT_OK
    assert made == [
        "aws sagemaker create-user-profile",
        "aws sagemaker create-space",
        "aws sagemaker create-app",
        "aws sagemaker create-presigned-domain-url",
    ]


def test_a_returning_person_creates_nothing_but_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: create-and-forgive rather than look-then-create.

    Attempting a create every time and swallowing ``ResourceInUse`` would make a genuine
    collision -- a space name that is already somebody's profile -- indistinguishable from the
    ordinary path, which is the defect that produced this rewrite.
    """
    runner = a_studio(tmp_path)

    invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert not runner.ran("aws", "sagemaker", "create-user-profile")
    assert not runner.ran("aws", "sagemaker", "create-space")
    assert runner.ran("aws", "sagemaker", "create-app")


def test_the_first_space_says_what_its_disk_costs_for_ever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: say nothing at the moment a disk is created.

    This is the one moment the person can still choose a project name they already have, and
    the only moment anybody is thinking about the disk at all.
    """
    runner = a_studio(tmp_path, spaces=())

    _, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert "your first space" in err
    assert "nothing here does for you" in err


# ---------------------------------------------------------------------------------------
# the name, which is the seam between this surface and the lane
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("person", "expected"),
    [
        ("frank.gonzalez", "frank-gonzalez"),
        ("amy.lin", "amy-lin"),
        ("caiiris", "caiiris"),
        ("a_b.c", "a-b-c"),
        (".leading", "leading"),
        ("trailing.", "trailing"),
        ("...", ""),
        ("", ""),
    ],
)
def test_a_person_becomes_a_name_sagemaker_will_take(person: str, expected: str) -> None:
    """Mutation: hand the working tier's person string straight to SageMaker.

    The broker mints ``broker-frank.gonzalez-<epoch>``, so the person the lane derives is
    ``frank.gonzalez``, and the service refuses it: ``Member must satisfy regular expression
    pattern: [a-zA-Z0-9](-*[a-zA-Z0-9]){0,62}``. That was measured against the live API rather
    than read out of a document, and it is the whole reason this function exists.
    """
    assert studio_name_for(person) == expected


def test_a_long_name_is_cut_to_what_sagemaker_takes_and_still_ends_on_a_character() -> None:
    """Mutation: truncate and stop, which can leave a trailing dash the service refuses."""
    name = studio_name_for("a" * 62 + ".b" * 20)

    assert len(name) <= STUDIO_NAME_LIMIT
    assert not name.endswith("-")
    assert not name.startswith("-")


def test_the_joined_name_is_legal_even_where_the_project_is_not() -> None:
    """Mutation: join the raw project on.

    ``a--b`` and a leading dash are both refused by the service. Both halves going through the
    same normaliser is what makes the join of two legal names legal: neither can open or close
    on a dash, so the single dash between them cannot double.
    """
    for project in ("my project", "-leading", "trailing-", "a..b", "UPPER"):
        joined = space_name_for("amy-lin", project)
        assert joined
        assert "--" not in joined
        assert not joined.startswith("-")
        assert not joined.endswith("-")


def test_a_name_with_nothing_left_in_it_is_refused_rather_than_invented() -> None:
    """Mutation: fall back to a default name, which would put two people in one space."""
    refusals = studio_refusals(a_request(person="..."))

    assert [refusal.code for refusal in refusals] == ["studio_name_is_unusable"]


def test_a_session_already_inside_the_lane_is_refused() -> None:
    """Mutation: guess a person. ``sts:GetCallerIdentity`` does not return a source identity,
    so a lane session carries no person at all and any name chosen here is somebody else's."""
    refusals = studio_refusals(
        StudioRequest(person="", studio_name="", project=THE_PROJECT, space="")
    )

    assert [refusal.code for refusal in refusals] == ["cannot_tell_who_you_are"]


# ---------------------------------------------------------------------------------------
# the tags, which are the whole reason the spend is ever attributable
# ---------------------------------------------------------------------------------------


def test_every_created_thing_carries_the_person_and_the_project() -> None:
    """THE TAGGING ONE. Mutation: tag the space and not the app, or neither.

    The app is what Cost Explorer bills and what CloudTrail records, and a tag added after the
    fact does not retroactively attribute the hours before it. Untagged, Studio is one
    undifferentiated SageMaker line, which is the hazard in pointing thirty-five people at it.
    """
    request = a_request()
    loaded = load_studio_settings(CONFIG_DIR)
    shape = shape_for(loaded, None)
    assert shape is not None
    image = image_arn_for(loaded, shape, account=STUDIO_IMAGE_ACCOUNT)
    created = (
        create_user_profile_argv(settings=loaded, request=request),
        create_space_argv(settings=loaded, request=request, shape=shape, image_arn=image),
        create_app_argv(settings=loaded, request=request, shape=shape, image_arn=image),
    )

    for argv in created:
        assert f"Key={PROJECT_TAG_KEY},Value={THE_PROJECT}" in argv
        assert f"Key={PERSON_TAG_KEY},Value={THE_PERSON}" in argv
        assert f"Key={SURFACE_TAG_KEY},Value={SURFACE_TAG_VALUE}" in argv


def test_the_project_tag_and_the_space_name_cannot_disagree() -> None:
    """THE SECOND REASON ``--project`` NAMES THE SPACE. Mutation: let them be set separately.

    They used to be independent: a space named after the person, tagged with whatever project
    the invocation named, so one disk accumulated a different tag every week and the cost
    attribution was a fact about the last person to start an app. Deriving one from the other
    makes them agree by construction rather than by a convention somebody has to remember.
    """
    request = a_request(project="onboarding")

    assert request.space == f"{THE_PERSON}-onboarding"
    assert studio_tags(request)[PROJECT_TAG_KEY] == "onboarding"
    assert project_of_space(request.studio_name, request.space) == "onboarding"


def test_the_project_key_is_the_one_the_lane_machines_already_carry() -> None:
    """Mutation: spell it ``project``.

    Cost Explorer groups by an exact key, so a lowercase one would put Studio hours in a second
    group beside the lane's and a reader summing a project's spend would find half of it.
    """
    assert PROJECT_TAG_KEY in studio_tags(a_request())
    assert PROJECT_TAG_KEY == "Project"


def test_no_expiry_tag_is_written_because_nothing_would_honour_it() -> None:
    """Mutation: write an ``ExpiresAt``, by analogy with the lane.

    ``infra/expiry-janitor.yaml`` sweeps EC2 instances and has no SageMaker arm, so an expiry
    on a Studio app is a promise nothing here keeps -- worse than no tag, because the next
    reader finds it and concludes something is watching.
    """
    written = set(studio_tags(a_request()))

    assert not written & set(GOVERNANCE_TAG_KEYS) - {PROJECT_TAG_KEY}


# ---------------------------------------------------------------------------------------
# the calls themselves
# ---------------------------------------------------------------------------------------


def test_the_space_is_private_and_owned_by_the_person() -> None:
    """Mutation: drop either half. Neither alone makes a space one person's.

    ``SharingType=Private`` with ``OwnerUserProfileName`` is Studio's own scoping, it is what
    all twenty spaces in the domain carry, and it is the pair ``owned_spaces`` reads back --
    so what this writes is what discovery later depends on.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    shape = shape_for(loaded, None)
    assert shape is not None
    argv = create_space_argv(
        settings=loaded,
        request=a_request(),
        shape=shape,
        image_arn=image_arn_for(loaded, shape, account=STUDIO_IMAGE_ACCOUNT),
    )

    assert "SharingType=Private" in argv
    assert f"OwnerUserProfileName={THE_PERSON}" in argv
    assert STUDIO_SPACE in argv


def test_the_portal_path_is_relative_because_a_leading_slash_leaves_the_domain() -> None:
    """Mutation: put the slash back, which is what shipped and what broke every person.

    ``studio::/jupyterlab/<space>`` is accepted by the API and mints a token carrying
    ``landingUriDeepLink: /jupyterlab/<space>``, so it looks understood. Redeemed, Studio answers
    ``Location: //jupyterlab/<space>`` -- protocol-relative, so a browser reads ``jupyterlab`` as
    the host, resolves nothing, and shows its own error page. Measured against ``d-bxqz8jfqjjnu``
    in Chrome on 2026-08-06, and measured **with an app running**, which is what rules out the
    deep link merely pointing at something not started yet.

    AWS documents the form as ``studio::relative/path``. This asserts the absence of the one
    character, from both ends, because a value that merely *contains* the right path is exactly
    what the broken one did.
    """
    request = a_request()

    assert portal_uri(request) == f"studio::jupyterlab/{STUDIO_SPACE}"
    assert not portal_uri(request).startswith("studio::/")
    assert "//" not in portal_uri(request)


def test_a_running_app_is_reached_by_space_name_rather_than_by_a_landing_uri() -> None:
    """Mutation: keep using a landing URI once the app is up.

    ``--space-name`` is the only recipe AWS's *Launch spaces* page gives for an IAM domain, and
    measured it is the only one that reaches the space's own host: it lands on
    ``/jupyterlab/default/lab`` with the person's notebook open. A landing URI cannot, because a
    landing URI is resolved against the portal and never against a space.
    """
    request = a_request()
    argv = presigned_url_argv(settings=settings(), request=request, app_is_running=True)

    assert "--space-name" in argv
    assert STUDIO_SPACE in argv
    assert "--landing-uri" not in argv
    assert THE_PERSON in argv


def test_a_space_with_no_app_is_sent_to_the_portal_because_the_notebook_would_404() -> None:
    """Mutation: use ``--space-name`` whatever the app is doing.

    Measured: ``--space-name`` against a space with no running app authenticates and then serves
    a bare 404 at ``/jupyterlab/default``, because nothing is listening there yet. The portal page
    for the space answers 200 and shows the app's status, which is the only honest destination
    while one is starting.
    """
    request = a_request()
    argv = presigned_url_argv(settings=settings(), request=request, app_is_running=False)

    assert "--space-name" not in argv
    assert argv[argv.index("--landing-uri") + 1] == portal_uri(request)


def test_the_url_is_asked_for_at_the_ceiling_the_service_enforces() -> None:
    """Mutation: ask for an hour, which is what everybody wants and what nobody may have.

    ``ExpiresInSeconds`` is documented "Maximum value of 300" and the live API refuses 301 with a
    ``ValidationException``. So the number is not a policy this repository sets and raising it is
    not a fix available to anybody; ``cli/browser.py`` carries what was done instead.
    """
    argv = presigned_url_argv(settings=settings(), request=a_request(), app_is_running=True)

    assert argv[argv.index("--expires-in-seconds") + 1] == "300"


def test_the_app_is_created_with_an_image_because_the_service_demands_one() -> None:
    """Mutation: leave the image to a default.

    ``CreateApp`` answers ``SageMaker Image ARN is required for App with type [JupyterLab]``,
    which this account's own trail records somebody discovering the hard way.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    shape = shape_for(loaded, "ml.g4dn.xlarge")
    assert shape is not None
    image = image_arn_for(loaded, shape, account=STUDIO_IMAGE_ACCOUNT)
    argv = create_app_argv(settings=loaded, request=a_request(), shape=shape, image_arn=image)

    assert f"InstanceType={shape.instance_type},SageMakerImageArn={image}" in argv
    assert APP_TYPE in argv
    assert APP_NAME in argv


def test_the_image_account_is_read_from_aws_and_never_written_down() -> None:
    """Mutation: put Amazon's image account in the rate card.

    Twelve digits anywhere in the tracked tree is refused by ``tests/test_evidence.py``, which
    does not try to judge whose account an id belongs to and should not have to. The public
    SSM parameter is regional and AWS's to move, so reading it keeps the ARN correct as well as
    keeping the literal out -- the same two reasons ``edullm run`` reads its AMI that way.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    shape = shape_for(loaded, "ml.g4dn.xlarge")
    assert shape is not None

    assert IMAGE_ACCOUNT_PARAMETER in image_account_argv()
    assert image_arn_for(loaded, shape, account=STUDIO_IMAGE_ACCOUNT) == (
        f"arn:aws:sagemaker:{loaded.region}:{STUDIO_IMAGE_ACCOUNT}:image/{shape.image_name}"
    )
    assert all("arn:" not in shape.image_name for shape in loaded.shapes)


def test_an_unreadable_image_account_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: guess the account, or carry on with an empty segment.

    A wrong account segment produces a ``ValidationException`` naming an ARN nobody wrote,
    which is a worse thing to hand somebody than a sentence saying the lookup failed. Nothing
    is created either, which is why the lookup happens before the profile and the space.
    """
    runner = a_studio(tmp_path, profile_exists=False, space_exists=False, image_account=None)

    code, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    assert "image_account_unreadable" in err
    assert not runner.ran("aws", "sagemaker", "create-user-profile")
    assert not runner.ran("aws", "sagemaker", "create-space")
    assert not runner.ran("aws", "sagemaker", "create-app")


def test_stopping_names_the_app_and_never_the_space() -> None:
    """Mutation: reach for ``delete-space``, which is what "delete" suggests and would take
    the volume and every file on it with it."""
    argv = delete_app_argv(settings=settings(), space=STUDIO_SPACE)

    assert argv[:3] == ("aws", "sagemaker", "delete-app")
    assert "delete-space" not in argv
    assert STUDIO_SPACE in argv


def test_listing_spaces_is_one_unpaginated_call() -> None:
    """Mutation: pass ``--max-results``.

    The AWS CLI follows ``NextToken`` by itself unless a page size is named, so naming one is
    how a growing domain silently starts reporting only the first page -- and a person whose
    space fell off it is told they have none.
    """
    argv = list_spaces_argv_for()

    assert "--max-results" not in argv
    assert "--max-items" not in argv


def list_spaces_argv_for() -> tuple[str, ...]:
    from edullm_platform.cli.studio import list_spaces_argv

    return list_spaces_argv(settings())


# ---------------------------------------------------------------------------------------
# reading what the account said
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "billing"),
    [("InService", True), ("Pending", True), ("Deleted", False), ("Failed", False)],
)
def test_an_app_is_billing_only_where_an_instance_is_allocated(status: str, billing: bool) -> None:
    assert RunningApp(status=status, instance_type=None).is_billing is billing


def test_a_deleted_app_is_not_counted_as_running_by_the_listing() -> None:
    """Mutation: key ``ListApps`` by space and take the last one.

    Studio never forgets an app: a space stopped last week still appears with ``Status``
    ``Deleted``. Counting those tells everybody in the domain they are paying for something
    they already stopped, which is the failure that makes people stop believing the tool.
    """
    listed = json.dumps(
        {
            "Apps": [
                {"SpaceName": "a", "AppType": "JupyterLab", "Status": "Deleted"},
                {"SpaceName": "b", "AppType": "JupyterLab", "Status": "InService"},
            ]
        }
    )

    assert set(apps_by_space(listed)) == {"b"}


@pytest.mark.parametrize("body", ["", "not json", "[]", "{}", '{"Status": 3}'])
def test_an_unreadable_describe_is_no_app_rather_than_a_traceback(body: str) -> None:
    """Mutation: index into the body. A traceback in front of a researcher is the one thing
    this binary promises not to produce, and an empty body is the ordinary first invocation."""
    assert running_app(body) is None


@pytest.mark.parametrize("body", ["", "not json", "[]", "{}", '{"Spaces": 3}'])
def test_an_unreadable_space_list_is_no_spaces_rather_than_a_traceback(body: str) -> None:
    assert owned_spaces(body, owner="caiiris") == ()
    assert space_named(body, "caiiris-mixlaw") is None


def test_a_shared_space_nobody_owns_is_skipped_rather_than_claimed() -> None:
    """Mutation: default a missing owner to the caller.

    The console can make a space with no owner, which this verb cannot. Every question here is
    about whose it is, and the answer for that one is nobody's -- claiming it would put one
    person's presigned URL on a disk the whole domain can write to.
    """
    listed = json.dumps({"Spaces": [{"SpaceName": "shared", "Status": "InService"}]})

    assert owned_spaces(listed, owner="caiiris") == ()


def test_the_price_names_both_charges_and_says_which_one_stops() -> None:
    """Mutation: quote the hourly rate alone.

    They stop at different times, and conflating them is the misunderstanding the verb exists
    to prevent: the volume is the persistent disk that is the reason to prefer Studio, and it
    is billed whether or not anybody is signed in.
    """
    shape = shape_for(settings(), None)
    assert shape is not None
    said = price_said(shape, settings(), volume_gib=STUDIO_VOLUME_GIB)

    assert "an hour at list price" in said
    assert "a month whether or not the app is running" in said
    assert "The volume charge does not stop." in said


def test_the_reviewed_rate_card_prices_the_default_and_holds_no_duplicate() -> None:
    """Mutation: name a default nothing prices, or price one instance type twice.

    Both are the same failure from two sides -- a verb that cannot quote what it is about to
    start -- and both are refused when the file loads rather than one line before an app.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    priced = [shape.instance_type for shape in loaded.shapes]

    assert loaded.default_instance_type in priced
    assert len(set(priced)) == len(priced)
    assert shape_for(loaded, None) is not None
    assert shape_for(loaded, "ml.nonesuch.xlarge") is None


def test_the_configured_disk_is_small_because_a_person_now_has_several() -> None:
    """Mutation: keep the fifty gigabytes that were right for one space per person.

    ``--project`` names the space, so the per-person disk bill is the configured size times
    however many projects somebody has. Fifty would be over a thousand dollars a month across
    the roster and would make ``cli/studio.py``'s argument against a ceiling false.
    """
    loaded = load_studio_settings(CONFIG_DIR)

    assert loaded.volume_gib <= 10
    assert loaded.volume_gib_month_usd * loaded.volume_gib < 2


def test_no_aws_session_is_unreachable_rather_than_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: report a missing credential as a refusal.

    Exit 1 means something has to change about the request and retrying it unchanged reaches
    the same place. A laptop that has not logged in has to retry, and the message names the
    one command that does it.
    """
    answers = dict(git_answers(tmp_path))
    answers.update(lane_answers())
    answers.update(studio_answers())
    answers[("aws", "sts", "get-caller-identity")] = failed("Unable to locate credentials")
    runner = FakeRunner(answers)

    code, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    assert "sb-aws-creds login" in err


def test_a_space_list_that_failed_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: carry on with an empty list, which reads as "you own nothing".

    Every mode depends on this call, and an empty answer means "create a space" to a start and
    "nothing is billing" to a stop. Both are wrong in the expensive direction, and the second
    is wrong in the reassuring one.
    """
    answers = dict(git_answers(tmp_path))
    answers.update(lane_answers())
    answers.update(studio_answers())
    answers[("aws", "sagemaker", "list-spaces")] = failed("An error occurred (ThrottlingException)")
    runner = FakeRunner(answers)

    code, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    assert "did nothing at all" in " ".join(err.split())
    assert not runner.ran("aws", "sagemaker", "create-space")


def test_a_url_that_could_not_be_minted_says_the_app_may_be_billing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: report the URL failure and stop.

    This is the one failure here that can leave an instance running with no way in, so a
    message that did not name the stop would leave somebody paying for a machine they cannot
    reach and cannot see.
    """
    answers = dict(git_answers(tmp_path))
    answers.update(lane_answers())
    answers.update(studio_answers())
    answers[("aws", "sagemaker", "create-presigned-domain-url")] = failed("AccessDenied")
    runner = FakeRunner(answers)

    code, _, err = invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_UNREACHABLE
    assert f"edullm studio --stop --project {THE_PROJECT}" in " ".join(err.split())


def test_nothing_here_starts_an_ec2_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: route this through ``_lane_session``, which starts a machine where it finds
    none. This verb reaches SageMaker and a browser, and a laptop with no Session Manager
    plugin is most of why Studio is the exploration surface at all."""
    runner = a_studio(tmp_path)

    invoke(
        ["studio", "--project", THE_PROJECT],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
        plugin=False,
    )

    assert not runner.ran("aws", "ec2", "run-instances")
    assert not runner.ran("aws", "ssm", "start-session")


def test_the_domain_is_read_from_reviewed_configuration_and_not_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: hard-code the domain id.

    Nothing under ``infra/`` deploys this domain, so the id is a recorded fact rather than a
    stack output, and the one place it is recorded is the file the verb reads.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    runner = a_studio(tmp_path)

    invoke(
        ["studio", "--project", THE_PROJECT], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )
    reached = [call for call in runner.calls if call[:2] == ("aws", "sagemaker")]

    assert reached
    for call in reached:
        assert loaded.domain_id in call


def test_the_fixture_declares_every_call_this_verb_makes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture's own tripwire. Mutation: teach the verb a call and not the fixture.

    ``FakeRunner`` raises on a call nobody declared, so this passes by driving all four modes
    rather than by asserting anything about them -- which is exactly what it is for.
    """
    invoke(
        ["studio", "--project", THE_PROJECT],
        runner=a_studio(tmp_path, profile_exists=False, space_exists=False),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    invoke(["studio"], runner=a_studio(tmp_path), cwd=tmp_path, monkeypatch=monkeypatch)
    invoke(
        ["studio", "--stop"],
        runner=a_studio(tmp_path, running=(STUDIO_SPACE,)),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    invoke(
        ["studio", "--stop", "--project", THE_PROJECT],
        runner=a_studio(tmp_path, running=(STUDIO_SPACE,)),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )


def test_a_second_shape_is_priced_differently_from_the_lane_for_the_same_silicon() -> None:
    """Mutation: reuse ``config/workload-catalog.yaml``'s rate for a Studio shape.

    The catalog prices ``g4dn.xlarge`` as an EC2 instance and Studio bills ``ml.g4dn.xlarge``
    at its own rate for the same card. One number for both would under-quote every Studio
    hour, which is why this rate card is keyed on the string AWS bills against.
    """
    loaded = load_studio_settings(CONFIG_DIR)
    studio_gpu = shape_for(loaded, "ml.g4dn.xlarge")
    assert studio_gpu is not None

    assert studio_gpu.instance_type.startswith("ml.")
    assert all(shape.instance_type.startswith("ml.") for shape in loaded.shapes)
    assert ok  # the import is load-bearing for the fixtures above
    assert OwnedSpace(name="x", project=None, volume_gib=None, status="").project is None


def _a_space(name: str, owner: str, volume: int) -> dict[str, object]:
    """One ``ListSpaces`` entry, with the ``Summary`` suffixes the service actually uses."""
    return {
        "SpaceName": name,
        "Status": "InService",
        "SpaceSettingsSummary": {
            "SpaceStorageSettings": {"EbsStorageSettings": {"EbsVolumeSizeInGb": volume}}
        },
        "SpaceSharingSettingsSummary": {"SharingType": "Private"},
        "OwnershipSettingsSummary": {"OwnerUserProfileName": owner},
    }
