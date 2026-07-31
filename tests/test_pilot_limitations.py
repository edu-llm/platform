"""The three things a submitter has to know, and where they are now said.

**The page was taken out of the README on 2026-07-31 and is not coming back.** It moved to a
local, gitignored document on the owner's standing decision that the README is a public
artifact and a candid list of what this platform has not finished is not. That decision is
settled; what follows is how the requirement behind it is met anyway.

**The requirement was never the page.** It was that a pilot user learns three things before
being caught out by them: that cancelling does not stop a job, that a checkpoint omits
optimizer state, and that ``team`` routes an approval rather than granting a permission. A
README section was one way to deliver that, and on reflection a weak one -- it puts the facts
where somebody has already decided to go looking, and nobody looks before their first run.

**So they are printed in the run summary instead, and that is strictly better.** Every
accepted submission ends on the "Your run" step summary; it is the one page in this system
every submitter reads, at the moment each of these beliefs would otherwise form wrong. The
tests below hold the text there. Five tests that read the README section were deleted when it
left, and these replace them against the surface that now carries the facts.

**What stayed a gap, honestly.** Nothing here claims the full page is readable, because it is
not. The bundle records that the candid assessment is private and that the operational subset
is delivered at the point of use, which is what is true.
"""

from __future__ import annotations

import re
from pathlib import Path

from workflow_support import load_workflow

from edullm_platform.evidence import scan_for_secrets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
README_PATH = PROJECT_ROOT / "README.md"
SUBMIT_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml"

SECTION_HEADING = "## Pilot limitations"


def readme_text() -> str:
    return README_PATH.read_text(encoding="utf-8")


def summary_step_script() -> str:
    """The step that writes the "Your run" summary, as one string to search.

    Read out of the workflow rather than out of a rendered summary because rendering one
    needs a submission to have been admitted. What is asserted is that the text is in the
    step that always runs on a successful submission, which is the property that matters:
    there is no branch of that step where a submitter is shown the table without it.
    """
    jobs = load_workflow(SUBMIT_WORKFLOW_PATH)["jobs"]
    steps = [step for job in jobs.values() for step in job.get("steps", [])]
    matching = [step for step in steps if "## Your run" in str(step.get("run", ""))]
    assert len(matching) == 1, (
        f"expected exactly one step to write the run summary, found {len(matching)}. If it "
        "was split, point this helper at the one a submitter reads."
    )
    # Adjacent Python string literals rejoined, so the assertions below read the sentence a
    # submitter sees rather than the source lines it happens to be typed across. Without this
    # a reflow of the heredoc breaks tests that are about wording, which trains the next
    # person to weaken the assertion instead of looking at what changed.
    return re.sub(r'"\s*\n\s*"', "", str(matching[0]["run"]))


def test_the_readme_does_not_carry_the_section_the_record_says_it_lost() -> None:
    """Mutation: restore the section and leave the record saying it is private.

    **This is the tripwire the five deleted tests cost, pointed the other way, and it is here
    because the defect it catches happened within the hour.** The section was removed, an
    editor with the file open wrote its buffer back, and the section returned -- while the
    criteria went on reporting that the page is private, the Phase 5 paragraph in that same
    README went on saying the page was taken out, and the proof bundle went on recording
    both. Nothing failed, because the only tests that read the section had just been deleted.

    A recorded absence needs a check as much as a recorded presence does. If the section comes
    back, this fails, which is the signal that the record and the tree disagree and somebody
    has to say which one is right.
    """
    assert SECTION_HEADING not in readme_text(), (
        f"{README_PATH.name} carries {SECTION_HEADING!r} again, but the Phase 5 record says "
        "the page is private and delivered through the run summary instead. Either take the "
        "section back out, or re-cut criterion 11 and say the decision changed."
    )


def test_a_submitter_is_told_that_cancelling_does_not_stop_the_job() -> None:
    """Mutation: drop it, or soften it to "cancellation is best effort".

    The most expensive of the three and the only one that costs money while being wrong
    about it. A person who cancels a workflow and believes the spend stopped has no reason
    to check, so this is not discovered by observation -- it is discovered on the bill.

    ``batch:TerminateJob`` is held by no identity in this account, so the sentence is
    accurate rather than cautious, and it names the action a submitter can actually take.
    """
    script = summary_step_script()

    assert "Cancelling the workflow does not stop your job" in script
    assert "batch:TerminateJob" in script
    assert "Ask an admin" in script


