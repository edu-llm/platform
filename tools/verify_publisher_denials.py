"""Show that the deployed publisher role is still as narrow as its template says.

Runs in the publish workflow under a real publisher session, between the source-identity
gate and the build, so a role that has been widened stops a publish rather than being
discovered after one. It attempts every action in
``edullm_platform.publisher_denials`` and exits non-zero unless every single one came
back as an authorization failure naming that action. A not-found, a malformed parameter
and a timeout are failures rather than refusals, and each of them is what a permitted
call looks like when it is pointed at something that is not there.

Only the machine-readable reason reaches the two streams: the runner log is world
readable for any public caller repository, and an AWS denial message names the account.
The record written to ``--output`` is masked field by field by the contract that holds it.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.build_tooling import RegistryUnreadableError, load_registry
from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.contracts.repository_registry import UnknownRepositoryError
from edullm_platform.publisher_denials import DenialNotProvenError, attempt_denials

NOT_PROVEN_EXPLANATION = (
    "This run could not show the publisher role is refused every action outside ECR, "
    "so it must not publish an image."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        registry = load_registry(arguments.registry)
    except RegistryUnreadableError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    try:
        registered = registry.repository_by_name(arguments.repository)
    except UnknownRepositoryError:
        print("unregistered_repository", file=sys.stderr)
        return 1

    try:
        matrix = attempt_denials(
            region=arguments.region,
            ecr_repository=registered.ecr_repository,
        )
    except DenialNotProvenError as exc:
        print(str(exc), file=sys.stderr)
        print(NOT_PROVEN_EXPLANATION, file=sys.stderr)
        return 1
    except ValidationError:
        # The refusals were real and the record of them is not writable, which is a bug
        # here rather than a finding about the role. It still stops the publish: a
        # denial that cannot be written down cannot be evidence.
        print("invalid_denial_record", file=sys.stderr)
        return 1

    try:
        arguments.output.write_bytes(canonical_json_bytes(matrix) + b"\n")
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
