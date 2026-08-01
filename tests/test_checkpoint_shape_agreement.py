"""What happens when OLMo-core changes the rule this platform mirrors.

The tool exists because two files agree today and nothing holds them together. These
exercise the agreement itself against the real checkout, and then every way the reading can
go wrong, because the failure mode that matters is not a wrong answer but a quiet one: a
parser that finds nothing and reports agreement is worse than no check at all.

The synthetic sources below are cut down to the shape the parser reads rather than copied
from the library, so a test failing here says the parser changed rather than that OLMo-core
moved a comment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.checkpoints import (
    OLMO_CORE_FULL_CHECKPOINT,
    OLMO_CORE_WEIGHTS_ONLY,
)
from tools.verify_checkpoint_shape_agreement import (
    CHECKPOINT_MODULE,
    CheckpointShapeDrift,
    compare_shapes,
    main,
    read_library_shapes,
)

OLMO_CORE_CHECKOUT = Path("/Users/philote/projects-local/OLMo-core")


def library_source(
    *,
    metadata_fname: str = ".metadata.json",
    required: str | None = None,
    weights_only: bool = True,
) -> str:
    """A checkpointer with the parts the parser reads and nothing else."""
    paths = required or (
        '        f"{dir}/train/rank0.pt",\n'
        '        f"{dir}/model_and_optim/.metadata",\n'
        '        f"{dir}/{cls.METADATA_FNAME}",'
    )
    lines = [
        "class Checkpointer:",
        f'    METADATA_FNAME: ClassVar[str] = "{metadata_fname}"',
        "",
        "    @classmethod",
        "    def dir_is_checkpoint(cls, dir):",
        "        dir = normalize_path(dir)",
    ]
    if weights_only:
        lines += ['        if file_exists(f"{dir}/.metadata"):', "            return True"]
    lines += ["        paths_to_check = [", *paths.splitlines(), "        ]"]
    lines += [
        "        for path in paths_to_check:",
        "            if not file_exists(path):",
        "                return False",
        "        return True",
    ]
    return "\n".join(lines) + "\n"


def test_the_two_files_agree_in_the_checkout_this_platform_builds_from() -> None:
    """The claim the whole tool exists to keep true, checked against the real library.

    Skipped rather than failed when the checkout is absent, because a contributor without
    OLMo-core beside the platform has not broken anything. The build gate has both, which
    is where this check is load-bearing.
    """
    module = OLMO_CORE_CHECKOUT / CHECKPOINT_MODULE
    if not module.exists():
        pytest.skip("the OLMo-core checkout is not beside this repository")

    compare_shapes(module.read_text(encoding="utf-8"))


def test_the_shapes_are_read_out_of_the_source_rather_than_assumed() -> None:
    """Both halves come back, and the class attribute is resolved rather than hardcoded."""
    weights_only, full = read_library_shapes(library_source())

    assert weights_only == frozenset(OLMO_CORE_WEIGHTS_ONLY)
    assert full == frozenset(OLMO_CORE_FULL_CHECKPOINT)
    assert ".metadata.json" in full, "METADATA_FNAME was not resolved from the class"


def test_a_renamed_metadata_file_is_drift_rather_than_a_near_miss() -> None:
    """The subtlest real change: same structure, one filename moved.

    This is the case a structural check would pass and a reader would never notice, and it
    is the one that would have this platform refuse every checkpoint a resume would load.
    """
    with pytest.raises(CheckpointShapeDrift) as raised:
        compare_shapes(library_source(metadata_fname=".checkpoint-metadata.json"))

    assert raised.value.reason == "checkpoint_shape_drift"
    assert ".checkpoint-metadata.json" in raised.value.detail


def test_a_newly_required_file_is_reported_as_one_we_do_not_check_for() -> None:
    """The library tightening. Ours would call a checkpoint loadable that is not."""
    with pytest.raises(CheckpointShapeDrift) as raised:
        compare_shapes(
            library_source(
                required=(
                    '        f"{dir}/train/rank0.pt",\n'
                    '        f"{dir}/model_and_optim/.metadata",\n'
                    '        f"{dir}/{cls.METADATA_FNAME}",\n'
                    '        f"{dir}/model_and_optim/.manifest",'
                )
            )
        )

    assert raised.value.reason == "checkpoint_shape_drift"
    assert "model_and_optim/.manifest" in raised.value.detail
    assert "we do not check for it" in raised.value.detail


def test_a_dropped_requirement_is_reported_in_the_other_direction() -> None:
    """The library loosening. Ours would refuse a checkpoint a resume would load."""
    with pytest.raises(CheckpointShapeDrift) as raised:
        compare_shapes(
            library_source(
                required=(
                    '        f"{dir}/train/rank0.pt",\n        f"{dir}/{cls.METADATA_FNAME}",'
                )
            )
        )

    assert raised.value.reason == "checkpoint_shape_drift"
    assert "model_and_optim/.metadata" in raised.value.detail
    assert "no longer does" in raised.value.detail


def test_the_method_being_renamed_away_is_drift_and_not_silence() -> None:
    """The whole point of the tool. Finding nothing must never read as finding agreement."""
    source = library_source().replace("dir_is_checkpoint", "directory_is_a_checkpoint")

    with pytest.raises(CheckpointShapeDrift) as raised:
        compare_shapes(source)

    assert raised.value.reason == "shape_method_missing"


def test_the_class_being_renamed_away_is_drift() -> None:
    source = library_source().replace("class Checkpointer:", "class CheckpointManager:")

    with pytest.raises(CheckpointShapeDrift) as raised:
        compare_shapes(source)

    assert raised.value.reason == "checkpointer_class_missing"


def test_a_path_built_from_something_this_cannot_read_is_drift() -> None:
    """Moving a path out of a literal is a contract change like any other.

    The parser could have skipped what it did not understand and compared the rest. That
    would pass a file whose remaining literals happen to match while a computed path was
    added beside them.
    """
    source = library_source(
        required=(
            '        f"{dir}/train/rank0.pt",\n'
            '        f"{dir}/model_and_optim/.metadata",\n'
            '        f"{dir}/{cls.metadata_name()}",'
        )
    )

    with pytest.raises(CheckpointShapeDrift) as raised:
        compare_shapes(source)

    assert raised.value.reason == "checkpoint_path_unresolvable"


def test_the_early_return_disappearing_is_drift_rather_than_an_empty_set() -> None:
    """A weights-only checkpoint stops being accepted and this platform still offers it."""
    with pytest.raises(CheckpointShapeDrift) as raised:
        compare_shapes(library_source(weights_only=False))

    assert raised.value.reason == "weights_only_shape_missing"


def test_a_file_that_does_not_parse_is_reported_rather_than_skipped() -> None:
    with pytest.raises(CheckpointShapeDrift) as raised:
        compare_shapes("class Checkpointer:\n    def dir_is_checkpoint(cls, dir)\n")

    assert raised.value.reason == "checkpoint_module_unparseable"


def test_a_missing_checkout_exits_two_rather_than_reporting_agreement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two rather than one, because this is the check failing rather than the claim.

    A build gate that cannot find the file has learned nothing, and the distinction between
    "the shapes disagree" and "I could not look" is what tells a reader which to fix.
    """
    assert main(["--olmo-core-root", str(tmp_path)]) == 2

    assert "checkpoint_module_unreadable" in capsys.readouterr().err


def test_the_real_checkout_exits_zero_through_the_command_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    if not (OLMO_CORE_CHECKOUT / CHECKPOINT_MODULE).exists():
        pytest.skip("the OLMo-core checkout is not beside this repository")

    assert main(["--olmo-core-root", str(OLMO_CORE_CHECKOUT)]) == 0

    assert "are the ones the library requires" in capsys.readouterr().out
