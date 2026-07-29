"""Find out whether a workflow's own token can read *who* approved an environment gate.

**What is already established.** The GitHub documentation for
``GET /repos/{owner}/{repo}/actions/runs/{run_id}/approvals`` says "required reviewers
with read access to the repository contents and deployments can use this endpoint", which
reads as though a ``GITHUB_TOKEN`` that is not a reviewer would be refused. It is not. A
token reporting ``admin: false, maintain: false, pull: false, push: false, triage: false``
on ``edu-llm/platform`` was answered HTTP 200 with ``[]`` by both ``/approvals`` and
``/pending_deployments``, rather than 403. So the endpoint is not gated behind reviewer
status, and a job declaring ``permissions: actions: read`` reaches it.

**The residual this closes.** That ``[]`` came from a run with no environment, so it
established reachability and nothing about the body. An endpoint that is reachable and
always answers empty for a non-reviewer token would be no use to the admission gate, which
needs the approver's login. This probe points the same two calls at a run that *did* pass
an environment gate and reports whether the body is populated.

**Which run, and who says it was gated.** The probe cannot establish on its own that a run
passed a gate — an empty ``/approvals`` body is exactly what a run with no gate returns, so
reading emptiness as "not readable" would be the same mistake in the other direction.
``--environment`` is therefore required: the operator names the environment they know the
run gated on, it goes into the record, and the record says whether the response mentioned
it. ``/pending_deployments`` is read as well, because a gate still waiting is a third
outcome and reporting it as an unreadable body would be wrong.

**Read-only, and it stays that way.** Two GET calls. There is no ``--dry-run`` because
there is nothing to undo: the whole tool is the two calls named above, both are listed in
:data:`PROBED_ENDPOINTS`, and neither can change anything.

**No approver is named.** The finding is whether a login is *present*, so the record holds
a count and a boolean. The login itself is a person, and this repository does not put
people in evidence records it does not have to.

**One thing this tool refuses to do.** A GitHub run ID is a bare decimal number, and this
repository's secret scan cannot tell a twelve-digit one from an AWS account ID. Rather
than write a record that then fails to be committable, or mask the identifier the record
is about, a twelve-digit run ID is refused up front with a reason that says so.

**What it takes from the shared capture tooling, and what it does not.**
:func:`~edullm_platform.capture_tooling.write_record`, which is the whole of the write
path. Nothing else: this tool shells out to ``gh`` rather than to ``aws``, and what a
refused ``gh api`` call prints is the service's own stderr, which is the value of the
message to the operator and precisely what a reason token throws away -- so the call
wrapper, the error type and the reasons stay here.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.capture_tooling import CaptureFailedError, write_record
from edullm_platform.evidence import AWS_ACCOUNT_ID_PATTERN

#: The two endpoints read, in order. Both are GET; this tuple is the whole of what this
#: tool touches, so a reader does not have to take the docstring's word for it.
PROBED_ENDPOINTS: Final = ("approvals", "pending_deployments")

#: ``owner/repository``, in the character sets GitHub allows for each.
REPOSITORY_PATTERN: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")

#: The status line ``gh api --include`` puts in front of the headers, for example
#: ``HTTP/2.0 200 OK``. Read rather than inferred from the exit status, because a 403 is a
#: finding about the endpoint and a failure to reach GitHub at all is not.
STATUS_LINE_PATTERN: Final = re.compile(r"^HTTP/[0-9.]+\s+(?P<status>[0-9]{3})")

GH_CALL_TIMEOUT_SECONDS: Final = 60


class ProbeFailedError(RuntimeError):
    """GitHub could not be read, so there is nothing honest to write down."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class GitHubResponse:
    status: int
    body: Any


@dataclass(frozen=True)
class EndpointReading:
    """One endpoint's answer, reduced to what the record keeps."""

    status: int
    entry_count: int
    states: tuple[str, ...]
    environments: tuple[str, ...]
    approver_login_count: int

    @property
    def reachable(self) -> bool:
        return self.status == 200

    def as_record(self) -> dict[str, Any]:
        return {
            "http_status": self.status,
            "entry_count": self.entry_count,
            "states": list(self.states),
            "environments": list(self.environments),
            "approver_login_count": self.approver_login_count,
        }


def endpoint_path(repository: str, run_id: int, endpoint: str) -> str:
    return f"repos/{repository}/actions/runs/{run_id}/{endpoint}"


def gh_command(path: str) -> list[str]:
    return ["gh", "api", "--include", "-H", "Accept: application/vnd.github+json", path]


def split_head_and_body(stdout: str) -> tuple[str, str]:
    """The headers and the body, split at the first blank line however it is spelled."""
    for separator in ("\r\n\r\n", "\n\n"):
        head, found, body = stdout.partition(separator)
        if found:
            return head, body
    return stdout, ""


def parse_gh_response(stdout: str) -> GitHubResponse:
    """Split ``gh api --include`` output into its status and its body.

    ``gh`` prints the status line, the headers, a blank line and then the body, and it does
    this for an error response too — it only changes its exit status. That is why the
    status is read here rather than taken from the exit code: HTTP 403 is the finding this
    probe was written to rule out, and a ``gh`` that could not reach GitHub at all is a
    different thing that must not be filed as one.
    """
    head, body = split_head_and_body(stdout)
    match = STATUS_LINE_PATTERN.match(head.lstrip())
    if match is None:
        raise ProbeFailedError("gh_response_unreadable")
    status = int(match.group("status"))
    if not body.strip():
        return GitHubResponse(status=status, body=None)
    try:
        return GitHubResponse(status=status, body=json.loads(body))
    except ValueError as exc:
        raise ProbeFailedError("gh_response_body_unreadable") from exc


