"""The personal default team: what it saves, and the four things it must not become.

**THE FEATURE IS TYPING FEWER CHARACTERS AND IT MUST BUY NOTHING ELSE.** Seven people on the
roster sit on two declared groups each, so ``resolve_team`` cannot answer for them and they
pass ``--team`` on every ``check`` and every ``submit``, permanently. A default answers that
once. Everything below the first two cases is about the boundary: a default is a prefill, so
it goes through the same checks a typed team goes through, it is beaten by a typed team, it
reaches no network, and it says out loud that it was a default rather than a keystroke.

The identity case is the one worth reading first.
:func:`test_a_default_you_are_not_on_produces_the_same_bytes_as_typing_it` runs the same
submission twice, once with the team typed and once with it defaulted, and compares the
output byte for byte. A preference that could reach an outcome a keystroke could not is a
permission dressed as a convenience, and the way to hold that shut is to assert that nothing
downstream can tell the two apart.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from edullm_platform.cli.configuration import load_reviewed_configuration
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
from edullm_platform.cli.preferences import (
    DefaultTeam,
    default_team_file,
    read_default_team,
)
from edullm_platform.cli.preflight import SCRATCH_TEAM, resolve_team
from edullm_platform.contracts.authorization import evaluate_authorization
from edullm_platform.contracts.policy import RequestFacts
from tests.cli_support import (
    CONFIG_DIR,
    SUBMITTER,
    SUBMITTER_ON_TWO_TEAMS,
    SUBMITTER_TEAM,
    FakeRunner,
    default_team_path,
    git_answers,
    invoke,
    write_default_team,
    write_spec,
)

#: A declared group ``config/organization.yaml`` does not put :data:`SUBMITTER` on, which is
#: what makes it the team a default must not be able to launder.
NOT_MY_TEAM = "pre-training"

CHECK = ("check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment")


def stated_file(home: Path) -> Path:
    """The preference path for a stated home and a stated empty environment.

    **BOTH HALVES HAVE TO BE STATED AND SAYING ONLY ONE OF THEM WROTE INTO A REAL HOME.** A
    GitHub Actions runner exports ``XDG_CONFIG_HOME``, which is the whole point of reading it
    and is also what beats the ``home`` argument, so a case that named a temporary home and
    left the environment ambient resolved to ``/home/runner/.config/edullm/team`` and created
    it. Locally it passed, because no laptop here sets the variable. Every case below goes
    through this rather than through the two-argument call it is easy to write half of.
    """
    path = default_team_file(environ={}, home=home)
    assert path is not None
    return path


def checkout(tmp_path: Path) -> tuple[Path, FakeRunner]:
    write_spec(tmp_path)
    return tmp_path, FakeRunner(git_answers(tmp_path))


def team_row(out: str) -> str:
    """The one line the manifest block prints the team and its origin on."""
    rows = [line for line in out.splitlines() if line.strip().startswith("team ")]
    assert len(rows) == 1, f"expected one team row, found {rows}"
    return rows[0]


def test_a_default_answers_the_question_the_roster_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point. Somebody on two declared groups stops being asked on every command.

    Mutation: leave the refusal in place and treat the default as advice. ``team_is_ambiguous``
    is correct and it is also permanent for seven people, so a default that did not actually
    clear it would be a file that changes nothing.
    """
    root, runner = checkout(tmp_path)
    write_default_team(root, "input-core\n")

    code, out, err = invoke(
        list(CHECK), runner=runner, cwd=root, monkeypatch=monkeypatch,
        login=SUBMITTER_ON_TWO_TEAMS,
    )

    assert code == EXIT_OK, out + err
    assert "team_is_ambiguous" not in out
    assert "input-core" in team_row(out)


