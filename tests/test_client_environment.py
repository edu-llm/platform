"""Reading a run out of its container, and finding the corpus it was given.

What ``tests/test_client_layout.py`` holds is that the client agrees with the platform.
What this holds is the behaviour a researcher meets when something is not as the platform
left it, which is most of the time this package is read by a human. A training script is
written on a laptop with none of these variables set, run once inside a job that has all of
them, and debugged afterwards by somebody exporting three of them by hand.

The three states that produces are each covered below, because they fail differently and
only one of them fails loudly on its own.
"""

from __future__ import annotations

import pytest
from edullm_client import (
    MissingRunEnvironmentError,
    UnresolvedDatasetError,
    run_environment,
)
from edullm_client.environment import OPTIONAL_VARIABLES, REQUIRED_VARIABLES

RUN_ID = "run_019fbddb-5125-7045-95aa-1951e5ca3f31"
TEAM = "memory-split"
OUTPUT_PREFIX = f"s3://sbsandbox-intern-edullm-outputs/teams/{TEAM}/runs/{RUN_ID}/"


def container(**changes: str | None) -> dict[str, str]:
    """A container the platform started, with whatever this test needs changed or removed.

    Built here rather than read from the evidence tree, because these tests are about
    absence and a captured job carries everything. ``tests/test_client_layout.py`` is where
    the same reader is checked against a real capture, so the two together cover both that
    this mapping is the right shape and that the right shape is handled.
    """
    base: dict[str, str | None] = {
        "EDULLM_RUN_ID": RUN_ID,
        "EDULLM_TEAM": TEAM,
        "EDULLM_COMMIT_SHA": "298afac6e1e4a5b6c7d8e9f0a1b2c3d4e5f60718",
        "EDULLM_DATASET_RELEASE": "regmix-10b-v1",
        "EDULLM_OUTPUT_BUCKET": "sbsandbox-intern-edullm-outputs",
        "EDULLM_OUTPUT_PREFIX": OUTPUT_PREFIX,
        "EDULLM_CHECKPOINT_DIR": OUTPUT_PREFIX + "checkpoints/",
        "EDULLM_WANDB_PROJECT": "olmo-core-memory-split",
        "WANDB_PROJECT": "olmo-core-memory-split",
        "WANDB_ENTITY": "eduLLM",
        "WANDB_RUN_GROUP": "an-ablation",
        "WANDB_USERNAME": "philote",
        "EDULLM_DATASET_ID": "pretrain/regmix-10b",
        "EDULLM_DATASET_VERSION": "v1",
        "EDULLM_DATASET_TOKENIZER": "tokenizer/dolma2-bpe",
    }
    base.update(changes)
    return {name: value for name, value in base.items() if value is not None}


def test_a_container_the_platform_started_reads_as_the_run_it_is() -> None:
    run = run_environment(container())

    assert run.run_id == RUN_ID
    assert run.team == TEAM
    assert run.dataset_release == "regmix-10b-v1"
    assert run.output_prefix == OUTPUT_PREFIX
    assert run.checkpoint_dir == OUTPUT_PREFIX + "checkpoints/"
    assert run.wandb_project == "olmo-core-memory-split"
    assert run.experiment == "an-ablation"
    assert run.wandb_username == "philote"


def test_every_missing_variable_is_named_at_once() -> None:
    """Mutation: raise on the first absent name instead of collecting them.

    This is the message somebody reproducing a run on a laptop meets, and the difference
    between the two implementations is the difference between one edit to a shell profile
    and ten runs of the same script. Inside a real job it is worse than that, because each
    round trip is a submission, an approval and a queue wait.

    Asserted over the whole required set rather than a sample, so a variable added to that
    tuple without being added to the message cannot pass.
    """
    with pytest.raises(MissingRunEnvironmentError) as raised:
        run_environment({})

    reported = str(raised.value)
    for name in REQUIRED_VARIABLES:
        assert name in reported, f"{name} is required and the refusal does not mention it"


def test_an_empty_value_is_an_absence_rather_than_a_location_called_nothing() -> None:
    """Mutation: test presence with ``in`` instead of truthiness.

    Batch carries an empty string through to the container unchanged, and every variable
    here names something. An empty output prefix accepted as a value is a training script
    that computes ``"" + "checkpoints/"`` and writes to a relative path on the instance,
    which is the local-disk failure the platform's checkpoint guard exists to prevent,
    reached by a different route.
    """
    with pytest.raises(MissingRunEnvironmentError, match="EDULLM_OUTPUT_PREFIX"):
        run_environment(container(EDULLM_OUTPUT_PREFIX=""))


def test_the_optional_half_is_absent_rather_than_wrong_on_an_ordinary_run() -> None:
    """A run with no experiment, no attributed submitter and no published corpus.

    Every one of these is ordinary. Most of the roster has no W&B account, ``none`` is the
    common answer for a corpus, and a manifest compiled before the experiment field existed
    carries no group. A client that required any of them would refuse runs the platform
    admits.
    """
    lean = container(**dict.fromkeys(OPTIONAL_VARIABLES, None))

    run = run_environment(lean)

    assert run.experiment is None
    assert run.wandb_username is None
    assert run.dataset_id is None
    assert run.dataset_tokenizer is None


