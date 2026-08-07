"""How a sign-in URL gets to a browser, and the three ways that used to go wrong.

**THE THING UNDER TEST IS A CREDENTIAL'S ROUTE FROM ONE PROCESS TO ANOTHER**, so the cases are
ordered by what each one costs. A credential that leaks is first, a page that never opens is
second, and the wording is last. Nothing here launches a browser: :func:`open_a_browser` is the
one function that would, and it takes its opener as an argument for exactly that reason.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from edullm_platform.cli.browser import (
    HANDOFF_PREFIX,
    handoff_page,
    no_browser_here,
    open_a_browser,
    sweep_stale_handoffs,
    write_handoff,
)

#: Long enough to be the thing this module exists for, and shaped like what AWS mints.
A_URL = "https://studio-d-example.studio.us-east-1.sagemaker.aws/auth?token=" + "eyJab" * 800


# ---------------------------------------------------------------------------------------
# the one that costs a credential
# ---------------------------------------------------------------------------------------


def test_the_page_is_written_where_only_its_owner_can_read_it(tmp_path: Path) -> None:
    """THE ONE THAT MATTERS HERE. Mutation: write it with the default mode.

    The file holds a bearer credential: whoever reads it inside its life is signed in as that
    person. A default-mode file in a shared temporary directory is readable by every account on
    the machine, which is the leak the whole file-based route was chosen to avoid -- so a route
    that reintroduced it would be worse than the command line it replaced.
    """
    page = write_handoff(A_URL, directory=tmp_path)

    assert stat.S_IMODE(page.stat().st_mode) == 0o600
    assert stat.S_IMODE(page.parent.stat().st_mode) == 0o700
    assert A_URL in page.read_text(encoding="utf-8")


def test_the_url_never_reaches_a_command_line(tmp_path: Path) -> None:
    """Mutation: hand the URL to the opener instead of the file.

    ``argv`` is world-readable through ``ps`` for as long as the browser takes to start, and
    ``ShellExecute`` on Windows will not carry a URL this long anyway. What the opener is given
    has to be the short ``file://`` address and never the credential.
    """
    handed: list[str] = []
    page = write_handoff(A_URL, directory=tmp_path)

    assert open_a_browser(page, opener=lambda url: bool(handed.append(url)) or True)
    assert handed == [page.as_uri()]
    assert A_URL not in handed[0]
    assert len(handed[0]) < len(A_URL)


def test_a_stale_page_is_deleted_and_a_live_one_is_left_alone(tmp_path: Path) -> None:
    """Mutation: sweep everything, or sweep nothing.

    Sweeping everything deletes the page of a browser that is still starting, which is a person
    watching a blank tab. Sweeping nothing leaves a string that reads exactly like a live
    credential in a temporary directory for somebody to find in a support bundle later.
    """
    stale = write_handoff(A_URL, directory=tmp_path).parent
    fresh = write_handoff(A_URL, directory=tmp_path).parent
    os.utime(stale, (0, 0))

    swept = sweep_stale_handoffs(directory=tmp_path, older_than_seconds=300)

    assert swept == 1
    assert not stale.exists()
    assert fresh.exists()


def test_the_sweep_leaves_anything_it_did_not_write(tmp_path: Path) -> None:
    """Mutation: sweep on age alone.

    The default directory is the system temporary directory, which is everybody's. A sweep that
    matched on age would delete other programs' work, and would do it as a side effect of
    somebody opening a notebook.
    """
    somebody_elses = tmp_path / "important-build-cache"
    somebody_elses.mkdir()
    os.utime(somebody_elses, (0, 0))

    assert sweep_stale_handoffs(directory=tmp_path, older_than_seconds=0) == 0
    assert somebody_elses.exists()


def test_the_sweep_never_raises_over_a_directory_it_cannot_read(tmp_path: Path) -> None:
    """Mutation: let the error out.

    This runs on the way past something a person asked for. A permission error on a leftover
    directory belonging to somebody else is not a reason to fail to open their notebook.
    """
    assert sweep_stale_handoffs(directory=tmp_path / "nothing-here", older_than_seconds=0) == 0


# ---------------------------------------------------------------------------------------
# the one that costs a five-minute credential and a person's afternoon
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("named", ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"])
def test_an_ssh_session_is_never_handed_a_browser(named: str) -> None:
    """THE FAILURE THAT SUCCEEDS. Mutation: open one anyway.

    On a remote host that can reach a display, ``xdg-open`` opens a window nobody is sitting in
    front of and consumes the single-use sign-in. The person at the keyboard sees a success
    message and no browser, and their second attempt burns a second credential the same way.
    Three variables because OpenSSH sets different ones depending on how the session was made.
    """
    said = no_browser_here({named: "10.0.0.1 52814 10.0.0.2 22"})

    assert said is not None
    assert "SSH" in said


def test_a_desktop_session_is_handed_one() -> None:
    """The control. Mutation: refuse everywhere, which would make the flag the only route."""
    assert no_browser_here({"DISPLAY": ":0"}) is None


def test_an_opener_that_throws_is_a_refusal_rather_than_a_traceback(tmp_path: Path) -> None:
    """Mutation: let the platform backend's exception out.

    ``webbrowser`` raises out of its three backends in ways that are neither documented nor
    alike -- a missing ``osascript``, a ``WinError`` from ``startfile``, an absent ``xdg-open``.
    Every one of them means the same thing to the caller, which is that the URL has to be
    printed now, and a traceback is the one thing this binary promises never to show.
    """

    def explodes(url: str) -> bool:
        raise OSError("no browser here")

    assert open_a_browser(write_handoff(A_URL, directory=tmp_path), opener=explodes) is False


# ---------------------------------------------------------------------------------------
# what the page says
# ---------------------------------------------------------------------------------------


def test_the_page_moves_on_without_javascript() -> None:
    """Mutation: redirect with a script.

    A browser configured to refuse scripts still honours a meta refresh, and there is nothing
    in this page worth debugging. The anchor is what is left if even that is off -- a link to
    click, which is still not a string to select and copy.
    """
    page = handoff_page(A_URL)

    assert 'http-equiv="refresh"' in page
    assert "content=\"0; url=" in page
    assert f'href="{A_URL}"' in page
    assert "<script" not in page


def test_the_url_is_escaped_for_the_attribute_it_sits_in() -> None:
    """Mutation: interpolate it raw.

    Correct today, because a presigned URL is base64url and carries nothing to escape. That is
    a fact about AWS's token format rather than about this code, nobody here controls it, and
    no test would catch it changing -- which is the whole argument for not depending on it.
    """
    page = handoff_page('https://example.test/?a="><script>alert(1)</script>')

    assert "<script>alert(1)" not in page
    assert "&quot;" in page


def test_a_directory_is_made_per_invocation_so_two_at_once_cannot_collide(
    tmp_path: Path,
) -> None:
    """Mutation: write one well-known filename.

    Two shells opening two spaces at the same moment would otherwise have the second overwrite
    the first, and the first browser would redeem the second person's credential.
    """
    first = write_handoff(A_URL, directory=tmp_path)
    second = write_handoff(A_URL, directory=tmp_path)

    assert first != second
    assert first.parent.name.startswith(HANDOFF_PREFIX)
    assert second.parent.name.startswith(HANDOFF_PREFIX)
