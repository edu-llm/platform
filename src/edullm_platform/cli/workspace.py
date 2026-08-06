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

**WHERE THAT FILE IS DEPENDS ON THE OPERATING SYSTEM, AND ASSUMING OTHERWISE IS WHAT MADE
THIS PACKAGE UNUSABLE ON WINDOWS.** :func:`gh_config_directory` implements ``gh``'s own
four-step rule rather than the one convention that happens to hold on the developer's
laptop. The header on that function carries the rule and the source for it.

**WHAT NATIVE WINDOWS IS, AS OF THIS CHANGE.** The identity half is repaired and the layout
was swept rather than assumed: ``gh``'s configuration is found where ``gh`` puts it, the
second failure the WSL diagnostic names cannot arise here because ``gh run download --dir``
is handed a Windows temporary directory by a Windows process rather than across the boundary
that breaks it, and the repository-relative paths that travel to the workflow already go
through ``as_posix``. One residue is this module's and it is not silent: a ``gh`` installed
as a ``.bat`` or ``.cmd`` shim rather than as ``gh.exe`` would be found by
:func:`shutil.which`, which reads ``PATHEXT``, and then not started by :mod:`subprocess`,
which appends only ``.exe`` to a bare name. Every mainstream Windows install of ``gh`` and
``git`` is an ``.exe``, and the failure is a ``FileNotFoundError`` naming the program rather
than a wrong answer, which is the difference that decides whether something is worth code.

**AND THE REASON THIS DEFECT SURVIVED SO LONG IS NOT IN THIS FILE.** A submitter of ``None``
does not make ``check`` louder, it makes it quieter: the team refusal that would have fired
is dropped along with the identity it depends on, so a Windows install with no login at all
looked like a working one. That is being repaired elsewhere. It is worth knowing here because
this module answers ``None`` for a living, and every one of those answers is currently read
by something that relaxes rather than refuses.
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
    "gh_config_directory",
    "github_interop_diagnostic",
    "github_login",
    "read_git_facts",
    "repository_name_from_remote",
]

LOGIN_VARIABLE = "EDULLM_GITHUB_LOGIN"

#: What a command that ran out of time exits with, which is the convention ``timeout(1)``
#: set and every shell script since has read.
TIMED_OUT: Final = 124

#: What ``gh`` names its own directory under ``%AppData%``. A name this project reads rather
#: than owns, spelling and space included, because it is the literal in ``go-gh``.
GH_WINDOWS_DIRECTORY: Final = "GitHub CLI"

#: What :func:`platform.system` answers on Windows, compared case-folded because that is the
#: only comparison an injected value cannot get subtly wrong.
WINDOWS: Final = "windows"


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
                # THE ENCODING IS NAMED BECAUSE `text=True` ALONE MEANS FOUR DIFFERENT
                # CODECS. With no encoding it is the locale's, which is UTF-8 on macOS and
                # Linux and the ANSI code page on Windows -- usually cp1252, against a `gh`
                # that emits UTF-8. Most of what `gh` prints is ASCII and survives; a log
                # line, a branch name or a pull request title with an accented character
                # does not, and cp1252 has undefined bytes, so a UTF-8 sequence can raise
                # UnicodeDecodeError out of this call rather than merely mangle -- which
                # takes `edullm logs` down entirely. It changes again under Python 3.15,
                # where PEP 686 turns UTF-8 mode on by default, so an unpinned
                # `uv tool install` would have two researchers on one install line decoding
                # `gh` differently from each other.
                #
                # `replace` rather than strict, for the same reason: a mangled character in
                # a log line is a worse-looking log line, and a raised exception is a dead
                # verb.
                encoding="utf-8",
                errors="replace",
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

    ``gh.exe`` reads its credential from ``%AppData%``, and this is Linux, so
    :func:`gh_config_directory` correctly refuses to look there and finds nothing -- so
    ``check`` decides nobody is logged in and refuses on the roster, while ``submit`` falls
    through to ``gh api user`` and succeeds. Two verbs disagreeing about who you are is a bug
    report nobody can write.

    **AND THE ``%AppData%`` LOOKUP ADDED FOR NATIVE WINDOWS DOES NOT RESCUE THIS ONE,
    DELIBERATELY.** That branch is guarded on the operating system this process is running
    on, which under WSL is Linux, whatever the ``gh`` on PATH happens to be. Ungating it
    would let a Linux process read a credential out of a Windows path and would then be
    wrong for every ordinary Linux machine that has an ``APPDATA`` variable from somewhere;
    ``tests/test_cli_release.py`` holds a case that fails if anybody tries.

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
    the whole of what is useful here.

    **NATIVE WINDOWS IS A SUPPORTED ARRANGEMENT AS OF THIS CHANGE, WHICH IS WHY THIS SAYS SO
    RATHER THAN SAYING WHAT IT USED TO.** The line here read "native Windows is not
    supported", and it was a disclaimer nothing enforced: a researcher in VS Code, whose
    integrated terminal on Windows is PowerShell by default, met the identical
    check-and-submit disagreement above by a different route and got no warning at all,
    because the detector below correctly answers no. That was not an unsupported platform,
    it was an undiagnosable one. :func:`gh_config_directory` now resolves ``gh``'s
    configuration the way ``gh`` resolves it on each platform, both halves of the WSL failure
    are checked above for the native case, and what remains is in this module's header.
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


