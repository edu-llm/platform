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

**It now also describes the container, and it still cannot create one.** The ``execution``
key carries a second request: the job definition an accepted run registers for itself, so
that the container which runs is the digest the manifest declared rather than whichever one
``infra/batch-compute.yaml`` pins. That is a larger thing to be describing -- a job
definition names the two IAM roles a container runs as -- and it is the same split for the
same reason, only more so. This function decides what identity the run would be given and
holds neither ``batch:RegisterJobDefinition`` nor ``iam:PassRole``; the state machine holds
both, against a scope this function cannot influence.
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
    blocking_findings_from_ecr,
    image_scan_summary_from_ecr,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.execution import (
    batch_register_job_definition_request,
    batch_submit_request,
)

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
    # Required rather than defaulted, for the reason the ASL gives about `approver`: an
    # execution reaching this handler without it is a hand-started execution, and the useful
    # thing to do with one is stop. Defaulting it to OLMo-core's repository would restore the
    # exact constant Phase 6 removed, and restore it somewhere no test was looking.
    "ecr_repository",
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

    # THE ONE CALLER-SUPPLIED FIELD THAT DECIDES WHAT WAS LOOKED AT RATHER THAN WHAT IS
    # ALLOWED, AND WHY IT IS CHECKED HERE INSTEAD OF TRUSTED.
    #
    # `ecr_repository` tells ReadImageScan which repository holds this digest. It has to
    # come from the caller: that state runs first, before this function, so nothing in the
    # state machine can look it up, and the mapping is not derivable from the GitHub name
    # anyway. Every other field in this event is either the caller's own claim, which policy
    # judges on its merits, or something the state machine read for itself. This is neither.
    #
    # Left unchecked it would be the cleanest possible bypass of the image gate: point the
    # read at a repository with no findings, and `image_scan_is_reviewed` sees a spotless
    # COMPLETE scan and admits the run -- with the manifest still naming, and Batch still
    # running, the image nobody scanned. So the field is treated as a hint and the registry
    # in this zip as the authority, and disagreement stops the execution.
    #
    # Only when the repository is registered. An unregistered one is a policy question with
    # an answer a submitter should receive as a decision record, and raising here would
    # convert that refusal into an execution failure nobody can read back.
    declared_ecr_repository = str(_require(event, "ecr_repository"))
    claimed_repository = manifest_payload.get("repository")
    if isinstance(claimed_repository, str) and repositories.is_registered(claimed_repository):
        registered_ecr_repository = repositories.repository_by_name(
            claimed_repository
        ).ecr_repository
        if declared_ecr_repository != registered_ecr_repository:
            raise AdmissionEventError(
                "the admission event's ecr_repository is not the one this repository is "
                f"registered against: the event says {declared_ecr_repository!r} and "
                f"config/repositories.yaml records {registered_ecr_repository!r} for "
                f"{claimed_repository!r}, so the scan findings in hand describe images "
                "the manifest does not name"
            )

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
        # Both readings come off the same describe result, so the count and the list
        # cannot disagree about which image they describe. The gate refuses them when
        # they disagree about how many findings block, which is what makes a mapping
        # that silently dropped one fail closed rather than open.
        image_scan_findings=blocking_findings_from_ecr(
            event.get("image_scan"), policy=policy.image_scan
        ),
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
        # an execution block would be one InputPath away from being submitted anyway. Both
        # requests live under the same rule, and the second is the one that makes the rule
        # matter: a registration request minted for a refused run would be a job definition
        # naming two IAM roles, built for a submission the platform has just declined.
        #
        # Both are passed through to the SDK integrations untouched -- the ASL sends each
        # by a single JSONata reference and builds neither -- so their key sets are a hard
        # contract between this function and the state machine, and reshaping one here is
        # the same change as editing the ASL. Seam tests hold each pair together.
        register_request = batch_register_job_definition_request(
            manifest=outcome.intent.manifest,
            target=outcome.execution,
            run_id=run_id,
        )
        answer["execution"] = {
            "target": json.loads(canonical_json_bytes(outcome.execution)),
            "register_request": register_request,
            "submit_request": batch_submit_request(
                manifest=outcome.intent.manifest,
                target=outcome.execution,
                run_id=run_id,
                # THE ONE FIELD OF THE SUBMIT REQUEST THIS FUNCTION CANNOT FINISH, AND THE
                # REASON IT IS A NAME RATHER THAN AN ARN.
                #
                # A run is executed on the revision registered a moment from now, and that
                # revision's ARN does not exist until Batch has replied -- so the state
                # machine's RegisterJobDefinition state merges the returned
                # JobDefinitionArn into this request before SubmitToBatch passes it
                # through. What is written here is what that merge overwrites.
                #
                # The value therefore has to be chosen for what happens if the merge is
                # ever dropped, not for what happens when it works. Batch's
                # `jobDefinition` accepts a name as well as an ARN, and the name read
                # straight back out of the registration is the choice that degrades in the
                # safe direction: Batch resolves it to the highest active revision, and
                # this name is minted from the run id, so its only revision is the one just
                # registered. A dropped merge submits the same image; a dropped
                # registration submits nothing at all, because the definition does not
                # exist. Neither can reach `target.job_definition_arn`, which is the
                # deployed definition whose image is pinned in CloudFormation and is
                # exactly the silent wrong answer this change exists to remove.
                #
                # Read off the registration rather than rebuilt from `job_definition_name`,
                # so the two strings cannot disagree. A second author of this name would be
                # a submission against a definition nobody registered.
                job_definition=register_request["JobDefinitionName"],
                # WHOSE RUN THIS IS, READ OFF THE RECORD RATHER THAN OFF THE REQUEST.
                #
                # `outcome.intent.submitter` is the submitter admission recorded and the
                # decision was made about, so what W&B shows and what the lineage says are
                # the same person by construction. Taking it from the event instead would
                # let the two diverge for a caller that sent one thing and had another
                # written down.
                #
                # `None` for anybody with no recorded W&B account, which is most of the
                # roster and is a whole run that works -- see the comment on `members` in
                # config/organization.yaml for why a guess would be worse than a blank.
                wandb_username=inventory.wandb_username_for(outcome.intent.submitter),
                # Read off the event rather than the manifest, because the manifest is
                # hashed and a grouping key added to it changes the digest of every record
                # written before it existed. CompiledSubmission.experiment carries the
                # measurement behind that.
                #
                # `get` rather than a required field, unlike `ecr_repository` above, and the
                # difference is what each absence means. A request with no `ecr_repository`
                # is hand-built and should stop. A request with no `experiment` is one that was
                # already in flight when this shipped -- the form requires the field, so the
                # only way to reach here without it is to have crossed the approval gate
                # before the deploy. Refusing those would fail runs a lead had released for
                # a reason that has nothing to do with them.
                experiment=event.get("experiment"),
                # WHICH CORPUS, RESOLVED HERE BECAUSE THIS IS WHERE THE REGISTRY IS.
                #
                # `reference_for` returns None for `none` and for `dolma-2026-07`, which are
                # registered releases rather than published corpora, and that None is the
                # right answer rather than a lookup that missed: a run reading nothing is
                # told nothing about a corpus.
                #
                # A manifest naming something in neither list cannot reach this line --
                # `is_registered` covers both and denies outright with
                # `unregistered_dataset` well before an execution block is built.
                dataset_reference=dataset_registry.reference_for(
                    outcome.intent.manifest.dataset_release
                ),
                # The same source as wandb_username above and for the same reason: the
                # submitter admission recorded and decided about, rather than the one the
                # caller sent, so the tag the cancel path authorises against and the
                # lineage record cannot name different people.
                submitter=outcome.intent.submitter,
            ),
        }
    return answer
