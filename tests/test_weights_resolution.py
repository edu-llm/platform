from collections.abc import Sequence

import pytest

from edullm_platform.contracts.results import (
    CheckpointManifest,
    ResultManifest,
)
from edullm_platform.weights import (
    ResolvedWeights,
    ResultManifestReader,
    UnresolvableWeightsError,
    WeightsSource,
    resolve_weights_from_run,
)

RUN = "run_019fa446-8a4e-7094-9e29-d44fffbd2491"
ATTEMPT_ONE = "att_019fa446-8a4e-7094-9e29-d44fffbd2491"
ATTEMPT_TWO = "att_019fa446-8a4e-7094-9e29-d44fffbd2492"
PREFIX = "s3://sbsandbox-intern-edullm-outputs/teams/pre-training/runs/" + RUN + "/"


def checkpoint(step: int, *, certified: bool, created_at: str) -> CheckpointManifest:
    uri = f"{PREFIX}checkpoints/step{step}/"
    return CheckpointManifest(
        schema_version=1,
        uri=uri,
        step=step,
        epoch=None,
        created_at=created_at,
        size_bytes=1024,
        checksum="sha256:" + f"{step:064d}",
        success_marker_uri=f"{uri}_SUCCESS" if certified else None,
    )


def result(attempt_id: str, checkpoints: tuple[CheckpointManifest, ...]) -> ResultManifest:
    return ResultManifest(
        schema_version=1,
        run_id=RUN,
        attempt_id=attempt_id,
        outcome="succeeded",
        output_prefixes=(PREFIX,),
        checkpoints=checkpoints,
        wandb_run=None,
        retention_class="standard",
        completed_at="2026-08-04T12:00:00.000000Z",
    )


class FakeReader:
    def __init__(self, manifests: Sequence[ResultManifest]) -> None:
        self._manifests = tuple(manifests)

    def result_manifests_for(self, run_id: str) -> Sequence[ResultManifest]:
        return tuple(entry for entry in self._manifests if entry.run_id == run_id)


def test_the_fake_reader_satisfies_the_protocol():
    reader: ResultManifestReader = FakeReader(())
    assert reader.result_manifests_for(RUN) == ()


def test_a_run_id_resolves_to_its_checkpoints_in_step_order():
    reader = FakeReader(
        [
            result(
                ATTEMPT_ONE,
                (
                    checkpoint(100, certified=True, created_at="2026-08-04T10:00:00.000000Z"),
                    checkpoint(200, certified=True, created_at="2026-08-04T10:30:00.000000Z"),
                ),
            )
        ]
    )
    resolved = resolve_weights_from_run(reader, run_id=RUN)
    assert resolved.source is WeightsSource.PRIOR_RUN
    assert resolved.reference == RUN
    assert [entry.step for entry in resolved.checkpoints] == [100, 200]
    assert resolved.uncertified_steps == ()


def test_an_uncertified_checkpoint_resolves_and_is_named():
    # Two bugs at once, in opposite directions. A pass-through that reports nothing gives
    # uncertified_steps == (), and a filter that drops the uncertified one gives two
    # checkpoints -- which is what the live store would produce, since 258 of the 260
    # checkpoints in it carry no marker. Both are asserted, so neither passes.
    reader = FakeReader(
        [
            result(
                ATTEMPT_ONE,
                (
                    checkpoint(100, certified=True, created_at="2026-08-04T10:00:00.000000Z"),
                    checkpoint(200, certified=False, created_at="2026-08-04T10:30:00.000000Z"),
                    checkpoint(300, certified=True, created_at="2026-08-04T11:00:00.000000Z"),
                ),
            )
        ]
    )
    resolved = resolve_weights_from_run(reader, run_id=RUN)
    assert [entry.step for entry in resolved.checkpoints] == [100, 200, 300]
    assert resolved.uncertified_steps == (200,)


def test_a_run_whose_every_checkpoint_is_uncertified_still_resolves():
    # This is the shape of every research team's run in the store. If it refused, there
    # would be nothing to evaluate and no measurement slice.
    reader = FakeReader(
        [
            result(
                ATTEMPT_ONE,
                (
                    checkpoint(0, certified=False, created_at="2026-08-04T10:00:00.000000Z"),
                    checkpoint(40, certified=False, created_at="2026-08-04T10:30:00.000000Z"),
                ),
            )
        ]
    )
    resolved = resolve_weights_from_run(reader, run_id=RUN)
    assert [entry.step for entry in resolved.checkpoints] == [0, 40]
    assert resolved.uncertified_steps == (0, 40)


