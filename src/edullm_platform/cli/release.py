"""What this install is, how to replace it, and whether a newer one exists.

ONE STRING SAYS HOW TO INSTALL THIS AND NOTHING ELSE IS ALLOWED TO SAY IT. The command in
``pyproject.toml``'s comment was wrong for as long as it existed -- ``uv tool install
edullm --from git+...`` names the console script where ``--from`` wants the distribution,
and uv answers "Package name (``edullm-platform``) provided with ``--from`` does not match
install request (``edullm``)" -- and by the time anybody ran it, it had been copied into
two transcripts. :func:`install_command` is the one place it is spelled, and
``tests/test_cli_install_command.py`` holds every other copy to it.

``uv tool upgrade`` MUST NEVER BE SUGGESTED, AND IT IS THE COMMAND EVERYBODY WILL TRY.
Verified on uv 0.9.17 against an install from this repository: ``uv tool upgrade
edullm-platform`` answers ``Nothing to upgrade``, and so does the same command with
``--reinstall``. It is not that the answer is unhelpful -- it is that the answer is wrong,
so a researcher who types the obvious thing is told they are current when they are months
behind. The install line with ``--force`` is the upgrade, which is why there is only one
line here rather than two.

**HOW AN INSTALL KNOWS WHAT IT IS.** ``project.version`` alone cannot say: it is a literal,
it moves only when a release is cut, and two installs from different commits between two
tags read the same. So the version is only half of :class:`InstalledVersion`; the other
half is read from ``direct_url.json``, which PEP 610 requires an installer to write into
the ``.dist-info`` of anything installed from a URL. uv writes the resolved commit there
and the ref that was asked for, so every git install can name the exact source it was built
from without a build hook, a version-control plugin, or anybody editing a number.

That last point is worth stating because the obvious alternative looks fine and is not.
``hatch-vcs`` would derive the version from ``git describe`` at build time, and
``uv tool install git+...`` builds the wheel on the researcher's machine -- so the version
of every install would depend on what uv's cached checkout happens to contain. It happens
to contain the tags today. It also has to survive ``actions/checkout``'s shallow default in
every workflow that syncs this project, and a fallback version silently reintroduces
exactly the "0.1.0 forever" bug this module exists to end. A committed literal plus a
recorded commit has neither failure mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from edullm_platform.cli.workspace import CommandRunner

__all__ = [
    "DISTRIBUTION",
    "InstalledVersion",
    "LatestRelease",
    "install_command",
    "installed_version",
    "latest_release",
    "probe_failed_said",
    "staleness_said",
]

#: The distribution, which is not the console script. Getting these two the wrong way round
#: is the whole of the bug this module's docstring opens with.
DISTRIBUTION: Final = "edullm-platform"

#: How long the version probe may take before ``submit`` gives up on it and dispatches
#: anyway. Short because the probe is a courtesy and the dispatch is the job: a researcher
#: on a bad connection should wait for the thing they asked for, not for the thing that was
#: going to tell them about a newer version.
PROBE_TIMEOUT_SECONDS: Final = 5.0

#: What a release tag looks like here, used to tell a tag name from anything else an API
#: might answer with. Anchored, so a body that is not a tag is discarded rather than
#: compared against.
TAG_PATTERN: Final = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")


def install_command(*, repository: str, tag: str | None = None) -> str:
    """The one line that installs this, upgrades it, and repairs a broken install.

    ``--force`` rather than a separate upgrade command, because uv has no working upgrade
    for a git-installed tool and because one idempotent line is one thing to remember. It
    prints both versions when it replaces one, which is the confirmation an upgrade would
    have given.

    No ``--from``. The bare URL installs the distribution the repository declares and puts
    its console script on the path; ``--from`` exists for naming a *different* distribution
    out of the same source, which is not what anybody wants here and is what the wrong
    command was reaching for.
    """
    pinned = f"@{tag}" if tag else ""
    return f"uv tool install --force git+https://github.com/{repository}{pinned}"


@dataclass(frozen=True)
class InstalledVersion:
    """Which edullm this is, from the two places that know.

    ``version`` is the literal in ``pyproject.toml`` at the commit this was built from.
    ``revision`` and ``commit`` come from ``direct_url.json`` and are absent for anything
    not installed from a URL -- an editable install out of a checkout, most of all, which
    is what the suite and every maintainer are running.
    """

    version: str | None
    revision: str | None = None
    commit: str | None = None

    @property
    def installed(self) -> bool:
        return self.version is not None

    @property
    def tag(self) -> str | None:
        """The release this was installed from, where it was installed from one.

        Read from the ref that was asked for rather than assembled from ``version``,
        because those two disagree in the case that matters: an install from a branch or a
        bare URL carries the version of the last release and is not that release.
        """
        if self.revision is not None and TAG_PATTERN.fullmatch(self.revision):
            return self.revision
        return None

    def said(self) -> str:
        """``0.2.0 (v0.2.0, b73cdd73bcc7)`` -- what ``--version`` prints.

        The commit is in there because it is the only field that is different for two
        installs made from two commits between the same pair of tags, which is the state
        most installs are in most of the time.
        """
        if self.version is None:
            return "(not installed -- running from a source tree)"
        detail = [part for part in (self.revision, _short(self.commit)) if part]
        return f"{self.version} ({', '.join(detail)})" if detail else self.version


def installed_version() -> InstalledVersion:
    """Read the distribution's own metadata, or admit there is none."""
    import json
    from importlib.metadata import Distribution, PackageNotFoundError

    try:
        distribution = Distribution.from_name(DISTRIBUTION)
    except PackageNotFoundError:
        return InstalledVersion(version=None)
    version = distribution.metadata["Version"]
    direct = distribution.read_text("direct_url.json")
    if not direct:
        return InstalledVersion(version=version)
    try:
        document = json.loads(direct)
    except json.JSONDecodeError:
        return InstalledVersion(version=version)
    vcs = document.get("vcs_info") if isinstance(document, dict) else None
    if not isinstance(vcs, dict):
        return InstalledVersion(version=version)
    return InstalledVersion(
        version=version,
        revision=_text(vcs.get("requested_revision")),
        commit=_text(vcs.get("commit_id")),
    )


