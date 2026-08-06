"""The producer of the message #337 taught the notifier to render.

Nothing in this repository sent that envelope, so the renderer was reachable only by hand and
what a lead actually received was GitHub's own deployment notification. These cases are about
the join: that the document the compile job writes is the document the message is built from,
that the reader in the notifier accepts what the builder emits, and that the one field the
builder adds cannot come from a submitter.

**The compiled submission here is compiled, not written down.** ``compile_form`` runs the real
``tools/compile_submission.py`` against the real ``config/``, so the comparison between what
the compiler writes and what the builder reads is made against the compiler rather than
against a fixture copy of it. A fixture would go on agreeing with this module for as long as
somebody remembered to edit both, which is the failure the module is for.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from test_compile_submission_cli import EXIT_OK, compile_form, form

from edullm_platform.notifications.approval import (
    APPROVAL_DETAIL_TYPE,
    PLATFORM_EVENT_SOURCE,
    load_policy,
    read_approval_requested,
)
from edullm_platform.notifications.facts import Catalogs
from edullm_platform.notifications.messages import render_approval_requested
from edullm_platform.run_history import load_run_history
from tools.build_approval_envelope import (
    EXIT_NOT_OWED,
    EXIT_UNUSABLE,
    SUBMISSION_FIELDS,
    URL_FIELD,
    EnvelopeError,
    envelope_for,
    main,
    run_url,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config"
EVENTS = PROJECT_ROOT / "fixtures" / "events"

SERVER_URL = "https://github.com"
PLATFORM_REPOSITORY = "edu-llm/platform"
WORKFLOW_RUN_ID = "31080677880"
WORKFLOW_RUN_ATTEMPT = "1"
#: The documentation account rather than this one, because a tracked file may not carry a real
#: account id -- ``tests/test_evidence.py`` reads every tracked file for the twelve-digit token
#: and admits only this value. Nothing here depends on which account it is: the account is a
#: component of the queue URL and of nothing the envelope says.
ACCOUNT = "123456789012"
REGION = "us-east-1"
OCCURRED_AT = "2026-08-06T02:31:00+00:00"

#: A submission a lead is actually asked about, which the default form is not: under policy
#: v5 the only routes are automatic and routine, and everything under
#: ``automatic_below_cost_usd`` on one cell with a reviewed scan is released by nobody. Eight
#: A10G for the training profile's twenty-four hours over two attempts clears that line, and
#: the command satisfies the launcher and checkpoint guards that would otherwise refuse the
#: submission before a gate was chosen.
GATED = {
    "compute_profile": "gpu-8xa10g",
    "workload_profile": "olmo-core-train",
    "command": [
        "bash",
        "-lc",
        'torchrun --nproc-per-node=8 -m olmo_core.train --save-folder "$EDULLM_CHECKPOINT_DIR"',
    ],
}


@pytest.fixture(scope="module")
def catalogs() -> Catalogs:
    return Catalogs.load(CONFIG)


def compiled(tmp_path: Path, **overrides: object) -> dict[str, Any]:
    """A real compiled submission, refused loudly rather than returned empty."""
    exit_code, document = compile_form(tmp_path, payload=form(**overrides))
    assert exit_code == EXIT_OK, document
    return document


def built(document: dict[str, Any]) -> dict[str, Any]:
    return envelope_for(
        document,
        url=run_url(
            server_url=SERVER_URL,
            repository=PLATFORM_REPOSITORY,
            workflow_run_id=WORKFLOW_RUN_ID,
        ),
        account=ACCOUNT,
        region=REGION,
        event_id=f"{WORKFLOW_RUN_ID}-{WORKFLOW_RUN_ATTEMPT}",
        occurred_at=datetime.fromisoformat(OCCURRED_AT),
    )


def run_builder(
    tmp_path: Path, document: object, *, attempt: str = WORKFLOW_RUN_ATTEMPT
) -> tuple[int, Path]:
    submission = tmp_path / "compiled-submission.json"
    submission.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / f"approval-request-{attempt}.json"
    code = main(
        [
            "--submission",
            str(submission),
            "--output",
            str(output),
            "--account",
            ACCOUNT,
            "--region",
            REGION,
            "--server-url",
            SERVER_URL,
            "--repository",
            PLATFORM_REPOSITORY,
            "--workflow-run-id",
            WORKFLOW_RUN_ID,
            "--workflow-run-attempt",
            attempt,
            "--occurred-at",
            OCCURRED_AT,
        ]
    )
    return code, output


# ---------------------------------------------------------------------------------------
# The document the compiler writes is the document the message is built from
# ---------------------------------------------------------------------------------------


def test_the_builder_names_exactly_the_fields_the_compiler_writes(tmp_path: Path) -> None:
    """Mutation: add a key to the document in ``tools/compile_submission.py`` alone.

    That is how ``image_scan_reviewed`` came to be missing in the first place -- the renderer
    read a field off the envelope, the envelope was assembled from the document, and nobody
    held the two lists against each other. Compared in both directions: a field the compiler
    writes and this does not name is dropped from the message silently, and a field this names
    and the compiler does not write makes every envelope unbuildable at the first dispatch.
    """
    document = compiled(tmp_path)

    assert set(document) == set(SUBMISSION_FIELDS), (
        "tools/compile_submission.py and tools/build_approval_envelope.py disagree about "
        "what a compiled submission carries"
    )


def test_the_detail_is_the_compiled_document_plus_the_url(tmp_path: Path) -> None:
    """Mutation: drop one field from ``SUBMISSION_FIELDS``.

    The safety argument for this message is that a submitter cannot make it say anything the
    document the approver context was rendered from does not say, and that argument is only
    true while the copy is total. A builder that selected a subset would be free to omit the
    machine or the bound, which are the two values a lead reads to decide.
    """
    document = compiled(tmp_path)

    detail = built(document)["detail"]

    assert set(detail) == set(document) | {URL_FIELD}
    for field in SUBMISSION_FIELDS:
        assert detail[field] == document[field], field


def test_a_field_nobody_has_reviewed_is_refused_rather_than_dropped(tmp_path: Path) -> None:
    """Mutation: compare with ``issubset`` instead of equality.

    A subset check passes for a document carrying a ninth field, and the field then either
    reaches a lead unreviewed or vanishes depending on which side the check was written on.
    Refusing means the next person to add something to the compiled submission has to decide
    whether a lead should see it, which is a decision rather than an accident.
    """
    document = compiled(tmp_path)
    document["priority"] = "urgent"

    with pytest.raises(EnvelopeError, match="priority"):
        built(document)


def test_a_document_missing_a_field_the_message_reads_is_refused(tmp_path: Path) -> None:
    """Mutation: fill a missing field with a default instead of refusing.

    A default here is a message that reads as complete and is not. The renderer already says
    so where it genuinely cannot know something -- an unpriced profile leaves the cost unknown
    and still sends -- and the difference is that those gaps are the renderer's to describe.
    A builder inventing one would be a gap nothing announces.
    """
    document = compiled(tmp_path)
    del document["manifest"]

    with pytest.raises(EnvelopeError, match="manifest"):
        built(document)


# ---------------------------------------------------------------------------------------
# The notifier's own reader accepts what this writes
# ---------------------------------------------------------------------------------------


def test_the_reader_in_the_notifier_reads_what_the_builder_writes(
    tmp_path: Path, catalogs: Catalogs
) -> None:
    """Mutation: spell the source or the detail type as a literal in the builder.

    This is the failure that would have been invisible. ``message_for`` asks each reader in
    turn and each answers ``None`` for an envelope that is not its own, and a delivery none of
    them claims is reported as a *success* rather than a retry -- so an envelope with the
    wrong ``source`` would be accepted by the queue, acknowledged by the function, and never
    posted anywhere, with nothing red and no dead letter. Both constants are imported from the
    module that reads them for exactly that reason, and this asserts the join end to end
    rather than asserting the two strings are equal.
    """
    document = compiled(tmp_path, **GATED)

    envelope = built(document)
    facts = read_approval_requested(
        envelope,
        catalogs=catalogs,
        policy=load_policy(CONFIG),
        history=load_run_history(CONFIG),
    )

    assert facts is not None, (
        "the notifier's reader declined the envelope this builder writes, which on the "
        "deployed function is a message nobody gets and nothing reports"
    )
    assert facts.run_id == document["run_id"]
    assert facts.compute_profile == document["manifest"]["compute_profile"]
    assert facts.url == run_url(
        server_url=SERVER_URL,
        repository=PLATFORM_REPOSITORY,
        workflow_run_id=WORKFLOW_RUN_ID,
    )
    # The message renders rather than merely parsing, because a facts object that renders to
    # an exception is a delivery that dead-letters.
    assert render_approval_requested(facts).text


def test_the_envelope_carries_what_the_committed_fixture_carries(tmp_path: Path) -> None:
    """Mutation: drop ``resources`` or misspell ``detail-type`` as ``detail_type``.

    ``fixtures/events/approval-requested.sanitized.json`` is what #337 wrote its renderer and
    its own tests against, so it is the description of this envelope that already existed. A
    builder emitting a different shape would be a producer and a consumer tested separately
    and never together.
    """
    fixture = json.loads((EVENTS / "approval-requested.sanitized.json").read_text("utf-8"))

    envelope = built(compiled(tmp_path))

    assert set(envelope) == set(fixture)
    assert set(envelope["detail"]) == set(fixture["detail"])
    assert envelope["source"] == fixture["source"] == PLATFORM_EVENT_SOURCE
    assert envelope["detail-type"] == fixture["detail-type"] == APPROVAL_DETAIL_TYPE


# ---------------------------------------------------------------------------------------
# The one field the builder supplies
# ---------------------------------------------------------------------------------------


def test_the_url_is_assembled_here_and_never_taken_from_the_document(tmp_path: Path) -> None:
    """Mutation: accept a ``--url`` flag and pass the value through.

    The URL is the only thing on this message a lead clicks, so it is the only thing on it
    worth aiming somewhere else. Every part of it comes off the ``github`` context of the
    workflow run -- none of them a dispatch input -- and a document that carries its own is
    refused rather than overridden, because a silent override is a control nobody can see
    working.
    """
    expected = f"https://github.com/{PLATFORM_REPOSITORY}/actions/runs/{WORKFLOW_RUN_ID}"
    # Both spellings of the server, because ``github.server_url`` has no trailing slash and a
    # concatenation that happens to be right for one of them is wrong for the other. Asserting
    # only the slashed form is how a first version of this case passed while the tool built
    # `https://github.comedu-llm/platform/...` for the value the workflow actually supplies.
    for server in ("https://github.com", "https://github.com/"):
        assert (
            run_url(
                server_url=server,
                repository=PLATFORM_REPOSITORY,
                workflow_run_id=WORKFLOW_RUN_ID,
            )
            == expected
        ), server

    document = compiled(tmp_path)
    document[URL_FIELD] = "https://example.invalid/please-approve"

    # Matched on the reason rather than on the field name. The totality check below it refuses
    # this document too, as a field nobody has decided about -- and the remedy that refusal
    # invites is to add ``url`` to SUBMISSION_FIELDS, which is the defect. A case matching only
    # the word "url" passes with the dedicated guard deleted.
    with pytest.raises(EnvelopeError, match="must never be read from it"):
        built(document)


# ---------------------------------------------------------------------------------------
# Who is owed a message
# ---------------------------------------------------------------------------------------


def test_an_automatic_submission_is_owed_no_message(tmp_path: Path) -> None:
    """Mutation: remove the class check and rely on the workflow condition alone.

    The condition in ``submit-run.yml`` is an expression over a job output, and an expression
    that resolves to the empty string is exactly how the approver-login step came to run for
    the automatic gate. Checking again here means a wrong condition produces a red job rather
    than a lead woken at two in the morning to release a run policy had already released.
    """
    document = compiled(tmp_path)
    document["approval_class"] = "automatic"

    code, output = run_builder(tmp_path, document)

    assert code == EXIT_NOT_OWED
    assert not output.exists(), "an automatic submission left an envelope somebody could send"


def test_a_submission_that_needs_a_lead_is_written(tmp_path: Path) -> None:
    """The other side of the case above, so the refusal is not the only path exercised.

    Mutation: refuse every class rather than the automatic one. A check that says no to
    everything satisfies the case above and sends nothing at all, which is the state this
    whole change exists to end.
    """
    document = compiled(tmp_path, **GATED)
    assert document["approval_class"] != "automatic"

    code, output = run_builder(tmp_path, document)

    assert code == EXIT_OK
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["detail"]["run_id"] == document["run_id"]


def test_the_event_id_distinguishes_a_rerun_from_a_redelivery(tmp_path: Path) -> None:
    """Mutation: use the run id alone, or a fresh uuid, as the event id.

    Nothing on this queue deduplicates -- the notifier writes nothing, so there is no
    conditional write to make a replay inert -- which means the only way to tell a second
    attempt of a run from SQS delivering the same message twice is what the envelope says.
    The run id alone cannot; a uuid cannot either, because it differs on a redelivery too.

    Driven through ``main`` rather than through :func:`envelope_for`, because the id is
    assembled by the argument handling and a case that passes ``event_id`` itself asserts only
    that the field is copied. That is how a first version of this passed with the attempt
    dropped from the id entirely.
    """
    document = compiled(tmp_path, **GATED)

    ids = []
    for attempt in ("1", "2"):
        code, output = run_builder(tmp_path, document, attempt=attempt)
        assert code == EXIT_OK
        ids.append(json.loads(output.read_text(encoding="utf-8"))["id"])

    assert ids[0] == f"{WORKFLOW_RUN_ID}-1"
    assert ids[0] != ids[1], (
        "a second attempt of one run and a redelivery of one message are indistinguishable, "
        "and nothing on this queue deduplicates"
    )


def test_an_unreadable_submission_is_not_a_refusal_on_the_merits(tmp_path: Path) -> None:
    """Mutation: return the refusal code for a file that could not be parsed.

    The two mean different things to whoever reads the job. A refusal says the platform
    decided something; an unusable input says the wiring is wrong. The compile job already
    separates these two and says why at length.
    """
    submission = tmp_path / "compiled-submission.json"
    submission.write_text("{not json", encoding="utf-8")

    code = main(
        [
            "--submission",
            str(submission),
            "--output",
            str(tmp_path / "out.json"),
            "--account",
            ACCOUNT,
            "--region",
            REGION,
            "--server-url",
            SERVER_URL,
            "--repository",
            PLATFORM_REPOSITORY,
            "--workflow-run-id",
            WORKFLOW_RUN_ID,
            "--workflow-run-attempt",
            WORKFLOW_RUN_ATTEMPT,
        ]
    )

    assert code == EXIT_UNUSABLE
