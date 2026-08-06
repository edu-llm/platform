"""The thin client's restatement of the layout, held to the thing it restates.

``edullm_client`` exists so that a research container can join an S3 path and open a W&B run
without installing ``edullm_platform``, and the price of that is a second copy of the bucket
names, the prefix shape and the W&B entity. A second copy that nothing compares is exactly
the drift the package was written to end, in one more place rather than four fewer.

**So the comparison happens here, in the platform's own suite, rather than in the client's.**
This process can import both. A container can import only one of them. That asymmetry is
what makes this the right home for these assertions and is also why the client ships no
tests of its own for anything the platform already owns.

Every test below fails on a change to the platform, not to the client. That is the direction
that matters. Nobody editing ``contracts/results.py`` is thinking about a package in
``client/``, and the failure they get here is the reminder, with the two values printed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import edullm_client
from edullm_client.environment import (
    INTERPRETER_VARIABLES,
    OPTIONAL_VARIABLES,
    REQUIRED_VARIABLES,
)

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset import PUBLISHED_DATASET_BUCKET
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.results import OUTPUTS_BUCKET, output_prefix
from edullm_platform.execution import CONTAINER_SHAPES, WANDB_ENTITY, batch_submit_request
from tests.test_phase3_execution import RUN_ID, manifest, target
from tests.test_phase4_training_submission import published_reference

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPTURED_RUNS = PROJECT_ROOT / "fixtures" / "evidence" / "phase-4" / "runs"

#: Teams from the form's closed dropdown plus one that is only a lead's, because the shape
#: of a prefix should not depend on which group is in it and a single example cannot say so.
TEAMS = ("scratch", "memory-split", "data-prep", "eval-inference")


def test_the_client_writes_a_run_where_the_platform_says_a_run_writes() -> None:
    """Mutation: move ``runs/`` or drop the trailing slash in either implementation.

    The workload role is scoped with a prefix condition against this exact shape, so a
    client that produced ``teams/{team}/{run_id}/`` would hand a training script a location
    every write is denied at. That denial arrives on the first checkpoint, which on a
    twelve-hour run at a thirty-minute interval is half an hour of GPU time after the
    mistake, and the message names an S3 key rather than a layout.

    Compared over several teams rather than one, because a hard-coded team inside either
    implementation would agree with a single-example test perfectly.
    """
    for team in TEAMS:
        assert edullm_client.output_prefix(team=team, run_id=RUN_ID) == output_prefix(
            team=team, run_id=RUN_ID
        )


def test_the_client_names_the_two_buckets_the_contracts_name() -> None:
    """Mutation: rename either bucket on one side.

    These are separate constants in separate distributions and neither can import the
    other, so nothing but this connects them. The outputs bucket is the one a run writes to
    and the published bucket is the sealed one it reads, and confusing them is not a typo
    that fails cleanly. A run told to read from the outputs bucket lists a prefix it is
    permitted to list and finds nothing there.
    """
    assert edullm_client.OUTPUTS_BUCKET == OUTPUTS_BUCKET
    assert edullm_client.PUBLISHED_DATASET_BUCKET == PUBLISHED_DATASET_BUCKET


def test_the_client_logs_into_the_entity_the_injected_key_can_write_to() -> None:
    """Mutation: change the entity in either place.

    The key the job definition injects belongs to a team-scoped service account, so the
    entity is a property of that credential rather than a preference. A client naming a
    different one authenticates and then has nowhere to put anything, and W&B's documented
    behaviour for an unentitled team service account is to log into its parent team anyway,
    which is the same place right up until it is not.
    """
    assert edullm_client.WANDB_ENTITY == WANDB_ENTITY


def container_environment_names(**submission: Any) -> set[str]:
    """Every variable name one submitted container holds, from both mechanisms.

    Batch merges the registered job definition's declared environment with the submit
    request's override, so neither half alone is what a container gets. Reading both is the
    only way this comparison is about the container rather than about one API call.
    """
    request = batch_submit_request(
        manifest=manifest(),
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
        **submission,
    )
    names = {entry["Name"] for entry in request["ContainerOverrides"]["Environment"]}
    for shape in CONTAINER_SHAPES.values():
        names |= {name for name, _ in shape.default_environment}
    return names


def test_the_client_requires_nothing_the_container_is_not_given() -> None:
    """THE ONE THAT MATTERS. Mutation: require a variable the platform never sends.

    ``run_environment`` refuses outright when a required variable is absent, so a required
    set that is wider than what the platform sends is a client that cannot be used inside
    the platform at all. It fails on the first line of every training script, on every run,
    including the ones already approved.

    The required set is deliberately not the whole set. Three dataset variables are absent
    on a run that named ``none``, and two W&B variables are absent on a run with no
    experiment or no attributed submitter, so requiring the union would refuse ordinary
    runs. The minimum is checked against a submission carrying none of the optional halves,
    which is the leanest container the platform can start.
    """
    minimal = container_environment_names()

    missing = sorted(set(REQUIRED_VARIABLES) - minimal)
    assert missing == [], (
        f"edullm_client requires {missing}, which the platform does not put in the leanest "
        "container it starts. run_environment() would refuse every run"
    )


def test_the_client_knows_every_variable_the_container_is_given() -> None:
    """The other direction, and the one that rots quietly rather than loudly.

    Mutation: add a variable to ``batch_submit_request`` and not to the client. Nothing
    fails. The platform starts telling containers something and the package whose job is to
    present that to a researcher does not mention it, so thirty scripts go on reading
    ``os.environ`` directly for the one fact the client does not carry, which is how the
    four separate copies this package replaced came to exist in the first place.

    Checked against the fullest submission the platform can compile rather than the leanest,
    because the optional half is where new fields have been added twice already. ``submitter``
    is passed and does not appear, deliberately, since it is recorded as a Batch job tag and
    never reaches the container.

    ``INTERPRETER_VARIABLES`` is subtracted alongside the two the client presents, and is a
    third list rather than an exemption written into this test. Those names are read by
    CPython rather than by any workload, so presenting them as run facts would be inventing
    a question nobody asks -- but a name that is in none of the three lists is still a
    variable the platform started sending and the client is silent about, which is what this
    exists to catch. Declaring them keeps that catch exact for the next one.
    """
    fullest = container_environment_names(
        experiment="an-experiment",
        wandb_username="somebody",
        submitter="somebody",
        dataset_reference=published_reference("regmix-10b-v1"),
    )

    unknown = sorted(
        fullest
        - set(REQUIRED_VARIABLES)
        - set(OPTIONAL_VARIABLES)
        - set(INTERPRETER_VARIABLES)
    )
    assert unknown == [], (
        f"the platform gives a container {unknown} and edullm_client does not present "
        "them, so a script that needs one has to read os.environ itself"
    )


def newest_captured_container() -> dict[str, str]:
    """The environment of the most recently observed real job, as a mapping.

    Read from the phase 4 evidence tree rather than constructed, because a request this
    suite builds and a container AWS actually started are two different claims. The newest
    rather than all five, and the four older ones are not counter-examples: they ran before
    the checkpoint directory and the W&B variables were sent at all, so they are a record of
    the environment growing. Requiring every historical capture to satisfy today's client
    would pin the client to 2026-07-28.
    """
    captures = sorted(CAPTURED_RUNS.glob("*/batch-job.sanitized.json"))
    assert captures, "the phase 4 evidence tree records no batch job at all"
    jobs = [json.loads(path.read_text(encoding="utf-8")) for path in captures]
    newest = max(jobs, key=lambda job: str(job["observed_at"]))
    return {entry["name"]: entry["value"] for entry in newest["container_environment"]}


def test_the_client_reads_a_container_aws_actually_started() -> None:
    """Mutation: change any field's source variable in ``run_environment``.

    Every assertion above compares this repository against itself. This one compares the
    client against a capture of a job that ran, which is the only evidence here that the
    two mechanisms merge the way the code says they do. ``EDULLM_OUTPUT_BUCKET`` in
    particular is only ever sent by the job definition, and the sole proof that it survives
    into a container at all is that it is in this record.
    """
    captured = newest_captured_container()

    run = edullm_client.run_environment(captured)

    assert run.run_id == captured["EDULLM_RUN_ID"]
    assert run.team == captured["EDULLM_TEAM"]
    assert run.output_prefix == edullm_client.output_prefix(team=run.team, run_id=run.run_id)
    assert run.checkpoint_dir == edullm_client.checkpoint_prefix(
        team=run.team, run_id=run.run_id
    )
    assert run.output_bucket == edullm_client.OUTPUTS_BUCKET
    assert run.wandb_entity == edullm_client.WANDB_ENTITY
    assert run.experiment == captured["WANDB_RUN_GROUP"]


def test_the_client_resolves_a_captured_run_to_the_corpus_it_read() -> None:
    """The published path, against the same capture rather than against a fixture.

    A corpus URI that is wrong by one segment is a job that fails on a listing, which is
    recoverable. A corpus URI that is wrong because the id and the version were split out of
    a string is worse, because ``pretrain/regmix-10b`` has a slash in it and a split that
    assumed otherwise produces a URI that exists and holds a different corpus.
    """
    run = edullm_client.run_environment(newest_captured_container())

    location = run.dataset()

    assert location.mode == "published"
    assert location.dataset_id == "pretrain/regmix-10b"
    assert location.tokenizer == "tokenizer/dolma2-bpe"
    assert location.paths == ("s3://edullm-data/pretrain/regmix-10b/v1/",)


def test_every_published_corpus_is_where_the_client_would_look_for_it() -> None:
    """Mutation: change the join in ``published_dataset_uri``.

    Each registry entry carries the URI its own validator reconstructed from its id and its
    version, so this compares the client's join against a value a reviewer approved rather
    than against a second computation of the same join. Over every registered corpus rather
    than one, because the ids differ in depth and a client that got the shape right for
    ``pretrain/regmix-10b`` could still be wrong for one with another segment.
    """
    registry = load_yaml(PROJECT_ROOT / "config" / "datasets.yaml", DatasetRegistry)

    assert registry.published, "the registry lists no published corpus, so this proves nothing"
    for entry in registry.published:
        assert (
            edullm_client.published_dataset_uri(
                dataset_id=entry.dataset_id, version=entry.version
            )
            == entry.uri
        ), entry.reference_id


def test_a_team_dataset_sits_beside_the_runs_rather_than_inside_one() -> None:
    """The one prefix in this package that the platform does not also construct.

    Nothing in ``edullm_platform`` builds ``teams/{team}/datasets/{name}/``, so there is no
    second implementation to compare against and this test is the statement of the shape
    rather than a comparison. What it can still hold is the part that matters, which is that
    the prefix is inside the same ``teams/{team}/`` the workload role is scoped to. A group
    dataset written outside it is one every job is denied, and one written under a run is
    one the next run cannot name without knowing the id of the job that produced it.
    """
    prefix = edullm_client.team_dataset_prefix(team="data-prep", name="tokenized-2026-08")

    assert prefix == f"s3://{OUTPUTS_BUCKET}/teams/data-prep/datasets/tokenized-2026-08/"
    assert prefix.startswith(f"s3://{OUTPUTS_BUCKET}/teams/data-prep/")
    assert "/runs/" not in prefix
