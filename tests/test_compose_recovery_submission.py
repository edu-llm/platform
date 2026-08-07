"""The recovery composer, against the one mistake it exists to make impossible.

``test_the_bound_travels_through_untouched`` is the reason this file exists. Everything else here
protects that property from being refactored away.
"""

from __future__ import annotations

import shlex
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from tools.compose_recovery_submission import (
    LOAD_PATH_KEY,
    Recovery,
    checkpoint_prefix_for,
    command_with_load_path,
    describe,
    dispatch_command,
    newest_checkpoint,
    read_intent,
)

DEAD = "run_019fdd8f-ad71-7000-8000-000000000001"
TEAM = "curriculum-learning"
#: A bound in the command as well as in the manifest, which is how OLMo-core carries it.
COMMAND = (
    "torchrun --nproc-per-node 8 -m olmo_core.train "
    "--steps 44000 --save-folder \"$EDULLM_CHECKPOINT_DIR\""
)


class Manifest:
    """Only the fields the composer reads. A stand-in rather than a built RunManifest, so the
    test does not have to satisfy validators unrelated to what is being asserted."""

    repository = "OLMo-core"
    commit_sha = "08df5aa0100000000000000000000000000000ab"
    image_digest = "sha256:" + "a" * 64
    workload_profile = "olmo-core-train"
    compute_profile = "gpu-8xb200"
    dataset_release = "regmix-10b-v1"
    team = TEAM
    wandb_project = "edullm"
    command = COMMAND
    maximum_runtime_hours = Decimal(23)
    maximum_attempts = 2


def recovery(**overrides: Any) -> Recovery:
    fields: dict[str, Any] = {
        "dead_run_id": DEAD,
        "load_path": checkpoint_prefix_for(team=TEAM, run_id=DEAD),
        "newest_checkpoint": f"s3://sbsandbox-intern-edullm-outputs/teams/{TEAM}/runs/{DEAD}/checkpoints/step31000/model.pt",
        "manifest": Manifest(),
    }
    fields.update(overrides)
    return Recovery(**fields)


def test_the_bound_travels_through_untouched() -> None:
    """Mutation: recompose the command from parsed parts, or accept a --steps argument.

    THE FAILURE THIS PREVENTS DOES NOT LOOK LIKE A FAILURE. A recovery with a shorter bound
    restores the correct weights onto a different learning-rate curve. The optimizer state is
    right, the schedule is not, nothing errors, and the model is worse. So the bound is not
    something this tool computes, defaults, or can be told -- it is carried, and the assertion is
    that the original characters are still present.
    """
    composed = command_with_load_path(COMMAND, "s3://bucket/prefix/")

    # Compared as words rather than as a string, because shlex.join re-quotes and the claim is
    # about what the shell will receive, not about the characters in between.
    original = shlex.split(COMMAND)
    produced = shlex.split(composed)

    assert produced[: len(original)] == original, "the original command was not carried verbatim"
    assert produced[len(original) :] == [f"{LOAD_PATH_KEY}=s3://bucket/prefix/"], (
        "a recovery adds exactly one word and changes none"
    )
    assert "--steps" in produced and produced[produced.index("--steps") + 1] == "44000"

    # And nothing in the tool's own surface offers a way to supply one.
    from tools.compose_recovery_submission import build_parser

    options = {action.dest for action in build_parser()._actions}
    assert "steps" not in options
    assert "maximum_runtime_hours" not in options
    assert "command" not in options


def test_the_emitted_dispatch_carries_the_original_bound_in_both_places() -> None:
    """The bound lives in the command and in a form field, and a recovery that shortened either
    would be wrong. Both come off the intent record."""
    emitted = dispatch_command(recovery())

    assert "-f maximum_runtime_hours=23" in emitted
    assert "--steps 44000" in emitted
    assert "-f compute_profile=gpu-8xb200" in emitted
    assert "-f workload_profile=olmo-core-train" in emitted
    # The commit and digest are the dead run's, so the recovery runs the same code on the same
    # image. A recovery on a newer commit is a different experiment wearing a recovery's name.
    assert "-f commit_sha=08df5aa0100000000000000000000000000000ab" in emitted
    assert f"-f image_digest={Manifest.image_digest}" in emitted