@dataclass(frozen=True)
class LatestRelease:
    """What the releases endpoint said, including that it would not say anything."""

    tag: str | None = None
    published_at: datetime | None = None
    #: Why there is no tag, for the one line ``submit`` prints when it could not ask.
    unreachable: str | None = None


def latest_release(
    runner: CommandRunner,
    *,
    repository: str,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> LatestRelease:
    """One ``gh api`` call, which is allowed to fail and is never allowed to block.

    ``gh`` rather than a Python HTTP client for the reason every other call in this package
    uses ``gh``: it is already installed, it already holds the credential, and adding a
    request library would put a second idea of authentication into a binary whose whole
    design is not to have one.

    Every failure answers :class:`LatestRelease` with ``unreachable`` set rather than
    raising. A repository with no releases answers 404 and is one of those failures, which
    is the state this repository is in until the first tag is cut -- so the ordinary case
    on day one is the probe saying nothing useful, and it has to be harmless.
    """
    result = runner(
        (
            "gh",
            "api",
            f"repos/{repository}/releases/latest",
            "--jq",
            "[.tag_name, .published_at] | @tsv",
        ),
        timeout=timeout,
    )
    if not result.ok:
        return LatestRelease(unreachable=_first_line(result.stderr or result.stdout))
    tag, _, published = result.text.partition("\t")
    if not TAG_PATTERN.fullmatch(tag.strip()):
        return LatestRelease(
            unreachable=f"the releases endpoint answered {tag.strip()[:60]!r}, not a tag"
        )
    return LatestRelease(tag=tag.strip(), published_at=_instant(published.strip()))


def staleness_said(
    installed: InstalledVersion,
    latest: LatestRelease,
    *,
    repository: str,
    now: datetime | None = None,
) -> str | None:
    """The warning, or ``None`` where there is nothing truthful to warn about.

    **THIS WARNS AND DOES NOT REFUSE, AND THE DRIFT RATE IS THE ARGUMENT.** ``config/``
    took 55 commits in the last thirty days and moved twice within hours of the CLI
    merging, so a release is cut most days and *being behind is the normal state of every
    install*, not an exceptional one. A refusal on the normal state is a refusal everybody
    learns to skip, and a skip flag everybody passes protects nobody -- while the cost of
    the refusal lands on whoever is submitting at the wrong hour.

    Three more things point the same way. The probe is required to fail open, so any
    network failure already bypasses it: making it a gate would advertise an enforcement
    that airplane mode defeats. The dangerous direction is bounded -- admission re-derives
    every verdict inside AWS from its own config, so the worst a stale approval costs is a
    lead's click and a refusal that would have happened anyway. And staleness is not
    evidence about *this* submission: most of those 55 commits are additions, which a stale
    CLI answers with a false refusal rather than a false approval, so refusing on staleness
    is refusing a submission the checks just cleared on the strength of a probability.

    What the warning has to earn its place with is the remedy, so it carries the exact line
    rather than the word "upgrade".
    """
    if latest.tag is None or not installed.installed:
        return None
    current = installed.tag or (f"v{installed.version}" if installed.version else None)
    if current == latest.tag:
        return None
    age = _age_said(latest.published_at, now)
    behind = f"{latest.tag} is the current release{age}, and this is {installed.said()}."
    return (
        f"{behind} The reviewed configuration travels inside the install, so this one is "
        "checking against a copy that old. Most changes only cost you a refusal that is "
        "not real; a shape or a dataset withdrawn since then is the direction that costs "
        "an approval. Submitting anyway -- admission re-checks all of it. To be current:\n"
        f"  {install_command(repository=repository, tag=latest.tag)}"
    )


def probe_failed_said(latest: LatestRelease) -> str | None:
    """Said when the probe could not run, because silence would read as a clean bill.

    Short and on stderr. A researcher who is told nothing assumes they were checked, and
    the one case this fires in most often -- a repository with no releases yet -- is
    precisely the case where nobody has ever been checked.
    """
    if latest.unreachable is None:
        return None
    return (
        f"could not check whether this edullm is the current one: {latest.unreachable}. "
        "Dispatching anyway; this check never blocks a submission."
    )


def _age_said(published_at: datetime | None, now: datetime | None) -> str:
    """How long the current release has been out, in whole days, or nothing.

    Days rather than hours because the number is there to convey scale -- somebody eleven
    days behind should feel differently from somebody one day behind -- and an hour count
    on a release cut this morning invites a precision the probe does not have.
    """
    if published_at is None:
        return ""
    moment = datetime.now(UTC) if now is None else now
    days = max((moment - published_at).days, 0)
    if days < 1:
        return ", cut today"
    return f", cut {days} day{'s' if days != 1 else ''} ago"


def _short(commit: str | None) -> str | None:
    """Twelve characters, which is what the image tag carries and what transcripts print."""
    return commit[:12] if commit else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0][:120] if stripped else "gh answered with nothing"


def _instant(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
