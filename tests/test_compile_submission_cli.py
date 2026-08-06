"""The compile step, run against the document the resolve job hands it.

The two are written and read in different jobs -- one holding an ECR role, one deliberately
holding no credential at all -- so this is the only place the seam between them is
exercised. Everything here runs the real ``main`` against the reviewed configuration in
``config/``, because the values that decide a submission's fate are in those files and a
fixture copy of them would be a second answer to every question they settle.

**What used to happen, and is the reason this module exists.** The compile step passed
``image_scan_summary=None`` because it had nowhere to get one, an absent summary reads as
nobody having looked, and ``image_scan_findings_unreviewed`` is denied outright -- so every
submission naming a digest outside the two-entry allowlist in
``config/image-exceptions.yaml`` was refused before a reviewer saw it. That is the
fail-closed direction and it was correct; it was also the whole platform behind a hand-
maintained list.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.errors import RetiredDatasetReleaseError
from edullm_platform.run_history import NO_HISTORY_PACKAGED, NOTHING_LIKE_THIS_YET
from edullm_platform.submission import require_a_dataset_release_that_is_current
from tools.compile_submission import EXIT_OK, EXIT_REFUSED, EXIT_UNUSABLE, main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"

SUBMITTER = "caiiris"
REPOSITORY_URL = "https://github.com/edu-llm/OLMo-core"
RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"

COMMIT_SHA = "8076c077533eb79742f4ed22aade439df123a593"
PUBLISHED_DIGEST = "sha256:" + "1a" * 32
REBUILT_DIGEST = "sha256:" + "2b" * 32
DIGEST_FROM_ANOTHER_COMMIT = "sha256:" + "9f" * 32

FIRST_PUSH = "2026-07-26T09:02:00.000000Z"
SECOND_PUSH = "2026-07-26T18:30:00.000000Z"
SCANNED_AT = "2026-07-26T22:07:12.000000Z"


def clean_scan() -> dict[str, Any]:
    return {"schema_version": 1, "status": "COMPLETE", "scanned_at": SCANNED_AT}


def scan_with(**counts: int) -> dict[str, Any]:
    return {**clean_scan(), **counts}


def form(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repository": "OLMo-core",
        "commit_sha": COMMIT_SHA,
        "workload_profile": "olmo-core-check",
        # `none` rather than dolma-2026-07, and the change is the subject of one case below
        # rather than a detail. `retired` in config/datasets.yaml used to remove a menu item
        # and enforce nothing, so this default named a retired release through every case in
        # this module and compiled clean each time. The compile job refuses it now. Nothing
        # here is about the dataset, so the field holds the answer a run that reads no
        # corpus would give.
        "dataset_release": "none",
        "team": "data-prep",
        "wandb_project": "olmo-core-tokenize",
        "experiment": "dolma-tokenization",
        "command": ["python", "-m", "olmo_core.data.tokenize"],
        # Required since the workload profile stopped declaring one. Every form a submitter
        # fills in now names a machine, so every form here does too.
        "compute_profile": "cpu-32vcpu",
    }
    payload.update(overrides)
    return payload


def resolved(
    *,
    published: list[dict[str, str]] | None = None,
    image_scan: dict[str, Any] | None = None,
    blocking_findings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """The document ``tools/resolve_published_image.py`` writes, in its own shape."""
    document: dict[str, Any] = {
        "published": (
            published
            if published is not None
            else [{"image_digest": PUBLISHED_DIGEST, "pushed_at": FIRST_PUSH}]
        ),
        "image_scan": clean_scan() if image_scan is None else image_scan,
    }
    if blocking_findings is not None:
        document["blocking_findings"] = blocking_findings
    return document


def compile_form(
    tmp_path: Path,
    *,
    payload: dict[str, object] | None = None,
    document: object = None,
    published_images: Path | None = None,
    submitter: str = SUBMITTER,
    client_version: str = "",
) -> tuple[int, dict[str, Any]]:
    """Compile one form, defaulting to a dispatch that named no install.

    Empty by default because that is what the Actions tab sends and what every dispatch
    made before the input existed sends, so it is the state most of this module should be
    written against.
    """
    inputs = tmp_path / "submission-form.json"
    inputs.write_text(json.dumps(payload if payload is not None else form()), encoding="utf-8")
    if published_images is None:
        published_images = tmp_path / "published-image.json"
        published_images.write_text(
            json.dumps(document if document is not None else resolved()), encoding="utf-8"
        )
    output = tmp_path / "compiled-submission.json"
    # A day with nothing committed, passed on every case here because the alternative is a
    # day this job could not read -- which fails closed and routes to a lead, and would
    # therefore answer "routine" for every case in this module regardless of what it was
    # asking about. The ceiling has its own section below; everything above it is about
    # something else and wants the quiet day.
    quiet_day = tmp_path / "empty-run-index.json"
    quiet_day.write_text(json.dumps({"format_version": 1, "runs": []}), encoding="utf-8")
    exit_code = main(
        [
            "--inputs",
            str(inputs),
            "--config-dir",
            str(CONFIG_DIR),
            "--published-images",
            str(published_images),
            "--submitter",
            submitter,
            "--client-version",
            client_version,
            "--repository-url",
            REPOSITORY_URL,
            "--output",
            str(output),
            "--summary",
            str(tmp_path / "approver-context.md"),
            "--run-index",
            str(quiet_day),
            "--run-id",
            RUN_ID,
        ]
    )
    compiled = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    assert isinstance(compiled, dict)
    return exit_code, compiled


def approver_context(tmp_path: Path) -> str:
    """What the reviewer would read, written by the same call the workflow makes."""
    return (tmp_path / "approver-context.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------
# Who is submitting
# ---------------------------------------------------------------------------------------


def test_a_submitter_the_roster_does_not_name_is_refused_and_nothing_is_written(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: read the roster only for the W&B attribution line.

    The compile job loads ``config/organization.yaml`` already and holds the dispatching
    login already, so the answer is in hand before the approval gate. Admission answers the
    same question afterwards, and afterwards is what costs the approver.

    ``EXIT_REFUSED`` rather than ``EXIT_UNUSABLE`` because this is a verdict on the
    submission. The workflow prints a different sentence for each, and the one for a
    refusal is the one that says no reviewer was asked.
    """
    exit_code, compiled = compile_form(tmp_path, submitter="not-a-member")

    assert exit_code == EXIT_REFUSED
    assert compiled == {}
    assert "config/organization.yaml" in capsys.readouterr().err


