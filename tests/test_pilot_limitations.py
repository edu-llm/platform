"""The pilot limitations page, read out of the file a reader will actually open.

Three people who did not build this platform are about to be given access to it, and the
adoption rules make a written limitations page the precondition for that: a pilot whose
users have not read what is missing is not a pilot, it is an unannounced release. The page
is also the reason the phase's remaining checks are allowed to wait, so it carries weight
a paragraph in a proof bundle does not.

**These tests read the shipped ``README.md`` and never a fixture, and that is the whole
design.** A limitations page that is true in a test and absent from the file a reader opens
is worse than no page at all: the suite goes green, the rung is recorded as reachable, and
the reader is still told nothing. A fixture here would assert that somebody once wrote the
right sentence somewhere, which is not the claim anybody needs to be able to make.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
README_PATH = PROJECT_ROOT / "README.md"

SECTION_HEADING = "## Pilot limitations"

#: Matched word for word and mark for mark rather than loosely. See
#: ``test_the_cancellation_wording_is_the_sentence_a_reader_can_act_on`` for why the
#: wording is fixed here instead of being left to whoever next edits the page.
CANCELLATION_SENTENCE = (
    "Cancelling the workflow does not stop the job; ask an admin, and note the mandatory "
    "timeout bounds it."
)

#: What must never reach a page written for people outside the account. Every committed
#: capture in this repository has its account id masked by a tool; a page somebody types
#: by hand is the one place in the tree where the unmasked value has nothing standing in
#: its way.
DISCLOSURES = ("056956104102", "AKIA", "aws_secret_access_key")


def readme_text() -> str:
    return README_PATH.read_text(encoding="utf-8")


def limitations_section() -> str:
    """The section on its own, so no test here can be satisfied by prose elsewhere.

    The rest of this README already discusses cancellation, the mandatory attempt timeout
    and the empty team bindings, in the language of phases and acceptance criteria. A test
    that read the whole file would therefore pass on exactly the material the pilot page
    exists to replace, and would go on passing if the page were deleted.
    """
    text = readme_text()
    assert SECTION_HEADING in text, (
        f"{README_PATH.name} has no {SECTION_HEADING!r} section, so a pilot user has "
        "nowhere to read what is missing before they are given access"
    )
    return text.split(SECTION_HEADING, 1)[1].split("\n## ", 1)[0]


def limitations_prose() -> str:
    """The section as a reader meets it, with markdown's soft wraps closed up.

    The page is hard-wrapped, so a sentence longer than a line carries a newline that
    renders as a space. Matched against the raw file, a fixed sentence would be pinning
    where the lines happen to break as well as what it says, and rewrapping a paragraph is
    not editing it. A test that failed on a rewrap is one people learn to work around,
    which would cost the sentence below the protection it is here to have.
    """
    return " ".join(limitations_section().split())


def test_the_readme_carries_a_pilot_limitations_section() -> None:
    assert SECTION_HEADING in readme_text()


def test_the_page_names_the_three_things_a_pilot_user_has_to_know() -> None:
    """Together rather than one test each, because the set is what makes the page a page.

    Any one of these alone reads as a thorough disclosure and leaves the reader exposed on
    the other two: they will lose an optimizer state they assumed was saved, wait for a
    cancellation that is never coming, or read a team field as an access control. A page
    naming two of the three is the failure worth catching, and a page naming two of the
    three passes every test that checks them separately.
    """
    section = limitations_prose()

    assert "optimizer" in section, "the page does not mention the optimizer state a resume loses"
    assert "batch:TerminateJob" in section, "the page does not say cancellation cannot happen"
    assert "`team`" in section, "the page does not mention the team field at all"


def test_the_cancellation_wording_is_the_sentence_a_reader_can_act_on() -> None:
    """Verbatim, because in this one case the wording is the substitution.

    This limitation stands in for a check nobody is going to build before the pilot opens,
    and a written limitation may only substitute for a missing check when the reader can
    act on it. "Ask an admin" is the next step and "the mandatory timeout bounds it" is
    the worst case if nobody takes it; between them a reader who has given up on a run
    knows what to do and what it costs to do nothing. A softer paraphrase --
    "cancellation is not supported" -- is true, shorter, and hands the reader neither
    half, which is a disclosure rather than a substitute. So the sentence is pinned here
    instead of being left to whoever next tightens the prose.
    """
    assert CANCELLATION_SENTENCE in limitations_prose()


def test_the_page_says_the_team_field_routes_approval_rather_than_granting_anything() -> None:
    """In those terms, because "team" reads as an access control to everybody who has met one.

    A reader who takes it for one will assume naming their own team keeps their outputs
    away from everyone else's, and will assume naming somebody else's would be refused.
    Neither is true: the value routes the approval and is recorded, and nothing is checked
    against it.
    """
    section = limitations_prose()

    assert "routes" in section
    assert "not a permission" in section


def test_the_page_does_not_promise_a_checkpoint_can_resume_training() -> None:
    """The checkpoint protocol calls a checkpoint resumable, and it means something narrower.

    ``edullm_platform.checkpoints`` reports COMMITTED when a success marker certifies the
    payload beside it, which establishes that the bytes are whole. It says nothing about
    what is in them, and what is in them is a model state dict and a step number. A page
    that repeated the word without the qualification would tell a researcher their long
    run can pick up where it left off.
    """
    section = limitations_prose()

    assert "cannot resume training" in section
    assert "starts the optimizer cold" in section


@pytest.mark.parametrize("disclosure", DISCLOSURES)
def test_the_page_discloses_no_account_id_and_no_credential(disclosure: str) -> None:
    """The whole file, not the section, because the reason to scan does not stop at a heading.

    This is the only document in the repository written for people outside the team that
    built it, and the pilot page is the part of it most likely to grow a worked example --
    a queue arn, a console link, a copied command with a profile in it. Scanning only the
    section would leave the paste one heading away from being unchecked.
    """
    assert disclosure not in readme_text()
