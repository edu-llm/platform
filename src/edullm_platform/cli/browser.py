"""How a sign-in URL reaches a browser, which is the thing printing it never did.

**THE VERB PRINTED A FOUR-THOUSAND-CHARACTER CREDENTIAL AND ASKED A PERSON TO CARRY IT.** That
is the failure this module exists to end, and it is worth stating in its measured form because
every one of the three reported symptoms is a variant of it. On 2026-08-06 the URL
``edullm studio`` printed was 4,251 characters; it lived 300 seconds, which
:data:`~edullm_platform.cli.studio.PRESIGNED_URL_SECONDS` records is the service's hard ceiling
and not a setting; and AWS documents what happens to a late one -- *"If you try to use the URL
after the timeout limit expires, you are directed to the Amazon Web Services console sign-in
page"* -- which is precisely the page people said they were being asked to sign in to, with
credentials this organisation issues nobody.

Selecting four thousand characters out of a terminal, without the line breaks the terminal drew
into them, is not a thing that reliably works. So the fix is not a shorter URL or a longer life,
neither of which is available: it is that **the process that mints the credential is the process
that spends it**, a few hundred milliseconds later, with no human in between.

**IT GOES THROUGH A FILE ON DISK RATHER THAN A COMMAND LINE, AND THERE ARE THREE REASONS.**
The obvious route is to hand the URL to ``open``, ``xdg-open`` or ``start`` as an argument.

1. **A command line is public.** ``argv`` is readable by every process on the machine -- ``ps``
   prints it. A presigned Studio URL is a bearer credential: whoever holds it is signed in as
   that person for twelve hours. Handing one to a subprocess publishes it to every other user
   on a shared box for as long as the browser takes to start. :func:`write_handoff` writes
   ``0600`` into a ``0700`` directory instead, so the credential is readable by its owner.
2. **Windows will not carry it.** ``os.startfile`` reaches ``ShellExecute``, whose URL argument
   is bounded by ``INTERNET_MAX_URL_LENGTH`` at 2,083 characters. The URL is twice that. There
   is no quoting that fixes a length limit.
3. **One code path beats three.** ``cmd.exe`` splits on ``&``, POSIX shells split on
   whitespace, and every platform has a different limit and a different escape. A ``file://``
   URL naming a short path has none of those properties anywhere, so macOS, Windows and Linux
   run the same two lines rather than three branches nobody can test all of.

Today's URLs happen to carry no ``&`` and no ``%`` -- one ``token=`` parameter and nothing
else -- so reason 3 is insurance rather than a live bug. Reasons 1 and 2 are live.

**NOTHING HERE OPENS A BROWSER ON A MACHINE THAT HAS NOBODY LOOKING AT IT.**
:func:`no_browser_here` is the guard, and it is not a nicety: over SSH, ``xdg-open`` either
fails or -- worse -- opens a window on the *remote* desktop, where the sign-in is consumed by
nobody and the person at the keyboard sees a success message and no browser. That burns the URL,
and the second attempt is a second five-minute credential going the same way. On a machine that
cannot show a page, the URL is printed and said to have been printed.
"""

from __future__ import annotations

import html
import os
import shutil
import tempfile
import time
import webbrowser
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Protocol

__all__ = [
    "HANDOFF_PREFIX",
    "Opener",
    "handoff_page",
    "no_browser_here",
    "open_a_browser",
    "sweep_stale_handoffs",
    "write_handoff",
]

#: What every directory this module makes is called, so a sweep can find its own leavings and
#: nothing else. Under the system temporary directory rather than the home directory, because
#: the file is dead in five minutes and a home directory is where things accumulate unnoticed.
HANDOFF_PREFIX: Final = "edullm-open-"


class Opener(Protocol):
    """Whatever actually shows a page, so a test can be handed one that shows nothing.

    :func:`webbrowser.open`'s own signature, narrowed to the one argument this passes. Injected
    for ``notifications/delivery.py``'s reason: a default that is the real thing means a test
    supplying its own gets the code path the person gets, rather than a branch that exists only
    under test.
    """

    def __call__(self, url: str) -> bool: ...


def handoff_page(url: str) -> str:
    """The one-line document whose only job is to become somewhere else.

    A ``meta refresh`` at zero seconds rather than JavaScript, because a browser configured to
    refuse scripts still honours it, and because there is nothing here worth debugging. The
    anchor underneath is what somebody sees if even that is disabled -- a link they can click,
    which is still not a string they have to select and copy.

    **THE URL IS ESCAPED FOR AN ATTRIBUTE, WHICH IS NOT A THEORETICAL CONCERN ABOUT THIS INPUT.**
    A presigned URL is a base64url token today and carries nothing ``html.escape`` would touch.
    It is escaped because the alternative is a function whose correctness depends on a fact
    about AWS's token format that nobody here controls and no test would catch changing.
    """
    quoted = html.escape(url, quote=True)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        '<meta charset="utf-8">\n'
        f'<meta http-equiv="refresh" content="0; url={quoted}">\n'
        "<title>Opening SageMaker Studio</title>\n"
        "<body style=\"font: 16px system-ui, sans-serif; margin: 4rem\">\n"
        "<p>Opening SageMaker Studio&hellip;</p>\n"
        f'<p><a href="{quoted}">Continue</a> if this page does not move on its own.</p>\n'
        "</body>\n"
        "</html>\n"
    )


