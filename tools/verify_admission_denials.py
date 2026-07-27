"""Show that the deployed admission role can still do only the one thing it may do.

Runs in the submission workflow under a real admission session, after the protected
environment gate and before the state machine is started, so a role that has been widened
stops a submission rather than being discovered after one. It attempts every action in
``edullm_platform.admission_denials`` and exits non-zero unless every single one came back
as an authorization failure of that call. A not-found, a malformed parameter, a throttle
and a timeout are failures rather than refusals, and each of them is what a permitted call
looks like when it is pointed at something that is not there.

The exit status says which kind of answer this was. ``0`` means every attempt was refused
and the record was written. ``1`` means the matrix was not proved: something was permitted,
or something failed in a way that establishes nothing. ``2`` means the run could not be set
up or written down at all — an argument that does not describe the deployed admission
machine, a session that is not the admission role, an unwritable output path — none of
which is a finding about how wide the role is.

Every action is attempted whatever the ones before it answered, and a run that could not
prove the matrix prints one line per action rather than the first line that went wrong.
Reaching this account costs a workflow run, so a run that reported one problem at a time
would cost one run per problem.

Only the machine-readable outcome reaches the two streams: the runner log is world
readable, and an AWS denial message names the account. The record written to ``--output``
is masked field by field by the contract that holds it, and the serialized document is
scanned once more before it is written, because an artifact is the copy that gets kept.

There are no AWS credentials in the environment this was written in, so this command has
never been run against the account. Everything it decides is decided from the CLI's own
output, which is why the parsing and the verdict are functions over recorded text rather
than something only a live session can exercise.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.admission_denials import (
    LINEAGE_BUCKET,
    AdmissionSetupError,
    attempt_admission_denials,
)
from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.evidence import scan_for_secrets

NOT_PROVEN_EXPLANATION = (
    "This run could not show the admission role is refused everything but starting the "
    "admission state machine, so it must not submit a run."
)

NOT_SET_UP_EXPLANATION = (
    "This run could not be set up against the deployed admission state machine, so it "
    "attempted nothing and established nothing."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--state-machine-arn",
        required=True,
        help=(
            "the deployed admission state machine. Required rather than derived, because "
            "the ARN carries the account ID and nothing in this repository holds one."
        ),
    )
    parser.add_argument(
        "--lineage-bucket",
        default=LINEAGE_BUCKET,
        help=(
            "the real lineage bucket the write probe is aimed at. A bucket that does not "
            "exist is answered NoSuchBucket before anybody is authorized, so the default "
            "is the deployed name and an override still has to be one of this project's."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        run = attempt_admission_denials(
            region=arguments.region,
            state_machine_arn=arguments.state_machine_arn,
            lineage_bucket=arguments.lineage_bucket,
        )
    except AdmissionSetupError as exc:
        # A precondition rather than an outcome: without arguments that describe the
        # deployed machine and a session this record can describe, there is nothing for
        # any refusal to be written down in.
        print(exc.reason.value, file=sys.stderr)
        print(NOT_SET_UP_EXPLANATION, file=sys.stderr)
        return 2

    if not run.proven:
        # Every action, in matrix order, including the ones that were refused. Which five
        # held is as much of the answer as which one did not.
        for outcome in run.summary:
            print(outcome, file=sys.stderr)
        print(NOT_PROVEN_EXPLANATION, file=sys.stderr)
        return 1

    try:
        matrix = run.matrix()
    except ValidationError:
        # The refusals were real and the record of them is not writable, which is a bug
        # here rather than a finding about the role. It still stops the submission: a
        # denial that cannot be written down cannot be evidence.
        print("invalid_denial_record", file=sys.stderr)
        return 1

    document = canonical_json_bytes(matrix) + b"\n"
    try:
        scan_for_secrets(document.decode("utf-8"))
    except ValueError:
        # Every field was masked and scanned on its way into the record, so reaching here
        # means a value that is safe alone became a credential shape once the fields were
        # serialized next to each other. Nothing is written, and nothing is quoted.
        print("record_holds_a_credential", file=sys.stderr)
        return 1

    try:
        arguments.output.write_bytes(document)
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
