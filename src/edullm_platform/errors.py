"""Exceptions raised across the submission path, in a module neither side of it owns.

``SubmissionRefusedError`` lived in :mod:`edullm_platform.submission` for as long as that
module was the only thing raising it. It stopped being: the rules deciding which image a
declared commit resolves to are in :mod:`edullm_platform.image_resolution`, they refuse the
same way the rest of compiling refuses, and ``compile_submission`` calls them. So the two
modules point at each other.

**The failure that produced this module was reproduced rather than anticipated.** With the
exception defined in ``submission.py``, adding ``from edullm_platform.image_resolution import
resolve_image`` to that module's import block makes ``import edullm_platform.submission``
raise ``ImportError: cannot import name 'SubmissionRefusedError' from partially initialized
module``. Position is as much the cause as the cycle is -- the class sat below
``submission.py``'s own imports, so by the time ``image_resolution`` asked for it the
partially initialized module did not have the name yet. It presents as an ImportError on the
package rather than on the feature, which is the kind that reads like a broken installation
and sends somebody to reinstall their environment.

A module of its own rather than a home on either side, because an exception owned by one end
of a dependency is precisely what produced the cycle. Nothing else is here yet; the denial
vocabularies that admission, Batch and the publisher each keep are registries of *reasons*
rather than exceptions, and they stay where they are.
"""

from __future__ import annotations

__all__ = ["SubmissionRefusedError"]


class SubmissionRefusedError(ValueError):
    """The form describes something that cannot be resolved into a manifest.

    Raised in the credential-free compile job, before a reviewer is asked for anything.
    Refusing here rather than letting the request reach a gate is deliberate: a submission
    naming an unregistered dataset is going to be denied by admission whatever a reviewer
    says, and spending a human's attention on it first teaches reviewers that approving is
    a formality.
    """