def test_the_team_row_says_the_team_was_inherited_and_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: prefill the team and print it the way a typed one is printed.

    ``--team`` already reports its origin on this line, and the reason is that a value
    somebody chose and a value nothing checked are different facts. A default is a third
    origin and is the only one that leaves no trace in the command, so a transcript read six
    weeks later has nothing else to go on. The path is printed rather than a phrase, because
    the reader's next move is to open the file.
    """
    root, runner = checkout(tmp_path)
    path = write_default_team(root, "input-core\n")

    code, out, err = invoke(
        list(CHECK), runner=runner, cwd=root, monkeypatch=monkeypatch,
        login=SUBMITTER_ON_TWO_TEAMS,
    )

    assert code == EXIT_OK, out + err
    row = team_row(out)
    assert "your default" in row
    assert str(path) in row


def test_a_typed_team_beats_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: let the file win, or refuse when the two disagree.

    The preference exists so that the usual case costs nothing to say. Overriding it for one
    run is the other half of that bargain, and a default that could not be overridden would be
    worse than the flag it replaces.
    """
    root, runner = checkout(tmp_path)
    write_default_team(root, "input-core\n")

    code, out, err = invoke(
        [*CHECK, "--team", SCRATCH_TEAM], runner=runner, cwd=root, monkeypatch=monkeypatch,
        login=SUBMITTER_ON_TWO_TEAMS,
    )

    assert code == EXIT_OK, out + err
    row = team_row(out)
    assert SCRATCH_TEAM in row
    assert "named on the command line" in row
    assert "input-core" not in row


def test_a_default_beats_what_the_roster_would_have_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: consult the file only where the roster is ambiguous.

    A default that lost to the roster would work for the seven people on two groups and do
    nothing for anybody else, which is a preference that applies where the tool happens to
    have no opinion. The roster's single answer is an inference from a membership list and a
    default is a statement, so the statement wins. ``scratch`` is a group this submitter is in,
    so nothing below is refused and the assertion is about precedence alone.
    """
    root, runner = checkout(tmp_path)
    write_default_team(root, f"{SCRATCH_TEAM}\n")

    code, out, err = invoke(list(CHECK), runner=runner, cwd=root, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    row = team_row(out)
    assert SCRATCH_TEAM in row
    assert SUBMITTER_TEAM not in row


def test_a_default_you_are_not_on_produces_the_same_bytes_as_typing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**THE ONE THAT DECIDES WHETHER THIS FEATURE IS SAFE.**

    Mutation: skip ``_check_team`` for a team that came from the file, on the reasoning that
    somebody who wrote it down meant it. That is the failure this whole design is arranged
    against. A preference would then be a way to reach a team the roster does not put you on
    without meeting the refusal a keystroke meets, and team is what cost attribution groups
    on, so the run would land in a group's total without anybody choosing that at the moment
    of submitting.

    Asserted as byte equality of the refusal section rather than as the presence of a code,
    because the claim is stronger than "it is also refused": the two runs reach the same
    refusal, in the same words, and neither buys anything the other does not.

    **THE ONE LINE THAT DIFFERS IS THE ONE THAT SHOULD, AND IT IS ASSERTED RATHER THAN
    EXCLUDED.** This compared the whole of stdout while a refused check printed no manifest.
    It prints one now, so the team row carries where the value came from, and that row is the
    difference: a reader who is being refused for a team they did not type this time needs to
    be told which file typed it for them. A default that produced the *same* origin line as a
    keystroke would be the tool concealing where a claim came from.
    """
    typed_root, typed_runner = checkout(tmp_path / "typed")
    typed = invoke(
        [*CHECK, "--team", NOT_MY_TEAM],
        runner=typed_runner, cwd=typed_root, monkeypatch=monkeypatch,
    )

    defaulted_root, defaulted_runner = checkout(tmp_path / "defaulted")
    write_default_team(defaulted_root, f"{NOT_MY_TEAM}\n")
    defaulted = invoke(
        list(CHECK), runner=defaulted_runner, cwd=defaulted_root, monkeypatch=monkeypatch
    )

    assert typed[0] == EXIT_REFUSED
    assert "refused  submitter_not_in_claimed_team" in typed[1]
    # Split rather than sliced, so this reads the refusal section whatever precedes it.
    marker = "1 refusal. Nothing was dispatched."
    assert defaulted[0] == typed[0]
    assert defaulted[1].split(marker)[1] == typed[1].split(marker)[1]
    assert defaulted[2] == typed[2]
    # And the row that says where the claim came from, which is what the two do not share.
    assert f"team              {NOT_MY_TEAM}         named on the command line" in typed[1]
    assert "your default, in " in defaulted[1]


