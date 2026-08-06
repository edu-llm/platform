"""What the expiry janitor's deployment zip has to be, beyond what its digest says.

**THE DIGEST COMPARISON LEFT THIS FILE AND THE ARGUMENT THAT PUT IT HERE IS WHY.** This module
opened by saying it mirrored tests/test_phase3_lifecycle_package.py, which had said it mirrored
tests/test_phase2_lambda_package.py, on the reasoning that a third function checked a third way
is how the release procedure stops describing all of them. That reasoning was right and it did
not go far enough: the notifier arrived after all three, was mirrored by nobody, and drifted
with nothing going red until #294 deployed three functions by hand and somebody noticed only
two of them had reached the pending register. The comparison is now written once over
release_lambda.FUNCTIONS in tests/test_released_zips.py.

The janitor's own argument against the ``compare_release`` escape hatch survives that move and
is worth keeping here, because this is still the function it is about: nothing is running these
bytes, so there is no deployed function to be stale, and a tolerance recorded ahead of the thing
it tolerates is a tolerance nobody can tell from a bug. Consulting the register is not writing
in it. The register is empty, an unexplained difference is reported as SKEWED exactly as a bare
comparison would report it, and an entry for this function would still have to be written by
hand into a reviewed diff.

THE FAILURE THAT COMPARISON CATCHES IS THE QUIETEST OF THE FOUR. A validator running stale bytes
refuses a submission for a reason correct about the bytes and wrong about the account. A
recorder running stale bytes writes lineage that looks right and does not join. A notifier
running stale bytes posts a message that is wrong about the money. A janitor running stale bytes
stops nothing, and a sweep that stops nothing is indistinguishable from a quiet morning -- there
is no researcher waiting on it and no record that fails to parse.

What is left here is what is this function's own: that the record and the template name one
object and one version, that the template runs the entry point its builder packages for, that
the archive carries no configuration, and that the never-uploaded placeholder is in all three
places that name it or in none.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_RECORD_PATH = PROJECT_ROOT / "infra" / "expiry-janitor-release.yaml"
TEMPLATE_PATH = PROJECT_ROOT / "infra" / "expiry-janitor.yaml"

# The same path insertion tools/build_janitor_lambda.py performs on itself, and for the same
# reason its comment gives: the builder imports its Phase 2 sibling by bare module name, so
# tools/ has to be importable before this module can import the builder at all.
TOOLS_DIRECTORY = PROJECT_ROOT / "tools"
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from tools.build_admission_lambda import build_package
from tools.build_janitor_lambda import (
    ARTIFACT_KEY,
    HANDLER_ENTRY_POINT,
    JANITOR_CONFIG,
    JANITOR_ENTRYPOINT,
)


def release_record() -> dict[str, Any]:
    loaded = yaml.safe_load(RELEASE_RECORD_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def janitor_function() -> dict[str, Any]:
    """The one Lambda in expiry-janitor.yaml, read out of the template rather than named.

    Read by type rather than by logical id, so renaming the resource does not silently make
    this test assert nothing. Asserted to be the only one so that a second function added to
    this template is a failure here rather than a silent choice of whichever came first.
    """
    template = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    functions = [
        value
        for value in template["Resources"].values()
        if isinstance(value, dict) and value.get("Type") == "AWS::Lambda::Function"
    ]
    assert len(functions) == 1, "this template declares exactly one janitor"
    properties = functions[0]["Properties"]
    assert isinstance(properties, dict)
    return properties


@pytest.fixture(scope="module")
def package(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    output = tmp_path_factory.mktemp("janitor") / "expiry-janitor.zip"
    return build_package(
        PROJECT_ROOT, output, entrypoint=JANITOR_ENTRYPOINT, configuration=JANITOR_CONFIG
    )


def test_the_record_and_the_template_name_the_key_this_builder_writes() -> None:
    """Mutation: edit the key in one of the three places and not the others.

    Three things name this artifact and none is derived from another: the builder writes an
    object, the template deploys one, and the record describes one. That the record and the
    template agree with each other is asserted once for all four functions in
    tests/test_released_zips.py; what is this function's own is that both of them agree with
    the constant its builder uploads under.
    """
    assert release_record()["s3_key"] == ARTIFACT_KEY
    assert janitor_function()["Code"]["S3Key"] == ARTIFACT_KEY


def test_the_template_runs_the_entry_point_the_builder_packages_for() -> None:
    """Mutation: point Handler at the recorder's entry point.

    The three functions share a package and differ by which entry point the template names, so
    a Handler naming another one imports cleanly and then behaves as the wrong function -- or,
    here, does not import at all, because the zip carries only the modules this entrypoint
    reaches.
    """
    assert janitor_function()["Handler"] == HANDLER_ENTRY_POINT


@pytest.mark.slow
def test_the_janitor_zip_carries_no_configuration_at_all(package: dict[str, object]) -> None:
    """Mutation: pass the validator's config list to this builder.

    THE PROPERTY THAT MAKES A SWEEP-INTERVAL EDIT FREE FOR THIS FUNCTION. Read off the built
    archive rather than off the declared list, because it is the archive that is uploaded: what
    this asserts is that no byte of ``config/`` is inside the object whose digest the test above
    compares, so no edit under ``config/`` can move it.

    The janitor does read two numbers out of ``config/reports/researcher-lane.yaml``, which is
    exactly why this matters here. They reach it through the environment that
    ``infra/expiry-janitor.yaml`` sets, not through the zip, so changing one costs a stack
    deploy and not a Lambda release.
    """
    with zipfile.ZipFile(str(package["path"])) as archive:
        configuration = [
            name for name in archive.namelist() if name.startswith("edullm_platform/config/")
        ]

    assert configuration == []
    assert package["configuration"] == []


def test_the_never_uploaded_placeholder_is_in_every_file_that_names_it_or_in_none() -> None:
    """THE CHECK THAT KEEPS A DORMANT DEPLOY FROM BECOMING A PERMANENT ONE.
    Mutation: paste a real object version into the template and leave the workflow's guard.

    Three files carry the same token while this function has never been uploaded: the release
    record, the template's ``S3ObjectVersion``, and the phase-3 workflow's janitor step, which
    checks for it and skips rather than failing an unrelated dispatch on a stack nobody can
    create yet. A skip that outlived the placeholder would be a deploy step that never runs and
    never says so, which is the shape of silence this whole slice is about.

    So the invariant is all three or none, and the release that removes the placeholder has to
    remove the guard in the same change.
    """
    token = "PLACEHOLDER-UNTIL-THE-FIRST-UPLOAD"
    workflow = PROJECT_ROOT / ".github" / "workflows" / "deploy-phase3-batch.yml"
    carrying = {
        "record": token == str(release_record()["s3_object_version"]),
        "template": token == str(janitor_function()["Code"]["S3ObjectVersion"]),
        "workflow guard": token in workflow.read_text(encoding="utf-8"),
    }

    assert len(set(carrying.values())) == 1, (
        "the never-uploaded placeholder is in some of these and not the others, so either a "
        f"deploy is pinned to a version nobody uploaded or a guard skips for ever: {carrying}"
    )
