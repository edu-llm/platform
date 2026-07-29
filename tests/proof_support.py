"""Which half of the proof check this session is running, and why there are two.

A proof bundle makes two kinds of claim, and they cost four orders of magnitude apart.

**Coherence** is everything the tree can be asked without executing a test: that the
criteria cite node ids pytest still collects, that the phase's own tests are the ones the
bundle says it owns, and that no selection would re-enter the generator. One
``--collect-only`` child answers all of it in about a second.

**Reproduction** is the recorded suite result actually reproducing, which needs a nested
run of the whole suite plus a targeted run of every cited node id. Measured on one
machine: 66s for the shared full-suite child and 53s across the four targeted ones, for
120s of a 235s serial suite.

Reproduction is off by default and runs nightly. The argument is that it re-executes
tests the pull request has already run: every node id a proof generator selects is a test
in this repository, and ``checks (python 3.12)`` runs the whole repository on the same
commit as a required check. A cited test that a change breaks fails that check; a cited
test a change deletes or renames fails collection, which is coherence and stays here. The
nested run is a strict subset of the outer one, so on the pull-request path it is a second
opinion on a question already answered rather than a question of its own.

What it is not is worthless. Reproducing is the only thing that shows the *generator*
still produces a bundle whose counts describe the tree, and that matters on the day
somebody regenerates. That day is deliberate and rare, and a nightly run is in front of
it. ``.github/workflows/nightly.yml`` sets ``EDULLM_REPRODUCE_PROOFS`` and fails loudly.

Not collected by pytest: the filename deliberately does not start with ``test_``.
"""

from __future__ import annotations

import os
from typing import Final

import pytest

#: Set this to run the expensive half. The nightly workflow sets it; nothing else does.
#: Any non-empty value counts, so ``EDULLM_REPRODUCE_PROOFS=1 uv run pytest -q`` is the
#: whole of the local incantation.
REPRODUCE_ENV: Final = "EDULLM_REPRODUCE_PROOFS"

SKIP_REASON: Final = (
    f"reproducing a recorded suite result needs a nested full pytest run; set "
    f"{REPRODUCE_ENV}=1 to do it here. It runs nightly in .github/workflows/nightly.yml, "
    "and every test it would execute is already executed by this same suite."
)


def reproducing() -> bool:
    """Whether this session was asked to reproduce recorded suite results."""
    return bool(os.environ.get(REPRODUCE_ENV))


def skip_unless_reproducing() -> None:
    """Stop here unless the expensive half was asked for.

    Called from the session fixture that performs the nested runs rather than from each
    test, so that requesting the fixture is what decides. A test that only needs
    coherence asks for the coherence fixture and never reaches this.
    """
    if not reproducing():
        pytest.skip(SKIP_REASON)