def test_the_roster_refusal_leaves_no_approver_context_for_a_reviewer_to_read(
    tmp_path: Path,
) -> None:
    """A refused submission asks nobody, so the page a reviewer would read is not written."""
    summary = tmp_path / "approver-context.md"
    inputs = tmp_path / "submission-form.json"
    inputs.write_text(json.dumps(form()), encoding="utf-8")
    published = tmp_path / "published-image.json"
    published.write_text(json.dumps(resolved()), encoding="utf-8")

    exit_code = main(
        [
            "--inputs",
            str(inputs),
            "--config-dir",
            str(CONFIG_DIR),
            "--published-images",
            str(published),
            "--submitter",
            "not-a-member",
            "--repository-url",
            REPOSITORY_URL,
            "--output",
            str(tmp_path / "compiled-submission.json"),
            "--summary",
            str(summary),
            "--run-id",
            RUN_ID,
        ]
    )

    assert exit_code == EXIT_REFUSED
    assert not summary.exists()


# ---------------------------------------------------------------------------------------
# Which repository is being submitted for
# ---------------------------------------------------------------------------------------


def test_a_repository_nothing_registers_is_refused_and_nothing_is_written(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: let the workload profile check be the one that catches this.

    ``edullm-data`` is registered and has no workload profile, and
    ``tokenizer-flores-validation`` is neither. Both compile against
    ``olmo-core-check`` today into the same refusal -- that the profile belongs to
    ``OLMo-core`` -- which is true and is about the wrong field for the second one. The
    compile job loads ``config/repositories.yaml`` already, so it can say which of the two
    a submitter is looking at before a reviewer is asked either way.

    ``EXIT_REFUSED`` rather than ``EXIT_UNUSABLE`` because this is a verdict on the
    submission. The workflow prints a different sentence for each, and the one for a
    refusal is the one that says no reviewer was asked.
    """
    exit_code, compiled = compile_form(
        tmp_path, payload=form(repository="tokenizer-flores-validation")
    )

    assert exit_code == EXIT_REFUSED
    assert compiled == {}
    reported = capsys.readouterr().err
    assert "config/repositories.yaml" in reported
    assert "olmo-core-check" not in reported


def test_the_unregistered_repository_refusal_leaves_no_approver_context_to_read(
    tmp_path: Path,
) -> None:
    """A refused submission asks nobody, so the page a reviewer would read is not written."""
    summary = tmp_path / "approver-context.md"
    inputs = tmp_path / "submission-form.json"
    inputs.write_text(
        json.dumps(form(repository="tokenizer-flores-validation")), encoding="utf-8"
    )
    published = tmp_path / "published-image.json"
    published.write_text(json.dumps(resolved()), encoding="utf-8")

    exit_code = main(
        [
            "--inputs",
            str(inputs),
            "--config-dir",
            str(CONFIG_DIR),
            "--published-images",
            str(published),
            "--submitter",
            SUBMITTER,
            "--repository-url",
            REPOSITORY_URL,
            "--output",
            str(tmp_path / "compiled-submission.json"),
            "--summary",
            str(summary),
            "--run-id",
            RUN_ID,
        ]
    )

    assert exit_code == EXIT_REFUSED
    assert not summary.exists()


def test_a_registered_repository_with_no_workload_is_refused_for_the_workload(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half of the same fork, and the reason the check reads the registry.

    ``edullm-data`` is registered -- ECR repository, pinned base, publisher role -- and has
    no workload profile, so what stands in its way is the catalog rather than the registry.
    The refusal has to say so, or registering a repository would start answering a question
    it was never asked and send its first submitter to a file that is already correct.
    """
    exit_code, _ = compile_form(tmp_path, payload=form(repository="edullm-data"))

    assert exit_code == EXIT_REFUSED
    reported = capsys.readouterr().err
    assert "olmo-core-check" in reported
    assert "config/repositories.yaml" not in reported


# ---------------------------------------------------------------------------------------
# Which dataset a submission may name
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("retired", "instead"),
    [("dolma-2026-07", "`none`"), ("fineweb-edu-1b-v2", "fineweb-edu-1b-v6")],
)
def test_a_retired_release_is_refused_here_rather_than_only_kept_off_the_form(
    retired: str,
    instead: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE HOLE THIS CLOSED, ASSERTED AT THE JOB EVERY SUBMISSION GOES THROUGH. Mutation:
    take the check out and rely on the dropdown.

    Both names are registered, both were held off the submission form by ``retired`` in
    config/datasets.yaml, and until this check existed both compiled clean and classified
    routine. Measured that way rather than reasoned about: on 2026-08-05 each of them
    cleared ``edullm check`` with "no refusals", compiled as routine here, and was admitted.
    The option list was the only thing refusing them, and a ``choice`` input is GitHub
    validating a form rather than this platform enforcing a rule -- so a dispatch arriving
    by any other route reached a green run and an immutable record naming a corpus nobody
    publishes.

    Parameterised over both because they are the two halves of one flag and the harm is
    different at each. ``dolma-2026-07`` had nothing published under it ever, so the record
    a run leaves is false. ``fineweb-edu-1b-v2`` is real and sealed, so the record is true
    and the result is against a version its owner superseded, which looks exactly like a
    result against the current one.

    The replacement is asserted because a refusal that names no alternative sends a
    submitter back to the registry to work one out, and the registry knows: v6 is the only
    un-retired entry on ``pretrain/fineweb-edu-1b``, and nothing was ever published under
    the release id at all.
    """
    exit_code, compiled = compile_form(tmp_path, payload=form(dataset_release=retired))

    assert exit_code == EXIT_REFUSED
    assert compiled == {}
    reported = capsys.readouterr().err
    assert "retired" in reported
    assert instead in reported
    assert "config/datasets.yaml" in reported


def test_the_retired_refusal_does_not_send_a_resume_to_name_a_corpus_it_did_not_read() -> (
    None
):
    """THE SCOPING, ASSERTED ON THE TEXT BECAUSE THE TEXT IS THE WHOLE OF IT. Mutation:
    shorten the refusal to "pick the current version instead".

    A run resuming from a checkpoint written against a retired corpus has to go on naming
    that corpus. Naming the current one to get past a refusal writes exactly the false
    lineage record this rule exists to prevent, so a refusal that recommends it would be
    causing the harm it was built to stop.

    That is also the argument for where this rule lives. It is refused before the approval
    gate and it is not a ``denied_outright`` condition, so the honest route stays open: the
    flag is a reviewed line in config/datasets.yaml that a pull request can clear, where a
    condition policy denies outright is refusable by nobody at all.

    Nothing in the tree needs that route today, which is worth saying rather than leaving
    to be discovered. Batch's second attempt is the same job with the same run id and never
    re-enters the submission path, and ``tools/build_gpu_training_submission.py`` dispatches
    ``--resume-from`` with ``dataset_release: none``. This is scoping written down before
    the case arrives.
    """
    registry = load_yaml(CONFIG_DIR / "datasets.yaml", DatasetRegistry)

    with pytest.raises(RetiredDatasetReleaseError) as refusal:
        require_a_dataset_release_that_is_current("fineweb-edu-1b-v2", datasets=registry)

    assert "reproducing an earlier result" in str(refusal.value)
    assert "clear the flag in a pull request" in str(refusal.value)
    assert "rather than naming a different one" in str(refusal.value)
    assert "names the corpus it did not read" in str(refusal.value)


def test_a_dataset_the_family_rule_refuses_is_left_to_policy_rather_than_named_retired(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: refuse every retired entry here, whatever else is wrong with it.

    This check runs before compiling and the denied-outright conditions are derived inside
    it, so a name that is both retired and not a corpus would be reported one way here and
    the other way by ``edullm check``, which collects every refusal and asks policy first.
    Two spellings of one submission's problem is the failure ``cli/preflight.py``'s own
    docstring warns about, arriving through ordering rather than through a code.

    So the retirement check stands aside for anything policy already owns, and a tokenizer
    is the live instance: no tokenizer entry is retired today, and the guard is what keeps
    the two sides agreeing if one ever is.
    """
    exit_code, _ = compile_form(tmp_path, payload=form(dataset_release="smollm2-bpe-v1"))

    assert exit_code == EXIT_REFUSED
    reported = capsys.readouterr().err
    assert "dataset_is_not_a_corpus" in reported
    assert "retired" not in reported


def test_the_version_its_owner_calls_current_compiles(tmp_path: Path) -> None:
    """Mutation: refuse the whole corpus rather than the retired version of it.

    ``pretrain/fineweb-edu-1b`` is registered twice and only one of the two entries is
    retired, so a refusal reaching the corpus rather than the entry would take a corpus off
    the form as well as off the registry. This is the direction the parameterised test above
    cannot fail in.
    """
    exit_code, compiled = compile_form(
        tmp_path, payload=form(dataset_release="fineweb-edu-1b-v6")
    )

    assert exit_code == EXIT_OK
    assert compiled["manifest"]["dataset_release"] == "fineweb-edu-1b-v6"


# ---------------------------------------------------------------------------------------
# What the resolve job's answer decides
# ---------------------------------------------------------------------------------------


def test_the_image_the_resolver_found_is_the_one_the_manifest_names(tmp_path: Path) -> None:
    exit_code, compiled = compile_form(tmp_path)

    assert exit_code == EXIT_OK
    assert compiled["manifest"]["image_digest"] == PUBLISHED_DIGEST
    assert compiled["manifest"]["commit_sha"] == COMMIT_SHA


def test_a_commit_built_twice_compiles_to_the_image_the_registry_took_most_recently(
    tmp_path: Path,
) -> None:
    exit_code, compiled = compile_form(
        tmp_path,
        document=resolved(
            published=[
                {"image_digest": PUBLISHED_DIGEST, "pushed_at": FIRST_PUSH},
                {"image_digest": REBUILT_DIGEST, "pushed_at": SECOND_PUSH},
            ]
        ),
    )

    assert exit_code == EXIT_OK
    assert compiled["manifest"]["image_digest"] == REBUILT_DIGEST


def test_a_clean_scan_compiles_where_an_absent_one_was_refused(tmp_path: Path) -> None:
    """THE BEHAVIOUR THIS CHANGES. Mutation: keep passing ``image_scan_summary=None``.

    The summary was absent because this job could not ask ECR, an absent summary is not a
    reviewed one, and ``image_scan_findings_unreviewed`` is denied outright -- so a
    submission whose digest was not in the allowlist never reached a reviewer at all. The
    digest below is in no allowlist, and it compiles because the scan says there is nothing
    to review rather than because the rule was relaxed.
    """
    exit_code, compiled = compile_form(tmp_path)

    assert exit_code == EXIT_OK
    assert compiled["approval_class"] == "automatic"


def test_an_image_carrying_a_blocking_finding_goes_to_a_lead_with_the_findings_named(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Policy v4. Mutation: route it to the lead gate, or route it and say nothing.

    This was refused before a reviewer until 2026-08-05, which meant no approver could
    release it and the only remedy on offer was editing a security file. It is an exception
    now, so it reaches the admin gate and one person can decide.

    What that costs if it is done badly is an approval given blind, which is worse than the
    wall was. So the summary the approver reads has to carry the findings themselves, and
    the section header is asserted as well as the counts: a sentence buried under the cost
    table is one somebody scrolls past.
    """
    exit_code, compiled = compile_form(
        tmp_path,
        document=resolved(
            image_scan=scan_with(critical=2, high=8),
            # Two the shipped config/image-exceptions.yaml has never seen, so this is the
            # unreviewed verdict rather than the reviewed one. The real base image's four
            # criticals all carry a review, which is what makes them the wrong fixture here.
            blocking_findings=[
                {"vulnerability_id": "CVE-2026-90001", "package_name": "perl"},
                {"vulnerability_id": "CVE-2026-90002", "package_name": "glibc"},
            ],
        ),
    )

    assert exit_code == EXIT_OK
    assert compiled["approval_class"] == "routine"
    assert compiled["approving_environment"] == "run-approval-lead"

    summary = approver_context(tmp_path)
    assert "## Unreviewed image scan findings" in summary
    assert "2 findings at CRITICAL" in summary
    assert "CVE-2026-90001 in perl" in summary
    assert "CVE-2026-90002 in glibc" in summary
    assert "config/image-exceptions.yaml" in summary
    assert "Read this before releasing" in summary
    # And on the log the submitter is already watching, for the same reason the placement
    # warning is printed there as well as put in the summary.
    assert "carry no recorded review" in capsys.readouterr().err


def test_a_count_with_no_findings_behind_it_does_not_ask_a_lead_to_review_them(
    tmp_path: Path,
) -> None:
    """The verdict the fixture above used to produce by accident, kept on purpose.

    The registry counted four criticals and the resolve job handed over none of them. That
    is not a set of findings nobody reviewed, it is a set this platform failed to read, and
    the two send a reader to opposite places. It matters more now than it did as a refusal:
    an admin can release this one, and the difference between "decide about these CVEs" and
    "we could not fetch them" is the difference between a judgement and a rubber stamp.
    """
    exit_code, compiled = compile_form(
        tmp_path, document=resolved(image_scan=scan_with(critical=4, high=8))
    )

    assert exit_code == EXIT_OK
    assert compiled["approving_environment"] == "run-approval-lead"

    summary = approver_context(tmp_path)
    assert "did not read them all" in summary
    assert "config/image-exceptions.yaml" not in summary


def test_a_scan_that_had_not_finished_when_it_was_read_says_so_rather_than_naming_a_cve(
    tmp_path: Path,
) -> None:
    """Mutation: print the unreviewed-findings sentence for every unreviewed verdict.

    Zero findings and a status of IN_PROGRESS are the same numbers as a clean scan and the
    opposite fact. ECR scans asynchronously after the push, so this is what a submission
    dispatched a few seconds after a build actually meets. It reaches the admin gate like
    any other unreviewed digest, and the admin has to be told there is nothing to review
    yet rather than handed a list of nothing: waiting is the answer here and a judgement is
    the answer in the test above.
    """
    exit_code, compiled = compile_form(
        tmp_path,
        document=resolved(image_scan={**clean_scan(), "status": "IN_PROGRESS"}),
    )

    assert exit_code == EXIT_OK
    assert compiled["approving_environment"] == "run-approval-lead"

    summary = approver_context(tmp_path)
    assert "scan as IN_PROGRESS rather than COMPLETE" in summary
    assert "the scan has to finish first" in summary
    assert "config/image-exceptions.yaml" not in summary


def test_a_commit_with_nothing_published_is_refused_and_told_to_build_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The resolver writes this rather than failing, because the refusal that names the
    # build workflow is better than anything it could say from inside a job with no
    # submitter in front of it.
    exit_code, _compiled = compile_form(
        tmp_path, document={"published": [], "image_scan": None}
    )

    assert exit_code == EXIT_REFUSED
    assert "build-research-image.yml" in capsys.readouterr().err


def test_an_override_the_declared_commit_did_not_publish_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, _compiled = compile_form(
        tmp_path, payload=form(image_digest=DIGEST_FROM_ANOTHER_COMMIT)
    )

    assert exit_code == EXIT_REFUSED
    assert DIGEST_FROM_ANOTHER_COMMIT in capsys.readouterr().err


def test_an_override_the_declared_commit_did_publish_is_honoured(tmp_path: Path) -> None:
    exit_code, compiled = compile_form(
        tmp_path,
        payload=form(image_digest=PUBLISHED_DIGEST),
        document=resolved(
            published=[
                {"image_digest": PUBLISHED_DIGEST, "pushed_at": FIRST_PUSH},
                {"image_digest": REBUILT_DIGEST, "pushed_at": SECOND_PUSH},
            ]
        ),
    )

    assert exit_code == EXIT_OK
    assert compiled["manifest"]["image_digest"] == PUBLISHED_DIGEST


# ---------------------------------------------------------------------------------------
# What the approver is told the shape has taken
# ---------------------------------------------------------------------------------------


def test_the_approver_reads_the_measurement_the_submitter_was_already_shown(
    tmp_path: Path,
) -> None:
    """THE GAP THIS CLOSED. Mutation: drop ``run_history`` from the render call again.

    ``config/run-history.json`` has been in the checkout this job reads since it was first
    committed, and every approval page rendered before this said no reading was packaged,
    because the argument carrying it was the one keyword this call did not pass. The same
    ``history_for`` on the same shape was meanwhile printing a figure to the submitter
    through ``cli/preflight.py``, so the person spending nothing read what runs like this
    take and the lead spending it read that nobody had measured.

    Asserted on the sentence rather than on the argument, because the argument is what
    could be passed and still reach a renderer that ignored it. The shape below is the one
    the form defaults to and the committed reading holds a cohort for it, so a page missing
    the figure is this bug and not a thin sample.
    """
    exit_code, _compiled = compile_form(tmp_path)

    assert exit_code == EXIT_OK
    summary = approver_context(tmp_path)
    assert "## What runs of this shape have taken" in summary
    assert NO_HISTORY_PACKAGED not in summary
    assert "succeeded runs of this workload, on this machine, on this dataset" in summary
    assert "took a median of" in summary
    # The date the reading was taken travels with it, so a lead reading a page in a month
    # can discount a measurement of a platform that no longer exists.
    assert "Measured on" in summary


def test_a_shape_the_reading_has_never_seen_says_so_rather_than_borrowing_a_figure(
    tmp_path: Path,
) -> None:
    """Mutation: word an unmeasured shape the same way an unpackaged reading is worded.

    ``olmo-eval-check`` has never run here and every other workload on ``olmo-eval-full``
    has, so no rung of the ladder answers for it. The page has to say that in its own
    words: "nothing of this shape has run" is a fact about the platform and "no reading is
    packaged" is a fact about the install, and a lead who reads the second where the first
    is true concludes the tool is broken rather than that they are the first to try this.
    Before the argument was passed, every page said the second one.
    """
    exit_code, _compiled = compile_form(
        tmp_path,
        payload=form(
            repository="olmo-eval-full",
            workload_profile="olmo-eval-check",
            wandb_project="olmo-eval",
        ),
    )

    assert exit_code == EXIT_OK
    summary = approver_context(tmp_path)
    assert NOTHING_LIKE_THIS_YET in summary
    assert NO_HISTORY_PACKAGED not in summary


def test_a_reading_this_tree_cannot_parse_stops_the_job_rather_than_reading_as_absent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: catch the parse error and carry on with no reading.

    Absent and unreadable are different findings and only one of them is about the
    install. Degrading a corrupt reading to "no run history is packaged" would put a
    sentence about an ordinary editable checkout on the page of a job running from a
    checkout of ``main``, where that sentence is false and the real problem is invisible.
    ``edullm check`` already refuses this locally through the same loader, so the two sides
    agree about what a broken file means.
    """
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    (config / "run-history.json").write_text("{not json", encoding="utf-8")

    inputs = tmp_path / "submission-form.json"
    inputs.write_text(json.dumps(form()), encoding="utf-8")
    published = tmp_path / "published-image.json"
    published.write_text(json.dumps(resolved()), encoding="utf-8")

    exit_code = main(
        [
            "--inputs",
            str(inputs),
            "--config-dir",
            str(config),
            "--published-images",
            str(published),
            "--submitter",
            SUBMITTER,
            "--repository-url",
            REPOSITORY_URL,
            "--output",
            str(tmp_path / "compiled-submission.json"),
            "--summary",
            str(tmp_path / "approver-context.md"),
            "--run-id",
            RUN_ID,
        ]
    )

    assert exit_code == EXIT_UNUSABLE
    assert "run-history.json" in capsys.readouterr().err


def test_a_checkout_carrying_no_reading_compiles_and_says_nothing_was_measured(
    tmp_path: Path,
) -> None:
    """Mutation: make the reading required the way the six rules are.

    An install from before the first reading was committed, an editable checkout and a
    directory a test built all carry no ``run-history.json``, and none of them is a broken
    platform. A measurement is the one file here whose absence is a thing to say out loud
    rather than a reason to refuse a submission nobody would otherwise decline.
    """
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    (config / "run-history.json").unlink()

    inputs = tmp_path / "submission-form.json"
    inputs.write_text(json.dumps(form()), encoding="utf-8")
    published = tmp_path / "published-image.json"
    published.write_text(json.dumps(resolved()), encoding="utf-8")
    summary = tmp_path / "approver-context.md"

    exit_code = main(
        [
            "--inputs",
            str(inputs),
            "--config-dir",
            str(config),
            "--published-images",
            str(published),
            "--submitter",
            SUBMITTER,
            "--repository-url",
            REPOSITORY_URL,
            "--output",
            str(tmp_path / "compiled-submission.json"),
            "--summary",
            str(summary),
            "--run-id",
            RUN_ID,
        ]
    )

    assert exit_code == EXIT_OK
    assert NO_HISTORY_PACKAGED in summary.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------
# The day's ceiling, which is the only thing here that reads past this submission
# ---------------------------------------------------------------------------------------


def index_holding(
    tmp_path: Path, entries: list[dict[str, Any]], *, name: str = "run-index.json"
) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"format_version": 1, "runs": entries}), encoding="utf-8")
    return path


