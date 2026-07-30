"""The Lambda the admission state machine invokes.

A thin shell over :func:`edullm_platform.admission.admit`. Everything worth testing is in
that function, which takes no I/O and no clock; this module exists to turn a Step Functions
event into its arguments and its result into a payload, and to decide where configuration
comes from.

**It decides, and it does not record.** The handler holds no S3 permission and makes no AWS
call. It returns the two records and the state machine writes them, which buys three
things: the write appears as a first-class event in the execution history rather than
inside an opaque function, the component that parses an untrusted manifest cannot write
anything at all, and the bytes S3 stores are the canonical serialization rather than a
re-encoding of it. The last of those depends on returning mappings round-tripped through
the canonical bytes; the note on the return value says why, and it was learned from a
live run that stored every record quoted and escaped.

**Configuration is what was deployed, not what was sent.** The policy, roster, catalog and
dataset registry are packaged into the deployment artifact and read from disk. Nothing in
the event can supply or override them. That is what makes ``policy_version`` in a decision
record a fact about the platform rather than a claim by the caller.

**It resolves where an accepted run would go, and it still cannot send it there.** Phase 3
adds ``config/execution-targets.yaml`` to that packaged set and an ``execution`` key to the
answer, carrying the resolved target and the exact parameter block the state machine passes
to ``batch:SubmitJob``. The split is the same one the S3 writes use and the reason is
sharper: the component that parses a manifest an attacker could shape decides *what would
be submitted* and holds no permission to submit it, and the launch appears as a first-class
event in the execution history rather than inside this function's logs.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edullm_platform.admission import UnreadableManifestError, admit
from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.execution import ExecutionTargetCatalog
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    image_scan_summary_from_ecr,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.execution import batch_submit_request

__all__ = [
    "AdmissionContextError",
    "AdmissionEventError",
    "account_id_from_context",
    "config_directory",
    "handler",
]

#: Where the packaged configuration lives inside the deployment artifact. Overridable so a
#: test can point at the repository's own ``config/`` without staging a build.
CONFIG_DIRECTORY_VARIABLE = "EDULLM_CONFIG_DIR"

_REQUIRED_EVENT_FIELDS = (
    "run_id",
    "submitter",
    "approving_environment",
    "approved_manifest_sha256",
    "manifest",
    "workflow_run",
)


class AdmissionEventError(ValueError):
    """The state machine sent something this handler cannot interpret."""


class AdmissionContextError(ValueError):
    """Lambda did not say which account this invocation is running in.

    Distinct from an event error because nothing the caller sent is at fault and no
    decision record could describe it. Without the account there is no ARN to build, so the
    handler refuses rather than guessing one.
    """


def config_directory() -> Path:
    configured = os.environ.get(CONFIG_DIRECTORY_VARIABLE)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "config"


def account_id_from_context(context: object) -> str:
    """Read the account this function is running in out of its own invocation context.

    From the context rather than from an environment variable or from STS, and the reason
    is different for each of the two.

    An environment variable is deployment configuration: it says what somebody wrote into a
    template, which is a claim that can be wrong, and a wrong one would build queue and
    job-definition ARNs pointing at another account -- where the submit would fail with a
    message about a missing queue rather than about a misconfigured function.
    ``invoked_function_arn`` is Lambda's own statement about where this invocation is
    happening and cannot disagree with it.

    STS would be equally true and costs a network call from a component whose whole design
    property is that it makes none. It would also mean this handler needed a permission,
    and ``sts:GetCallerIdentity`` cannot be denied by a policy -- so the grant would be
    invisible in a role diff while the call itself became a failure mode on the admission
    path.
    """
    arn = getattr(context, "invoked_function_arn", None)
    segments = arn.split(":") if isinstance(arn, str) else []
    if len(segments) < 6 or not segments[4].isdigit() or len(segments[4]) != 12:
        raise AdmissionContextError(
            "the invocation context carries no usable invoked_function_arn, so the "
            "account this function runs in is unknown and no execution target ARN can be "
            "built"
        )
    return segments[4]


def _require(event: Mapping[str, Any], field: str) -> Any:
    if field not in event:
        raise AdmissionEventError(f"the admission event is missing {field!r}")
    return event[field]


def handler(event: Mapping[str, Any], context: object = None) -> dict[str, Any]:
    account_id = account_id_from_context(context)

    missing = [field for field in _REQUIRED_EVENT_FIELDS if field not in event]
    if missing:
        raise AdmissionEventError(
            f"the admission event is missing {', '.join(sorted(missing))}"
        )

    config = config_directory()
    policy = load_yaml(config / "policy.yaml", ApprovalPolicy)
    inventory = load_yaml(config / "organization.yaml", OrganizationInventory)
    # The registry that answers "is this repository registered". It has always been in the
    # packaged set -- the builder copies config/*.yaml -- and nothing read it, while the
    # fact it answers was derived from the roster's pilot list instead.
    repositories = load_yaml(config / "repositories.yaml", RepositoryRegistry)
    catalog = load_yaml(config / "workload-catalog.yaml", WorkloadCatalog)
    dataset_registry = load_yaml(config / "datasets.yaml", DatasetRegistry)
    image_scan_registry = load_yaml(
        config / "image-exceptions.yaml", ImageScanExceptionRegistry
    )
    execution_targets = load_yaml(config / "execution-targets.yaml", ExecutionTargetCatalog)

    try:
        approving_environment = ApprovalEnvironment(_require(event, "approving_environment"))
    except ValueError as exc:
        raise AdmissionEventError(
            "the admission event names an approval environment this platform does not "
            "define; the trust policy enumerates exactly "
            f"{', '.join(member.value for member in ApprovalEnvironment)}"
        ) from exc

    workflow_run = GitHubWorkflowRunReference.model_validate(_require(event, "workflow_run"))
    manifest_payload = _require(event, "manifest")
    if not isinstance(manifest_payload, Mapping):
        raise UnreadableManifestError("the admission event's manifest is not an object")

    outcome = admit(
        manifest_payload=manifest_payload,
        approved_manifest_sha256=str(_require(event, "approved_manifest_sha256")),
        run_id=str(_require(event, "run_id")),
        submitter=str(_require(event, "submitter")),
        approver=event.get("approver") or None,
        approving_environment=approving_environment,
        workflow_run=workflow_run,
        policy=policy,
        inventory=inventory,
        repositories=repositories,
        catalog=catalog,
        execution_targets=execution_targets,
        account_id=account_id,
        dataset_registry=dataset_registry,
        image_scan_registry=image_scan_registry,
        # The state machine puts the ECR describe result here, from a task it ran itself.
        # It is deliberately not passed through from the execution input: the caller
        # supplies the manifest, and letting it also supply the scan findings would let it
        # declare its own image clean. The ASL builds this key from the ReadImageScan
        # state's Result and from nowhere else.
        image_scan_summary=image_scan_summary_from_ecr(event.get("image_scan")),
        recorded_at=datetime.now(tz=UTC),
    )

    run_id = outcome.intent.run_id
    answer: dict[str, Any] = {
        "accepted": outcome.accepted,
        "run_id": run_id,
        "reason": outcome.decision.reason.value,
        "detail": outcome.decision.detail,
        "intent_key": f"intent/{run_id}.json",
        "decision_key": f"decision/{run_id}.json",
        "conflict_key": f"conflicts/{run_id}.json",
        # Objects, and objects parsed back out of the canonical bytes rather than built
        # any other way. Both halves of that matter, and the first live run of this phase
        # is why.
        #
        # Returning the canonical *string* is the obvious thing and it is wrong: the S3
        # SDK integration JSON-encodes whatever the Body path yields, so a string is
        # stored quoted and escaped -- `"{\"run_id\":...}"` -- and every reader has to
        # parse the object twice. Measured against us-east-1 on 2026-07-27 with a
        # throwaway bucket and state machine, writing the same record both ways.
        #
        # An object is stored as ordinary JSON, and the same measurement pinned how:
        # compact separators, non-ASCII left unescaped, nulls kept, and keys in the order
        # they arrive rather than sorted. That agrees with canonical_json_bytes on every
        # axis except ordering, and ordering is ours: round-tripping through the canonical
        # bytes yields a mapping whose keys are already sorted, so what S3 stores is
        # byte-identical to what was hashed. Building the mapping with model_dump instead
        # would hand Step Functions field-definition order and quietly lose that.
        "intent": json.loads(canonical_json_bytes(outcome.intent)),
        "decision": json.loads(canonical_json_bytes(outcome.decision)),
    }
    if outcome.execution is not None:
        # Present only when accepted, and absent rather than null when not: the state
        # machine's Choice branches on `accepted`, and a rejected submission that carried
        # an execution block would be one InputPath away from being submitted anyway.
        #
        # `submit_request` is passed through to batch:submitJob untouched -- the ASL sends
        # it by InputPath and does not build it -- so its key set is a hard contract
        # between this function and the state machine, and reshaping it here is the same
        # change as editing the ASL. A seam test holds the two together.
        answer["execution"] = {
            "target": json.loads(canonical_json_bytes(outcome.execution)),
            "submit_request": batch_submit_request(
                manifest=outcome.intent.manifest,
                target=outcome.execution,
                run_id=run_id,
                # Still the target's static definition, and this is the call site the
                # required argument exists to name. Executing a run on the digest it
                # declared means registering a definition first and submitting against the
                # revision that comes back, which is a change to the state machine, to what
                # this function answers and to the admission role's grants. Until that
                # ships, the ARN sent here is the one the templates pin -- unchanged
                # behaviour, now stated rather than defaulted.
                job_definition_arn=outcome.execution.job_definition_arn,
            ),
        }
    return answer
