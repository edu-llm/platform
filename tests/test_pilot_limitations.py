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

from edullm_platform.evidence import scan_for_secrets

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

#: The paragraph the queue limitation lives in, named by its opening so that the tests
#: below assert something about one paragraph rather than about the page.
QUEUE_PARAGRAPH_LEAD = "**Nobody is watching the queue for you.**"

#: Matched word for word, like the cancellation sentence and for the same reason. See
#: ``test_the_queue_paragraph_says_a_run_that_never_placed_still_kept_its_record``.
QUEUE_INTENT_SENTENCE = (
    "Your run intent and the decision to admit it are written before Batch is reached at "
    "all, so a run that never places loses nothing but time and still has a record under "
    "its run id."
)

#: The next step, which is what makes the paragraph a substitute rather than a disclosure.
QUEUE_NEXT_STEP_SENTENCE = (
    "If a run has not started within an hour, ask, and quote the run id."
)


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


def queue_paragraph() -> str:
    """The one paragraph about a job that cannot get capacity, with soft wraps closed up.

    There has to be exactly one, and that is asserted here rather than assumed. The page
    already told a reader that nobody is watching the queue; what it owes them now is the
    other half, and the cheap way to add a half is a second paragraph beside the first. Two
    paragraphs on the same subject are how a reader ends up acting on whichever one they
    stopped at, so the shape is checked where the wording is.
    """
    paragraphs = [
        prose
        for prose in (" ".join(block.split()) for block in limitations_section().split("\n\n"))
        if prose.startswith(QUEUE_PARAGRAPH_LEAD)
    ]

    assert len(paragraphs) == 1, (
        f"{len(paragraphs)} paragraphs of the pilot limitations page open "
        f"{QUEUE_PARAGRAPH_LEAD!r}; the queue limitation is one paragraph so that a reader "
        "cannot act on half of it"
    )
    return paragraphs[0]


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


def test_the_queue_paragraph_says_a_run_that_never_placed_still_kept_its_record() -> None:
    """The page owes this because criterion 9 stopped being a check on 2026-07-31.

    That criterion asked for a capacity failure to be surfaced without losing the run
    intent, and it moved to Phase 8 with the queue-wait detector it cannot be closed
    without. What a transferred check would have protected is owed to this page instead, on
    the same terms Phase 3's cancellation checks were transferred on: in words a reader can
    act on rather than as a disclosure.

    The half that is already true is the half worth writing down. Admission records the
    intent and the decision before Batch is reached at all, so a job that never places costs
    an afternoon and nothing else -- and a reader who has not been told that has no way to
    tell a run that is waiting from a run that was dropped, which are the same thing to look
    at and very different things to have happened.

    Asserted inside the queue paragraph rather than against the section, and the mutation is
    why. A second paragraph restating that nobody is watching would satisfy every check made
    against the whole page, tell the reader the same thing twice, and put the new half
    somewhere they may never reach.
    """
    paragraph = queue_paragraph()

    assert QUEUE_INTENT_SENTENCE in paragraph
    assert QUEUE_NEXT_STEP_SENTENCE in paragraph
    assert "`RUNNABLE`" in paragraph, (
        "the paragraph no longer says what a job that cannot be placed actually does"
    )
    assert "no alarm notices" in paragraph, (
        "the paragraph no longer says that nothing is watching for it"
    )


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


def test_the_page_says_a_gpu_checkpoint_under_a_new_team_has_never_been_written() -> None:
    """The page owes this because criterion 6 stopped being a check on 2026-07-31.

    That criterion asked for a GPU run claiming a team other than ``platform`` to write its
    checkpoint, and it moved to Phase 6's closeout as a deferral. A deferral may never be
    pilot-blocking, so what it would have protected is owed to this page instead -- and the
    harm it guards is a real one: an unwritable checkpoint fails at the end of a GPU run,
    after the money is spent.

    Written as something a reader can do rather than as a disclosure, which is the test a
    written limitation has to pass before it may substitute for a check. Both halves are
    built and proved separately -- the workload role reaches every team's prefix, and Phase
    4's GPU runs wrote checkpoints under ``platform`` -- so the reader is not being told to
    expect failure. They are being told they are the first, and to look.
    """
    section = limitations_prose()

    assert "`platform`" in section, (
        "the page does not name the one team every GPU run so far has claimed"
    )
    assert "first" in section
    assert "checkpoint landed" in section, (
        "the page mentions the gap without telling the reader what to check"
    )


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


def test_the_page_discloses_no_account_id_and_no_credential() -> None:
    """The whole file, not the section, because the reason to scan does not stop at a heading.

    This is the only document in the repository written for people outside the team that
    built it, and the pilot page is the part of it most likely to grow a worked example --
    a queue arn, a console link, a copied command with a profile in it. Scanning only the
    section would leave the paste one heading away from being unchecked.

    **Through the shared scanner rather than against a list of literals, and the first
    attempt at this test is why.** It named the account id it was looking for, which put the
    account id in a tracked file, which is the disclosure it existed to prevent -- caught in
    CI by ``test_evidence.py`` and not locally, because that check reads the tracked tree and
    the new file was not yet added. Asking ``scan_for_secrets`` is both shorter and stronger:
    it refuses any twelve-digit run, any access key id, any secret key and any session token,
    rather than the three spellings somebody thought of.
    """
    scan_for_secrets(readme_text())