def no_browser_here(environ: Mapping[str, str]) -> str | None:
    """Why a browser must not be opened on this machine, or ``None`` where one may be.

    Two cases and they fail differently, so they are said differently.

    **An SSH session is the dangerous one**, because it can succeed. ``xdg-open`` on a remote
    host with a display reachable to it opens a window nobody is sitting in front of, consumes
    the single-use sign-in, and reports success to a person looking at a terminal on another
    continent. Detected on the three variables OpenSSH sets on the server side.

    **A Linux box with no display is the ordinary one**: nothing can be shown, ``xdg-open``
    fails, and the useful thing to do is print. macOS and Windows are never in this case --
    both always have a window server -- so the check is scoped to the platform where the
    variables mean anything rather than applied everywhere and wrong twice.
    """
    if any(environ.get(named) for named in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        return (
            "this is an SSH session, so a browser opened here would open on the machine you "
            "are logged in to rather than the one you are sitting at"
        )
    headless = not environ.get("DISPLAY") and not environ.get("WAYLAND_DISPLAY")
    if os.name == "posix" and not _is_macos() and headless:
        return "this machine has no display, so there is no browser here to open"
    return None


def _is_macos() -> bool:
    """macOS, which has a window server whether or not ``DISPLAY`` is set.

    Its own function so :func:`no_browser_here` reads as the ruling it is, and so the one
    platform test in this module has one place to be wrong.
    """
    import sys

    return sys.platform == "darwin"


def write_handoff(url: str, *, directory: Path | None = None) -> Path:
    """Put the page somewhere private and hand back where, as a path.

    ``mkdtemp`` rather than ``mkstemp`` in a shared directory: it creates the directory at
    ``0700`` in one syscall, which is the property that matters, and it gives the sweep in
    :func:`sweep_stale_handoffs` a single thing to remove rather than a file whose directory is
    everybody's. The file inside is written ``0600`` before the URL goes into it -- opened
    through :func:`os.open` with the mode, rather than written and then ``chmod``-ed, because
    the second order leaves a window in which the credential is on disk world-readable.

    ``directory`` is for tests and for nothing else; the default is the system temporary
    directory, which is what gets cleaned up by the operating system when this fails to.
    """
    home = Path(tempfile.mkdtemp(prefix=HANDOFF_PREFIX, dir=None if directory is None else directory))
    page = home / "open.html"
    descriptor = os.open(page, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(handoff_page(url))
    return page


def sweep_stale_handoffs(
    *, directory: Path | None = None, older_than_seconds: float, now: float | None = None
) -> int:
    """Delete the hand-off directories of previous invocations, and say how many.

    **THE CREDENTIAL IN ONE OF THESE IS DEAD LONG BEFORE IT IS DELETED, AND IT IS STILL WORTH
    DELETING.** A presigned URL expires in five minutes, so a file older than that is inert; what
    it is not is invisible. Somebody reading a bug report, a backup or a support bundle finds a
    string that looks exactly like a live credential and has to work out that it is not, and the
    honest way to spare them that is not to keep it.

    Never raises. A sweep runs on the way past something a person actually asked for, and a
    permission error on somebody else's leftover directory is not a reason to fail opening a
    notebook. Directories that vanish underneath it -- two invocations sweeping at once -- are
    the same non-event.
    """
    root = Path(tempfile.gettempdir()) if directory is None else directory
    moment = time.time() if now is None else now
    swept = 0
    try:
        candidates = list(root.iterdir())
    except OSError:
        return 0
    for candidate in candidates:
        if not candidate.name.startswith(HANDOFF_PREFIX) or not candidate.is_dir():
            continue
        try:
            if moment - candidate.stat().st_mtime <= older_than_seconds:
                continue
            shutil.rmtree(candidate)
        except OSError:
            continue
        swept += 1
    return swept


def open_a_browser(page: Path, *, opener: Opener | None = None) -> bool:
    """Show that page, and say whether anything claimed to.

    The argument is a ``file://`` URL made by :meth:`~pathlib.Path.as_uri`, which is the one
    spelling all three platforms take: ``os.startfile`` resolves it through ``ShellExecute``,
    ``osascript``'s ``open location`` requires a URL rather than a path, and ``xdg-open`` accepts
    either. Handing a bare Windows path to the macOS backend is the bug this avoids.

    **``True`` MEANS SOMETHING WAS LAUNCHED AND NEVER THAT SOMEBODY SAW IT.** ``webbrowser``
    reports whether it found a browser to hand the page to, not whether that browser rendered.
    The caller has to say what it did in words the person can check against their own screen,
    which is why :mod:`edullm_platform.cli.main` prints a sentence rather than declaring success.

    Every exception is a ``False``. ``webbrowser`` raises out of the platform backends in ways
    that are neither documented nor uniform -- a missing ``osascript``, a ``WinError`` from
    ``startfile``, an ``xdg-open`` that is not there -- and each of them means the same thing
    to the caller, which is that the URL now has to be printed instead.
    """
    show = webbrowser.open if opener is None else opener
    try:
        return bool(show(page.as_uri()))
    except Exception:  # noqa: BLE001 -- the platform backends raise whatever they like
        return False