def released_by_nobody(
    number: int,
    cost: str | None = "482.10",
    *,
    approval_class: str = "automatic",
    at: datetime | None = None,
) -> dict[str, Any]:
    """One entry of the shape the exposure is made of, as the workflow writes it.

    $482.10 is sixteen hours of ``gpu-8xl40s`` in one cell, priced from the catalog on
    2026-08-06, and it is the widest single run this account releases by nobody.
    """
    entry: dict[str, Any] = {
        "run_id": f"run_019fd5e1-38da-7019-b7d3-67d561db3{number:03d}",
        # Spelled through ``int`` for the reason tests/test_run_index.py spells its
        # own the same way: an eleven-digit literal is what the account-id guard in
        # tests/test_evidence.py is looking for, and it cannot tell one from a
        # padded account number.
        "workflow_run_id": int("30281990942") + number,
        "workflow_run_url": "https://github.com/edu-llm/platform/actions/runs/1",
        "submitter": f"researcher-{number:02d}",
        "repository": "OLMo-core",
        "commit_sha": COMMIT_SHA,
        "team": "data-prep",
        "experiment": "context-length-sweep",
        "compute_profile": "gpu-8xl40s",
        "approval_class": approval_class,
        "fanout_size": None,
        "minted_at": (at or datetime.now(UTC)).isoformat(),
    }
    if cost is not None:
        entry["maximum_compute_cost_usd"] = cost
    return entry