def test_a_submitter_is_told_the_checkpoint_leaves_the_optimizer_behind() -> None:
    """Mutation: drop it, or describe it as a checkpoint without qualification.

    The one that silently corrupts a result rather than failing. A resumed run whose
    optimizer restarted cold produces a loss curve that looks like training and is not the
    training that was intended, and nothing in the system reports it.
    """
    script = summary_step_script()

    assert "checkpoint does not include optimizer state" in script
    assert "restarts its optimizer cold" in script


def test_a_submitter_is_told_that_team_routes_approval_rather_than_granting_access() -> None:
    """Mutation: drop it, or let the wording imply the field is an access control.

    The one that produces a wrong mental model of the whole platform. ``team`` is recorded
    and routed on; it is not enforced, and no submission is refused for naming a team the
    submitter is not on. A user who believes otherwise treats a mis-routed request as a
    permissions problem and asks for access nobody needs to grant.
    """
    script = summary_step_script()

    assert "does not grant you anything" in script
    assert "Any lead may approve any run" in script


def test_all_three_are_on_the_page_every_accepted_submission_ends_on() -> None:
    """Mutation: move one of them to a page of its own, or behind a conditional.

    The delivery mechanism is the claim being made. Each sentence above could be true of a
    document nobody opens; what makes this an improvement on the README section is that the
    three arrive together, unavoidably, on the summary a submitter is already reading to find
    their run id. Split them up and that property is gone while every test above still passes.
    """
    script = summary_step_script()
    heading = "### Three things this does not do yet"

    assert heading in script
    body = script.split(heading, 1)[1]
    assert "Cancelling the workflow does not stop your job" in body
    assert "checkpoint does not include optimizer state" in body
    assert "does not grant you anything" in body


def test_the_first_gpu_checkpoint_under_a_new_team_is_warned_about_and_only_it_is() -> None:
    """Mutation: print it to every run, or drop it and leave criterion 6 a gap.

    This is what Phase 5 criterion 6's deferral is granted in exchange for. No GPU run
    claiming a team other than ``platform`` has written a checkpoint here: the permission is
    in place and the machinery is proven, but only under ``platform``, so the combination is
    untested rather than known broken. The harm if it does not work is a run that trains to
    completion and saves nothing, discovered after the money is spent.

    **Both halves are asserted, and the conditional half is the one that keeps it read.** A
    warning shown to every submission is a warning nobody reads by the third run, which
    would satisfy the deferral's letter while destroying what it was for. So this also fails
    if the guard is removed.
    """
    script = summary_step_script()

    assert 'if manifest.checkpoint is not None and manifest.team != "platform":' in script
    assert "may be the first to write a checkpoint on a GPU" in script
    assert "trains to completion and saves nothing" in script


def test_the_page_discloses_no_account_id_and_no_credential() -> None:
    """The whole file, and it always was, which is why this one survives the page leaving.

    This is still the only document in the repository written for people outside the team
    that built it, so it is still the one most likely to grow a worked example -- a queue
    arn, a console link, a copied command with a profile in it. The scan never depended on
    the limitations section existing; it reads the README end to end.

    **Through the shared scanner rather than against a list of literals, and the first
    attempt at this test is why.** It named the account id it was looking for, which put the
    account id in a tracked file, which is the disclosure it existed to prevent -- caught in
    CI by ``test_evidence.py`` and not locally, because that check reads the tracked tree and
    the new file was not yet added. Asking ``scan_for_secrets`` is both shorter and stronger:
    it refuses any twelve-digit run, any access key id, any secret key and any session token,
    rather than the three spellings somebody thought of.
    """
    scan_for_secrets(readme_text())


def test_the_summary_a_submitter_reads_discloses_no_account_id_and_no_credential() -> None:
    """The same scan, against the surface that now carries the facts the README used to.

    The reason to repeat it is that this text is new, is written for the same outside
    audience, and sits in a workflow file next to role arns and queue names -- which is
    precisely the neighbourhood where a worked example gets pasted in to make a sentence
    concrete.
    """
    scan_for_secrets(summary_step_script())
