import io
import json
from typing import Any

import pytest
from pydantic import ValidationError

from edullm_platform.contracts.results import (
    OUTPUTS_BUCKET,
    EvalMetric,
    EvalMetrics,
    ResultManifest,
)
from edullm_platform.eval_metrics import read_olmo_eval_metrics
from edullm_platform.lifecycle_handler import handler
from edullm_platform.lifecycle_projection import project_batch_state_change
from tests.test_phase3_lifecycle_projection import (
    CONTAINER_OUTPUT_PREFIX,
    EVENTBRIDGE_EVENT_ID,
    OCCURRED_AT_INSTANT,
    RUN_ID,
    TEAM,
    RecordingStore,
    attempt_block,
    detail,
    envelope,
    sqs_batch,
)

RUN = "run_019fa446-8a4e-7094-9e29-d44fffbd2491"
ATTEMPT = "att_019fa446-8a4e-7094-9e29-d44fffbd2491"
PREFIX = "s3://sbsandbox-intern-edullm-outputs/teams/eval-inference/runs/" + RUN + "/"

# Shaped like olmo-eval's own metrics.json: a summary block carrying each task's primary
# metric only, and a tasks block carrying all of them.
HARNESS_OUTPUT = {
    "summary": {"arc_challenge": {"acc_raw": 0.41}},
    "tasks": [
        {
            "task_name": "arc_challenge",
            "num_instances": 1172,
            "metrics": {"acc_raw": 0.41, "acc_per_token": 0.37},
        }
    ],
}


def test_metrics_are_read_from_the_tasks_block_and_not_the_summary():
    # The bug this catches is the eval team's own, learned the hard way and written into their
    # SKILL.md: `summary` carries each task's primary metric only, so a reader that used it
    # drops acc_per_token silently and the second column vanishes from the table.
    metrics = read_olmo_eval_metrics(HARNESS_OUTPUT)
    assert metrics.metrics == (
        EvalMetric(task="arc_challenge", key="acc_per_token", value=0.37, instances=1172),
        EvalMetric(task="arc_challenge", key="acc_raw", value=0.41, instances=1172),
    )


def test_the_summary_and_the_tasks_block_disagree_in_this_fixture_on_purpose():
    # Without this, the test above would pass against a reader that used `summary`, because
    # `summary` also carries acc_raw at 0.41. What separates the two readers is the key the
    # summary does not have, so the fixture has to be one where they differ and this says so.
    summary_keys = set(HARNESS_OUTPUT["summary"]["arc_challenge"])  # type: ignore[index]
    task_keys = set(HARNESS_OUTPUT["tasks"][0]["metrics"])  # type: ignore[index,arg-type]
    assert summary_keys < task_keys


def test_a_summary_only_document_is_refused_rather_than_read_as_empty():
    # An empty metrics block and a document this reader does not understand are different
    # facts, and a run that scored something must not record that it scored nothing.
    with pytest.raises(ValueError, match="no `tasks` block"):
        read_olmo_eval_metrics({"summary": {"arc_challenge": {"acc_raw": 0.41}}})


def test_a_task_reporting_no_metric_is_refused_by_name():
    with pytest.raises(ValueError, match="arc_challenge"):
        read_olmo_eval_metrics(
            {"tasks": [{"task_name": "arc_challenge", "num_instances": 10, "metrics": {}}]}
        )


def test_a_task_reporting_no_denominator_is_refused_by_name():
    with pytest.raises(ValueError, match="instances"):
        read_olmo_eval_metrics(
            {"tasks": [{"task_name": "arc_challenge", "metrics": {"acc_raw": 0.4}}]}
        )


def test_metrics_are_recorded_once_each_in_a_stated_order():
    with pytest.raises(ValidationError, match="once each"):
        EvalMetrics(
            schema_version=1,
            harness="olmo-eval",
            metrics=(
                EvalMetric(task="arc_challenge", key="acc_raw", value=0.41, instances=10),
                EvalMetric(task="arc_challenge", key="acc_raw", value=0.42, instances=10),
            ),
        )