def test_a_run_written_back_out_is_the_run_that_was_read() -> None:
    """``as_environment`` is what a launcher hands a child process.

    The round trip has to be exact, because the child is usually a rank started by
    ``torch.distributed.run`` and a rank that disagrees with rank zero about the checkpoint
    directory writes a shard of a checkpoint somewhere the others will not look for it.

    The only asymmetry is deliberate and is asserted rather than tolerated. Both spellings
    of the project come back from the one field the reader kept, so a container whose two
    spellings had drifted apart is normalised on the way through rather than passed on.
    """
    original = container()

    round_tripped = run_environment(original).as_environment()

    assert round_tripped == original
    assert set(round_tripped) == set(REQUIRED_VARIABLES) | set(OPTIONAL_VARIABLES)


def test_a_published_corpus_resolves_to_the_sealed_prefix_it_lives_at() -> None:
    location = run_environment(container()).dataset()

    assert location.mode == "published"
    assert location.paths == ("s3://edullm-data/pretrain/regmix-10b/v1/",)
    assert location.dataset_id == "pretrain/regmix-10b"
    assert location.version == "v1"
    assert location.tokenizer == "tokenizer/dolma2-bpe"


def test_a_run_that_named_no_corpus_is_refused_rather_than_given_an_empty_one() -> None:
    """Mutation: return an empty ``DatasetLocation`` instead of raising.

    This is the one place in the package that raises where it could have returned nothing,
    and the asymmetry with the W&B path is the point. A trainer handed no shards does not
    stop. It either dies twenty minutes later inside a DataLoader with a message about an
    empty index, or falls back to whatever its own config named and produces a clean loss
    curve for a corpus nobody chose. Both are more expensive than a refusal in the first
    second, and the refusal names both ways out.
    """
    none_selected = container(
        EDULLM_DATASET_RELEASE="none",
        EDULLM_DATASET_ID=None,
        EDULLM_DATASET_VERSION=None,
        EDULLM_DATASET_TOKENIZER=None,
    )

    with pytest.raises(UnresolvedDatasetError) as raised:
        run_environment(none_selected).dataset()

    reported = str(raised.value)
    assert "'none'" in reported
    assert "paths=" in reported, "the refusal does not say how to name a corpus by hand"


def test_two_thirds_of_a_corpus_is_refused_because_the_third_is_the_tokenizer() -> None:
    """Mutation: fill the absent field with a default, or resolve what is present.

    THE STATE THIS GUARDS IS NOT ONE THE PLATFORM CAN PRODUCE. The three variables are
    appended together or not at all. It is produced by a person exporting two of them into a
    shell while reproducing a run, which is a routine thing to do and is why this is worth a
    refusal rather than a comment.

    The tokenizer is the field that makes it worth refusing over. A corpus opened with the
    wrong tokenizer does not raise, because the ids still fall inside the embedding table.
    The only symptom is a loss curve that is merely bad, which reads as a hyperparameter
    problem and gets debugged as one.
    """
    half_exported = container(EDULLM_DATASET_TOKENIZER=None)

    with pytest.raises(UnresolvedDatasetError, match="EDULLM_DATASET_TOKENIZER"):
        run_environment(half_exported).dataset()


def test_explicit_paths_are_taken_as_written_when_they_are_already_locations() -> None:
    """The mode for a corpus this platform did not publish.

    A full URI passes through untouched, including one pointing at the sealed bucket, so a
    script reading a subset of a published corpus is not forced to go through the registry
    for a prefix it already has.
    """
    named = ["s3://edullm-data/pretrain/regmix-10b/v1/", "s3://somewhere-else/shards/"]

    location = run_environment(container()).dataset(paths=named)

    assert location.mode == "explicit"
    assert location.paths == tuple(named)
    assert location.tokenizer is None, (
        "explicit mode resolved a tokenizer, which means it read one from the environment "
        "for a corpus that environment does not describe"
    )


def test_a_bare_name_resolves_under_the_team_that_owns_it() -> None:
    """The shape a data-prep job's output is read back at.

    A group that tokenizes a corpus for itself writes it under ``teams/{team}/datasets/``,
    which is the only prefix outside a run's own output that the workload role may write to.
    Supporting the bare name is what keeps thirty scripts from formatting that prefix inline,
    and formatting it inline is how the copies this package replaced started.
    """
    location = run_environment(container()).dataset(paths=["tokenized-2026-08"])

    assert location.paths == (
        f"s3://sbsandbox-intern-edullm-outputs/teams/{TEAM}/datasets/tokenized-2026-08/",
    )


def test_explicit_paths_win_over_a_corpus_the_form_also_named() -> None:
    """Mutation: prefer the environment, or refuse the overlap.

    Both alternatives are worse than this. Preferring the environment means a script that
    passed paths reads somewhere else without saying so, which is the exact class of silent
    substitution this package exists to remove. Refusing means a run that names a corpus on
    the form for the record and reads a subset of it cannot be expressed, and that run is
    legitimate.

    Nothing warns, deliberately. A warning that fires on a legitimate case is one people
    learn to scroll past, and this log is already the place a real failure has to be visible.
    """
    location = run_environment(container()).dataset(paths=["held-out"])

    assert location.mode == "explicit"
    assert location.dataset_id is None


def test_an_empty_list_of_paths_is_a_mistake_rather_than_a_fallback() -> None:
    """Mutation: treat ``[]`` the same as ``None``.

    They arrive from different places. ``None`` is a caller that did not ask for explicit
    mode, and ``[]`` is a caller whose own path-building produced nothing, usually a glob
    that matched no shard. Falling back to the published corpus for the second one turns a
    script that meant to read one thing into one that quietly reads another.
    """
    with pytest.raises(UnresolvedDatasetError):
        run_environment(container()).dataset(paths=[])
