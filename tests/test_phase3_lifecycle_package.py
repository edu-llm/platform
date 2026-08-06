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

**THE DIGEST COMPARISON THAT USED TO BE HERE IS NOW WRITTEN ONCE FOR ALL FOUR FUNCTIONS,
BECAUSE MIRRORING IS WHAT LEFT THE FOURTH WITHOUT ONE.** This module said it mirrored its
Phase 2 sibling, the janitor's module said it mirrored this one, and the notifier arrived
after all three and was mirrored by nobody -- so it was the one function whose zip could move
with nothing going red, and it drifted for an unknown period before #294 found it by hand.
``tests/test_released_zips.py`` now parametrizes the comparison over
``release_lambda.FUNCTIONS``, which is the table a function must be in before a release can
be cut for it, so a fifth is covered on the day it is added rather than on the day somebody
copies this file again.

What is left here is what is this recorder's own. It does not mirror the Phase 2 module's
checks on the wheel platform or on where configuration lands, which are properties of
``build_package`` itself and are already asserted once; asserting them twice would make a
change to the shared builder fail in two places and be understood in neither.

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
