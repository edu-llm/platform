"""What the researcher's own checkout and their ``gh`` login say, read locally.

TWO PROGRAMS ARE SHELLED OUT TO AND NEITHER IS ``aws``. ``git`` answers which repository
this is, which commit is checked out, whether the tree is clean and whether the commit is
anywhere a build could have seen it. ``gh`` carries the credential, because
``docs-frank/reference/system-overview.md`` puts the submission behind ``gh workflow run``
and the trust policy pins the credential to the workflow file rather than to a person --
so there is no credential for this binary to hold and nothing for it to ask a researcher
to configure.

**Every call goes through :class:`CommandRunner` so the suite can be hermetic.** A test
that shelled out for real would need a git repository, a GitHub token and a network, and
the first two are the cheap half of that problem. The interesting behaviour here is what
the CLI concludes from an answer, so the answers are supplied.

**The identity is read out of ``gh``'s own configuration before it is asked for over the
network.** ``check`` promises to refuse in under a second without touching anything, and
an off-roster submitter is the first refusal it makes, so the login has to be free. ``gh``
writes it into ``hosts.yml`` at login; asking the API is the fallback for a configuration
laid out somewhere this does not expect.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

__all__ = [
    "CommandResult",
    "CommandRunner",
    "GitFacts",
    "SubprocessRunner",
    "ToolMissingError",
    "github_interop_diagnostic",
    "github_login",
    "read_git_facts",
    "repository_name_from_remote",
]

LOGIN_VARIABLE = "EDULLM_GITHUB_LOGIN"

#: What a command that ran out of time exits with, which is the convention ``timeout(1)``
#: set and every shell script since has read.
TIMED_OUT: Final = 124


class ToolMissingError(RuntimeError):
    """A program the CLI drives is not on PATH.

    Its own type because the remedy is an installation rather than an edit, and a message
    about a missing ``gh`` printed as though it were a refusal sends somebody to look at
    their spec.
    """


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        return self.stdout.strip()


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...


class SubprocessRunner:
    """The real one. Captures both streams, never raises on a non-zero exit.

    Non-raising because almost every call here has a meaningful failure: a directory that
    is not a git repository, a commit no remote carries, a ``gh`` that is not logged in.
    Each of those is an answer this CLI turns into a sentence, and an exception would make
    the caller write the same try block six times.

    ``timeout`` is unset almost everywhere and deliberately so: a dispatch or a log read
    takes as long as GitHub takes, and cutting one off would turn a slow answer into no
    answer. The one caller that passes it is the version probe, which is a courtesy the
    submission must not wait on. A timeout reads as a failed command rather than as an
    exception, for the same reason a non-zero exit does.

    ``env`` is what the lane verbs pass the credential they assumed in, rather than writing an
    AWS profile into a file the researcher then has to know the name of. It is unset for every
    ``git`` and ``gh`` call, which is every call this binary made before the lane existed.
    """

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        if shutil.which(argv[0]) is None:
            raise ToolMissingError(
                f"{argv[0]} is not on PATH. edullm drives git and gh rather than holding a "
                "credential of its own, so both have to be installed and gh has to be "
                "logged in: gh auth login."
            )
        try:
            completed = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                cwd=None if cwd is None else str(cwd),
                check=False,
                timeout=timeout,
                # OVERLAID ON THE AMBIENT ENVIRONMENT RATHER THAN REPLACING IT. A replaced
                # environment loses PATH, HOME and the AWS region, and the failure is an aws
                # binary that cannot be found by a call that was about credentials.
                env=None if env is None else {**os.environ, **env},
            )
        except subprocess.TimeoutExpired as expired:
            return CommandResult(
                returncode=TIMED_OUT,
                stdout="",
                stderr=f"{argv[0]} did not answer within {expired.timeout}s",
            )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def github_interop_diagnostic(
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    kernel_release: str | None = None,
) -> str | None:
    """Said at startup when the ``gh`` on PATH is a Windows executable under WSL.

    **THIS IS THE ONE WINDOWS PROBLEM WORTH CODE, BECAUSE IT IS THE ONE THAT IS SILENT.**
    WSL's ``appendWindowsPath`` defaults to true, so a distribution with no Linux ``gh``
    installed resolves ``gh`` to ``/mnt/c/.../gh.exe`` and every call in this package
    quietly goes to a different operating system. Two things then break in ways nobody
    could reasonably diagnose:

    ``gh.exe`` reads its credential from ``%AppData%``, which the Linux-side lookup in
    :func:`_login_from_gh_config` will never find -- so ``check`` decides nobody is logged
    in and refuses on the roster, while ``submit`` falls through to ``gh api user`` and
    succeeds. Two verbs disagreeing about who you are is a bug report nobody can write.

    And ``compiled_submission`` hands ``gh run download --dir`` a Linux
    ``TemporaryDirectory``. A Windows executable cannot write to a bare ``/tmp/...`` path,
    the download fails, the method answers ``None`` by design, and ``None`` is
    indistinguishable from "the compile job has not finished" -- so ``status`` silently
    stops resolving run ids and every ``status`` and ``cancel`` falls through to a dispatch
    that costs a runner.

    **SAID RATHER THAN WORKED AROUND, AND SAID RATHER THAN REFUSED.** Path rewriting to
    make ``gh.exe`` usable would be clever, cross-OS, slow, and would still leave the
    credential in the wrong place; refusing would take the platform away from somebody who
    is one ``apt install`` from working. One line naming the executable and the remedy is
    the whole of what is useful here. Native Windows is not supported and this does not
    make it so -- it only names the failure on the arrangement that is.
    """
    variables = os.environ if environ is None else environ
    locate = shutil.which if which is None else which
    if not _under_wsl(variables, kernel_release):
        return None
    windows = [
        f"{tool} is {found}"
        for tool in ("gh", "git")
        if (found := locate(tool)) is not None and _is_a_windows_executable(found)
    ]
    if not windows:
        return None
    return (
        f"{', and '.join(windows)} -- a Windows executable on WSL's inherited PATH, and "
        "edullm cannot use it. gh.exe reads your login from %AppData% rather than "
        "~/.config/gh, so check and submit disagree about who you are, and it cannot write "
        "to a Linux temporary directory, so status stops resolving run ids. Install the "
        "Linux build inside WSL -- for Ubuntu, `sudo apt install gh git` -- or set "
        "appendWindowsPath=false under [interop] in /etc/wsl.conf. Nothing below this line "
        "is trustworthy until you do."
    )


def _under_wsl(variables: Mapping[str, str], kernel_release: str | None) -> bool:
    """Whether this is a Linux kernel Microsoft shipped, by the two signals there are.

    The environment variable is what WSL itself sets and is the cheap answer; the kernel
    release string is the one that survives a login shell that scrubbed the environment,
    and is what everything from Docker to Ansible keys on. Neither is read on a machine
    that is not Linux, so a macOS ``/mnt``-mounted anything cannot trip this.
    """
    if variables.get("WSL_DISTRO_NAME") or variables.get("WSL_INTEROP"):
        return True
    if kernel_release is not None:
        return "microsoft" in kernel_release.lower()
    if not sys.platform.startswith("linux"):
        return False
    return "microsoft" in platform.uname().release.lower()


def _is_a_windows_executable(path: str) -> bool:
    """A ``.exe``, or anything reached through the Windows drives WSL mounts at ``/mnt``."""
    lowered = path.lower()
    return lowered.endswith((".exe", ".bat", ".cmd")) or lowered.startswith("/mnt/")


@dataclass(frozen=True)
class GitFacts:
    """Everything the submission needs from the working tree, and how sure it is.

    ``commit_sha`` is the resolved forty characters rather than a branch name, deliberately.
    The workflow resolves a ref for somebody who typed the only name they know their work
    by, and it says why in its own comment: a branch moves and a record must not. Sending
    the resolved commit means the record names what was checked out at the moment of
    submitting rather than whatever the branch points at when the runner picks it up.
    """

    root: Path | None
    repository: str | None
    branch: str | None
    commit_sha: str | None
    #: Paths git reports as modified, added, deleted or untracked, capped for a message.
    dirty_paths: tuple[str, ...]
    #: Whether any remote-tracking ref in this clone contains the commit. False is not proof
    #: that nothing was pushed -- a clone that has not fetched since the push says the same
    #: thing -- which is why the refusal it produces names ``git fetch`` beside ``git push``.
    commit_on_a_remote: bool

    @property
    def is_a_repository(self) -> bool:
        return self.root is not None


def read_git_facts(runner: CommandRunner, *, cwd: Path) -> GitFacts:
    root = runner(("git", "rev-parse", "--show-toplevel"), cwd=cwd)
    if not root.ok:
        return GitFacts(
            root=None,
            repository=None,
            branch=None,
            commit_sha=None,
            dirty_paths=(),
            commit_on_a_remote=False,
        )
    top = Path(root.text)
    remote = runner(("git", "remote", "get-url", "origin"), cwd=top)
    head = runner(("git", "rev-parse", "HEAD"), cwd=top)
    branch = runner(("git", "rev-parse", "--abbrev-ref", "HEAD"), cwd=top)
    status = runner(("git", "status", "--porcelain"), cwd=top)
    commit_sha = head.text if head.ok and len(head.text) == 40 else None
    contains = (
        runner(("git", "branch", "--remotes", "--contains", commit_sha), cwd=top)
        if commit_sha is not None
        else CommandResult(returncode=1, stdout="", stderr="")
    )
    return GitFacts(
        root=top,
        # The remote decides the repository name rather than the directory, because a clone
        # can be named anything and `config/repositories.yaml` is keyed on the GitHub name.
        repository=repository_name_from_remote(remote.text) if remote.ok else None,
        branch=branch.text if branch.ok and branch.text != "HEAD" else None,
        commit_sha=commit_sha,
        dirty_paths=tuple(
            line[3:] for line in status.stdout.splitlines() if len(line) > 3
        )
        if status.ok
        else (),
        commit_on_a_remote=contains.ok and bool(contains.text),
    )


def repository_name_from_remote(url: str) -> str | None:
    """``git@github.com:edu-llm/OLMo-core.git`` and the https spelling both give OLMo-core.

    The last path segment with any ``.git`` suffix removed, which is the whole rule and
    holds for both transports. Returns ``None`` for an empty remote rather than an empty
    string, so a caller cannot accidentally look up the repository named "".
    """
    trimmed = url.strip().removesuffix("/")
    if not trimmed:
        return None
    tail = trimmed.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    name = tail.removesuffix(".git")
    return name or None


def github_login(
    runner: CommandRunner,
    *,
    environ: Mapping[str, str] | None = None,
    config_home: Path | None = None,
    allow_network: bool = False,
) -> str | None:
    """Who ``gh`` says you are, for free where possible.

    ``allow_network`` is the switch between the two halves of this binary. ``check``
    promises to answer without asking anything, so it reads the file ``gh`` wrote at login
    and reports nobody when there is none. The verbs that were going to reach GitHub anyway
    may ask, because for them the call costs nothing extra.
    """
    variables = os.environ if environ is None else environ
    declared = variables.get(LOGIN_VARIABLE, "").strip()
    if declared:
        return declared
    from_config = _login_from_gh_config(variables, config_home)
    if from_config is not None:
        return from_config
    if not allow_network:
        return None
    answered = runner(("gh", "api", "user", "--jq", ".login"))
    return answered.text or None


def _login_from_gh_config(
    variables: Mapping[str, str], config_home: Path | None
) -> str | None:
    """``gh``'s own ``hosts.yml``, at whichever location that program would read it from.

    ``GH_CONFIG_DIR`` overrides rather than being tried first and then fallen back from,
    which is ``gh``'s own semantics and is also what makes this testable: a directory
    declared and empty has to mean "nobody is logged in", not "look in the home directory
    of whoever is running the suite".
    """
    import yaml

    if config_home is not None:
        directory = config_home
    else:
        declared = variables.get("GH_CONFIG_DIR", "").strip()
        if declared:
            directory = Path(declared)
        else:
            xdg = variables.get("XDG_CONFIG_HOME", "").strip()
            directory = Path(xdg) / "gh" if xdg else Path.home() / ".config" / "gh"
    hosts = directory / "hosts.yml"
    if not hosts.is_file():
        return None
    try:
        document = yaml.safe_load(hosts.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(document, dict):
        return None
    entry = document.get("github.com")
    if not isinstance(entry, dict):
        return None
    user = entry.get("user")
    return user if isinstance(user, str) and user else None
