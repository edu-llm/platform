"""What an install says it is, whether it is current, and the Windows failure that is silent.

THE THREE THINGS UNDER TEST HERE ALL FAIL QUIETLY, which is why each gets a file's worth of
attention rather than a line. A version that never moves reports success. A staleness probe
that raises where it should warn takes the platform away from anybody offline. And a
Windows ``gh`` on WSL's inherited PATH makes ``check`` and ``submit`` disagree about who you
are while both exit zero.

Nothing here reaches a network. The probe is driven through a runner that answers what a
test declared, the same arrangement ``tests/cli_support.py`` argues for.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
from edullm_platform.cli.workspace import SubprocessRunner, github_interop_diagnostic
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
    ``uv tool upgrade``, which answers ``Nothing to upgrade`` and sends them away believing
    they fixed it. The line has to be in the message, pinned to the release the message is
    about.
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