def compile_against_the_day(
    tmp_path: Path, run_index: Path | None
) -> tuple[int, dict[str, Any], str]:
    """The whole compile step, against a real day's ledger and the reviewed configuration."""
    inputs = tmp_path / "submission-form.json"
    inputs.write_text(json.dumps(form()), encoding="utf-8")
    published = tmp_path / "published-image.json"
    published.write_text(json.dumps(resolved()), encoding="utf-8")
    output = tmp_path / "compiled-submission.json"
    summary = tmp_path / "approver-context.md"

    argv = [
        "--inputs",
        str(inputs),
        "--config-dir",
        str(CONFIG_DIR),
        "--published-images",
        str(published),
        "--submitter",
        SUBMITTER,
        "--repository-url",
        REPOSITORY_URL,
        "--output",
        str(output),
        "--summary",
        str(summary),
        "--run-id",
        RUN_ID,
    ]
    if run_index is not None:
        argv += ["--run-index", str(run_index)]

    exit_code = main(argv)
    compiled = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    assert isinstance(compiled, dict)
    page = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return exit_code, compiled, page


def test_a_quiet_day_leaves_the_automatic_class_exactly_as_it_was(tmp_path: Path) -> None:
    """THE ORDINARY DAY, WHICH IS THE HALF THAT MAKES THE OTHER ASSERTIONS MEAN ANYTHING.

    Mutation: route every submission to a lead. Every other case in this section passes,
    because every other case asserts that something reaches a lead. This is the one that
    fails, and without it the whole section is satisfied by a control that is stuck on --
    which is a worse outcome than no ceiling, because it is the state somebody fixes by
    deleting the ceiling.

    ``config/run-history.json`` measures the ordinary submission here as a check with a
    median of five minutes, so a day of them is two orders of magnitude under the bound.
    """
    index = index_holding(tmp_path, [released_by_nobody(1, "1.01")])

    exit_code, compiled, page = compile_against_the_day(tmp_path, index)

    assert exit_code == EXIT_OK
    assert compiled["approval_class"] == "automatic"
    assert compiled["approving_environment"] == "run-approval-automatic"
    assert "## Why this is in front of you" not in page