def gh_config_directory(
    variables: Mapping[str, str], *, system: str, home: Path | None
) -> Path | None:
    """Where ``gh`` keeps ``config.yml`` and ``hosts.yml``, by ``gh``'s own four-step rule.

    ``GH_CONFIG_DIR``, then ``XDG_CONFIG_HOME/gh``, then ``%AppData%\\GitHub CLI`` on Windows
    only, then ``~/.config/gh``. That is the order in ``ConfigDir`` in ``go-gh``'s
    ``pkg/config/config.go``, which is the function the ``gh`` binary actually calls, and it
    is the order ``gh help environment`` prints for ``GH_CONFIG_DIR``. Both were read rather
    than remembered, because a rule guessed at here is the same bug facing the other way.

    **THE THIRD STEP IS THE WHOLE OF WHY THIS FUNCTION EXISTS.** Native Windows ``gh`` writes
    ``hosts.yml`` under ``%AppData%``, nowhere near ``~/.config``, so a lookup that knew only
    the Unix steps decided nobody was logged in on a machine where ``gh auth login`` had just
    succeeded. ``check`` then refused on the roster while ``submit``, which may ask the
    network, answered with the person's actual login: two verbs disagreeing about who you
    are, with no warning, because the WSL detector correctly says this is not WSL.

    **AND THE SECOND STEP COMES BEFORE THE THIRD ON WINDOWS TOO, WHICH LOOKS WRONG AND IS
    NOT.** ``XDG_CONFIG_HOME`` is read on every platform by ``go-gh``; only the ``AppData``
    branch is guarded by the operating system. A Windows researcher who has that variable
    set, from a dotfiles repository or from a runner that exports one, has a ``gh`` reading
    from it, and a lookup that jumped straight to ``%AppData%`` would miss them. Putting
    Windows first would also move the answer on macOS and Linux, which is the half of this
    that already worked.

    ``system`` and ``home`` are injected rather than read, so that every branch is reachable
    from a suite on any machine. ``home`` is ``None`` where there is no home directory to
    name, which is a container with no passwd entry: there is nowhere ``gh`` could have
    written a login, so there is no login, rather than an exception out of a lookup whose
    honest answer is "nobody".
    """
    declared = _declared(variables, "GH_CONFIG_DIR")
    if declared:
        return Path(declared)
    config_home = _declared(variables, "XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "gh"
    app_data = _declared(variables, "AppData")
    if system.casefold() == WINDOWS and app_data:
        return Path(app_data) / GH_WINDOWS_DIRECTORY
    if home is None:
        return None
    return home / ".config" / "gh"


def _declared(variables: Mapping[str, str], name: str) -> str:
    """One environment variable, under either spelling of its name, or the empty string.

    **THE SECOND SPELLING IS ABOUT ``AppData`` AND IS NOT DECORATION.** Windows environment
    variables are case-insensitive and Python's own ``os.environ`` upper-cases every key it
    holds on that platform, so the real mapping this is handed on Windows carries
    ``APPDATA``. ``go-gh`` asks for ``AppData`` and Go's ``os.Getenv`` is case-insensitive
    there, so that is the name worth writing down; a mapping a test hands over is an ordinary
    dictionary and is not. Reading one spelling would leave a lookup that passed against
    every fixture and found nothing on a Windows machine, which is the exact shape of failure
    this whole function is repairing.
    """
    for spelling in (name, name.upper()):
        value = variables.get(spelling, "").strip()
        if value:
            return value
    return ""


def _home() -> Path | None:
    """The real home directory, or nothing where the machine cannot name one."""
    try:
        return Path.home()
    except RuntimeError:
        return None


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

    directory = (
        config_home
        if config_home is not None
        else gh_config_directory(variables, system=platform.system(), home=_home())
    )
    if directory is None:
        return None
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
