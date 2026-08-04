"""That the deployed lifecycle recorder is the recorder this tree describes.

The admission validator has had this check since Phase 4 bought it with a live GPU run
refused by a validator holding a stale catalog. The recorder never got one, and on
2026-07-31 it was found running bytes that matched neither the current tree nor the tree as
it stood that morning. Nothing had failed, because nothing was looking.

**The recorder's drift is quieter than the validator's, which is the reason to check it
rather than a reason not to.** A validator running stale bytes refuses a submission, and a
refusal is at least an event somebody reads. A recorder running stale bytes writes lineage
that looks exactly like correct lineage. The records are immutable, so nothing can be
corrected afterwards, and the first person to notice is whoever reads a projection later
and finds it does not join.

**Two things now rest on this that did not exist at its last release.** The cost report
derives per-run spend from the ``started_at`` and ``ended_at`` this function writes onto
``attempt/`` records, and it is the platform's whole answer to attribution now that AWS
cost allocation tags have turned out to be unactivatable from a linked account. The
queue-wait detector reads the same event stream. Neither is worth more than the bytes
underneath it.

Mirrors ``tests/test_phase2_lambda_package.py`` deliberately: same three assertions, same
release-record shape, same slow marker on the one that builds. What it does not mirror is
that file's checks on the wheel platform and on where configuration lands, which are
properties of ``build_package`` itself and are already asserted once. Asserting them twice
would make a change to the shared builder fail in two places and be understood in neither.

The one packaging check that is this function's own is the last one here: that its archive
carries no configuration whatsoever. That is not a property of the builder -- the builder
packages whatever it is handed -- it is a property of this handler, and it is what makes a
roster or catalog edit cost this function nothing.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELEASE_RECORD_PATH = PROJECT_ROOT / "infra" / "lifecycle-recorder-release.yaml"
EVENTS_TEMPLATE_PATH = PROJECT_ROOT / "infra" / "batch-events.yaml"

# The same path insertion tools/build_lifecycle_lambda.py performs on itself, and for the
# same reason its comment gives: the builder imports its Phase 2 sibling by bare module
# name, so tools/ has to be importable before this module can import the builder at all.
TOOLS_DIRECTORY = PROJECT_ROOT / "tools"
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from tools.build_admission_lambda import build_package
from tools.build_lifecycle_lambda import (
    ARTIFACT_KEY,
    HANDLER_ENTRY_POINT,
    RECORDER_CONFIG,
    RECORDER_ENTRYPOINT,
)


def release_record() -> dict[str, Any]:
    loaded = yaml.safe_load(RELEASE_RECORD_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def recorder_function() -> dict[str, Any]:
    """The one Lambda in batch-events.yaml, read out of the template rather than named.

    Asserted to be the only one so that a second function added to this template is a
    failure here rather than a silent choice of whichever came first.
    """
    template = yaml.safe_load(EVENTS_TEMPLATE_PATH.read_text(encoding="utf-8"))
    functions = [
        resource
        for resource in template["Resources"].values()
        if resource["Type"] == "AWS::Lambda::Function"
    ]
    assert len(functions) == 1, "this template declares exactly one recorder"
    properties = functions[0]["Properties"]
    assert isinstance(properties, dict)
    return properties


@pytest.fixture(scope="module")
def package(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    output = tmp_path_factory.mktemp("lifecycle") / "lifecycle-recorder.zip"
    return build_package(
        PROJECT_ROOT, output, entrypoint=RECORDER_ENTRYPOINT, configuration=RECORDER_CONFIG
    )


def test_the_template_and_the_record_name_the_same_object() -> None:
    """Mutation: release the zip and forget the template, or the other way round.

    Either alone deploys nothing. A new object version with no template edit leaves the
    resource's properties byte-identical, so the change set comes back empty and
    `deploy --no-fail-on-empty-changeset` reports success over unchanged code.
    """
    code = recorder_function()["Code"]
    recorded = release_record()

    assert code["S3Key"] == recorded["s3_key"]
    assert code["S3ObjectVersion"] == recorded["s3_object_version"]


def test_the_template_runs_the_entry_point_the_builder_packages_for() -> None:
    """Mutation: point Handler at the validator's entry point.

    The two functions share a package and differ by which entry point the template names,
    so a Handler naming the other one imports cleanly and then behaves as the wrong
    function. Both spellings are declared in the builder; this is the seam that holds the
    template to them.
    """
    assert recorder_function()["Handler"] == HANDLER_ENTRY_POINT
    assert recorder_function()["Code"]["S3Key"] == ARTIFACT_KEY


@pytest.mark.slow
def test_the_released_zip_is_the_one_this_tree_builds(package: dict[str, object]) -> None:
    """Mutation: change a contract the recorder imports and do not release it.

    THIS IS THE CHECK THE RECORDER DID NOT HAVE ON 2026-07-31, WHEN ITS ABSENCE WAS FOUND
    RATHER THAN SUFFERED. The deployed CodeSha256 matched neither this tree nor the tree
    as it stood that morning, so the function had been running unaccounted-for bytes for
    an unknown period and every lineage record it wrote in that time was written by code
    nobody could point at.

    The build is deterministic, so this compares a digest rather than a timestamp: an
    unchanged tree rebuilds to the recorded digest and needs no edit. It fails only when
    the packaged bytes have moved and the record has not, which is exactly the window in
    which the account is writing lineage this tree did not describe.
    """
    recorded = release_record()

    assert package["sha256"] == recorded["sha256"], (
        "the zip this tree builds is not the zip that was released. Something the package "
        "carries has changed -- the handler, the projection, or a contract either imports "
        "-- and the deployed recorder is still writing lineage with the previous bytes. "
        "Release it with the procedure in infra/README.md and update "
        "infra/lifecycle-recorder-release.yaml in the same commit."
    )


@pytest.mark.slow
def test_the_recorder_zip_carries_no_configuration_at_all(package: dict[str, object]) -> None:
    """Mutation: pass the validator's config list to this builder.

    THE PROPERTY THAT MAKES A ROSTER EDIT FREE FOR THIS FUNCTION. Read off the built
    archive rather than off the declared list, because it is the archive that is uploaded:
    what this asserts is that no byte of ``config/`` is inside the object whose digest the
    test above compares, so no edit under ``config/`` can move it.

    Until 2026-08-04 every file under ``config/`` was in here. The recorder opens none of
    them -- it projects a Batch state-change event and writes a lifecycle record -- so the
    entire cost was a red required check on changes eight team leads had just been given
    approval to make, clearable only by somebody holding AWS credentials.
    """
    with zipfile.ZipFile(str(package["path"])) as archive:
        configuration = [
            name for name in archive.namelist() if name.startswith("edullm_platform/config/")
        ]

    assert configuration == [], (
        f"the recorder reads no configuration and its zip carries {configuration}, so every "
        "edit to one of those files moves this function's release digest for nothing"
    )
    assert package["configuration"] == []
