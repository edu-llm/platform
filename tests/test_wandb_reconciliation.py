"""Asking W&B whether the run a lineage record names is a run W&B has.

``ResultManifest.wandb_run`` is composed out of the container's environment and never
checked against W&B, so a record can assert a page that is not there. Read live on
2026-08-04, 42 of the 102 result records carry a reference and 28 of them name a run the
entity does not hold.

**The way this can lie is by reading an outage as an absence.** With W&B unread, every
reference is trivially unresolvable, and a reconciliation that printed that would mark all 42
records as false on the morning a key lapsed -- which is the same false record it exists to
remove, with the sign flipped. Three states rather than two is the whole design, and several
tests here exist only to hold that line.

**The second way is by reading an unfindable run as an unlogged one.** Three of the 28 name a
run W&B does not have while the run logged perfectly well under a ``-died`` suffix in the same
project. The record is wrong about where and the run is not missing, and reporting those three
as runs that never logged would be an accusation against a submitter who did nothing wrong.

**The third is by moving into the recorder.** The reason this is a post-terminal reconciler
rather than a lookup inside ``wandb_run_for`` is that the recorder projects an event under a
timeout with a dead-letter queue behind it, and a W&B outage there loses the event, the
attempt and the result for a run that happened. The seam is asserted rather than left to a
comment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from infrastructure_support import INFRA_ROOT, load_template

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from wandb_reconciliation import (
    RESULT_PREFIX,
    WandbReference,
    WandbRunPresence,
    never_logged,
    observation_document,
    observe,
    presence_counts,
    read_references,
    render_section,
)

from edullm_platform.contracts.results import ResultManifest, WandbRunRef

RECORDER = PROJECT_ROOT / "src" / "edullm_platform" / "lifecycle_projection.py"
HANDLER = PROJECT_ROOT / "src" / "edullm_platform" / "lifecycle_handler.py"
LINEAGE_TEMPLATE = INFRA_ROOT / "lineage-bucket.yaml"

#: Three run ids off the account. The first logged nothing at all, the second logged under a
#: `-died` suffix in the project its record names, and the third resolves exactly.
#:
#: No uuid tail here may be twelve decimal digits. The last group of a uuid is twelve hex
#: characters, and about one in two hundred draws them all from 0-9, which the tracked-tree
#: account-id scan in `tests/test_evidence.py` reads as a leaked account id. `DIED_RUN` was
#: drawn that way and did fail that scan. The scan is right to flag it -- an account id also
#: sits bounded by hyphens in a bucket or log-group name, so exempting the shape would give
#: up a real leak -- so the id carries a hex letter instead.
SILENT_RUN = "run_019fc3ae-b197-70d3-80df-12d36d006be3"
DIED_RUN = "run_019fca21-8bb0-7061-bad8-770710961b02"
LOGGED_RUN = "run_019fbd28-b600-70fa-879b-34fafcd8fe68"

ATTEMPT = "att_019fa731-1b33-72a4-aec8-6b19c7cff944"
ENTITY = "eduLLM"


class FakeRun:
    """One W&B run in the shape ``tools/visibility_board.py`` reads the entity into.

    Written here rather than imported so that this module tests the Protocol the reconciler
    declares rather than the one class that happens to satisfy it today.
    """

    def __init__(
        self, *, project: str, path: str, display_name: str, run_id: str | None
    ) -> None:
        self.project = project
        self.path = path
        self.display_name = display_name
        self.run_id = run_id


def a_reference(
    run_id: str, *, project: str = "eduLLM", outcome: str = "succeeded"
) -> WandbReference:
    return WandbReference(
        run_id=run_id, entity=ENTITY, project=project, name=run_id, outcome=outcome
    )


def a_result(run_id: str, *, project: str | None = "eduLLM") -> dict[str, Any]:
    """A result record built through the contract, so a fixture cannot outlive the shape."""
    record = ResultManifest(
        schema_version=1,
        run_id=run_id,
        attempt_id=ATTEMPT,
        outcome="succeeded",
        output_prefixes=(f"s3://sbsandbox-intern-edullm-outputs/teams/platform/runs/{run_id}/",),
        wandb_run=(
            None if project is None else WandbRunRef(entity=ENTITY, project=project, run_id=run_id)
        ),
        retention_class="standard",
        completed_at="2026-08-04T05:27:21.355000Z",
    )
    return json.loads(record.model_dump_json())


def a_result_tree(root: Path, records: dict[str, Any]) -> Path:
    directory = root / RESULT_PREFIX
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in records.items():
        (directory / f"{name}.json").write_text(
            body if isinstance(body, str) else json.dumps(body), encoding="utf-8"
        )
    return root


# ----------------------------------------------------------------------------------------
# The three states
# ----------------------------------------------------------------------------------------


def test_a_reference_the_entity_holds_is_present() -> None:
    """The state every reference is supposed to be in, so the rest can be read as findings."""
    runs = [FakeRun(project="eduLLM", path="ab12cd34", display_name=LOGGED_RUN, run_id=LOGGED_RUN)]

    observed = observe([a_reference(LOGGED_RUN)], runs)

    assert [entry.presence for entry in observed] == [WandbRunPresence.PRESENT]
    assert not observed[0].names_nothing


def test_a_reference_nothing_in_the_entity_claims_is_a_run_that_logged_nowhere() -> None:
    """The 25. The record says the run reported and W&B has never heard of it."""
    runs = [FakeRun(project="eduLLM", path="ab12cd34", display_name=LOGGED_RUN, run_id=LOGGED_RUN)]

    observed = observe([a_reference(SILENT_RUN)], runs)

    assert observed[0].presence is WandbRunPresence.ABSENT
    assert observed[0].logged_nowhere
    assert observed[0].found_at is None
    assert never_logged(observed) == observed


def test_a_run_that_logged_under_another_name_is_absent_and_did_not_log_nowhere() -> None:
    """THE ONE THAT MATTERS. Mutation: read every absent reference as a run that never logged.

    Three of the 28 are runs whose W&B name carries a `-died` suffix the workload glued on.
    They logged every step they took. The reference is still false -- nothing at that name
    exists, so a reader following the record finds nothing -- and the run is not missing, so
    reporting it as unlogged spend would send somebody to ask a submitter why they turned
    logging off when they did not.
    """
    runs = [
        FakeRun(
            project="eduLLM",
            path="y79v44l7",
            display_name=f"{DIED_RUN}-died",
            run_id=DIED_RUN,
        )
    ]

    observed = observe([a_reference(DIED_RUN)], runs)

    assert observed[0].presence is WandbRunPresence.ABSENT
    assert observed[0].names_nothing
    assert not observed[0].logged_nowhere
    assert observed[0].found_at == "eduLLM/y79v44l7"
    assert observed[0].found_as == f"{DIED_RUN}-died"
    assert never_logged(observed) == ()


def test_a_run_that_logged_into_a_different_project_does_not_make_the_link_work() -> None:
    """Mutation: match on the run name alone and ignore which project the record names.

    The reference names an entity, a project and a name, and all three are what a reader
    would follow. A run of that name in another project makes the record wrong about where
    rather than right, and collapsing the two would report a dead link as a live one.
    """
    runs = [
        FakeRun(
            project="somewhere-else", path="zz00yy11", display_name=LOGGED_RUN, run_id=LOGGED_RUN
        )
    ]

    observed = observe([a_reference(LOGGED_RUN, project="eduLLM")], runs)

    assert observed[0].presence is WandbRunPresence.ABSENT
    assert observed[0].found_at == "somewhere-else/zz00yy11"
    assert not observed[0].logged_nowhere


def test_an_unreachable_wandb_marks_nothing_false() -> None:
    """THE ONE THAT MATTERS. Mutation: default the entity listing to an empty sequence.

    It is one character of difference and it turns this into a machine for manufacturing the
    exact defect it exists to remove. With W&B unread every reference is trivially
    unresolvable, so all 42 records would be printed as naming a run that does not exist, and
    every one of those claims would be false. The third state is the whole design.
    """
    references = [a_reference(SILENT_RUN), a_reference(LOGGED_RUN)]

    observed = observe(references, None)

    assert {entry.presence for entry in observed} == {WandbRunPresence.UNREACHABLE}
    assert never_logged(observed) == ()
    assert not any(entry.names_nothing for entry in observed)


def test_an_entity_that_was_read_and_holds_nothing_is_a_claim() -> None:
    """The other half of the state above, because a board that never says `absent` says nothing.

    An empty entity and an unread one are one value apart and mean opposite things. This is
    the case that must still produce findings, so that the caution above cannot be satisfied
    by never answering.
    """
    observed = observe([a_reference(SILENT_RUN)], [])

    assert observed[0].presence is WandbRunPresence.ABSENT
    assert observed[0].logged_nowhere


def test_every_state_is_named_in_the_counts_even_at_zero() -> None:
    """Mutation: build the counts from what was seen, which drops the states that were not.

    A document whose `unreachable` key is absent and one whose `unreachable` is 0 read the
    same to a person and differently to anything counting, and only the second says the
    question was asked.
    """
    counts = presence_counts(observe([a_reference(SILENT_RUN)], []))

    assert counts == {"present": 0, "absent": 1, "unreachable": 0}


# ----------------------------------------------------------------------------------------
# Reading the references out of the result tree
# ----------------------------------------------------------------------------------------


def test_the_references_come_out_of_the_result_records(tmp_path: Path) -> None:
    root = a_result_tree(
        tmp_path,
        {
            LOGGED_RUN: a_result(LOGGED_RUN),
            SILENT_RUN: a_result(SILENT_RUN, project="impl5-ssd"),
        },
    )

    reading = read_references(root)

    assert reading.results_read == 2
    assert {reference.run_id for reference in reading.references} == {LOGGED_RUN, SILENT_RUN}
    assert {reference.project for reference in reading.references} == {"eduLLM", "impl5-ssd"}


def test_a_record_that_names_no_wandb_run_is_not_a_finding(tmp_path: Path) -> None:
    """Mutation: count a record with no reference as a reference that does not resolve.

    A CPU job admitted before `WANDB_PROJECT` was set carries no reference at all, and 60 of
    the 102 result records are in that state. A record that claims nothing cannot claim
    anything false, and folding it in would report the platform's own history as a defect.
    """
    root = a_result_tree(tmp_path, {LOGGED_RUN: a_result(LOGGED_RUN, project=None)})

    reading = read_references(root)

    assert reading.references == ()
    assert reading.results_read == 1
    assert reading.without_reference == 1


def test_a_record_stored_as_a_string_holding_json_is_still_read(tmp_path: Path) -> None:
    """The state machine writes the handler's canonical bytes rather than re-encoding them.

    Both spellings are in the committed fixtures for the same prefix, so a reader that
    handled one would silently see half the store.
    """
    root = a_result_tree(tmp_path, {LOGGED_RUN: json.dumps(json.dumps(a_result(LOGGED_RUN)))})

    reading = read_references(root)

    assert [reference.run_id for reference in reading.references] == [LOGGED_RUN]


def test_a_record_this_tree_cannot_parse_is_counted_rather_than_dropped(tmp_path: Path) -> None:
    """Mutation: skip it, since one unreadable record out of a hundred changes nothing.

    It changes the denominator, which is the one thing this whole change is about. A store
    producing records this tree cannot read is a defect in the recorder, and a report that
    quietly described the readable subset would hide exactly that.
    """
    root = a_result_tree(
        tmp_path, {LOGGED_RUN: a_result(LOGGED_RUN), "broken": {"schema_version": 1}}
    )

    reading = read_references(root)

    assert reading.unparsed == 1
    assert reading.results_read == 1


def test_a_tree_that_is_not_there_raises_rather_than_reading_as_an_empty_one(
    tmp_path: Path,
) -> None:
    """THE ONE THAT MATTERS. Mutation: return an empty reading when the directory is absent.

    "102 result records were read and none of them names a W&B run" and "nobody synced the
    prefix" are opposite statements, and a reading has nowhere to carry the difference. An
    empty one would let a prefix nobody fetched render as a store with nothing false in it,
    which is the shape of pass this whole board exists to refuse.
    """
    with pytest.raises(FileNotFoundError):
        read_references(tmp_path)


# ----------------------------------------------------------------------------------------
# What the report says
# ----------------------------------------------------------------------------------------


def test_the_unreachable_section_says_it_did_not_ask(tmp_path: Path) -> None:
    """Mutation: render the table anyway, with `unreachable` in the last column.

    A table headed "records naming a run that does not exist" is read as an accusation
    whatever the last column says. The section is replaced rather than annotated.
    """
    root = a_result_tree(tmp_path, {SILENT_RUN: a_result(SILENT_RUN)})
    reading = read_references(root)

    rendered = "\n".join(
        render_section(observe(reading.references, None), reading=reading, entity=ENTITY)
    )

    assert "Not asked" in rendered
    assert SILENT_RUN not in rendered


def test_the_report_separates_a_dead_link_from_a_run_that_never_logged(tmp_path: Path) -> None:
    root = a_result_tree(
        tmp_path, {SILENT_RUN: a_result(SILENT_RUN), DIED_RUN: a_result(DIED_RUN)}
    )
    reading = read_references(root)
    runs = [
        FakeRun(
            project="eduLLM", path="y79v44l7", display_name=f"{DIED_RUN}-died", run_id=DIED_RUN
        )
    ]

    rendered = "\n".join(
        render_section(observe(reading.references, runs), reading=reading, entity=ENTITY)
    )

    assert "**1 of the 2 are runs nothing in the entity claims at all**" in rendered
    assert "nothing in the entity carries this run id" in rendered
    assert f"`{DIED_RUN}-died` in `eduLLM/y79v44l7`" in rendered


def test_the_report_refuses_to_ask_a_workload_to_log(tmp_path: Path) -> None:
    """THE ONE THAT MATTERS. Mutation: add a line telling the submitter to call wandb.init().

    The platform hands every container a project, an entity and an API key, and a workload
    that ignores all three is not a platform defect. Making the record honest about it is
    this repository's job; making the workload log is the researcher's, and a report that
    blurs the two turns a reconciliation into a policy nobody agreed to.
    """
    root = a_result_tree(tmp_path, {SILENT_RUN: a_result(SILENT_RUN)})
    reading = read_references(root)

    rendered = "\n".join(
        render_section(observe(reading.references, []), reading=reading, entity=ENTITY)
    )

    assert "Nothing here fails a run for not logging" in rendered
    assert "cannot make a workload call" in rendered


def test_the_machine_readable_answer_carries_the_population(tmp_path: Path) -> None:
    """Mutation: emit the observations alone, since the counts are derivable from them.

    They are derivable from each other and not from the store. A count of false references
    means nothing without the number of records it was taken over, and a denominator that
    moves without saying so is the other half of what this change is about.
    """
    root = a_result_tree(
        tmp_path,
        {LOGGED_RUN: a_result(LOGGED_RUN), SILENT_RUN: a_result(SILENT_RUN, project=None)},
    )
    reading = read_references(root)

    document = observation_document(
        observe(reading.references, []), reading=reading, entity=ENTITY
    )

    assert document["schema_version"] == 1
    assert document["results_read"] == 2
    assert document["results_without_reference"] == 1
    assert document["counts"] == {"present": 0, "absent": 1, "unreachable": 0}
    assert [entry["run_id"] for entry in document["observations"]] == [LOGGED_RUN]
    assert json.loads(json.dumps(document)), "the document has to survive a round trip"


# ----------------------------------------------------------------------------------------
# Where the answer is allowed to live
# ----------------------------------------------------------------------------------------


def test_the_recorder_does_not_reach_this_module() -> None:
    """THE ONE THAT MATTERS. Mutation: call this from `wandb_run_for` and be done in a line.

    That is the fix that looks smallest and is the one the investigation ruled out. The
    projection runs inside a Lambda triggered by an SQS event source mapping with a
    dead-letter queue behind it, so a W&B outage there stops the event, the attempt and the
    result being written for a run that demonstrably happened -- to improve a field naming
    where its charts would have been. The module lives under `tools/`, which neither Lambda
    builder packages, so the seam is structural; this asserts nobody has routed around it.
    """
    for path in (RECORDER, HANDLER):
        source = path.read_text(encoding="utf-8")
        assert "wandb_reconciliation" not in source, (
            f"{path.name} reaches the reconciler, which puts a network call inside the "
            "event recorder"
        )
    assert "not that it did" in RECORDER.read_text(encoding="utf-8"), (
        "wandb_run_for no longer says it is a naming contract rather than an observation, "
        "so either it started asking or the reason it does not has been deleted"
    )


def test_a_result_manifest_cannot_be_amended_so_the_answer_lives_elsewhere() -> None:
    """Why the observation is recomputed into a report instead of written beside the record.

    Mutation: write the answer back into `result/{run_id}.json`. The lineage bucket denies
    any `PutObject` that does not carry `If-None-Match`, and every writer sends `*`, so the
    key that already exists cannot be replaced -- the template says so in as many words. The
    claim is asserted against the template rather than repeated as a comment, because a
    bucket policy is one deploy away from being different and a reason nobody re-checks is a
    reason that expires.
    """
    template = load_template(LINEAGE_TEMPLATE)
    policy = next(
        properties["Properties"]["PolicyDocument"]
        for properties in template["Resources"].values()
        if properties["Type"] == "AWS::S3::BucketPolicy"
    )
    refusals = [
        statement
        for statement in policy["Statement"]
        if statement["Effect"] == "Deny" and "s3:PutObject" in str(statement["Action"])
    ]

    assert len(refusals) == 1
    assert refusals[0]["Condition"] == {"Null": {"s3:if-none-match": "true"}}
    assert "writes the result somewhere else" in LINEAGE_TEMPLATE.read_text(encoding="utf-8")


def test_the_module_documents_the_three_states_and_where_it_runs() -> None:
    """Mutation: drop the reasoning and leave the code.

    Every decision this module makes was a choice between two defensible options -- the
    recorder or the schedule, a new lineage key or a recomputed report, two states or three
    -- and the next person to touch it will reach for the other one unless the argument is
    written down where they will meet it.
    """
    source = (PROJECT_ROOT / "tools" / "wandb_reconciliation.py").read_text(encoding="utf-8")

    for claim in (
        "THE FIX IS NOT IN ``wandb_run_for``",
        "WHERE IT RUNS: THE AUDIT BOARD",
        "WHAT IT WRITES, AND WHY NOT INTO THE RECORD IT IS ABOUT",
        "THREE STATES",
        "IT REPORTS AND IT DOES NOT COMPEL",
    ):
        assert claim in source, claim