def test_the_claim_a_default_makes_is_authorized_exactly_as_a_typed_one_is() -> None:
    """The far side of the gate, where the CLI has been out of the picture for some time.

    A mis-claimed team stopped being a refusal inside AWS in v1.1.0 and became
    ``team_verified: false`` on the decision record. Nothing about that may depend on how the
    claim was arrived at, and nothing can, because the form carries a team id and no origin.
    This asserts the outcome rather than the mechanism, so that a future origin field on the
    form would be caught here rather than discovered in a cost report.
    """
    configuration = load_reviewed_configuration(CONFIG_DIR)
    facts = RequestFacts(
        claimed_team=NOT_MY_TEAM,
        repository_registered=True,
        dataset_registered=True,
        dataset_is_a_corpus=True,
        compute_profile_registered=True,
        capacity_block_backed=False,
        immutable_revision=True,
        immutable_image=True,
        image_scan_reviewed=True,
        estimated_cost_usd=Decimal("1.00"),
        maximum_runtime_hours=Decimal("0.5"),
        maximum_attempts=1,
    )

    decision = evaluate_authorization(
        SUBMITTER,
        None,
        facts,
        configuration.policy,
        configuration.inventory
    )

    assert decision.granted
    assert decision.claimed_team == NOT_MY_TEAM
    assert not decision.team_verified