def test_recovering_a_recovery_rewrites_the_load_path_rather_than_adding_a_second() -> None:
    """Mutation: always append. Two load paths in one command leaves the winner to whatever
    OLMo-core does with duplicate keys, which is not a thing to discover inside a paid window."""
    once = command_with_load_path(COMMAND, "s3://bucket/first/")
    twice = command_with_load_path(once, "s3://bucket/second/")

    assert twice.count(f"{LOAD_PATH_KEY}=") == 1
    assert "s3://bucket/second/" in twice
    assert "s3://bucket/first/" not in twice
    assert "--steps 44000" in twice


def test_the_prefix_is_derived_through_the_one_function_that_owns_it() -> None:
    """Mutation: assemble the prefix here. output_prefix exists because three places once
    answered this and two agreed; a fourth answer in the recovery tool would be that defect with
    a newer date."""
    prefix = checkpoint_prefix_for(team=TEAM, run_id=DEAD)

    assert prefix.startswith("s3://sbsandbox-intern-edullm-outputs/teams/")
    assert prefix.endswith(f"/runs/{DEAD}/checkpoints/")


def test_a_run_that_saved_nothing_yields_no_recovery() -> None:
    """Mutation: return the prefix anyway and let the submitter decide.

    A submission pointed at an empty prefix trains from step 0 and reports a successful resume.
    That is the same silent failure as a shortened bound, so an empty prefix is an error and not
    a warning -- there is nothing worth composing.
    """

    class Empty:
        def get_paginator(self, _: str) -> Any:
            class Pager:
                def paginate(self, **__: Any) -> list[dict[str, list[Any]]]:
                    return [{}]

            return Pager()

    assert newest_checkpoint(Empty(), "s3://bucket/prefix/") is None


def test_the_newest_checkpoint_is_the_newest_and_not_the_last_listed() -> None:
    """S3 lists lexicographically, so step9 sorts after step31000. Picking the last key would
    resume from an earlier checkpoint than the run reached and silently lose work."""

    class Listing:
        def get_paginator(self, _: str) -> Any:
            class Pager:
                def paginate(self, **__: Any) -> list[dict[str, list[dict[str, Any]]]]:
                    return [
                        {
                            "Contents": [
                                {
                                    "Key": "prefix/step31000/model.pt",
                                    "LastModified": datetime(2026, 8, 9, 20, tzinfo=UTC),
                                },
                                {
                                    "Key": "prefix/step9/model.pt",
                                    "LastModified": datetime(2026, 8, 9, 12, tzinfo=UTC),
                                },
                            ]
                        }
                    ]

            return Pager()

    assert newest_checkpoint(Listing(), "s3://bucket/prefix/") == "s3://bucket/prefix/step31000/model.pt"


def test_a_missing_intent_record_says_which_of_two_things_is_wrong() -> None:
    class Absent:
        def get_object(self, **_: Any) -> dict[str, Any]:
            raise KeyError("NoSuchKey")

    with pytest.raises(LookupError) as caught:
        read_intent(Absent(), bucket="lineage", run_id=DEAD)

    assert "run id is wrong" in str(caught.value)
    assert "never reached admission" in str(caught.value)


def test_the_printed_output_warns_about_the_bound_in_the_place_it_is_read() -> None:
    """Mutation: put the warning in the module docstring only.

    Nobody reads a docstring at three in the morning; they read what the terminal printed. So the
    sentence sits above the command it is about.
    """
    rendered = describe(recovery())

    assert "MUST NOT BE SHORTENED" in rendered
    assert "worse model" in rendered
    assert rendered.index("MUST NOT BE SHORTENED") < rendered.index("gh workflow run")