def test_uncertified_steps_is_a_report_and_never_a_filter():
    # The bug this catches directly, rather than by inference from the two cases above: a
    # resolution that removed the uncertified checkpoints and then listed them would satisfy
    # both of those tests' step lists if the filter and the report disagreed by nothing.
    # Here the count is asserted against the input rather than against what came back
    # through the resolution, so an implementation that drops any checkpoint fails.
    written = (
        checkpoint(0, certified=False, created_at="2026-08-04T10:00:00.000000Z"),
        checkpoint(40, certified=True, created_at="2026-08-04T10:30:00.000000Z"),
        checkpoint(80, certified=False, created_at="2026-08-04T11:00:00.000000Z"),
    )
    resolved = resolve_weights_from_run(FakeReader([result(ATTEMPT_ONE, written)]), run_id=RUN)
    assert len(resolved.checkpoints) == len(written)
    assert set(resolved.uncertified_steps) < {entry.step for entry in resolved.checkpoints}


def test_a_step_written_twice_by_two_attempts_resolves_to_the_later_write():
    # A retry legitimately rewrites step 200, because the first attempt's died before it was
    # certified. Both attempts report it, and evaluating the same step twice would put two
    # points on one x value in the curve.
    reader = FakeReader(
        [
            result(
                ATTEMPT_ONE,
                (checkpoint(200, certified=True, created_at="2026-08-04T10:00:00.000000Z"),),
            ),
            result(
                ATTEMPT_TWO,
                (
                    checkpoint(200, certified=True, created_at="2026-08-04T11:00:00.000000Z"),
                    checkpoint(400, certified=True, created_at="2026-08-04T11:30:00.000000Z"),
                ),
            ),
        ]
    )
    resolved = resolve_weights_from_run(reader, run_id=RUN)
    assert [entry.step for entry in resolved.checkpoints] == [200, 400]
    assert resolved.checkpoints[0].created_at.isoformat().startswith("2026-08-04T11:00")


def test_the_later_write_wins_whichever_order_the_attempts_are_read_in():
    # The bug this catches: last-one-wins on iteration order rather than on created_at.
    # Nothing promises the reader hands back attempts oldest-first, and with two attempts
    # the wrong rule agrees with the right one exactly half the time.
    older = result(
        ATTEMPT_ONE,
        (checkpoint(200, certified=True, created_at="2026-08-04T10:00:00.000000Z"),),
    )
    newer = result(
        ATTEMPT_TWO,
        (checkpoint(200, certified=False, created_at="2026-08-04T11:00:00.000000Z"),),
    )
    for order in ((older, newer), (newer, older)):
        resolved = resolve_weights_from_run(FakeReader(order), run_id=RUN)
        assert len(resolved.checkpoints) == 1
        assert resolved.checkpoints[0].created_at.isoformat().startswith("2026-08-04T11:00")
        assert resolved.uncertified_steps == (200,)


def test_a_run_nothing_knows_about_is_refused_by_name():
    with pytest.raises(UnresolvableWeightsError, match="no result record"):
        resolve_weights_from_run(FakeReader([]), run_id=RUN)


def test_a_run_that_saved_nothing_is_refused_differently_from_one_nothing_knows_about():
    # Two different remedies. "Nothing knows about this run" sends the reader to the run id;
    # "this run saved nothing" sends them to the training run's checkpoint contract.
    reader = FakeReader([result(ATTEMPT_ONE, ())])
    with pytest.raises(UnresolvableWeightsError, match="recorded no checkpoint"):
        resolve_weights_from_run(reader, run_id=RUN)


def test_the_two_refusals_do_not_share_a_message():
    # Both are UnresolvableWeightsError, so a caller matching on the type learns nothing.
    # If the two messages converged, the pair of tests above would both pass against a
    # single refusal and the distinction they exist to hold would be gone.
    def message(reader: FakeReader) -> str:
        with pytest.raises(UnresolvableWeightsError) as raised:
            resolve_weights_from_run(reader, run_id=RUN)
        return str(raised.value)

    assert message(FakeReader([])) != message(FakeReader([result(ATTEMPT_ONE, ())]))


def test_a_resolution_is_frozen():
    # ResolvedWeights is what a submission is compiled from and what an approver is shown.
    # A caller that could edit the checkpoint list after resolution could show one thing and
    # submit another.
    resolved = resolve_weights_from_run(
        FakeReader(
            [
                result(
                    ATTEMPT_ONE,
                    (checkpoint(1, certified=True, created_at="2026-08-04T10:00:00.000000Z"),),
                )
            ]
        ),
        run_id=RUN,
    )
    assert isinstance(resolved, ResolvedWeights)
    with pytest.raises(AttributeError):
        resolved.reference = "something-else"  # type: ignore[misc]