def test_a_default_naming_a_group_nothing_declares_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: validate the file when it is read and fall back to the roster when it is bad.

    Falling back would be the quiet failure. Somebody who typed their group's name with a
    capital letter would get a clean check against a group they did not choose, and the first
    sign of it would be a cost split. The value goes through the same lookup a typed one does,
    so the refusal names what was actually read.
    """
    root, runner = checkout(tmp_path)
    write_default_team(root, "Pre-Training\n")

    code, out, err = invoke(list(CHECK), runner=runner, cwd=root, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED, out + err
    assert "refused  unregistered_team" in out
    assert "Pre-Training" in out


def test_reading_a_default_reaches_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``check`` answers in a fraction of a second and asks nothing, default or no default.

    Mutation: resolve the default against the roster over the API, or probe for a release
    while the file is being read. The property is what makes ``check`` usable on a login node
    with no egress and worth running while editing, and a preference read off local disk has
    no business costing it.
    """
    root, runner = checkout(tmp_path)
    write_default_team(root, f"{SUBMITTER_TEAM}\n")

    code, out, err = invoke(list(CHECK), runner=runner, cwd=root, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    assert runner.ran("gh") == []
    assert [argv[0] for argv in runner.calls] == ["git"] * len(runner.calls)
    assert "\x1b" not in out + err


def test_the_ambiguous_refusal_names_the_file_that_ends_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: leave the refusal as it was.

    The moment a researcher meets ``team_is_ambiguous`` is the moment they would want to know
    they can stop meeting it, and it is the only moment the tool has their attention on the
    subject. One sentence and an address, with ``--team`` still named first because it is what
    answers the run they are trying to submit right now.
    """
    root, runner = checkout(tmp_path)

    code, out, _ = invoke(
        list(CHECK), runner=runner, cwd=root, monkeypatch=monkeypatch,
        login=SUBMITTER_ON_TWO_TEAMS,
    )

    assert code == EXIT_REFUSED
    assert "refused  team_is_ambiguous" in out
    assert "--team" in out
    assert str(default_team_path(root)) in " ".join(out.split())


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("pre-training", "pre-training"),
        ("pre-training\n", "pre-training"),
        ("  pre-training  \n", "pre-training"),
        ("\n\npre-training\n", "pre-training"),
        ("pre-training\nsomething somebody left here\n", "pre-training"),
        ("", None),
        ("\n   \n", None),
    ],
)
def test_a_file_typed_by_hand_is_read_the_way_a_person_would_have_written_it(
    contents: str, expected: str | None, tmp_path: Path
) -> None:
    """The file is edited by hand, so the reader has to survive being edited by hand.

    Mutation: strip the whole file and use it. A second line would then arrive at a refusal as
    a team id with a newline in the middle of it, printed back at a reader in two pieces. The
    first line with anything on it is the rule, and an empty file is the same as no file.
    """
    home = tmp_path / "home"
    path = stated_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")

    default = read_default_team(environ={}, home=home)

    assert default == DefaultTeam(path=path, team=expected)


def test_the_file_is_in_the_same_place_on_every_platform_this_supports(
    tmp_path: Path,
) -> None:
    """Mutation: branch on ``sys.platform`` and use Application Support on macOS.

    ``gh`` puts its own configuration under ``~/.config/gh`` on macOS as well as on Linux, and
    ``workspace._login_from_gh_config`` already reads it there. One rule means a researcher who
    works on a laptop and on WSL types the same path and reads the same refusal, and it means
    this needs no platform table to maintain and no dependency to resolve one.

    The variable beats the home directory rather than filling in behind it, which is the whole
    of what setting it means and is not academic: a GitHub Actions runner exports it, so a
    reading that treated it as a fallback would put the file somewhere else on the one machine
    this repository's own checks run on.
    """
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"

    assert default_team_file(environ={}, home=home) == home / ".config" / "edullm" / "team"
    assert default_team_file(
        environ={"XDG_CONFIG_HOME": str(elsewhere)}, home=home
    ) == elsewhere / "edullm" / "team"


def test_a_default_nobody_can_read_falls_back_to_the_roster(tmp_path: Path) -> None:
    """Mutation: raise, or refuse the command.

    A preference that will not open is not a submission anybody can be told is wrong, and
    stopping a run over it would make an unreadable file more disruptive than no file. Falling
    back cannot misattribute anything: the roster answers with a group the submitter is on, or
    it refuses as ambiguous and that refusal names this path, so the reader is sent to the file
    either way.
    """
    home = tmp_path / "home"
    path = stated_file(home)
    # A directory where a file should be, which is the readable shape of an unreadable file
    # and needs no permission bit that a root-owned test runner would ignore.
    path.mkdir(parents=True)

    default = read_default_team(environ={}, home=home)

    assert default == DefaultTeam(path=path, team=None)


def test_nothing_is_defaulted_where_there_is_no_home_to_have_written_it_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container with no passwd entry and no HOME, which is where an agent often runs.

    Mutation: let ``Path.home()`` raise. It is a ``RuntimeError`` out of ``pathlib``, so it
    would reach ``main`` as the traceback the whole binary is arranged to never show, on a
    machine where there was never a preference to read.
    """
    monkeypatch.setattr(Path, "home", _no_home)

    assert default_team_file(environ={}) is None
    assert read_default_team(environ={}) is None


def _no_home() -> Path:
    raise RuntimeError("could not determine home directory")


@pytest.mark.parametrize("typed", [False, True])
def test_submit_says_which_team_it_is_charging_when_nobody_typed_one(
    typed: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: leave ``submit`` silent, as it was.

    ``check`` prints the origin on the manifest row and ``submit`` prints no manifest, so a
    submission driven straight from a default went out with the team in a file on one laptop
    and nothing in the transcript. That is the one origin that leaves no trace anywhere: a
    ``--team`` is in the command that was typed, which is why the line is skipped when one was.

    On stderr, before the dispatch, and it cannot refuse. It is not the answer anybody asked
    for, and a person who reads it and disagrees still has the moment before a runner starts.
    """
    from tests.test_cli_submit import submitting

    root, runner = submitting(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")
    path = write_default_team(root, f"{SCRATCH_TEAM}\n")
    argv = ["submit", "--dataset", "none", "--experiment", "an-experiment", "--no-wait"]

    code, out, err = invoke(
        [*argv, "--team", SCRATCH_TEAM] if typed else argv,
        runner=runner, cwd=root, monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert (f"charging this to {SCRATCH_TEAM}" in err) is not typed
    assert (str(path) in err) is not typed


def test_the_resolver_takes_the_default_as_an_argument_rather_than_reading_the_disk() -> None:
    """The seam that keeps a submission's team decidable without a filesystem.

    Mutation: call ``read_default_team`` inside ``resolve_team``. Every other input to a
    refusal on this path is passed in, which is what lets the rules be exercised against a
    stated situation rather than against whatever the machine running the suite happens to
    have in its home directory.
    """
    configuration = load_reviewed_configuration(CONFIG_DIR)
    stated = DefaultTeam(path=Path("/nowhere/edullm/team"), team="input-core")

    team, source, refusal = resolve_team(
        configuration, submitter=SUBMITTER_ON_TWO_TEAMS, default=stated
    )

    assert team == "input-core"
    assert refusal is None
    assert "/nowhere/edullm/team" in source