def test_a_day_that_has_spent_its_unattended_budget_sends_the_next_run_to_a_lead(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE EXPOSURE, CLOSED, DRIVEN THROUGH THE JOB THE WORKFLOW ACTUALLY RUNS.

    Mutation: apply the ceiling only to the run in front of it, which is what every
    threshold before v6 did. Three runs of $482.10 are each under the per-run bound of
    $500, so the submission below stays automatic and the thirty-fifth one does too.

    The submission itself is a cheap check. That is the point and it is why this cannot be
    asserted on the cost: what routes it is the day rather than the request, and a rule
    reading the request cannot see the difference.
    """
    index = index_holding(
        tmp_path, [released_by_nobody(n) for n in (1, 2, 3)]
    )

    exit_code, compiled, _page = compile_against_the_day(tmp_path, index)

    assert exit_code == EXIT_OK
    assert compiled["approval_class"] == "routine"
    assert compiled["approving_environment"] == "run-approval-lead"
    reported = capsys.readouterr().err
    assert "$1,446.30" in reported
    assert "goes to a team lead instead" in reported


def test_the_lead_is_told_the_day_is_the_reason_and_not_the_request(
    tmp_path: Path,
) -> None:
    """Mutation: raise the class and say nothing on the page.

    A lead opening a request for a few dollars, released by a gate that exists for
    expensive things, learns nothing from the cost table about why they were woken. The
    section has to say that the question is about the account's day rather than about this
    figure, or the honest reading of the page is that the routing is broken.
    """
    index = index_holding(tmp_path, [released_by_nobody(n) for n in (1, 2, 3)])

    _exit_code, _compiled, page = compile_against_the_day(tmp_path, index)

    assert "## Why this is in front of you" in page
    assert "$1,446.30" in page
    assert "under the per-run bound" in page
    assert "config/policy.yaml" in page
    assert "resets at midnight UTC" in page


@pytest.mark.parametrize(
    ("described", "entries"),
    [
        pytest.param(
            "an entry from today with no figure on it",
            [released_by_nobody(1, None)],
            id="unpriced",
        ),
        pytest.param(
            "an index that holds nothing this tree can read",
            [{"run_id": "run_1"}],
            id="malformed",
        ),
    ],
)
def test_a_day_this_job_could_not_price_asks_a_lead(
    described: str,
    entries: list[dict[str, Any]],
    tmp_path: Path,
) -> None:
    """FAIL CLOSED, THROUGH THE TOOL. Mutation: catch the read failure and carry on.

    Reading a day that could not be read as a day with nothing in it switches the ceiling
    off precisely when the machinery behind it is broken. The ledger is a file on a branch
    that a force-push rewrites, so this is the failure that will actually happen.

    Both rows produce the same routing and different sentences, because only one of them is
    a thing somebody has to go and fix.
    """
    index = index_holding(tmp_path, entries)

    exit_code, compiled, page = compile_against_the_day(tmp_path, index)

    assert exit_code == EXIT_OK, described
    assert compiled["approving_environment"] == "run-approval-lead"
    assert "could not be read" in page


def test_no_index_at_all_asks_a_lead_rather_than_waving_the_run_through(
    tmp_path: Path,
) -> None:
    """Mutation: make ``--run-index`` optional in effect as well as in argparse.

    A workflow that stopped passing the file would otherwise turn the ceiling off with no
    red run anywhere, which is the exact failure shape ``config/reports/surfaces.yaml``
    recorded as a proven property for the whole of this platform's cost story. The argument
    stays optional because a policy carrying no ceiling has nothing to compare against, and
    that case is settled before this one is asked.
    """
    exit_code, compiled, page = compile_against_the_day(tmp_path, None)

    assert exit_code == EXIT_OK
    assert compiled["approving_environment"] == "run-approval-lead"
    assert "no --run-index" in page


def test_a_run_over_the_per_run_bound_is_not_told_the_day_is_why(tmp_path: Path) -> None:
    """Mutation: decide the section by comparing the cost against the per-run bound.

    A fan-out was always going to reach a lead, whatever the day has spent, so a section
    saying the day put it here would be false. The eight cells below cost a fraction of the
    per-run bound, so a cost comparison calls this one the day's doing; only re-asking
    ``classify_request`` gets it right, and the same is true of a cheap run on an unreviewed
    digest.

    Getting it wrong costs more than a wrong sentence. Nine approvers who meet the paragraph
    on every request stop reading it, and the one submission it exists for is the one where
    not reading it loses the whole point.
    """
    index = index_holding(tmp_path, [released_by_nobody(n) for n in (1, 2, 3)])
    inputs = tmp_path / "submission-form.json"
    inputs.write_text(
        json.dumps(form(fanout_size=8, fanout_index_parameter="seed")), encoding="utf-8"
    )
    published = tmp_path / "published-image.json"
    published.write_text(json.dumps(resolved()), encoding="utf-8")
    summary = tmp_path / "approver-context.md"

    exit_code = main(
        [
            "--inputs",
            str(inputs),
            "--config-dir",
            str(CONFIG_DIR),
            "--published-images",
            str(published),
            "--submitter",
            SUBMITTER,
            "--repository-url",
            REPOSITORY_URL,
            "--output",
            str(tmp_path / "compiled-submission.json"),
            "--summary",
            str(summary),
            "--run-index",
            str(index),
            "--run-id",
            RUN_ID,
        ]
    )

    assert exit_code == EXIT_OK
    assert "## Why this is in front of you" not in summary.read_text(encoding="utf-8")


def test_the_worst_case_is_handed_to_the_job_that_writes_the_index(tmp_path: Path) -> None:
    """The other end of the loop, without which the ceiling reads an empty day forever.

    Mutation: stop emitting ``maximum_compute_cost_usd`` as a step output. Every entry the
    index gains carries no figure, every subsequent reading is unpriced, and the mechanism
    fails closed on every submission -- a control stuck on, for a reason nothing on the page
    explains. It is the compile job's own figure rather than one derived downstream, because
    a second implementation of the arithmetic is a second answer to what the ceiling compares.
    """
    inputs = tmp_path / "submission-form.json"
    inputs.write_text(json.dumps(form()), encoding="utf-8")
    published = tmp_path / "published-image.json"
    published.write_text(json.dumps(resolved()), encoding="utf-8")
    github_output = tmp_path / "github-output"

    exit_code = main(
        [
            "--inputs",
            str(inputs),
            "--config-dir",
            str(CONFIG_DIR),
            "--published-images",
            str(published),
            "--submitter",
            SUBMITTER,
            "--repository-url",
            REPOSITORY_URL,
            "--output",
            str(tmp_path / "compiled-submission.json"),
            "--summary",
            str(tmp_path / "approver-context.md"),
            "--github-output",
            str(github_output),
            "--run-id",
            RUN_ID,
        ]
    )

    assert exit_code == EXIT_OK
    written = github_output.read_text(encoding="utf-8")
    recorded = next(
        line.split("=", 1)[1]
        for line in written.splitlines()
        if line.startswith("maximum_compute_cost_usd=")
    )
    # And it is the figure the approver page prints, rather than a second derivation of it.
    page = (tmp_path / "approver-context.md").read_text(encoding="utf-8")
    assert f"= **${recorded}**" in page
    assert Decimal(recorded) > 0


# ---------------------------------------------------------------------------------------
# An input this job could not read is not a judgement about the submission
# ---------------------------------------------------------------------------------------


def test_a_resolver_document_that_is_not_there_is_unusable_rather_than_a_refusal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Exit 2 rather than 1, the same separation the form and the reviewed configuration
    # already get: the workflow prints a different sentence for each, and a submission
    # nobody could read must not read as one nobody would approve.
    exit_code, _compiled = compile_form(tmp_path, published_images=tmp_path / "absent.json")

    assert exit_code == EXIT_UNUSABLE
    assert "unreadable" in capsys.readouterr().err


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("not a document", id="not an object"),
        pytest.param({"image_scan": None}, id="no published key"),
        pytest.param({"published": {}, "image_scan": None}, id="published is not a list"),
        pytest.param(
            {"published": [{"image_digest": PUBLISHED_DIGEST}], "image_scan": None},
            id="an entry with no push time",
        ),
        pytest.param(
            {
                "published": [{"image_digest": PUBLISHED_DIGEST, "pushed_at": "yesterday"}],
                "image_scan": None,
            },
            id="a push time that is not an instant",
        ),
        pytest.param(
            {
                "published": [
                    {"image_digest": PUBLISHED_DIGEST, "pushed_at": "2026-07-26T09:02:00"}
                ],
                "image_scan": None,
            },
            id="a push time with no offset",
        ),
        pytest.param(
            {"published": [], "image_scan": {"schema_version": 1, "status": "DONE"}},
            id="a scan status the contract does not define",
        ),
    ],
)
def test_a_resolver_document_this_job_cannot_read_stops_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    document: object,
) -> None:
    """Mutation: skip the entries that do not parse and compile the rest.

    Dropping a malformed entry silently turns a broken resolve into a commit with fewer
    published images, and the shortest way for that to end is a submission resolved to an
    older image than the one it should have run -- which is invisible in the record,
    because a rebuild legitimately looks like that.
    """
    exit_code, compiled = compile_form(tmp_path, document=document)

    assert exit_code == EXIT_UNUSABLE
    assert compiled == {}
    assert capsys.readouterr().err.strip() != ""


def test_the_resolver_document_is_required_rather_than_defaulted_to_nothing(
    tmp_path: Path,
) -> None:
    """Mutation: default it to an empty list when the argument is not given.

    Nothing published is a real answer with a refusal attached, so a default that spelled
    "I was not told" the same way would report every submission as an unbuilt commit the
    first time the workflow forgot to pass the file.
    """
    inputs = tmp_path / "submission-form.json"
    inputs.write_text(json.dumps(form()), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "--inputs",
                str(inputs),
                "--config-dir",
                str(CONFIG_DIR),
                "--submitter",
                SUBMITTER,
                "--repository-url",
                REPOSITORY_URL,
                "--output",
                str(tmp_path / "compiled-submission.json"),
            ]
        )

    assert exit_info.value.code == 2


# ---------------------------------------------------------------------------------------
# Which install typed the submission
# ---------------------------------------------------------------------------------------

#: A command that lost the quotes grouping ``bash -lc``'s program into one word. This is
#: what every ``edullm submit`` before 3.4.8 sent for the ``bash -lc`` line the guides
#: carry, because it rejoined the split command with a plain space.
UNQUOTED_COMMAND = ["bash", "-lc", "python", "train.py", "--steps", "20"]


@pytest.mark.parametrize(
    ("client_version", "expected"),
    [
        pytest.param("3.7.1", "Submitted by edullm 3.7.1.", id="an install that named itself"),
        pytest.param("", "names no edullm version", id="the Actions form"),
    ],
)
def test_the_log_says_which_install_typed_every_submission_that_compiles(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    client_version: str,
    expected: str,
) -> None:
    """Mutation: print it only when something is refused.

    Nothing this platform stores has ever recorded a client version, which is the recorded
    reason nobody can say whether anybody is on a current edullm. The submissions that
    answer that question are the ones that worked, so the line is printed before anything
    else happens rather than as part of a complaint.
    """
    exit_code, _compiled = compile_form(tmp_path, client_version=client_version)

    assert exit_code == EXIT_OK
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize(
    ("client_version", "recorded"),
    [
        pytest.param("3.7.1", "3.7.1", id="an install that named itself"),
        pytest.param("", None, id="the Actions form"),
        pytest.param("latest", None, id="something that is not a version"),
    ],
)
def test_the_compiled_submission_records_which_install_typed_it(
    tmp_path: Path, client_version: str, recorded: str | None
) -> None:
    """The one place the answer survives the job, and the reason it is this place.

    Mutation: fold it into the manifest instead. ``RunManifest`` is hashed whole and the
    digest is what an approver releases, so a field added there moves the digest of every
    record ever written -- ``CompiledSubmission.experiment`` measured exactly that. A
    sibling costs nothing and is what ``edullm status`` already downloads.
    """
    exit_code, compiled = compile_form(tmp_path, client_version=client_version)

    assert exit_code == EXIT_OK
    assert compiled["edullm_version"] == recorded
    assert "edullm_version" not in compiled["manifest"]


@pytest.mark.parametrize("client_version", ["", "3.4.7", "3.7.1", "latest"])
def test_nothing_is_refused_on_the_version_it_says_it_came_from(
    tmp_path: Path, client_version: str
) -> None:
    """Mutation: refuse anything below the release that fixed the last defect.

    A stale install that produces a valid submission still produces a valid submission, and
    the reviewed configuration this job judges against is the job's own rather than the
    submitter's. A floor here would refuse correct work on the strength of a probability,
    at the hour when whoever is submitting can least afford it -- and every one of the four
    values below has to compile for that to stay true, including the two this cannot order.
    """
    exit_code, compiled = compile_form(tmp_path, client_version=client_version)

    assert exit_code == EXIT_OK
    assert compiled["run_id"] == RUN_ID


def test_an_old_install_is_told_to_reinstall_rather_than_to_quote_what_it_unquoted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The refusal this whole change is about, from the install that caused it.

    Mutation: leave the refusal as it was. What the submitter then reads is "Quote the
    whole program", which is what they did -- their install took the quotes off between
    the spec and the form -- so the advice sends them to re-do the one thing that was
    already right and the second attempt is refused identically.
    """
    exit_code, compiled = compile_form(
        tmp_path, payload=form(command=UNQUOTED_COMMAND), client_version="3.4.7"
    )
    reported = capsys.readouterr().err

    assert exit_code == EXIT_REFUSED
    assert compiled == {}
    assert "Reinstall edullm" in reported
    assert "3.4.7" in reported
    assert "3.4.8" in reported
    assert "uv tool install --force" in reported


def test_a_submission_that_named_no_install_is_offered_both_readings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: read a blank field as an old install and accuse the reader of being behind.

    A dispatch from the Actions tab names no install and never will, and that is how
    tonight's probe runs were launched. Somebody who typed this command into the form by
    hand really did leave the quotes off, and the refusal above them is right; somebody on
    an install older than the field did not. The sentence has to leave both open.
    """
    exit_code, _compiled = compile_form(tmp_path, payload=form(command=UNQUOTED_COMMAND))
    reported = capsys.readouterr().err

    assert exit_code == EXIT_REFUSED
    assert "If you ran edullm submit" in reported
    assert "Actions form" in reported


def test_a_current_install_is_told_only_what_is_wrong_with_its_command(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: print the reinstall note whenever the refusal matches.

    A submission from an install that never unquoted anything arrived exactly as it was
    written, so the refusal's own advice is the correct advice and a second paragraph
    telling this reader to reinstall would send them round a loop that cannot help.
    """
    exit_code, _compiled = compile_form(
        tmp_path, payload=form(command=UNQUOTED_COMMAND), client_version="3.7.1"
    )
    reported = capsys.readouterr().err

    assert exit_code == EXIT_REFUSED
    assert "reads exactly one word as the command" in reported
    assert "Reinstall edullm" not in reported
    assert "uv tool install" not in reported


@pytest.mark.parametrize("client_version", ["", "3.4.7"])
def test_a_refusal_no_install_caused_never_mentions_the_install(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    client_version: str,
) -> None:
    """Mutation: annotate every refusal with the version, or match on something looser.

    An unregistered repository is a refusal about a field the submitter chose, and it reads
    the same from every install there has ever been. A version sentence beside it is a
    second thing to read that changes nothing, and a reader who meets one three times stops
    reading them -- including on the refusal where it is the whole answer.
    """
    exit_code, _compiled = compile_form(
        tmp_path,
        payload=form(repository="tokenizer-flores-validation"),
        client_version=client_version,
    )
    reported = capsys.readouterr().err

    assert exit_code == EXIT_REFUSED
    assert "config/repositories.yaml" in reported
    assert "Reinstall edullm" not in reported
    assert "uv tool install" not in reported
