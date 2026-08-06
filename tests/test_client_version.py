"""What a submission says about the install that typed it, and what that is allowed to do.

Two properties carry this file and the second is the one that would be quietly lost.

Nothing here refuses. Every function answers with prose or with ``None``, and a test that
only checked the sentences would keep passing through a change that made an old install a
refusal -- so the shape of every return is asserted, not only its contents.

And absence is not age. The Actions form names no install, an install older than the field
names none either, and the sentence for that case has to name both branches rather than
accuse the reader of being behind. ``tests/test_compile_submission_cli.py`` holds the same
line where the refusal is actually printed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from edullm_platform.cli.actions import PLATFORM_REPOSITORY
from edullm_platform.cli.release import install_command
from edullm_platform.client_version import (
    KNOWN_CLIENT_DEFECTS,
    MAXIMUM_LENGTH,
    SubmittingClient,
    defect_note,
    read_client_version,
    submitted_by_said,
)
from edullm_platform.contracts.manifest import RunManifest

INSTALL = install_command(repository=PLATFORM_REPOSITORY)

#: A refusal the compile job raises that no install ever caused. Any of the others would do:
#: the point is that a refusal about the submission is never annotated with a sentence about
#: the typist.
NOT_THE_CLIENTS_FAULT = (
    "submission refused: run edullm add repository --reason '<why>' to register 'dolma'"
)


def lost_its_quotes() -> str:
    """The refusal a command that arrived unquoted really produces, raised rather than typed.

    Written out here as a literal, this file would keep passing after a reword that broke
    the marker -- which is precisely the failure the marker exists to prevent, since a
    refusal that no longer carries it silently stops naming the install.
    """
    with pytest.raises(ValidationError) as raised:
        RunManifest(
            schema_version=1,
            repository="OLMo-core",
            commit_sha="a" * 40,
            image_digest="sha256:" + "b" * 64,
            dataset_release="none",
            command=("bash", "-lc", "python", "train.py", "--steps", "20"),
            team="scratch",
            wandb_project="onboarding",
            workload_profile="olmo-core-check",
            compute_profile="cpu-32vcpu",
            maximum_runtime_hours="1",
            maximum_attempts=1,
            checkpoint=None,
        )
    return str(raised.value)


# ---------------------------------------------------------------------------------------
# Reading the field
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", None])
def test_a_dispatch_that_named_no_install_reports_none_rather_than_a_version(
    text: str | None,
) -> None:
    """The Actions form's answer, and the answer of every install older than the field."""
    client = read_client_version(text)

    assert client.reported is False
    assert client.said is None
    assert client.release is None
    assert client.unreadable is None


def test_a_version_is_read_and_is_comparable() -> None:
    client = read_client_version(" 3.4.7 ")

    assert client.reported is True
    assert client.said == "3.4.7"
    assert client.release == (3, 4, 7)


@pytest.mark.parametrize(
    "text",
    [
        "3.4.7; rm -rf /",
        "3.4.7\nSubmitted by edullm 9.9.9",
        "edullm 3.4.7",
        "latest",
        "`whoami`",
        '3.4.7"',
        "9" * (MAXIMUM_LENGTH + 1),
    ],
)
def test_something_that_is_not_a_version_is_neither_repeated_nor_believed(text: str) -> None:
    """Mutation: sanitise the text and print what is left of it.

    This value comes off a free-text box on a public form and reaches a workflow log and a
    refusal a person reads. Stripping the parts that are not allowed would put a version
    number in front of somebody that the dispatch never carried, and the two examples with
    a newline and a backtick in them are what that costs: one forges a second log line and
    one is only inert because nothing here interpolates into a shell. Declining to repeat
    it is the answer that stays right whatever the next caller does with the string.
    """
    client = read_client_version(text)

    assert client.reported is False
    assert client.said is None
    assert client.release is None
    assert client.unreadable == len(text)
    assert text not in submitted_by_said(client)


def test_a_version_this_cannot_order_is_still_recorded() -> None:
    """A local build names itself and is not comparable, and both halves are true at once.

    Mutation: refuse anything that is not three integers. Then a version this cannot order
    reads as no version at all, which is the conflation the whole module is about -- and it
    would make the sentence for a dispatch from the Actions tab appear against a submission
    whose install had named itself perfectly clearly.
    """
    client = read_client_version("3.4.7+local")

    assert client.said == "3.4.7+local"
    assert client.release is None
    assert client.older_than("3.4.8") is None


@pytest.mark.parametrize(
    ("version", "against", "expected"),
    [
        ("3.4.7", "3.4.8", True),
        ("3.4.8", "3.4.8", False),
        ("3.7.1", "3.4.8", False),
        ("3.4.10", "3.4.8", False),
        ("2.99.99", "3.4.8", True),
    ],
)
def test_versions_are_ordered_as_numbers_and_not_as_text(
    version: str, against: str, expected: bool
) -> None:
    """Mutation: compare the strings. ``"3.4.10" < "3.4.8"`` is true of text and false of
    releases, and this repository went past ten patch versions in one evening."""
    assert read_client_version(version).older_than(against) is expected