def test_metrics_out_of_order_are_refused_as_well_as_repeated_ones():
    with pytest.raises(ValidationError, match="once each"):
        EvalMetrics(
            schema_version=1,
            harness="olmo-eval",
            metrics=(
                EvalMetric(task="hellaswag", key="acc_raw", value=0.41, instances=10),
                EvalMetric(task="arc_challenge", key="acc_raw", value=0.42, instances=10),
            ),
        )


def test_two_tasks_are_sorted_into_one_ordered_block():
    metrics = read_olmo_eval_metrics(
        {
            "tasks": [
                {"task_name": "hellaswag", "num_instances": 10, "metrics": {"acc_raw": 0.5}},
                {"task_name": "arc_challenge", "num_instances": 20, "metrics": {"acc_raw": 0.4}},
            ]
        }
    )
    assert [(entry.task, entry.instances) for entry in metrics.metrics] == [
        ("arc_challenge", 20),
        ("hellaswag", 10),
    ]


def test_a_result_manifest_written_before_this_field_existed_still_parses():
    # Every result record in the lineage store carries no eval_metrics key and none of them
    # can be rewritten. A required field here would make the history unreadable by the
    # contract that describes it -- the same argument exit_code and checkpoint_survey carry.
    manifest = ResultManifest.model_validate(
        {
            "schema_version": 1,
            "run_id": RUN,
            "attempt_id": ATTEMPT,
            "outcome": "succeeded",
            "output_prefixes": [PREFIX],
            "wandb_run": None,
            "retention_class": "standard",
            "completed_at": "2026-08-04T12:00:00.000000Z",
        }
    )
    assert manifest.eval_metrics is None


def test_a_result_manifest_carries_metrics_when_it_has_them():
    manifest = ResultManifest(
        schema_version=1,
        run_id=RUN,
        attempt_id=ATTEMPT,
        outcome="succeeded",
        output_prefixes=(PREFIX,),
        wandb_run=None,
        retention_class="standard",
        completed_at="2026-08-04T12:00:00.000000Z",
        eval_metrics=read_olmo_eval_metrics(HARNESS_OUTPUT),
    )
    assert manifest.eval_metrics is not None
    assert manifest.eval_metrics.harness == "olmo-eval"
    assert len(manifest.eval_metrics.metrics) == 2


class FakeMetricsReader:
    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self._documents = documents
        self.asked: list[str] = []

    def read_json(self, uri: str) -> dict[str, Any] | None:
        self.asked.append(uri)
        return self._documents.get(uri)


def succeeded_detail() -> dict[str, Any]:
    return detail("SUCCEEDED", attempts=[attempt_block()])


def project_with(reader: FakeMetricsReader | None):
    arguments = {} if reader is None else {"metrics_reader": reader}
    return project_batch_state_change(
        eventbridge_event_id=EVENTBRIDGE_EVENT_ID,
        detail=succeeded_detail(),
        occurred_at=OCCURRED_AT_INSTANT,
        **arguments,
    )


def test_the_projection_records_metrics_when_the_run_wrote_them():
    reader = FakeMetricsReader({f"{CONTAINER_OUTPUT_PREFIX}metrics.json": HARNESS_OUTPUT})
    projection = project_with(reader)
    assert projection.result is not None
    assert projection.result.eval_metrics is not None
    assert len(projection.result.eval_metrics.metrics) == 2
    # The key asked for is the one the container was handed plus metrics.json, with exactly
    # one slash. CONTAINER_OUTPUT_PREFIX already ends in one, so an f-string that adds
    # another asks for a key that is not there and reads as a run that scored nothing.
    assert reader.asked == [f"{CONTAINER_OUTPUT_PREFIX}metrics.json"]


