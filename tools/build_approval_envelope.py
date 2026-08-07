"""Turn a compiled submission into the envelope that asks a lead to release it.

``notifier_handler`` has been able to render this message since #337 and nothing has ever
sent it one, because the envelope it reads is the platform describing its own approval gate
and no part of the platform described it. This is that description. It is read by
:func:`edullm_platform.notifications.approval.read_approval_requested` and by nothing else.

**THE DETAIL IS THE COMPILED SUBMISSION PLUS ONE FIELD, AND THAT IS THE WHOLE SAFETY
ARGUMENT.** The figures on this message are what a lead approves a spend on, so the
question that matters is not whether the arithmetic is right -- the renderer re-derives the
cost through ``compute_maximum_compute_cost_usd`` and never trusts a total it is handed --
but whether a submitter can make the message say something the platform will not then
enforce. They cannot, because every value in ``detail`` except the URL is copied verbatim
out of ``compiled-submission.json``: the same document the approver context is rendered
from, the same document whose manifest hash crossed the gate, and the same document
admission re-derives its own verdict from inside AWS. A message that overstated the machine
or understated the cost would have to be a submission that did, and that submission is
refused by admission after the gate rather than admitted on the strength of a Slack line.

So this tool copies and does not compute. :func:`envelope_for` asserts the copy is total in
both directions, which is what stops a later edit quietly dropping a field the renderer
reads or adding one nobody reviewed.

**AND "NOBODY REVIEWED" IS NOT THE SAME AS "REVIEWED AND LEFT OFF."** The document may carry
a field a lead has no use for, and :data:`RECORDED_BUT_NOT_SAID` is where that decision is
written down. Without it the refusal below would ask a yes-or-no question and accept only
yes, so the only way to record a no would be to say the thing anyway.

**THE URL IS BUILT HERE AND IS NEVER PASSED IN WHOLE.** It is the one value on the message
that is not in the document, and it is the one a lead clicks. Assembling it from the server,
the repository and the workflow run id -- three values off the ``github`` context, none of
them a dispatch input -- means there is no spelling of this tool's arguments that aims a
lead at a page somebody chose. A ``--url`` flag would have been one line shorter and would
have made the phishing question a review question forever.

**THE SOURCE AND THE DETAIL TYPE ARE IMPORTED RATHER THAN SPELLED.** Both come from
``notifications.approval``, which is also where the reader gets them, so the producer and
the consumer cannot drift apart: renaming either constant moves both sides in one edit. They
were the obvious thing to write as string literals in a workflow, and a literal here is a
message that stops being recognised with nothing going red -- ``message_for`` answers
``None`` for an envelope no reader claims and reports it as a success, so the failure would
be a queue that accepts everything and a channel that receives nothing.

Exit codes: 0 the envelope was written, 1 no message is owed, 2 the inputs are unusable.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.notifications.approval import APPROVAL_DETAIL_TYPE, PLATFORM_EVENT_SOURCE

EXIT_OK = 0
EXIT_NOT_OWED = 1
EXIT_UNUSABLE = 2

#: The EventBridge envelope version every fixture in ``fixtures/events/`` carries. A
#: constant rather than an argument because nothing chooses it.
ENVELOPE_VERSION: Final = "0"

#: Which key of ``detail`` this tool supplies and the compiled submission does not.
URL_FIELD: Final = "url"

#: The approval class that owes nobody a message. ``run-approval-automatic`` carries a
#: branch policy and no reviewers, so there is no lead to ask and a message asking one would
#: name a person who was never going to be consulted.
AUTOMATIC_CLASS: Final = "automatic"

#: Every key the compiled submission is expected to carry into ``detail``. Held as a literal
#: so that ``tests/test_approval_envelope.py`` can compare it against what
#: ``tools/compile_submission.py`` actually writes, in both directions. A field added there
#: and not here would be dropped from the message silently; a field named here and absent
#: there would make every envelope unbuildable.
SUBMISSION_FIELDS: Final = (
    "approval_class",
    "approving_environment",
    "experiment",
    "image_scan_reviewed",
    "manifest",
    "manifest_sha256",
    "run_id",
    "submitter",
)

#: Fields the compiled submission carries that the approval message deliberately does not.
#:
#: **A SECOND LIST RATHER THAN A LONGER FIRST ONE, BECAUSE THE REFUSAL ABOVE ASKS A REAL
#: QUESTION AND "YES" WAS THE ONLY ANSWER IT ACCEPTED.** A field arriving on the document
#: is refused until somebody decides whether a lead should see it, which is right; with one
#: list, the only way to record the decision was to make the answer yes. So a field nobody
#: wants on the message could only be added by widening the message, and the guard would
#: have taught people to widen it.
#:
#: ``edullm_version`` is the first entry and the reason is what a lead is deciding. They read
#: the cost, the machine, the hours and the cell count and they release or they do not.
#: Which install typed the submission changes none of that: a stale install that produced a
#: valid submission produced a valid submission, and the compile job has already refused
#: anything it did not. It is recorded on the artifact, where somebody asking how many people
#: are on a current edullm can count it, and that is the whole of what it is for.
RECORDED_BUT_NOT_SAID: Final = ("edullm_version",)

#: What a compiled submission may carry, whichever of the two answers each field got.
KNOWN_FIELDS: Final = (*SUBMISSION_FIELDS, *RECORDED_BUT_NOT_SAID)


class EnvelopeError(ValueError):
    """The compiled submission is not something an approval request can be built from."""


def run_url(*, server_url: str, repository: str, workflow_run_id: str) -> str:
    """Where a lead goes to release or decline this run.

    The three parts are separate arguments rather than one string for the reason the module
    docstring gives: this is the only clickable thing on the message, so the shape is owned
    here and there is no argument spelling that substitutes a different page.
    """
    return f"{server_url.rstrip('/')}/{repository}/actions/runs/{workflow_run_id}"


def envelope_for(
    document: Mapping[str, Any],
    *,
    url: str,
    account: str,
    region: str,
    event_id: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    """The envelope, or a refusal because the document is not one this can describe.

    The copy is checked for totality in both directions. Overlap alone would be satisfied
    by a document carrying one of the eight, and a subset check in the other direction
    would let a ninth field arrive on the message without anybody reading it.
    """
    # BEFORE THE TOTALITY CHECK, WHICH WOULD ALSO CATCH IT AND WOULD SAY THE WRONG THING.
    # A document carrying a ``url`` is an unexpected field, so the check below refuses it
    # either way. It refuses it as "a field nobody has decided about", which invites the
    # reader to add it to SUBMISSION_FIELDS -- and doing that is exactly the change that
    # would let a submitter choose the link. This says so instead.
    if URL_FIELD in document:
        raise EnvelopeError(
            f"the compiled submission carries its own {URL_FIELD!r}, which must never be "
            "read from it. That field is built here from the workflow run precisely so "
            "that nothing a submitter supplies can become the link a lead clicks."
        )
    missing = [field for field in KNOWN_FIELDS if field not in document]
    if missing:
        raise EnvelopeError(
            f"the compiled submission carries none of {missing}, which the approval message "
            "reads. tools/compile_submission.py writes every field this needs, so a "
            "document missing one was not written by it or was written by an older copy."
        )
    unexpected = sorted(set(document) - set(KNOWN_FIELDS))
    if unexpected:
        raise EnvelopeError(
            f"the compiled submission carries {unexpected}, which this tool does not know "
            "whether the approval message should say. Add the field to SUBMISSION_FIELDS "
            "or to RECORDED_BUT_NOT_SAID once somebody has decided, rather than letting it "
            "reach a lead unreviewed."
        )

    return {
        "version": ENVELOPE_VERSION,
        "id": event_id,
        "detail-type": APPROVAL_DETAIL_TYPE,
        "source": PLATFORM_EVENT_SOURCE,
        "account": account,
        "time": occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "region": region,
        "resources": [],
        "detail": {**{field: document[field] for field in SUBMISSION_FIELDS}, URL_FIELD: url},
    }


def build_parser() -> argparse.ArgumentParser:
    """Named so tests/test_workflow_tool_arguments.py can import and read it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--submission", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--account", required=True, help="Read off the credentials step.")
    parser.add_argument("--region", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--repository", required=True, help="owner/name of the platform repo.")
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument(
        "--workflow-run-attempt",
        required=True,
        help="Part of the event id, so a re-run's message is distinguishable from the first.",
    )
    parser.add_argument(
        "--occurred-at",
        default=None,
        help="ISO 8601 instant the gate was reached. Defaults to now; tests pass it.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        document = json.loads(args.submission.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"the compiled submission is unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE
    if not isinstance(document, dict):
        print("the compiled submission is not a JSON object", file=sys.stderr)
        return EXIT_UNUSABLE

    # REFUSED HERE AS WELL AS IN THE WORKFLOW'S `if:`, AND THE DUPLICATION IS DELIBERATE.
    # The condition that keeps this job off an automatic submission is an expression over a
    # job output, and an expression that silently evaluates to the empty string is how the
    # approver-login step came to run for the automatic gate. If that condition is ever
    # wrong, the failure this produces is a red job rather than a lead woken to release a
    # run policy had already released.
    if document.get("approval_class") == AUTOMATIC_CLASS:
        print(
            "this submission is classified automatic, so its gate carries no reviewers and "
            "no lead is being asked for anything. Nothing was written.",
            file=sys.stderr,
        )
        return EXIT_NOT_OWED

    if args.occurred_at is None:
        occurred_at = datetime.now(tz=UTC)
    else:
        try:
            occurred_at = datetime.fromisoformat(args.occurred_at)
        except ValueError as exc:
            print(f"--occurred-at is not an ISO 8601 instant: {exc}", file=sys.stderr)
            return EXIT_UNUSABLE

    try:
        envelope = envelope_for(
            document,
            url=run_url(
                server_url=args.server_url,
                repository=args.repository,
                workflow_run_id=args.workflow_run_id,
            ),
            account=args.account,
            region=args.region,
            event_id=f"{args.workflow_run_id}-{args.workflow_run_attempt}",
            occurred_at=occurred_at,
        )
    except EnvelopeError as exc:
        print(f"the approval request cannot be assembled: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    args.output.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # The run id and nothing else. The detail carries a submitter and an experiment, and
    # this line goes into a workflow log a submitter can read.
    print(f"The approval request describes run {envelope['detail']['run_id']}.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