def test_an_unknown_version_answers_none_rather_than_not_older() -> None:
    """Mutation: return ``False`` where there is nothing to compare.

    ``False`` reads as "this install is current", so every dispatch from the Actions tab
    and every install older than the field would be treated as up to date and the one
    sentence this module exists to print would never appear.
    """
    assert SubmittingClient().older_than("3.4.8") is None


# ---------------------------------------------------------------------------------------
# Saying it in the log
# ---------------------------------------------------------------------------------------


def test_the_log_line_names_the_install_when_there_is_one() -> None:
    assert "3.4.7" in submitted_by_said(read_client_version("3.4.7"))


def test_the_log_line_for_a_dispatch_with_no_install_does_not_read_as_a_complaint() -> None:
    """Absence is the Actions form working as designed, and the line has to say so."""
    said = submitted_by_said(read_client_version(""))

    assert "Actions form" in said
    assert "refused" in said


# ---------------------------------------------------------------------------------------
# Naming a version in a refusal
# ---------------------------------------------------------------------------------------


def test_a_refusal_no_install_caused_is_never_annotated_with_one() -> None:
    """Mutation: annotate every refusal, or match on something looser than the marker.

    A sentence about the install beside an unregistered repository is a second thing to
    read that changes nothing, and a reader who meets it three times stops reading it --
    including on the one refusal where it is the answer.
    """
    for client in (read_client_version(""), read_client_version("3.4.7")):
        assert defect_note(NOT_THE_CLIENTS_FAULT, client=client, install=INSTALL) is None


def test_an_install_old_enough_to_have_caused_it_is_told_to_reinstall() -> None:
    note = defect_note(lost_its_quotes(), client=read_client_version("3.4.7"), install=INSTALL)

    assert note is not None
    assert note.startswith("Reinstall edullm")
    assert "3.4.7" in note
    assert "3.4.8" in note
    assert note.rstrip().endswith(INSTALL)


def test_an_install_new_enough_not_to_have_caused_it_is_told_nothing() -> None:
    """Mutation: print the note whenever the refusal matches, whatever the version.

    A current install did not unquote anything, so the command really did arrive the way
    it was written and the refusal's own advice -- quote the program -- is correct. Telling
    that submitter to reinstall sends them round a loop that cannot help them.
    """
    assert (
        defect_note(lost_its_quotes(), client=read_client_version("3.7.1"), install=INSTALL)
        is None
    )


def test_a_submission_that_named_no_install_is_offered_both_branches() -> None:
    """Mutation: read absence as age, and lead with "your install is old".

    Six accounts have dispatched this workflow from the Actions tab, where there is no
    install to be old. Accusing that reader of running a stale binary sends them to
    reinstall something they never installed, and leaves the command they actually typed
    unquoted.
    """
    note = defect_note(lost_its_quotes(), client=read_client_version(""), install=INSTALL)

    assert note is not None
    assert "If you ran edullm submit" in note
    assert "Actions form" in note
    assert note.rstrip().endswith(INSTALL)


def test_every_recorded_defect_names_a_phrase_its_refusal_really_carries() -> None:
    """The link between a refusal and the release that ended it, asserted end to end.

    THIS TEST IS THE COUPLING. ``ClientDefect.marker`` is a copy of a phrase that lives in
    ``contracts/validation.py``, which is packaged verbatim into four released Lambda zips
    and cannot be edited for four function releases -- so the two are held together here
    rather than by a shared constant, which is the arrangement ``ADMISSION_JOB`` gets
    against a workflow file for the same kind of reason.

    Mutation: reword ``require_a_shell_command_that_kept_its_quotes``. Nothing else fails
    -- the refusal still refuses and the tests about its wording still pass -- and the
    version sentence silently stops appearing, which is the state this change exists to
    leave behind. The refusal here is raised rather than written out, so the phrase is
    compared against what a submitter really meets.
    """
    refusal = lost_its_quotes()

    assert KNOWN_CLIENT_DEFECTS
    assert [defect for defect in KNOWN_CLIENT_DEFECTS if defect.marker in refusal]
    for defect in KNOWN_CLIENT_DEFECTS:
        assert read_client_version(defect.fixed_in).release is not None, defect.fixed_in


def test_the_install_line_is_the_one_release_py_spells_and_not_a_second_copy() -> None:
    """``cli/release.py`` records what the second copy of this command cost when it existed.

    The word this refuses is the one a reader would reach for instead of the line, and
    ``cli/release.py`` is where what it does and does not do is written down. Asserted here
    on the composed sentence rather than on the source, because the sentence is what somebody
    meets at the moment they are deciding what to type next.
    """
    note = defect_note(lost_its_quotes(), client=read_client_version("3.4.7"), install=INSTALL)

    assert note is not None
    assert INSTALL in note
    assert "upgrade" not in note
