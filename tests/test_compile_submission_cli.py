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
from pathlib import Path
from typing import Any

import pytest

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
        "workload_profile": "olmo-core-check-cpu",
        "dataset_release": "dolma-2026-07",
        "team": "data-prep",
        "wandb_project": "olmo-core-tokenize",
        "experiment": "dolma-tokenization",
        "command": ["python", "-m", "olmo_core.data.tokenize"],
    }
    payload.update(overrides)
    return payload


def resolved(
    *,
    published: list[dict[str, str]] | None = None,
    image_scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The document ``tools/resolve_published_image.py`` writes, in its own shape."""
    return {
        "published": (
            published
            if published is not None
            else [{"image_digest": PUBLISHED_DIGEST, "pushed_at": FIRST_PUSH}]
        ),
        "image_scan": clean_scan() if image_scan is None else image_scan,
    }


def compile_form(
    tmp_path: Path,
    *,
    payload: dict[str, object] | None = None,
    document: object = None,
    published_images: Path | None = None,
    submitter: str = SUBMITTER,
) -> tuple[int, dict[str, Any]]:
    inputs = tmp_path / "submission-form.json"
    inputs.write_text(json.dumps(payload if payload is not None else form()), encoding="utf-8")
    if published_images is None:
        published_images = tmp_path / "published-image.json"
        published_images.write_text(
            json.dumps(document if document is not None else resolved()), encoding="utf-8"
        )
    output = tmp_path / "compiled-submission.json"
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
            "--repository-url",
            REPOSITORY_URL,
            "--output",
            str(output),
            "--run-id",
            RUN_ID,
        ]
    )
    compiled = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    assert isinstance(compiled, dict)
    return exit_code, compiled


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
    ``olmo-core-check-cpu`` today into the same refusal -- that the profile belongs to
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
    assert "olmo-core-check-cpu" not in reported


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
    assert "olmo-core-check-cpu" in reported
    assert "config/repositories.yaml" not in reported


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
    assert compiled["approval_class"] == "routine"


def test_an_image_carrying_a_blocking_finding_is_still_refused_before_a_reviewer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other half. Mutation: read the summary and never let it deny anything.

    A summary nothing can refuse on is a number with no consequence attached, which is the
    rubber-stamping failure the approver context already had to be designed against.
    """
    exit_code, compiled = compile_form(
        tmp_path, document=resolved(image_scan=scan_with(critical=4, high=8))
    )

    assert exit_code == EXIT_REFUSED
    assert compiled == {}
    assert "image_scan_findings_unreviewed" in capsys.readouterr().err


def test_a_scan_that_had_not_finished_when_it_was_read_is_refused_rather_than_assumed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Zero findings and a status of IN_PROGRESS are the same numbers as a clean scan and
    # the opposite fact. ECR scans asynchronously after the push, so this is what a
    # submission dispatched a few seconds after a build actually meets.
    exit_code, _compiled = compile_form(
        tmp_path,
        document=resolved(image_scan={**clean_scan(), "status": "IN_PROGRESS"}),
    )

    assert exit_code == EXIT_REFUSED
    assert "image_scan_findings_unreviewed" in capsys.readouterr().err


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