def read_endpoint(repository: str, run_id: int, endpoint: str) -> GitHubResponse:
    command = gh_command(endpoint_path(repository, run_id, endpoint))
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=GH_CALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeFailedError(f"gh_call_timed_out:{endpoint}") from exc
    except OSError as exc:
        raise ProbeFailedError("gh_cli_unavailable") from exc
    if not completed.stdout.strip():
        # No status line means the call never got an HTTP answer, which says nothing
        # about the endpoint. The stderr is not echoed: it can carry a token.
        raise ProbeFailedError(f"gh_call_failed:{endpoint}")
    return parse_gh_response(completed.stdout)


def entries(body: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(body, list):
        return ()
    return tuple(entry for entry in body if isinstance(entry, Mapping))


def environment_names(entry: Mapping[str, Any]) -> tuple[str, ...]:
    """The environments one entry names, in either of the two shapes GitHub uses.

    ``/approvals`` gives a review a list of ``environments``; ``/pending_deployments``
    gives a waiting deployment one ``environment``. Both are read here so the two endpoints
    can be reduced by the same function.
    """
    found: list[str] = []
    listed = entry.get("environments")
    if isinstance(listed, list):
        found += [
            str(item["name"])
            for item in listed
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        ]
    single = entry.get("environment")
    if isinstance(single, Mapping) and isinstance(single.get("name"), str):
        found.append(str(single["name"]))
    return tuple(found)


def approver_login(entry: Mapping[str, Any]) -> str | None:
    user = entry.get("user")
    if not isinstance(user, Mapping):
        return None
    login = user.get("login")
    return login if isinstance(login, str) and login else None


def read_response(response: GitHubResponse) -> EndpointReading:
    found = entries(response.body)
    states = sorted({str(entry["state"]) for entry in found if isinstance(entry.get("state"), str)})
    environments = sorted({name for entry in found for name in environment_names(entry)})
    return EndpointReading(
        status=response.status,
        entry_count=len(found),
        states=tuple(states),
        environments=tuple(environments),
        approver_login_count=sum(1 for entry in found if approver_login(entry) is not None),
    )


def verdict_for(approvals: EndpointReading, pending: EndpointReading) -> str:
    if approvals.status == 403:
        return "approvals_forbidden"
    if not approvals.reachable:
        return "approvals_endpoint_answered_an_error"
    if approvals.approver_login_count:
        return "approver_login_readable"
    if approvals.entry_count:
        return "approvals_body_names_no_approver"
    if pending.entry_count:
        return "environment_gate_still_pending"
    return "approvals_body_empty"


def build_record(
    *,
    repository: str,
    run_id: int,
    environment: str,
    observed_at: datetime,
    approvals: EndpointReading,
    pending: EndpointReading,
) -> dict[str, Any]:
    verdict = verdict_for(approvals, pending)
    return {
        "schema_version": 1,
        "probe": "github-approvals-readability",
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "repository": repository,
        "run_id": run_id,
        # What the operator says the run gated on. The probe cannot establish this, and
        # recording the claim beside the answer is what keeps the two apart.
        "environment_under_test": environment,
        "approvals": approvals.as_record(),
        "pending_deployments": pending.as_record(),
        "findings": {
            "approvals_endpoint_reachable": approvals.reachable,
            "approvals_body_populated": approvals.entry_count > 0,
            "approver_login_readable": approvals.approver_login_count > 0,
            "environment_under_test_named_in_approvals": environment in approvals.environments,
        },
        "verdict": verdict,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe whether a non-reviewer token can read the approver of an "
            "environment gate."
        )
    )
    parser.add_argument("--repository", required=True, help="owner/repository")
    parser.add_argument(
        "--run-id",
        required=True,
        type=int,
        help="a workflow run that passed an environment gate.",
    )
    parser.add_argument(
        "--environment",
        required=True,
        help=(
            "the environment that run gated on. Recorded as the operator's claim; an "
            "empty approvals body means nothing without it."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def check_arguments(repository: str, run_id: int) -> None:
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ProbeFailedError("repository_unusable")
    if run_id <= 0:
        raise ProbeFailedError("run_id_unusable")
    if AWS_ACCOUNT_ID_PATTERN.fullmatch(str(run_id)) is not None:
        raise ProbeFailedError("run_id_indistinguishable_from_an_aws_account_id")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        check_arguments(arguments.repository, arguments.run_id)
        readings = {
            endpoint: read_response(
                read_endpoint(arguments.repository, arguments.run_id, endpoint)
            )
            for endpoint in PROBED_ENDPOINTS
        }
    except ProbeFailedError as exc:
        print(exc.reason, file=sys.stderr)
        return 2

    record = build_record(
        repository=arguments.repository,
        run_id=arguments.run_id,
        environment=arguments.environment,
        observed_at=datetime.now(tz=UTC).replace(microsecond=0),
        approvals=readings["approvals"],
        pending=readings["pending_deployments"],
    )
    try:
        write_record(arguments.output, record)
    except (ProbeFailedError, CaptureFailedError) as exc:
        print(exc.reason, file=sys.stderr)
        return 2
    except OSError:
        print("output_unwritable", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {"verdict": record["verdict"], "findings": record["findings"]},
            indent=2,
            sort_keys=True,
        )
    )
    if record["verdict"] == "approver_login_readable":
        return 0
    print(f"approver_login_not_readable:{record['verdict']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
