"""What an install says it is, whether it is current, and the Windows failures that are silent.

THE FOUR THINGS UNDER TEST HERE ALL FAIL QUIETLY, which is why each gets a file's worth of
attention rather than a line. A version that never moves reports success. A staleness probe
that raises where it should warn takes the platform away from anybody offline. A Windows
``gh`` on WSL's inherited PATH makes ``check`` and ``submit`` disagree about who you are
while both exit zero. And on native Windows the same disagreement arrived by a different
route, from a lookup that knew only the Unix places ``gh`` keeps ``hosts.yml``, with no
warning at all because the WSL detector correctly answered no.

The last two are tested together and belong together. They are one symptom reached two ways,
and a reader who finds either should find the other in the same file.

Nothing here reaches a network. The probe is driven through a runner that answers what a
test declared, the same arrangement ``tests/cli_support.py`` argues for, and the platform
every configuration lookup is asked about is injected rather than read off the machine
running the suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

import pytest

from edullm_platform.cli.actions import PLATFORM_REPOSITORY
from edullm_platform.cli.release import (
    InstalledVersion,
    LatestRelease,
    install_command,
    latest_release,
    probe_failed_said,
    staleness_said,
)
from edullm_platform.cli.workspace import (
    SubprocessRunner,
    gh_config_directory,
    github_interop_diagnostic,
    github_login,
)
from tests.cli_support import FakeRunner, failed, ok

NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
CUT = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


def probe(answer: object) -> FakeRunner:
    return FakeRunner({("gh", "api"): answer})  # type: ignore[dict-item]


# ---------------------------------------------------------------------------------------
# what this install is
# ---------------------------------------------------------------------------------------


def test_the_version_line_names_the_commit_and_not_only_the_version() -> None:
    """Mutation: print ``version`` alone, which is what it did.

    A release is cut per merge touching the CLI or the configuration, so two installs made
    hours apart routinely share a version and carry different reviewed configuration. The
    commit is the only field that separates them, and it is the field somebody needs when
    a refusal looks wrong.
    """
    said = InstalledVersion(
        version="0.2.0", revision="v0.2.0", commit="b73cdd73bcc73964de71ebfa8d527da469d1ba6a"
    ).said()

    assert said == "0.2.0 (v0.2.0, b73cdd73bcc7)"


def test_an_install_from_a_bare_url_still_names_its_commit() -> None:
    """The ordinary case for anybody who pasted the line without a tag.

    uv records the resolved commit in ``direct_url.json`` whatever ref was asked for, so
    there is no install from a URL that cannot say exactly what it was built from.
    """
    said = InstalledVersion(version="0.2.0", commit="b73cdd73bcc73964de71ebfa8d527da469d1ba6a").said()

    assert said == "0.2.0 (b73cdd73bcc7)"


def test_a_source_tree_admits_it_is_one_rather_than_inventing_a_version() -> None:
    assert "not installed" in InstalledVersion(version=None).said()


def test_only_a_release_ref_counts_as_the_tag_this_was_installed_from() -> None:
    """Mutation: derive the tag from ``version``.

    An install from a branch or a bare URL carries the version of the last release and is
    *not* that release -- it is some commit after it. Treating the version as a tag would
    tell somebody tracking main that they are current, which is the one group most likely
    to be running something nobody released.
    """
    assert InstalledVersion(version="0.2.0", revision="v0.2.0").tag == "v0.2.0"
    assert InstalledVersion(version="0.2.0", revision="main").tag is None
    assert InstalledVersion(version="0.2.0").tag is None


# ---------------------------------------------------------------------------------------
# asking what the current release is
# ---------------------------------------------------------------------------------------


def test_the_probe_is_one_call_to_the_releases_endpoint() -> None:
    runner = probe(ok("v0.4.1\t2026-08-03T09:00:00Z\n"))

    answer = latest_release(runner, repository=PLATFORM_REPOSITORY)

    assert answer == LatestRelease(tag="v0.4.1", published_at=CUT)
    assert len(runner.ran("gh", "api")) == 1
    assert f"repos/{PLATFORM_REPOSITORY}/releases/latest" in runner.ran("gh", "api")[0]


def test_the_probe_is_given_a_timeout_so_a_hung_call_cannot_hold_a_submission() -> None:
    """Mutation: drop the timeout.

    A dispatch may take as long as GitHub takes, because it is the thing being asked for.
    A courtesy that tells somebody about a newer version may not: on a captive portal the
    call does not fail, it hangs, and the submission hangs behind it.
    """
    recorded: list[float | None] = []

    def runner(argv: tuple[str, ...], *, cwd: object = None, timeout: float | None = None) -> object:
        recorded.append(timeout)
        return ok("v0.4.1\t\n")

    latest_release(runner, repository=PLATFORM_REPOSITORY)  # type: ignore[arg-type]

    assert recorded and recorded[0] is not None and recorded[0] > 0


@pytest.mark.slow
def test_the_real_runner_turns_a_timeout_into_a_failed_command_and_not_an_exception() -> None:
    """The other half of the timeout, against the runner that actually starts a process.

    Mutation: let ``TimeoutExpired`` escape. It is not one of the four exceptions
    ``main()`` catches, so it would leave a traceback where the contract is a warning --
    and it would do it on the exact machine the requirement is about, the one whose
    connection is bad enough for the probe to hang.
    """
    result = SubprocessRunner()(("sleep", "10"), timeout=0.2)

    assert not result.ok
    assert "did not answer" in result.stderr


def test_a_repository_with_no_releases_is_a_failed_probe_and_not_a_crash() -> None:
    """The state this repository is in until the first tag is cut, so it is the day-one case."""
    answer = latest_release(probe(failed("gh: Not Found (HTTP 404)")), repository=PLATFORM_REPOSITORY)

    assert answer.tag is None
    assert answer.unreachable is not None and "404" in answer.unreachable


def test_an_answer_that_is_not_a_tag_is_discarded_rather_than_compared() -> None:
    """Mutation: believe whatever came back.

    A proxy login page, an error body, or a release somebody named ``latest`` would all be
    compared against the installed version and would all read as "you are behind", which
    is a warning nobody can act on and a line researchers learn to ignore.
    """
    answer = latest_release(probe(ok("release-candidate\t\n")), repository=PLATFORM_REPOSITORY)

    assert answer.tag is None
    assert answer.unreachable is not None


# ---------------------------------------------------------------------------------------
# what it says about being behind
# ---------------------------------------------------------------------------------------


def test_being_behind_warns_and_never_refuses() -> None:
    """**THE DECISION THIS FILE IS REALLY ABOUT.** Mutation: return a refusal instead.

    ``config/`` took 55 commits in thirty days and moved twice within hours of the CLI
    merging, so a release is cut most days and being behind is the *normal* state of every
    install rather than an exceptional one. A refusal on the normal state is one everybody
    learns to skip; the flag they skip it with then protects nobody, and the cost lands on
    whoever is submitting at the wrong hour. The probe also has to fail open when GitHub
    cannot be reached, so a gate here would advertise an enforcement that airplane mode
    already defeats -- while admission re-derives every verdict inside AWS regardless.

    So this asserts the *shape* of the answer: a string, which the caller prints, and never
    an exception or an exit code.
    """
    warning = staleness_said(
        InstalledVersion(version="0.2.0", revision="v0.2.0"),
        LatestRelease(tag="v0.4.1", published_at=CUT),
        repository=PLATFORM_REPOSITORY,
        now=NOW,
    )

    assert warning is not None
    assert isinstance(warning, str)
    assert "v0.4.1" in warning and "v0.2.0" in warning


def test_the_warning_carries_the_exact_re_install_line() -> None:
    """Mutation: say "please upgrade".

    A warning naming no remedy costs the reader a search, and the search lands on
    ``uv tool upgrade``, whose answer depends on how they installed. Every install made
    from a release note is pinned at that release's tag, and for those it answers
    ``Nothing to upgrade`` and sends the reader away believing they fixed it. The line has
    to be in the message, pinned to the release the message is about.

    **THIS ASSERTS WHAT THE WARNING SAYS AND NOT WHAT UV DOES.** The uv behaviour the
    paragraph above rests on is checked by installing the tool and running the command, in
    ``tests/test_cli_install_command.py``, which is the only place in the suite that does.
    """
    warning = staleness_said(
        InstalledVersion(version="0.2.0", revision="v0.2.0"),
        LatestRelease(tag="v0.4.1", published_at=CUT),
        repository=PLATFORM_REPOSITORY,
        now=NOW,
    )

    assert warning is not None
    assert install_command(repository=PLATFORM_REPOSITORY, tag="v0.4.1") in warning
    assert "uv tool upgrade" not in warning


def test_the_warning_says_how_long_the_current_release_has_been_out() -> None:
    """Eleven days behind and one day behind are the same sentence without this."""
    warning = staleness_said(
        InstalledVersion(version="0.2.0", revision="v0.2.0"),
        LatestRelease(tag="v0.4.1", published_at=CUT),
        repository=PLATFORM_REPOSITORY,
        now=NOW,
    )

    assert warning is not None and "11 days ago" in warning


def test_a_current_install_is_told_nothing() -> None:
    """Mutation: print a line saying you are current.

    ``submit`` prints the manifest, the dispatch and the run id. A line on every submission
    saying nothing happened is the noise that stops the warning being read on the
    submission where it matters.
    """
    assert (
        staleness_said(
            InstalledVersion(version="0.4.1", revision="v0.4.1"),
            LatestRelease(tag="v0.4.1", published_at=CUT),
            repository=PLATFORM_REPOSITORY,
            now=NOW,
        )
        is None
    )


def test_a_source_tree_is_not_warned_at_all() -> None:
    """A maintainer running from a checkout has no version to be behind, and knows it."""
    assert (
        staleness_said(
            InstalledVersion(version=None),
            LatestRelease(tag="v0.4.1", published_at=CUT),
            repository=PLATFORM_REPOSITORY,
            now=NOW,
        )
        is None
    )


def test_a_failed_probe_produces_no_staleness_claim_in_either_direction() -> None:
    """Mutation: treat an unanswered probe as stale, or as current.

    Both are assertions about a question nobody answered. The second is the dangerous one
    and is the failure this whole mechanism exists to avoid.
    """
    assert (
        staleness_said(
            InstalledVersion(version="0.2.0", revision="v0.2.0"),
            LatestRelease(unreachable="gh: Not Found (HTTP 404)"),
            repository=PLATFORM_REPOSITORY,
            now=NOW,
        )
        is None
    )


def test_a_failed_probe_says_so_rather_than_leaving_silence_to_read_as_a_pass() -> None:
    said = probe_failed_said(LatestRelease(unreachable="gh: Not Found (HTTP 404)"))

    assert said is not None and "404" in said
    assert probe_failed_said(LatestRelease(tag="v0.4.1")) is None


# ---------------------------------------------------------------------------------------
# the WSL diagnostic
# ---------------------------------------------------------------------------------------


def windows_gh(tool: str) -> str | None:
    return {"gh": "/mnt/c/Program Files/GitHub CLI/gh.exe", "git": "/usr/bin/git"}.get(tool)


def linux_gh(tool: str) -> str | None:
    return {"gh": "/usr/bin/gh", "git": "/usr/bin/git"}.get(tool)


def test_a_windows_gh_under_wsl_is_named_at_startup() -> None:
    """**THE FAILURE THIS EXISTS FOR IS SILENT AND THAT IS THE WHOLE ARGUMENT.**

    ``gh.exe`` reads its credential from ``%AppData%``, which the Linux-side lookup never
    finds, so ``check`` refuses on the roster while ``submit`` falls through to
    ``gh api user`` and succeeds -- two verbs disagreeing about who you are. And
    ``gh run download --dir`` cannot write to a Linux temporary directory, so
    ``compiled_submission`` answers ``None``, which is indistinguishable from "still
    compiling", so ``status`` silently stops resolving run ids.

    Mutation: return ``None`` here. Every one of those symptoms then reads as an ordinary
    answer and the researcher debugs the platform instead of their PATH.
    """
    said = github_interop_diagnostic(
        environ={"WSL_DISTRO_NAME": "Ubuntu"}, which=windows_gh, kernel_release="5.15.0-microsoft"
    )

    assert said is not None
    assert "/mnt/c/Program Files/GitHub CLI/gh.exe" in said
    assert "%AppData%" in said
    assert "wsl.conf" in said or "apt install" in said


def test_a_linux_gh_under_wsl_is_not_complained_about() -> None:
    """WSL is supported. It is the Windows executable on its PATH that is not."""
    assert (
        github_interop_diagnostic(
            environ={"WSL_DISTRO_NAME": "Ubuntu"},
            which=linux_gh,
            kernel_release="5.15.0-microsoft",
        )
        is None
    )


def test_nothing_is_said_off_wsl_however_the_path_looks() -> None:
    """Mutation: key on the ``.exe`` alone.

    ``/mnt`` is an ordinary mount point on any Linux machine, and this diagnostic printed
    on a cluster login node would be a confident sentence about an operating system nobody
    is running.
    """
    assert (
        github_interop_diagnostic(environ={}, which=windows_gh, kernel_release="24.6.0")
        is None
    )


def test_a_scrubbed_environment_is_still_recognised_as_wsl() -> None:
    """A login shell that cleared the environment leaves the kernel release, and only that."""
    said = github_interop_diagnostic(
        environ={}, which=windows_gh, kernel_release="5.15.167.4-microsoft-standard-WSL2"
    )

    assert said is not None


def test_a_windows_git_is_named_beside_the_gh() -> None:
    """Both are shelled out to and both break the same way, so both are worth naming once."""

    def both_windows(tool: str) -> str | None:
        return f"/mnt/c/Program Files/Git/{tool}.exe"

    said = github_interop_diagnostic(
        environ={"WSL_INTEROP": "/run/WSL/8_interop"},
        which=both_windows,
        kernel_release="5.15.0-microsoft",
    )

    assert said is not None and "gh is" in said and "git is" in said


# ---------------------------------------------------------------------------------------
# where gh keeps its configuration, per platform
# ---------------------------------------------------------------------------------------
#
# ``gh_config_directory`` is a pure function of an environment mapping, a platform name and a
# home directory, which is what lets every branch below be exercised from a Mac. The rule it
# implements is not this project's: it is ``ConfigDir`` in ``go-gh``'s
# ``pkg/config/config.go``, which is what the ``gh`` binary calls, and it is what
# ``gh help environment`` prints under ``GH_CONFIG_DIR``.

#: A home directory spelled the way a Unix one is, for the cases that must not move.
UNIX_HOME = Path("/home/amy")

#: ``%AppData%`` as Windows actually spells it, backslashes included. Written with real
#: backslashes rather than forward ones deliberately: this is the string a Windows
#: ``os.environ`` hands over, and a test that quietly straightened it would be testing a
#: value no machine produces.
APP_DATA = r"C:\Users\amy\AppData\Roaming"

#: What ``gh auth login`` on native Windows leaves behind, and the whole of what was missed.
WINDOWS_GH_DIRECTORY = PureWindowsPath(r"C:\Users\amy\AppData\Roaming\GitHub CLI")

EVERY_PLATFORM = ("Windows", "Darwin", "Linux")


def resolved(
    variables: dict[str, str], *, system: str, home: Path | None = UNIX_HOME
) -> PureWindowsPath | None:
    """The answer as Windows would spell it, so one assertion reads on either flavour.

    ``gh_config_directory`` joins and never parses, so it is correct under ``PosixPath`` and
    under ``WindowsPath`` alike -- but the two spell the result differently, and a test
    comparing raw strings would pass on a Mac and fail on the machine it is about. Rendering
    through :class:`PureWindowsPath` normalises the separator on both, which is the one thing
    that differs and the one thing this function does not decide.
    """
    answer = gh_config_directory(variables, system=system, home=home)
    return None if answer is None else PureWindowsPath(str(answer))


@pytest.mark.parametrize("system", EVERY_PLATFORM)
def test_a_declared_config_dir_overrides_every_other_rule(system: str) -> None:
    """``GH_CONFIG_DIR`` first on every platform, which is ``gh``'s order and the suite's floor.

    ``tests/cli_support.invoke`` points this at an empty directory on every path, so that a
    maintainer who has run ``gh auth login`` does not have their own login answer the cases
    about nobody being logged in. That only works while a declared directory beats everything
    below it, including the platform branch added for Windows.

    Mutation: check ``XDG_CONFIG_HOME`` first, or let the Windows branch run before this one.
    Both make a declared and empty directory mean "look somewhere else", and the whole suite
    starts reading whoever ran it.
    """
    answer = resolved(
        {
            "GH_CONFIG_DIR": "/tmp/declared",
            "XDG_CONFIG_HOME": "/tmp/xdg",
            "AppData": APP_DATA,
        },
        system=system,
    )

    assert answer == PureWindowsPath("/tmp/declared")


@pytest.mark.parametrize("system", EVERY_PLATFORM)
def test_a_declared_config_dir_that_is_empty_means_nobody_is_logged_in(
    system: str, tmp_path: Path
) -> None:
    """The directory is answered as declared even when there is no ``hosts.yml`` under it.

    This is the property the hermetic suite rests on, asserted at the level that decides it
    rather than inferred from a green run elsewhere.
    """
    answer = gh_config_directory({"GH_CONFIG_DIR": str(tmp_path)}, system=system, home=UNIX_HOME)

    assert answer == tmp_path
    assert not (tmp_path / "hosts.yml").exists()


@pytest.mark.parametrize("system", EVERY_PLATFORM)
def test_xdg_is_honoured_on_every_platform_including_windows(system: str) -> None:
    """``go-gh`` guards the ``AppData`` branch on the operating system and does not guard this one.

    Mutation: make ``XDG_CONFIG_HOME`` Unix-only, which is the intuitive reading of a
    freedesktop variable and is not what ``ConfigDir`` does. A Windows researcher carrying
    the variable in a dotfiles repository has a ``gh`` reading from it, and this would then
    look under ``%AppData%`` at a directory ``gh`` never wrote.
    """
    answer = resolved({"XDG_CONFIG_HOME": "/tmp/xdg", "AppData": APP_DATA}, system=system)

    assert answer == PureWindowsPath("/tmp/xdg/gh")


def test_native_windows_reads_the_directory_gh_writes_under_appdata() -> None:
    """**THE DEFECT. Nothing below the ``AppData`` branch could ever have found this file.**

    A researcher on Windows installs the tool, runs ``gh auth login``, and ``gh`` writes
    ``hosts.yml`` under ``%AppData%\\GitHub CLI``. The lookup knew ``GH_CONFIG_DIR``,
    ``XDG_CONFIG_HOME`` and ``~/.config/gh``, so it concluded nobody was logged in: ``check``
    refused on the roster, and ``submit``, which may ask the network, answered with that same
    person's login. Two verbs disagreeing about who you are, and no warning, because the WSL
    detector correctly says this is not WSL.

    Mutation: drop the ``AppData`` branch, which restores the bug exactly. Or rename the
    directory to ``gh``, which is what every other tool would call it and is not what ``gh``
    calls it.
    """
    answer = resolved({"AppData": APP_DATA}, system="Windows", home=Path("C:/Users/amy"))

    assert answer == WINDOWS_GH_DIRECTORY


def test_appdata_is_found_under_the_upper_case_spelling_windows_actually_hands_over() -> None:
    """**THE MUTATION THAT WOULD HAVE SHIPPED GREEN AND STILL BROKEN WINDOWS.**

    ``go-gh`` asks for ``AppData`` and Go's ``os.Getenv`` is case-insensitive on Windows, so
    that is the spelling worth writing down. Python's ``os.environ`` upper-cases every key it
    holds on Windows, so the mapping this is really handed there carries ``APPDATA`` -- while
    a dictionary a test writes carries whatever the test typed. Reading one spelling passes
    every fixture in this file and finds nothing on the machine it is about, which is the
    same shape of failure as the bug being fixed.

    Mutation: read ``AppData`` alone. This case goes red and no other does.
    """
    answer = resolved({"APPDATA": APP_DATA}, system="Windows", home=Path("C:/Users/amy"))

    assert answer == WINDOWS_GH_DIRECTORY


@pytest.mark.parametrize("system", ("Darwin", "Linux"))
def test_appdata_is_ignored_off_windows(system: str) -> None:
    """The three platforms that work today do not move, and this is the branch that could move them.

    Two parameters rather than three because WSL answers ``Linux`` here, which is the point:
    the guard reads the operating system this process runs on, not the one the ``gh`` on PATH
    was built for, so it cannot rescue the WSL failure above and must not try to.

    Mutation: drop the ``system`` guard. ``AppData`` is set in a Wine prefix, on a
    cross-compiling runner and in any shell that inherited one, and a macOS laptop would then
    look for a login inside a Windows path that does not exist -- turning the whole roster
    off, which is the failure this change exists to end, aimed at the people it works for.
    """
    answer = resolved({"AppData": APP_DATA}, system=system)

    assert answer == PureWindowsPath("/home/amy/.config/gh")


@pytest.mark.parametrize("system", EVERY_PLATFORM)
def test_the_home_directory_answers_when_nothing_is_declared(system: str) -> None:
    """``~/.config/gh`` last on every platform, Windows included, exactly as ``go-gh`` ends.

    Windows reaches here whenever ``%AppData%`` is unset, which is rare and is not a case to
    invent a different answer for.
    """
    answer = resolved({}, system=system)

    assert answer == PureWindowsPath("/home/amy/.config/gh")


@pytest.mark.parametrize("variable", ("GH_CONFIG_DIR", "XDG_CONFIG_HOME", "AppData"))
def test_a_variable_set_to_whitespace_reads_as_unset(variable: str) -> None:
    """A blank value is a variable somebody exported and never filled in, not a directory named "".

    Mutation: drop the ``strip``. ``Path("") / "gh"`` is the relative path ``gh``, and a
    lookup relative to the working directory would answer differently depending on where the
    researcher happened to be standing.
    """
    answer = resolved({variable: "   "}, system="Windows")

    assert answer == PureWindowsPath("/home/amy/.config/gh")


def test_no_home_directory_answers_nothing_rather_than_raising() -> None:
    """A container with no passwd entry and no ``HOME`` has nowhere ``gh`` could have written.

    Mutation: return ``Path(".config/gh")``, or let :meth:`Path.home` raise through. The
    first invents a login file in the working directory; the second turns "nobody is logged
    in" into a traceback out of a verb that promised to answer in under a second.
    """
    assert gh_config_directory({}, system="Linux", home=None) is None


def test_a_windows_login_is_read_for_free_the_way_a_unix_one_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end of the defect: ``check`` finds the login without asking anybody.

    The runner answers nothing at all, so a call to ``gh api user`` raises rather than
    quietly rescuing this -- which is the difference between the two verbs agreeing and the
    two verbs agreeing by accident because both reached the network.

    Mutation: revert the resolver. The login is not found, ``allow_network=False`` answers
    ``None``, and this case names the person the file does.
    """
    monkeypatch.setattr("platform.system", lambda: "Windows")
    hosts = tmp_path / "GitHub CLI" / "hosts.yml"
    hosts.parent.mkdir(parents=True)
    hosts.write_text(
        "github.com:\n    users:\n        amy-on-windows:\n    user: amy-on-windows\n",
        encoding="utf-8",
    )
    runner = FakeRunner({})

    answered = github_login(runner, environ={"AppData": str(tmp_path)}, allow_network=False)

    assert answered == "amy-on-windows"
    assert runner.calls == []


def test_a_login_declared_in_the_environment_still_wins_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``EDULLM_GITHUB_LOGIN`` is above all of this and the new branch must not have got in front."""
    monkeypatch.setattr("platform.system", lambda: "Windows")
    hosts = tmp_path / "GitHub CLI" / "hosts.yml"
    hosts.parent.mkdir(parents=True)
    hosts.write_text("github.com:\n    user: amy-on-windows\n", encoding="utf-8")

    answered = github_login(
        FakeRunner({}),
        environ={"AppData": str(tmp_path), "EDULLM_GITHUB_LOGIN": "somebody-else"},
        allow_network=False,
    )

    assert answered == "somebody-else"
