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
from edullm_platform.contracts.image import GitHubWorkflowRunReference
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.workload import WorkloadCatalog

__all__ = ["AdmissionEventError", "config_directory", "handler"]

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


def config_directory() -> Path:
    configured = os.environ.get(CONFIG_DIRECTORY_VARIABLE)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "config"


def _require(event: Mapping[str, Any], field: str) -> Any:
    if field not in event:
        raise AdmissionEventError(f"the admission event is missing {field!r}")
    return event[field]


def handler(event: Mapping[str, Any], context: object = None) -> dict[str, Any]:
    del context  # The handler is a pure function of its event and its packaged config.

    missing = [field for field in _REQUIRED_EVENT_FIELDS if field not in event]
    if missing:
        raise AdmissionEventError(
            f"the admission event is missing {', '.join(sorted(missing))}"
        )

    config = config_directory()
    policy = load_yaml(config / "policy.yaml", ApprovalPolicy)
    inventory = load_yaml(config / "organization.yaml", OrganizationInventory)
    catalog = load_yaml(config / "workload-catalog.yaml", WorkloadCatalog)
    dataset_registry = load_yaml(config / "datasets.yaml", DatasetRegistry)

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
        catalog=catalog,
        dataset_registry=dataset_registry,
        recorded_at=datetime.now(tz=UTC),
    )

    run_id = outcome.intent.run_id
    return {
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