def test_the_projection_records_no_metrics_when_the_run_wrote_none():
    # A training run, a corpus validation and a tokenization write no metrics.json and are
    # not failing to. The bug this catches: raising, or recording an empty block, on every
    # workload that is not an evaluation -- which is most of them.
    projection = project_with(FakeMetricsReader({}))
    assert projection.result is not None
    assert projection.result.eval_metrics is None


def test_no_reader_is_not_the_same_as_no_metrics_being_written():
    # Every existing caller passes no reader, and that must keep working. Asserting it here
    # is what stops the parameter being made required and breaking the lifecycle recorder.
    projection = project_with(None)
    assert projection.result is not None
    assert projection.result.eval_metrics is None


class StoreThatAlsoReads:
    """A store the handler will hand a metrics reader, because it answers ``get_object``.

    Duck-typed exactly as ``RecordingStore`` is, and for the same reason: botocore is not
    importable here, so the seam is the shape of the call rather than a type.
    """

    def __init__(self, documents: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.written: list[dict[str, Any]] = []
        self._documents = documents
        self.fetched: list[tuple[str, str]] = []

    def put_object(self, **arguments: Any) -> Any:
        self.written.append(arguments)
        return {}

    def get_object(self, **arguments: Any) -> Any:
        wanted = (arguments["Bucket"], arguments["Key"])
        self.fetched.append(wanted)
        document = self._documents.get(wanted)
        if document is None:
            raise RuntimeError("NoSuchKey")
        return {"Body": io.BytesIO(json.dumps(document).encode())}


def written_result(store: StoreThatAlsoReads) -> dict[str, Any]:
    body = next(
        written["Body"] for written in store.written if written["Key"].startswith("result/")
    )
    return json.loads(body)


def test_the_deployed_handler_puts_the_scores_on_the_record_it_writes():
    # The seam this closes: a contract, a reader and a projection parameter with nothing
    # supplying one leave eval_metrics null on every record, and every test above still
    # passes. This is the only one that fails if the handler stops passing a reader.
    store = StoreThatAlsoReads(
        {(OUTPUTS_BUCKET, f"teams/{TEAM}/runs/{RUN_ID}/metrics.json"): HARNESS_OUTPUT}
    )
    handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)
    record = written_result(store)
    assert record["eval_metrics"]["harness"] == "olmo-eval"
    assert len(record["eval_metrics"]["metrics"]) == 2
    assert store.fetched == [(OUTPUTS_BUCKET, f"teams/{TEAM}/runs/{RUN_ID}/metrics.json")]


def test_a_store_that_only_writes_still_records_a_run_with_no_scores():
    # Every existing caller supplies one of these, so this is what stops the reader being
    # made mandatory and taking the recorder down on a training run.
    store = RecordingStore()
    handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)
    body = next(
        written["Body"] for written in store.written if written["Key"].startswith("result/")
    )
    assert json.loads(body)["eval_metrics"] is None


def test_a_refused_read_records_no_scores_rather_than_losing_the_whole_delivery():
    # The deliberate silent degradation, asserted so it is a decision rather than a
    # discovery. Until the GetObject grant is applied the recorder meets exactly this, and
    # raising here would dead-letter the delivery and lose the event, the attempt and the
    # result for a run that demonstrably happened.
    store = StoreThatAlsoReads({})
    handler(sqs_batch(envelope("SUCCEEDED", attempts=[attempt_block()])), store=store)
    assert written_result(store)["eval_metrics"] is None
    assert [written["Key"].split("/", maxsplit=1)[0] for written in store.written] == [
        "events",
        "attempt",
        "result",
    ]


def test_a_metrics_document_that_is_present_and_unreadable_still_raises():
    # The one case worth failing on: bytes are there and this cannot parse them. A reader
    # that swallowed it would be indistinguishable from the two cases above, and a run that
    # scored something would be recorded as one that scored nothing.
    reader = FakeMetricsReader(
        {f"{CONTAINER_OUTPUT_PREFIX}metrics.json": {"summary": {"arc_challenge": {"acc": 0.4}}}}
    )
    with pytest.raises(ValueError, match="no `tasks` block"):
        project_with(reader)
