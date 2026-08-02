"""Where a run's objects live, and which of the two ways its corpus was named.

Every function here is a pure join over strings. Nothing in this module opens a socket,
imports boto3 or consults the environment, so a program can print the location it is about
to write to before it has credentials to write anything.

**These are a restatement, and the restatement is the reason this package exists.** The
platform's own :mod:`edullm_platform.contracts.results` is the authority on the layout, and
it is not importable in a research container because installing it would install pydantic
and PyYAML beside somebody else's torch. Four repositories reconstructed the layout by hand
instead, which is four places it can drift. One restatement held to the original by a test
is a smaller surface than four reconstructions held to nothing.

``tests/test_client_layout_matches_the_platform.py`` is where that test lives. It imports
both and compares them, so the day the platform moves a prefix the client goes red in the
platform's own suite rather than in a training run six weeks later.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

__all__ = [
    "OUTPUTS_BUCKET",
    "PUBLISHED_DATASET_BUCKET",
    "DatasetLocation",
    "UnresolvedDatasetError",
    "checkpoint_prefix",
    "output_prefix",
    "published_dataset_uri",
    "resolve_dataset",
    "team_dataset_prefix",
]

#: The bucket a workload writes its own output to. Never the lineage bucket, whose entire
#: property is that only the platform writes to it.
OUTPUTS_BUCKET: Final = "sbsandbox-intern-edullm-outputs"

#: The sealed bucket the published corpora live in, owned by the dataset project rather than
#: by this account. A run reads from it and can never write to it.
PUBLISHED_DATASET_BUCKET: Final = "edullm-data"


def output_prefix(*, team: str, run_id: str, bucket: str = OUTPUTS_BUCKET) -> str:
    """Where one run's output goes.

    Both segments earn their place. ``teams/{team}`` is what makes cross-team isolation
    expressible in IAM at all, because a prefix condition can be written against it and the
    workload role is scoped with one. ``runs/{run_id}`` is what makes the platform's record
    of where a run wrote a claim a reader can follow rather than a decoration.

    **A container should prefer ``EDULLM_OUTPUT_PREFIX`` to calling this.** The platform
    sends the whole prefix precisely so that the container is not the thing that decides
    where its own output goes, and a container that computed a different answer would be
    denied by the workload role at the end of a run rather than at the start of one. This
    function is for the callers that have no such variable, which is every tool that reasons
    about a run it is not inside.

    Neither argument is defaulted. A default team is a run whose output is filed under
    somebody else's group on the one path where the team was not resolved, in a store
    nothing rewrites.
    """
    if not team:
        raise ValueError("an output prefix needs the team the run is charged to")
    if not run_id:
        raise ValueError("an output prefix needs the run it belongs to")
    return f"s3://{bucket}/teams/{team}/runs/{run_id}/"


def checkpoint_prefix(*, team: str, run_id: str, bucket: str = OUTPUTS_BUCKET) -> str:
    """Where a run's checkpoints go, which is one subprefix inside its output.

    Its own function rather than a caller's concatenation, because the suffix is the thing
    a retry has to find. Batch starts the second attempt with the same run id and therefore
    the same location, and a caller that wrote ``checkpoint/`` or omitted the trailing
    slash would produce a resume that silently starts from nothing while the first attempt's
    checkpoints sit one prefix away.
    """
    return output_prefix(team=team, run_id=run_id, bucket=bucket) + "checkpoints/"


def team_dataset_prefix(*, team: str, name: str, bucket: str = OUTPUTS_BUCKET) -> str:
    """Where a group keeps a corpus of its own, which is not one the platform published.

    Under ``teams/{team}/`` beside ``runs/`` rather than under a run, because a dataset
    outlives the job that produced it and a tokenization output filed under its producing
    run is one the next run cannot name without knowing that run's id.

    This is the writable half of the corpus story. Anything under it was written by a job in
    this account and carries no manifest digest, no version and no tokenizer record, which
    is exactly the difference between it and :func:`published_dataset_uri`.
    """
    if not team:
        raise ValueError("a dataset prefix needs the team that owns it")
    if not name:
        raise ValueError("a dataset prefix needs the name of the dataset")
    return f"s3://{bucket}/teams/{team}/datasets/{name}/"


def published_dataset_uri(
    *, dataset_id: str, version: str, bucket: str = PUBLISHED_DATASET_BUCKET
) -> str:
    """Where a published corpus lives, from the two facts the platform resolved it to.

    The id and the version are taken as two arguments rather than split out of a URI a
    caller assembled, for the reason the platform's registry gives about the same pair. The
    id carries slashes of its own, ``pretrain/regmix-10b`` being one, so a caller splitting
    a URI on the last two segments reads a version nobody registered whenever the id has a
    different depth than the one they tested against.
    """
    if not dataset_id:
        raise ValueError("a published dataset uri needs the dataset id the registry resolved")
    if not version:
        raise ValueError("a published dataset uri needs the version the registry resolved")
    return f"s3://{bucket}/{dataset_id}/{version}/"


class UnresolvedDatasetError(LookupError):
    """A run asked where its corpus is and neither of the two ways of saying has an answer.

    RAISED RATHER THAN WARNED, WHICH IS THE OPPOSITE OF WHAT THIS PACKAGE DOES FOR W&B, AND
    THE ASYMMETRY IS THE POINT. Telemetry that cannot start costs a run its dashboard.
    Training data that cannot be located costs a run its meaning, and the shapes that follow
    from continuing are all worse than stopping. A trainer handed no shards either dies
    twenty minutes later inside a DataLoader, with a message about an empty index rather
    than about a corpus, or falls back to whatever its own config named and produces a loss
    curve for something nobody chose.

    It is also cheap here. This is reached in the first second of a job, before a device is
    touched, so the refusal costs the queue wait and nothing else.
    """


@dataclass(frozen=True)
class DatasetLocation:
    """What a run reads, in whichever of the two ways it was named.

    One type for both modes rather than a union, because every caller does the same two
    things with it: passes ``paths`` to a reader, and passes ``tokenizer`` to whatever
    builds the vocabulary. Making the caller branch on which mode it got would push the
    distinction into thirty training scripts, and the distinction is only interesting to the
    two or three that record provenance.
    """

    #: ``published`` when the platform resolved a registered corpus for this run, and
    #: ``explicit`` when the caller named its own locations.
    mode: Literal["published", "explicit"]
    #: Every location the corpus occupies, each a fully qualified ``s3://`` URI. One entry
    #: in published mode, since a published corpus is one sealed prefix.
    paths: tuple[str, ...]
    #: The three facts the registry resolved, present in published mode and absent in
    #: explicit mode. ``tokenizer`` is the one that matters most and the one most likely to
    #: be dropped, because a mismatched tokenizer's ids usually still fall inside the
    #: embedding table, so the only symptom is a loss curve that is merely bad.
    dataset_id: str | None = None
    version: str | None = None
    tokenizer: str | None = None


def resolve_dataset(
    environment: Mapping[str, str | None],
    *,
    paths: Sequence[str] | None = None,
) -> DatasetLocation:
    """Where this run's corpus is, from the environment or from what the caller names.

    Takes a mapping rather than a :class:`~edullm_client.environment.RunEnvironment` so that
    a tool holding three strings can call it without constructing a whole run, and so that
    this module stays free of any import from its sibling. ``RunEnvironment.dataset`` is the
    call a training script makes.

    The two modes, in the order they are tried.

    - **Explicit.** ``paths`` names locations directly. An entry already beginning with
      ``s3://`` is taken as written; a bare entry is resolved under the run's team dataset
      prefix, which is the only prefix outside a run's own output that a workload role may
      write to. This is the mode for a corpus a group tokenized for itself, and for the
      second half of a job that reads a published corpus and its own held-out split.
    - **Published.** The platform resolved the form's ``dataset_release`` to a dataset id, a
      version and a tokenizer, and put all three in the environment. Nothing is derived
      here; the three are read and the sealed URI is assembled from two of them.

    Explicit wins when both are available, because a caller that passed paths did so on
    purpose and silently reading somewhere else instead is the failure this whole package
    exists to stop. Nothing warns about the overlap, deliberately. A run may legitimately
    name a corpus on the form for the record and read a subset of it, and a warning that
    fires on a legitimate case is a warning people learn to scroll past.
    """
    if paths is not None:
        if not paths:
            raise UnresolvedDatasetError(
                "resolve_dataset was given an empty sequence of paths. Pass None to fall "
                "back to the corpus the platform resolved, or pass the locations to read"
            )
        team = environment.get("EDULLM_TEAM")
        return DatasetLocation(
            mode="explicit",
            paths=tuple(_qualify(path, team=team) for path in paths),
        )

    dataset_id = environment.get("EDULLM_DATASET_ID") or None
    version = environment.get("EDULLM_DATASET_VERSION") or None
    tokenizer = environment.get("EDULLM_DATASET_TOKENIZER") or None
    trio = {
        "EDULLM_DATASET_ID": dataset_id,
        "EDULLM_DATASET_VERSION": version,
        "EDULLM_DATASET_TOKENIZER": tokenizer,
    }
    missing = sorted(name for name, value in trio.items() if value is None)
    if len(missing) == len(trio):
        release = environment.get("EDULLM_DATASET_RELEASE") or "none"
        raise UnresolvedDatasetError(
            f"this run named dataset_release {release!r}, so the platform resolved no "
            "published corpus for it and the environment carries no dataset id, version or "
            "tokenizer. Either submit with a corpus selected on the form, or pass paths= "
            "with the locations this run should read"
        )
    if dataset_id is None or version is None or tokenizer is None:
        # PART OF THE TRIO AND NOT ALL OF IT, WHICH IS THE ONE STATE WORTH REFUSING OVER.
        # The platform appends the three together or not at all, so this cannot happen to a
        # container the platform started. It happens to a container somebody exported two of
        # them into by hand while reproducing a run, and the shape of that mistake is a
        # correct corpus opened with the wrong tokenizer. That failure does not raise
        # anywhere downstream, because a mismatched tokenizer's ids still index the
        # embedding table, so this is the last place it can be caught at all.
        raise UnresolvedDatasetError(
            "the environment carries part of a published corpus and not all of it, with "
            f"{', '.join(missing)} unset. A corpus opened with a tokenizer that is not the "
            "one it was written with produces a plausible loss curve rather than an error, "
            "so a partial resolution is refused instead of half used"
        )
    return DatasetLocation(
        mode="published",
        paths=(published_dataset_uri(dataset_id=dataset_id, version=version),),
        dataset_id=dataset_id,
        version=version,
        tokenizer=tokenizer,
    )


def _qualify(path: str, *, team: str | None) -> str:
    """One explicit entry as a full URI, resolving a bare name under the team's datasets.

    A bare name is the common case and the one worth supporting, because the alternative is
    every script formatting the same prefix inline. It needs the team, and a run that has
    somehow lost ``EDULLM_TEAM`` cannot be given a default one, so that case is refused with
    the two ways out rather than filed under a group it does not belong to.
    """
    if path.startswith("s3://"):
        return path
    if not team:
        raise UnresolvedDatasetError(
            f"{path!r} is a bare name and resolving it needs EDULLM_TEAM, which this "
            "environment does not carry. Pass a full s3:// uri, or run this inside a job "
            "the platform started"
        )
    return team_dataset_prefix(team=team, name=path.strip("/"))
