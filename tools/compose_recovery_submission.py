"""Compose the submission that recovers a dead run, out of what that run actually asked for.

WHY THIS IS A TOOL AND NOT A RUNBOOK SECTION. Recovery on this platform is a fresh submission
rather than a retry, and that is measured rather than assumed: a run submitted with two attempts
and a bound it could not finish inside came back ``FAILED`` at ``Attempts 1 of 2``. A timeout
terminates the job instead of ending an attempt the ``EvaluateOnExit`` rules are consulted about,
so the second attempt insures against a host dying and against nothing else. If a run dies inside
a capacity block, somebody submits again pointing at the dead run's checkpoint prefix.

THE TRAP THIS EXISTS TO CLOSE, WHICH IS THE WHOLE REASON IT IS WORTH A FILE. **The recovering
submission has to carry the same bound as the original.** A shorter one restores the correct
weights onto a different learning-rate curve: the optimizer state is right, the schedule is not,
and nothing reports a problem. It looks like a successful recovery and produces a worse model,
discovered weeks later or never.

That is not a thing to write in a runbook and hope, because the person reading it is improvising
at three in the morning inside a window that is being billed, and shortening the bound is the
obvious thing to do when there are six hours left of a twenty-three hour plan. So this tool does
not ask for the bound and cannot be told one. **It reads the dead run's own intent record and
carries its command through byte for byte**, adding only the load path. The bound is not
recomputed, so it cannot be recomputed wrongly.

WHAT IT REFUSES. A dead run with no checkpoint under its prefix has nothing to recover from, and
a submission pointed at an empty prefix trains from step 0 while looking exactly like a recovery.
That is the same silent failure in a different costume, so it is an error here rather than a
warning: there is no useful recovery to compose and composing one anyway is the harm.

Nothing here changes anything. It makes read-only S3 calls and prints a command.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.results import output_prefix

#: A recovery was composed and is printed on stdout.
EXIT_OK: Final = 0
#: There is nothing to recover from, or the run cannot be recovered as submitted.
EXIT_NO_RECOVERY: Final = 1
#: Bad arguments, no credential, or a run this account has no intent record for.
EXIT_UNUSABLE: Final = 2

#: The OLMo-core key that points a fresh run at another run's checkpoints. Named here rather
#: than passed in, because a caller free to choose the key is a caller who can point a recovery
#: at nothing by misspelling it, and be told nothing.
LOAD_PATH_KEY: Final = "trainer.load_path"

#: Where a run's intent record lands. Written by the admission Lambda as intent/{run_id}.json.
INTENT_KEY: Final = "intent/{run_id}.json"

__all__ = [
    "EXIT_NO_RECOVERY",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "LOAD_PATH_KEY",
    "Recovery",
    "checkpoint_prefix_for",
    "command_with_load_path",
    "dispatch_command",
    "newest_checkpoint",
    "read_intent",
]


@dataclass(frozen=True)
class Recovery:
    dead_run_id: str
    load_path: str
    newest_checkpoint: str
    manifest: Any


def read_intent(client: Any, *, bucket: str, run_id: str) -> IntentRecord:
    """The dead run's intent record, validated rather than merely parsed.

    Through the contract, so a record this tool cannot fully understand is refused here instead
    of yielding a manifest with a field quietly missing. A recovery composed from a
    half-understood original is the failure this whole file is about.
    """
    key = INTENT_KEY.format(run_id=run_id)
    try:
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:
        message = (
            f"no intent record at s3://{bucket}/{key}. Either the run id is wrong, or that "
            f"submission never reached admission. ({exc})"
        )
        raise LookupError(message) from exc
    return IntentRecord.model_validate(json.loads(body))


def checkpoint_prefix_for(*, team: str, run_id: str) -> str:
    """Where the dead run wrote its checkpoints.

    Derived through ``output_prefix`` rather than assembled here, because that function exists
    precisely because three places once answered this question and two of them agreed. A fourth
    answer in this file would be the same defect with a newer date on it.
    """
    return output_prefix(team=team, run_id=run_id) + "checkpoints/"


def newest_checkpoint(client: Any, load_path: str) -> str | None:
    """The last checkpoint the dead run managed to write, or None if it wrote none.

    None is the answer that stops a recovery. A prefix with nothing under it means the run died
    before its first save interval, and a submission pointed at it trains from step 0 while
    reporting a successful resume -- so there is nothing here worth composing, and the caller
    turns this into an error rather than a note.
    """
    bucket, _, prefix = load_path.removeprefix("s3://").partition("/")
    paginator = client.get_paginator("list_objects_v2")
    latest: tuple[Any, str] | None = None
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for entry in page.get("Contents", []):
            stamp = entry.get("LastModified")
            key = str(entry.get("Key", ""))
            if stamp is None or not key:
                continue
            if latest is None or stamp > latest[0]:
                latest = (stamp, key)
    return None if latest is None else f"s3://{bucket}/{latest[1]}"


def command_with_load_path(command: str, load_path: str) -> str:
    """The original command, unchanged, plus the one thing a recovery adds.

    APPENDED RATHER THAN REBUILT, AND THAT IS THE POINT OF THE FUNCTION. Everything that decides
    what the run trains -- the step bound, the schedule, the batch size, the data mix -- travels
    in this string, and the one edit a recovery needs is orthogonal to all of it. Rebuilding the
    command from parsed parts would put every one of those values through this tool's
    understanding of it, which is exactly the exposure that makes a shortened bound possible.

    A command that already carries a load path is rewritten rather than given a second one, so
    recovering a recovery works and does not produce two keys whose winner depends on how
    OLMo-core parses duplicates.
    """
    assignment = f"{LOAD_PATH_KEY}={load_path}"
    words = shlex.split(command)
    rewritten = [
        assignment if word.startswith(f"{LOAD_PATH_KEY}=") else word for word in words
    ]
    if rewritten == words:
        rewritten.append(assignment)
    return shlex.join(rewritten)


def dispatch_command(recovery: Recovery, *, workflow: str = "submit-run.yml") -> str:
    """The literal dispatch, with every field carried from the original submission.

    Printed as one command so it can be pasted rather than transcribed field by field. Field
    order follows the form's own so a reader can check it against the page.
    """
    manifest = recovery.manifest
    fields = [
        ("repository", manifest.repository),
        ("commit_sha", manifest.commit_sha),
        ("image_digest", manifest.image_digest),
        ("workload_profile", manifest.workload_profile),
        ("compute_profile", manifest.compute_profile),
        ("dataset_release", manifest.dataset_release),
        ("team", manifest.team),
        ("wandb_project", manifest.wandb_project),
        ("command", command_with_load_path(manifest.command, recovery.load_path)),
        ("maximum_runtime_hours", str(manifest.maximum_runtime_hours)),
        ("maximum_attempts", str(manifest.maximum_attempts)),
    ]
    lines = [f"gh workflow run {workflow} \\"]
    lines += [f"  -f {name}={shlex.quote(value)} \\" for name, value in fields[:-1]]
    last, value = fields[-1]
    lines += [f"  -f {last}={shlex.quote(value)}"]
    return "\n".join(lines)


def describe(recovery: Recovery) -> str:
    manifest = recovery.manifest
    return "\n".join(
        [
            f"recovering       {recovery.dead_run_id}",
            f"newest saved     {recovery.newest_checkpoint}",
            f"resuming from    {recovery.load_path}",
            "",
            "THE BOUND BELOW IS THE DEAD RUN'S OWN AND MUST NOT BE SHORTENED. Restoring correct",
            "weights onto a shorter schedule reports nothing wrong and trains a worse model.",
            f"It is carried through from the intent record: {manifest.maximum_runtime_hours}h,",
            f"and the command is byte-for-byte the original plus {LOAD_PATH_KEY}.",
            "",
            dispatch_command(recovery),
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id", help="the run that died, whose checkpoints the next one reads")
    parser.add_argument("--lineage-bucket", default="sbsandbox-intern-edullm-lineage")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--aws-profile", default=None, help="a named AWS credential profile")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    if not arguments.run_id.startswith("run_"):
        print(f"{arguments.run_id} is not a run id", file=sys.stderr)
        return EXIT_UNUSABLE

    import boto3  # type: ignore[import-not-found]  # in the runtime, not in pyproject

    session = boto3.Session(profile_name=arguments.aws_profile, region_name=arguments.region)
    client = session.client("s3")

    try:
        intent = read_intent(client, bucket=arguments.lineage_bucket, run_id=arguments.run_id)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_UNUSABLE
    except ValueError as exc:
        print(f"the intent record could not be read as one: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    load_path = checkpoint_prefix_for(team=intent.manifest.team, run_id=arguments.run_id)
    newest = newest_checkpoint(client, load_path)
    if newest is None:
        print(
            f"{arguments.run_id} wrote nothing under {load_path}, so there is nothing to recover "
            "from. A submission pointed at an empty prefix trains from step 0 and reports a "
            "successful resume, which is worse than not submitting one. Start a fresh run "
            "instead, deliberately.",
            file=sys.stderr,
        )
        return EXIT_NO_RECOVERY

    print(
        describe(
            Recovery(
                dead_run_id=arguments.run_id,
                load_path=load_path,
                newest_checkpoint=newest,
                manifest=intent.manifest,
            )
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
