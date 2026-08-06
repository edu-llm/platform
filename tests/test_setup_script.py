"""Two setup scripts for two shells, held to one order by something that can fail.

WHY THERE ARE TWO FILES AND WHY THIS IS THE THING GUARDING THEM. `tools/set-up-a-laptop.sh`
and `tools/set-up-a-laptop.ps1` set the same laptop up for the same platform, and they exist
separately because a native Windows machine has no bash to branch inside of. Two files that
do one job drift, and the half that drifts is the half nobody runs: this repository is
developed on macOS, so the PowerShell one is the copy that will quietly stop matching.

THE ORDER IS WHAT IS ASSERTED, BECAUSE THE ORDER IS WHAT WAS WRONG. Both tracks below were
being taught in the wrong sequence until 2026-08-06. Two orderings cost real time and both
are pinned here rather than described in a comment somebody can edit past.

The first is the install. The distribution was `edullm-platform` until 4.2.2 and the console
script has always been `edullm`, so an install made before the rename is filed under a name
nobody types. Installing the new name does not replace it, the two entries own one
executable, and removing the old one afterwards deletes that file while `uv tool list` goes
on reporting a healthy tool. `tests/test_cli_install_command.py` proved that against uv with
a throwaway package. So the uninstall comes first, and it comes first in both scripts.

The second is the lane. A newcomer told about the Session Manager plugin first performs a
real download and an administrator prompt, clears that wall, and then meets the credential
broker, which they cannot install at all. The broker is upstream of everything else on that
track: it is what produces the credential the profile step selects and the identity step
proves. `tests/test_lane_prerequisites.py` makes the same ruling for the CLI's own refusals.

WHAT THIS FILE DOES NOT DO. It does not run the PowerShell script, because there is no
PowerShell on the machine this repository is developed on, and the case that would has been
left out rather than written as a skip that reads green. What is asserted about that file is
its step list, its ordering and its text. The bash script's two pieces of real logic are run.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

import pytest

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
BASH_SCRIPT: Final = PROJECT_ROOT / "tools" / "set-up-a-laptop.sh"
POWERSHELL_SCRIPT: Final = PROJECT_ROOT / "tools" / "set-up-a-laptop.ps1"

#: What everybody does, in the order it has to happen in. Written out here rather than
#: derived from either script, so that reordering the steps takes three edits and one of them
#: is this list with the reason beside it. That is the point: the order is the thing this
#: file exists to stop somebody changing casually.
SUBMISSION_ORDER: Final = (
    "uv",
    "git",
    "gh",
    "gh-login",
    "clear-the-former-name",
    "install",
    "on-the-path",
    "version",
    "reaches-github",
)

#: The lane, which is only for `run`, `shell` and `stop`. Broker first because it is the one
#: thing here nobody can self-serve, plugin last because it is the one thing here everybody
#: can, and the identity check between them because it is the proof the broker steps worked.
LANE_ORDER: Final = (
    "broker",
    "broker-login",
    "broker-profiles",
    "aws-profile",
    "identity",
    "session-plugin",
)


def bash_steps(name: str) -> tuple[str, ...]:
    """The ids out of one `NAME=( ... )` array in the shell script, in file order."""
    body = re.search(
        rf"^{name}=\((?P<body>.*?)^\)$", BASH_SCRIPT.read_text(encoding="utf-8"), re.DOTALL | re.MULTILINE
    )
    assert body is not None, f"{BASH_SCRIPT.name} declares no {name} array"
    return tuple(line.strip() for line in body.group("body").split("\n") if line.strip())


def powershell_steps(name: str) -> tuple[str, ...]:
    """The keys out of one `$Name = [ordered]@{ ... }` block, in file order.

    An ordered dictionary rather than a list beside a switch, so that the order and the body
    of each step are one object in that file too. A list that dispatched into functions
    declared elsewhere could be reordered without moving anything that runs.
    """
    text = POWERSHELL_SCRIPT.read_text(encoding="utf-8")
    opened = re.search(rf"^\${name} = \[ordered\]@\{{$", text, re.MULTILINE)
    assert opened is not None, f"{POWERSHELL_SCRIPT.name} declares no {name} ordered dictionary"
    closed = re.search(r"^\}$", text[opened.end() :], re.MULTILINE)
    assert closed is not None, f"{name} in {POWERSHELL_SCRIPT.name} is never closed"
    block = text[opened.end() : opened.end() + closed.start()]
    return tuple(re.findall(r"^\s{4}'([a-z0-9-]+)' = \{$", block, re.MULTILINE))


def bash_source(snippet: str, **environment: str) -> str:
    """Run one line against the shell script's own functions, with nothing else executed.

    The script guards its `main` behind a `BASH_SOURCE` check precisely so this can reach
    `broker_profiles` and `version_compare` without installing anything.
    """
    assignments = "".join(f"{key}={value} " for key, value in environment.items())
    finished = subprocess.run(
        ("bash", "-c", f"source '{BASH_SCRIPT}'; {assignments}{snippet}"),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=PROJECT_ROOT,
    )
    assert finished.returncode == 0, finished.stderr
    return finished.stdout


# ---------------------------------------------------------------------------------------
# the order, in both files
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("track", "expected"),
    [("SUBMISSION_STEPS", SUBMISSION_ORDER), ("LANE_STEPS", LANE_ORDER)],
)
def test_the_shell_script_walks_the_steps_in_the_order_this_file_declares(
    track: str, expected: tuple[str, ...]
) -> None:
    """Mutation: move `install` above `clear-the-former-name`, or `session-plugin` above
    `broker`.

    Both are one-line edits that look like tidying and neither breaks anything that runs, so
    nothing else in the suite would notice. The header of this file says what each costs.
    """
    assert bash_steps(track) == expected


@pytest.mark.parametrize(
    ("track", "expected"),
    [("SubmissionSteps", SUBMISSION_ORDER), ("LaneSteps", LANE_ORDER)],
)
def test_the_powershell_script_walks_the_same_steps_in_the_same_order(
    track: str, expected: tuple[str, ...]
) -> None:
    """**THE HALF THAT WILL DRIFT, AND THE ONLY THING WATCHING IT.** Mutation: add a step to
    the shell script and not to this one.

    Nobody here runs Windows, so the PowerShell script is touched only when somebody
    remembers it exists. A person set up from a file missing a step is set up wrongly and
    nothing tells either of them.
    """
    assert powershell_steps(track) == expected


def test_every_shell_step_named_has_a_function_behind_it() -> None:
    """Mutation: rename a `step_` function and leave the array alone.

    The script raises on this at run time, which is the right behaviour and is too late: the
    person finding out is somebody being set up, and the run has already changed their
    machine by the time it reaches the missing one.
    """
    text = BASH_SCRIPT.read_text(encoding="utf-8")
    missing = [
        step
        for step in SUBMISSION_ORDER + LANE_ORDER
        if not re.search(rf"^step_{step.replace('-', '_')}\(\) \{{$", text, re.MULTILINE)
    ]
    assert not missing, f"named in an array with no function to run: {', '.join(missing)}"


def test_the_former_name_is_cleared_before_the_new_one_is_installed() -> None:
    """**THE ORDERING THAT DELETES A WORKING INSTALL WHEN IT IS REVERSED.** Mutation: swap
    the two.

    Asserted on its own as well as inside the whole-list comparison above, because the
    whole-list failure says only that a list changed. This one says what the change costs,
    which is what somebody reading a red test at speed needs.
    """
    for track in (bash_steps("SUBMISSION_STEPS"), powershell_steps("SubmissionSteps")):
        assert track.index("clear-the-former-name") < track.index("install"), (
            "the install runs before the old distribution name is cleared. uv keeps both "
            "entries and they own one `edullm` executable, so the uninstall afterwards "
            "deletes it and leaves `uv tool list` reporting a healthy tool with nothing on "
            "PATH"
        )


def test_the_broker_is_the_first_lane_step_and_the_plugin_is_the_last() -> None:
    """Mutation: check for the plugin first, which reads as the cheaper check to do early.

    The two are not interchangeable. One names a thing a person can do and the other names a
    thing only somebody else can grant, so the order decides whether an afternoon is spent
    before or after learning it was unnecessary.
    """
    for track in (bash_steps("LANE_STEPS"), powershell_steps("LaneSteps")):
        assert track[0] == "broker"
        assert track[-1] == "session-plugin"
        assert track.index("broker-login") < track.index("broker-profiles"), (
            "`login` writes no AWS profile and `install-profiles` is what does, so running "
            "them the other way round writes a profile for a credential that does not exist"
        )
        assert track.index("aws-profile") < track.index("identity"), (
            "the identity call is what separates a missing login from an unset AWS_PROFILE, "
            "and it can only do that after the profile has been resolved"
        )


# ---------------------------------------------------------------------------------------
# what the two scripts say
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("script", [BASH_SCRIPT, POWERSHELL_SCRIPT], ids=["sh", "ps1"])
def test_neither_script_offers_a_way_to_install_the_broker(script: Path) -> None:
    """**Mutation: add an install line for `sb-aws-creds` to be helpful.**

    There is none that works. The package is marked private in its own `package.json` so it
    has never been publishable and `npm view sb-aws-creds` answers 404, and it is built out
    of a private repository this roster is not a member of, so a clone answers 404 too. An
    install line here would send fifteen people to a 404 and cost each of them the afternoon
    the check exists to save. The README inside the tarball demonstrates the hazard: it names
    a `pipx` line for a package that is Node and TypeScript.

    `src/edullm_platform/cli/lane.py` makes the same ruling for the CLI's own refusal.
    """
    text = " ".join(script.read_text(encoding="utf-8").split())
    for offered in ("npm install", "npm i -g", "pipx install", "git clone"):
        assert f"{offered} sb-aws-creds" not in text
        assert f"{offered} -g sb-aws-creds" not in text
    assert "sb-aws-creds" in text, "the script no longer mentions the broker at all"


@pytest.mark.parametrize("script", [BASH_SCRIPT, POWERSHELL_SCRIPT], ids=["sh", "ps1"])
def test_the_profile_step_says_the_step_is_going_away(script: Path) -> None:
    """Mutation: teach the export and say nothing about it being temporary.

    Thirty-five people are being set up by hand this week. A line taught to all of them and
    then withdrawn is thirty-five messages later, and the withdrawal will not reach everyone.
    Pull request 401 makes the lane verbs resolve the profile out of `~/.aws/config`
    themselves, so the export stops being anybody's job the day it merges.
    """
    text = " ".join(script.read_text(encoding="utf-8").split())
    assert "AWS_PROFILE" in text
    assert "401" in text, "the profile step does not say which change removes it"


@pytest.mark.parametrize("script", [BASH_SCRIPT, POWERSHELL_SCRIPT], ids=["sh", "ps1"])
def test_neither_script_claims_the_submission_path_needs_aws(script: Path) -> None:
    """Mutation: fold the lane steps into the one track everybody walks.

    Sixteen of the thirty-five hold no AWS role and none of them needs one to submit. A setup
    that runs the lane steps for everybody turns a working laptop into a blocked one, and the
    person concludes they cannot use the platform at all.
    """
    text = " ".join(script.read_text(encoding="utf-8").split())
    assert "needs no AWS access" in text
    assert "most people never need" in text


# ---------------------------------------------------------------------------------------
# the two pieces of real logic, run
# ---------------------------------------------------------------------------------------


def test_the_shell_script_parses(tmp_path: Path) -> None:
    """Mutation: any syntax error at all.

    `bash -n` rather than running it, because running it installs things. macOS ships bash
    3.2.57 and that is what `#!/usr/bin/env bash` finds on a laptop nobody has customised, so
    an array or an expansion from bash 4 parses here on a runner and fails on every machine
    this script is written for.
    """
    finished = subprocess.run(
        ("bash", "-n", str(BASH_SCRIPT)),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert finished.returncode == 0, finished.stderr


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("4.2.3", "4.2.3", "0"),
        ("4.2.2", "4.2.3", "-1"),
        ("4.2.3", "4.2.2", "1"),
        # The one a string comparison gets wrong, and the reason this is not a string
        # comparison. `sort -V` would answer it and `sort -V` is GNU, so it is not on macOS.
        ("4.10.0", "4.9.9", "1"),
        ("4.2", "4.2.0", "0"),
    ],
)
def test_the_version_comparison_orders_numerically(left: str, right: str, expected: str) -> None:
    """Mutation: compare the two version strings with `=` and call anything else stale.

    An install off the bare URL tracks the default branch, and `main` carries its version bump
    from the moment the pull request earning it merges until the tag is cut. So a laptop set
    up in that window is a patch *ahead* of `releases/latest`, and equality alone reports that
    person as behind and sends them round an upgrade loop that cannot end. Measured on
    2026-08-06: a fresh install answered 4.2.3 against a latest of v4.2.2.
    """
    assert bash_source(f'version_compare "{left}" "{right}"').strip() == expected


def test_a_config_with_no_broker_profile_yields_nothing(tmp_path: Path) -> None:
    """Mutation: fall through and let the identity call fail instead.

    This is the gap between the broker's two steps, and it is where people stop. `login` puts
    a token in the keychain and writes nothing here, so somebody who ran it and not
    `install-profiles` holds a working credential no AWS client can find.
    """
    config = tmp_path / "config"
    config.write_text(
        "[default]\nregion = us-east-1\n"
        "[profile personal]\ncredential_process = /usr/local/bin/other-broker --json\n",
        encoding="utf-8",
    )

    assert bash_source("broker_profiles", AWS_CONFIG_FILE=str(config)).split() == []


def test_a_bare_default_section_is_never_the_profile_this_picks(tmp_path: Path) -> None:
    """Mutation: accept `[default]` as a candidate.

    The AWS CLI spells that section bare rather than as `[profile default]`, and it is a
    profile a person did not pick. Picking it would be this script choosing a credential on
    somebody's behalf out of a section they may share with other work.
    `src/edullm_platform/cli/lane.py` applies the same rule in pull request 401.
    """
    config = tmp_path / "config"
    config.write_text(
        "[default]\ncredential_process = sb-aws-creds credentials --profile default\n"
        "[profile sbsandbox]\ncredential_process = sb-aws-creds credentials\nregion = us-east-1\n"
        "[profile work]\nsso_start_url = https://example.invalid\n",
        encoding="utf-8",
    )

    assert bash_source("broker_profiles", AWS_CONFIG_FILE=str(config)).split() == ["sbsandbox"]


def test_a_broker_invoked_by_a_quoted_path_with_a_space_still_counts(tmp_path: Path) -> None:
    """**Mutation: strip the quotes and split on whitespace, which was the first draft.**

    An npm global install under `C:\\Program Files\\...` is the ordinary case on Windows and a
    home directory with a space in it is ordinary on macOS. Split on spaces, that path yields
    a first token of `C:\\Program`, which matches nothing, and the profile is dropped. The
    person is then told to run `install-profiles`, which they have already run, against a file
    that already has what it should. Caught by running this against a fixture on 2026-08-06.
    """
    config = tmp_path / "config"
    config.write_text(
        "[profile sbsandbox]\n"
        "credential_process = /Users/jane doe/.nvm/bin/sb-aws-creds credentials\n"
        "[profile sbproduction]\n"
        'credential_process = "C:\\Program Files\\nodejs\\sb-aws-creds.exe" credentials\n',
        encoding="utf-8",
    )

    found = bash_source("broker_profiles", AWS_CONFIG_FILE=str(config)).split()

    assert found == ["sbproduction"], (
        "the unquoted path with a space is correctly not matched, because nothing can tell "
        "where that program name ends, and the quoted one must be"
    )


def test_two_broker_profiles_are_both_reported_rather_than_one_being_chosen(
    tmp_path: Path,
) -> None:
    """Mutation: take the first one.

    These are all the platform's own credentials, so there is no unsafe answer here, only an
    expensive one. Picking `sbproduction` for somebody who meant `sbsandbox` starts a machine
    on the wrong account's bill and reports success. The file does not say which account each
    label reaches, so there is nothing here that could break the tie.
    """
    config = tmp_path / "config"
    config.write_text(
        "[profile sbsandbox]\ncredential_process = sb-aws-creds credentials\n"
        "[profile sbproduction]\ncredential_process = sb-aws-creds credentials\n",
        encoding="utf-8",
    )

    found = bash_source("broker_profiles", AWS_CONFIG_FILE=str(config)).split()

    assert found == ["sbsandbox", "sbproduction"]
