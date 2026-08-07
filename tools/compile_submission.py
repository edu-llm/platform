"""Compile a dispatch form into the manifest policy judges, and say which gate it needs.

Run by the submission workflow's compile job, which holds no ``id-token`` permission and
reads no secret. That is the point: the classification that decides which approval gate a
submission goes to is computed before this job can reach AWS, and the workflow names the
gate from this tool's output rather than from the form.

Everything the account has to be asked arrives as a file. The resolve job holds a role that
may describe images and their scan findings, writes down what the registry answered for the
declared commit, and hands it over as an artifact -- so this job reads a document rather
than a registry, and keeps the property that makes its verdict worth anything.

Exit codes follow the repository's convention: 0 compiled, 1 the submission was refused,
2 the inputs could not be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from edullm_platform.admission import image_scan_refusal_detail
from edullm_platform.build_tooling import append_step_outputs
from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.cli.actions import PLATFORM_REPOSITORY
from edullm_platform.cli.release import install_command
from edullm_platform.client_version import (
    SubmittingClient,
    defect_note,
    read_client_version,
    submitted_by_said,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.identity import new_run_id
from edullm_platform.contracts.image_contents import ImageContentsRecord
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanSummary,
    ScanFinding,
    image_scan_verdict,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.daily_ceiling import CeilingReading, read_the_day
from edullm_platform.errors import SubmissionRefusedError
from edullm_platform.image_resolution import PublishedImage
from edullm_platform.placement import (
    CAPACITY_FILENAME,
    UnreadableCapacityError,
    placement_warning,
    read_capacity,
)
from edullm_platform.run_history import RunHistoryFormatError, load_run_history
from edullm_platform.run_index import RunIndexFormatError, from_document
from edullm_platform.submission import (
    SubmissionInputs,
    compile_submission,
    render_approver_context,
    require_a_dataset_release_that_is_current,
    require_registered_repository,
    require_submitter_on_the_roster,
)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNUSABLE = 2


class ResolvedImagesUnreadableError(ValueError):
    """The resolve job's answer is not one this job can act on.

    Its own error rather than a bare ``ValueError`` because the caller has to tell it from
    a refusal: an answer nobody could read is not a submission anybody would decline, and
    the workflow prints a different sentence for each.
    """


def read_published_images(
    document: object,
) -> tuple[list[PublishedImage], ImageScanSummary | None, tuple[ScanFinding, ...] | None]:
    """What ``tools/resolve_published_image.py`` wrote, read back into what compiling needs.

    Nothing here is skipped or defaulted on a malformed entry. Dropping one silently turns a
    broken resolve into a commit with fewer published images, and the shortest way for that
    to end is a run resolved onto an older image than the one it should have used -- which
    is invisible in the record afterwards, because a rebuild legitimately looks like that.
    """
    if not isinstance(document, dict):
        raise ResolvedImagesUnreadableError("the resolved-images document is not an object")
    entries = document.get("published")
    if not isinstance(entries, list):
        raise ResolvedImagesUnreadableError("the resolved-images document lists no published key")

    published: list[PublishedImage] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ResolvedImagesUnreadableError("a published entry is not an object")
        digest = entry.get("image_digest")
        pushed_at = entry.get("pushed_at")
        if not isinstance(digest, str) or not isinstance(pushed_at, str):
            raise ResolvedImagesUnreadableError("a published entry names no digest or no instant")
        try:
            taken = datetime.fromisoformat(pushed_at)
        except ValueError as exc:
            raise ResolvedImagesUnreadableError(f"{pushed_at!r} is not an instant") from exc
        # An instant with no offset would be read as local time by whatever compared it,
        # and ordering rebuilds is the one thing this value is for.
        if taken.tzinfo is None:
            raise ResolvedImagesUnreadableError(f"{pushed_at!r} carries no UTC offset")
        published.append(PublishedImage(image_digest=digest, pushed_at=taken))

    scan = document.get("image_scan")
    summary = None
    if scan is not None:
        try:
            summary = ImageScanSummary.model_validate(scan)
        except ValidationError as exc:
            raise ResolvedImagesUnreadableError(
                f"the recorded scan summary is not one: {exc}"
            ) from exc

    # Absent and empty are different answers. A missing key or a null means the findings
    # could not be read, and the gate refuses that because the count in the summary will not
    # match a list it does not have. An empty list means the registry reported nothing at a
    # blocking severity, which is a pass. Reading one as the other is the only way this file
    # could open the gate rather than close it.
    recorded = document.get("blocking_findings")
    if recorded is None:
        return published, summary, None
    if not isinstance(recorded, list):
        raise ResolvedImagesUnreadableError("blocking_findings is not a list")
    try:
        findings = tuple(ScanFinding.model_validate(entry) for entry in recorded)
    except ValidationError as exc:
        raise ResolvedImagesUnreadableError(f"a recorded finding is not one: {exc}") from exc
    return published, summary, findings


def read_the_days_ceiling(
    index: Path | None, *, policy: ApprovalPolicy, now: datetime
) -> CeilingReading | None:
    """What the day's ledger says, or ``None`` where no ceiling is configured.

    **THE ORDER OF THESE TWO QUESTIONS IS THE WHOLE OF WHAT KEEPS THIS HONEST.** An unset
    ceiling is asked about first and answers ``None``, which changes nothing anywhere. Every
    other outcome, including every way the ledger fails, is a reading -- and a reading that
    could not be taken routes to a lead. The reverse order would let a platform with no
    ceiling configured start sending runs to a lead the first time a fetch failed, and, far
    worse, would make "the index is missing" and "no ceiling is set" the same answer.

    Three things go wrong here and all three are the same verdict with different sentences.
    The workflow did not hand over an index, which is a job that changed shape. There is no
    file where it said, which is a fetch that failed or a branch that does not exist. The
    file will not parse, which is a document somebody has to look at. A reader of the log
    has to be able to tell those apart to fix any of them, and none of them may quietly
    become "the day is empty".
    """
    ceiling = policy.thresholds.automatic_daily_ceiling_usd
    if ceiling is None:
        return None
    if index is None:
        return read_the_day(
            None,
            ceiling_usd=ceiling,
            now=now,
            unreadable_because=(
                "this job was given no --run-index, so nothing here can say what the day "
                "has already committed"
            ),
        )
    try:
        runs = from_document(json.loads(index.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError, RunIndexFormatError) as exc:
        return read_the_day(
            None,
            ceiling_usd=ceiling,
            now=now,
            unreadable_because=f"the run index at {index} could not be read ({exc})",
        )
    return read_the_day(runs, ceiling_usd=ceiling, now=now)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    # Required rather than defaulted to nothing published. Nothing published is a real
    # answer with a refusal attached -- "build the commit before submitting it" -- so a
    # default that spelled "I was not told" the same way would report every submission as
    # an unbuilt commit the first time the workflow forgot to pass the file.
    parser.add_argument("--published-images", required=True, type=Path)
    parser.add_argument("--submitter", required=True)
    # NOT REQUIRED, AND THE DEFAULT IS THE HONEST ONE. A dispatch from the Actions tab
    # names no install, which is a legitimate path rather than a fault, so absence is the
    # ordinary case and reads as "this cannot be known" everywhere below. Nothing is
    # refused on it: see edullm_platform.client_version for why a floor here would cost
    # more than it could buy.
    parser.add_argument("--client-version", default="")
    parser.add_argument("--repository-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument(
        "--run-index",
        type=Path,
        default=None,
        help=(
            "the run-index.json off machine/run-index, which is what says how much of "
            "today has already been committed by runs nobody released. Optional only "
            "because a policy carrying no automatic_daily_ceiling_usd has nothing to "
            "compare against; where a ceiling is set, not passing this routes the "
            "submission to a team lead rather than waving it through"
        ),
    )
    parser.add_argument(
        "--run-id",
        help=(
            "Reuse an existing logical run id instead of minting one. The id keys the S3 "
            "records and names the Step Functions execution, so a re-run that minted a "
            "fresh one would defeat both deduplication mechanisms at once."
        ),
    )
    return parser


def _say_what_the_install_explains(
    refusal: str, *, client: SubmittingClient, install: str
) -> None:
    """Print the version sentence for the refusals a known defect explains, and no others.

    Both refusal paths get it because a defect could surface either way, and a helper is
    what stops the two arms of that answering differently.
    """
    note = defect_note(refusal, client=client, install=install)
    if note is not None:
        print(note, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Read first and printed first, so the log says which install typed this above whatever
    # happens next. On stdout rather than beside the refusals, because it is a fact about
    # every submission rather than a complaint about this one.
    #
    # FLUSHED, AND THE FIRST LIVE RUN IS WHY. stdout is block-buffered when it is not a
    # terminal and stderr is not, so on a runner this line was held until the process
    # exited and arrived *below* the refusal it was supposed to introduce -- naming the
    # install after the paragraph that needed it. Nothing in this file's own output
    # revealed that, because everything else it prints goes to stderr.
    client = read_client_version(args.client_version)
    print(submitted_by_said(client), flush=True)
    installs_with = install_command(repository=PLATFORM_REPOSITORY)

    try:
        payload = json.loads(args.inputs.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"submission inputs are unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        inputs = SubmissionInputs.model_validate(payload)
    except ValidationError as exc:
        print(f"the submission form is not valid: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        policy = load_yaml(args.config_dir / "policy.yaml", ApprovalPolicy)
        repositories = load_yaml(args.config_dir / "repositories.yaml", RepositoryRegistry)
        catalog = load_yaml(args.config_dir / "workload-catalog.yaml", WorkloadCatalog)
        registry = load_yaml(args.config_dir / "datasets.yaml", DatasetRegistry)
        image_scan_registry = load_yaml(
            args.config_dir / "image-exceptions.yaml", ImageScanExceptionRegistry
        )
        # What the published training images were measured to contain. The compile job is the
        # last place a factory the image does not have can be caught for nothing: after this
        # the run is classified, released and placed, and the container discovers it at exit
        # 70 with the machine allocated.
        image_contents = load_yaml(
            args.config_dir / "image-contents.yaml", ImageContentsRecord
        )
        # Read for two things, and admission resolves both independently from its own copy,
        # because what a run is labelled with must not depend on a file the compile job
        # could be pointed at. Whether this submitter is on the roster at all, which
        # admission answers only after a lead has released the gate. And whether their runs
        # can be attributed in W&B, so the approver context can say before the gate what
        # W&B will not say after it.
        inventory = load_yaml(args.config_dir / "organization.yaml", OrganizationInventory)
        # Whether the shape on the form is one this account has been able to get. Read
        # here beside the rest of the reviewed configuration, and unreadable here is an
        # unusable input rather than a refusal, for the reason
        # edullm_platform.placement.UnreadableCapacityError records: the fail-open
        # alternative is a file that stops parsing and takes the only warning about a
        # four-hour wait with it.
        capacity = read_capacity(args.config_dir / CAPACITY_FILENAME)
        # WHAT RUNS OF THIS SHAPE HAVE TAKEN, WHICH THE SUBMITTER WAS SHOWN AND THE
        # APPROVER WAS NOT. `edullm check` loads this out of the same directory through
        # `cli/configuration.py` and prints the measurement beside the ceiling, so a
        # researcher spending nothing reads "a median of 5m" against a one-hour bound while
        # the lead releasing it read that no reading was packaged. The reading was in the
        # checkout this job reads the whole time; the argument was simply not passed.
        #
        # `None` is a directory carrying no reading, which the renderer says out loud, and
        # a reading that will not parse raises for the reason `load_run_history` records:
        # a measurement this tree cannot read is a broken checkout rather than an absent
        # measurement, and `edullm check` already refuses that locally.
        run_history = load_run_history(args.config_dir)
    except (
        OSError,
        ValidationError,
        TypeError,
        UnreadableCapacityError,
        RunHistoryFormatError,
    ) as exc:
        print(f"reviewed configuration is unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        resolved = json.loads(args.published_images.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"the resolved images are unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        published_images, image_scan_summary, blocking_findings = read_published_images(resolved)
    except ResolvedImagesUnreadableError as exc:
        print(f"the resolved images are unreadable: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    # The scan summary is what chooses the gate, and it is read here from a job that holds
    # an ECR role rather than derived here from one that holds nothing. That is a real
    # relaxation of "the gate is chosen before the run reaches AWS", and the compensating
    # control is the one this file already described: admission re-derives the findings
    # from the registry itself and fails closed on disagreement, so an understated summary
    # buys a submitter a gate they still cannot pass. The gate chosen here is the floor and
    # never the ceiling. infra/iam/image-resolver-role.yaml carries the argument in full.
    try:
        # Before compiling rather than after, so that somebody the roster does not name is
        # told that and nothing else. A refusal naming a workload profile would send them
        # to correct a field that was never what stood in the way.
        require_submitter_on_the_roster(args.submitter, inventory=inventory)
        # And for the same reason, one field further along. Compiling refuses an
        # unregistered repository too, through the registry fact policy denies outright,
        # but only once the workload profile has been checked against it -- so for a
        # repository with no profile at all the refusal names the profile instead. Asked
        # here, the submitter is told which field is wrong and where the list lives.
        require_registered_repository(inputs.repository, repositories=repositories)
        # And one field further along again. This is the check the submission form was
        # standing in for: `retired` in config/datasets.yaml removed a menu item and
        # enforced nothing, so a dispatch reaching this job by any route other than the
        # dropdown compiled clean, classified routine and was admitted. Asked here rather
        # than added to policy's denied-outright list, for the reasons
        # require_a_dataset_release_that_is_current sets out -- chiefly that a resume from
        # a checkpoint written against a retired corpus has to keep naming that corpus, and
        # a refusal nobody can lift would make lying about it the only route.
        require_a_dataset_release_that_is_current(inputs.dataset_release, datasets=registry)
        # Read before compiling because the class this raises is decided inside
        # compile_submission, and read here rather than inside it for the reason every other
        # environmental fact is passed in: that function is given loaded configuration and
        # opens no file.
        daily_ceiling = read_the_days_ceiling(
            args.run_index, policy=policy, now=datetime.now(UTC)
        )
        submission = compile_submission(
            inputs,
            run_id=args.run_id or new_run_id(),
            policy=policy,
            repositories=repositories,
            catalog=catalog,
            dataset_registry=registry,
            image_contents=image_contents,
            image_scan_registry=image_scan_registry,
            image_scan_summary=image_scan_summary,
            image_scan_findings=blocking_findings,
            published_images=published_images,
            daily_ceiling=daily_ceiling,
        )
    except SubmissionRefusedError as exc:
        print(f"submission refused: {exc}", file=sys.stderr)
        _say_what_the_install_explains(str(exc), client=client, install=installs_with)
        return EXIT_REFUSED
    except ValidationError as exc:
        print(f"the submission does not compile into a valid manifest: {exc}", file=sys.stderr)
        # AFTER THE REFUSAL AND NOT INSTEAD OF IT. The refusal above names the field and
        # what is wrong with it, which is right for a submitter who really did type an
        # unquoted command. What it cannot know is that an old install may have unquoted a
        # correct one, so the note contradicts its remedy rather than replacing it, and
        # both readers get a line they can act on.
        _say_what_the_install_explains(str(exc), client=client, install=installs_with)
        return EXIT_REFUSED

    # After compiling rather than before it, because the profile to ask about is the one
    # the run lands on: `compute_profile` is resolved onto the manifest and reading it back
    # from there keeps that resolution the only place it happens. Printed as well as put in
    # the summary, and to stderr beside the refusals, because the two reach different
    # people at different moments -- the log is what the submitter is already watching, and
    # the summary is what survives on the run page for whoever releases it.
    placement_note = placement_warning(
        submission.manifest.compute_profile, capacity=capacity
    )
    if placement_note is not None:
        print(placement_note, file=sys.stderr)

    # WHAT THE APPROVER HAS TO READ NOW THAT ONE OF THEM CAN RELEASE THIS. Policy v4 moved
    # image_scan_findings_unreviewed out of denied_outright, so an unreviewed digest is an
    # exception rather than a refusal nobody could lift. classify_request still sends it to
    # the admin gate. What was missing was the findings themselves: the admin was being
    # asked to release a run whose only problem is a set of CVEs the page did not name.
    #
    # Derived from the same four-verdict function admission uses for its refusal text,
    # rather than a second sentence written here, because three of those four verdicts are
    # not a judgement anybody can make and telling them apart is the whole value of it. The
    # provenance record is what this reads; admission re-derives all of it from ECR after
    # the gate and fails closed on disagreement, so this can only understate.
    scan_note = (
        None
        if submission.facts.image_scan_reviewed
        else image_scan_refusal_detail(
            image_scan_verdict(
                image_digest=submission.manifest.image_digest,
                summary=image_scan_summary,
                policy=policy.image_scan,
                registry=image_scan_registry,
                blocking_findings=blocking_findings,
            ),
            summary=image_scan_summary,
            policy=policy.image_scan,
            registry=image_scan_registry,
            blocking_findings=blocking_findings,
        )
    )
    if scan_note is not None:
        print(scan_note, file=sys.stderr)

    # ON THE LOG WHATEVER THE VERDICT, AND THE UNDER CASE IS THE ONE WORTH ARGUING FOR. A
    # ceiling that speaks only when it fires is a ceiling nobody can tell apart from one
    # that is switched off, which is exactly the state this platform was in about every
    # other cost control it thought it had. Printing the running total on every compile
    # makes the mechanism observable on the ordinary day, and it is one line.
    if daily_ceiling is not None:
        print(daily_ceiling.said, file=sys.stderr)

    document = {
        "run_id": submission.run_id,
        "submitter": args.submitter,
        "approval_class": submission.approval_class.value,
        "approving_environment": submission.approving_environment.value,
        "manifest_sha256": submission.manifest_sha256,
        "manifest": json.loads(canonical_json_bytes(submission.manifest)),
        # A sibling of the manifest and deliberately not a key inside it: the digest above
        # is what an approver releases, and a field folded into the hashed document changes
        # the digest of every record written before that field existed.
        "experiment": submission.experiment,
        # THE SAME VERDICT THE NOTE ABOVE PRINTS, WRITTEN DOWN RATHER THAN ONLY PRINTED.
        # The approval message #337 added carries a clause naming an unreviewed scan as one
        # of the three things holding a cheap single cell back from releasing itself, and
        # that clause reads this field off the envelope. Nothing put it in the document, so
        # the clause was unreachable: an absent field reads as reviewed, which is the
        # permissive direction and exactly the one a lead should not be defaulted into.
        # A sibling for the same reason `experiment` is -- it is a fact about how the image
        # was judged, not about what will run, and the manifest is hashed.
        "image_scan_reviewed": submission.facts.image_scan_reviewed,
        # WHICH INSTALL TYPED THIS, AND WHY IT IS WRITTEN HERE RATHER THAN ANYWHERE DEEPER.
        # Nothing this platform stores has ever recorded a client version, which is the
        # recorded reason it is impossible to say whether anybody is on a current edullm.
        # This artifact is the cheapest place that changes: it is already uploaded for
        # every submission, already downloaded by `edullm status`, and carries no digest
        # anybody has published. The lineage record would be the natural home and is the
        # expensive one -- RunManifest is content-addressed and CompiledSubmission.experiment
        # records what a field added to it costs -- so the question is answerable from here
        # over the artifact retention window rather than forever, which is the whole of
        # what was asked for.
        #
        # `None` for a dispatch from the Actions tab, which names no install and is not
        # wrong for it.
        "edullm_version": client.said,
    }
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.summary is not None:
        args.summary.write_text(
            render_approver_context(
                submission,
                submitter=args.submitter,
                policy=policy,
                repository_url=args.repository_url,
                inventory=inventory,
                wandb_username=inventory.wandb_username_for(args.submitter),
                placement_note=placement_note,
                scan_note=scan_note,
                run_history=run_history,
                daily_ceiling=daily_ceiling,
            ),
            encoding="utf-8",
        )

    if args.github_output is not None:
        append_step_outputs(
            args.github_output,
            (
                ("run_id", submission.run_id),
                ("approval_class", submission.approval_class.value),
                ("environment", submission.approving_environment.value),
                ("manifest_sha256", submission.manifest_sha256),
                # WHAT THIS SUBMISSION IS AUTHORISED TO COMMIT, HANDED TO THE JOB THAT
                # WRITES THE INDEX. It is the figure this job just computed, the one the
                # approver page prints and the one the class was chosen against, and
                # recording it is what lets the next submission read the day. Passed as a
                # step output rather than recomputed downstream, because a second
                # implementation of the arithmetic is a second answer to the question the
                # ceiling is comparing.
                (
                    "maximum_compute_cost_usd",
                    str(submission.cost.maximum_compute_cost_usd),
                ),
            ),
        )

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
