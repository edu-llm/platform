"""The two modules that decide a submission's image, imported in both orders.

``image_resolution`` holds the rules that turn a declared commit into the digest that will
run, and ``compile_submission`` calls them, so ``submission`` imports ``image_resolution``.
Both refuse the same way. If the exception they both raise lives in either one of them, the
pair points at itself.

**This guards a failure that was reproduced rather than imagined.** With
``SubmissionRefusedError`` defined in ``submission.py``, adding the import that
``compile_submission`` needs makes ``import edullm_platform.submission`` raise::

    ImportError: cannot import name 'SubmissionRefusedError' from partially initialized
    module 'edullm_platform.submission' (most likely due to a circular import)

Position mattered as much as the cycle did: the class sat below that module's own import
block, so by the time ``image_resolution`` asked for it the partially initialized module did
not have the name yet. Moving it to :mod:`edullm_platform.errors` is what this asserts has
not been undone.

**In a subprocess, and that is the whole design.** Asserting this inside the pytest process
proves nothing, because ``sys.modules`` is warm by the time any test runs and every ordering
succeeds against a cache. Each ordering has to start cold, which is what makes these ``slow``.

Written before the move rather than after, and it passed then too -- there was no cycle yet.
It is a tripwire for one a later change would otherwise introduce, not a red-green step.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

#: Both orders, because a cycle is not symmetric: whichever module is imported first is the
#: one left partially initialized, so a pair can fail one way round and pass the other.
IMPORT_ORDERS = (
    ("edullm_platform.submission", "edullm_platform.image_resolution"),
    ("edullm_platform.image_resolution", "edullm_platform.submission"),
)


@pytest.mark.slow
@pytest.mark.parametrize(("first", "second"), IMPORT_ORDERS)
def test_the_two_modules_deciding_a_submission_image_import_in_either_order(
    first: str, second: str
) -> None:
    """Mutation: move ``SubmissionRefusedError`` back into either module.

    The failure is an ImportError on the package rather than on the feature, which reads
    like a broken installation and sends somebody to rebuild their environment rather than
    to look at an import line.
    """
    completed = subprocess.run(
        [sys.executable, "-c", f"import {first}; import {second}"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.slow
def test_the_shared_refusal_is_importable_without_importing_either_side() -> None:
    """Mutation: give ``errors.py`` an import of its own from either module.

    A module that exists to break a cycle has to be reachable without pulling in the pair it
    sits between, or it is a third participant rather than a way out.
    """
    program = (
        "import sys; import edullm_platform.errors; "
        "assert 'edullm_platform.submission' not in sys.modules; "
        "assert 'edullm_platform.image_resolution' not in sys.modules"
    )

    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
