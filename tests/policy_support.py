"""Two hourly rates, for the argument classification acquired and most tests do not vary.

``classify_request`` gates a compute profile whose hourly rate is above
:data:`~edullm_platform.contracts.policy.EXCEPTION_RATE_CEILING_USD_PER_HOUR`, and the rate
cannot be read off ``RequestFacts`` -- that model's structural digest is recorded in four
committed proof bundles, so it has no field for one. Every caller therefore passes a rate.

Most tests here are about something else: which bound was crossed, which approver is
sufficient, whether a fixture still classifies as it did. Those pass :data:`ROUTINE_RATE` and
their subject is unchanged. Tests about the gate itself pass :data:`GATED_RATE`, or the real
rate out of the catalog.

Named rather than written inline at each call site so that moving the ceiling is one edit
here and not a search for every ``Decimal("1.006")`` in the suite.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

#: gpu-1xa10g's rate, which is the rate a routine GPU run is priced at today. A real number
#: rather than a round one, so a test that starts depending on the value is depending on
#: something the catalog actually says.
ROUTINE_RATE: Final = Decimal("1.006")

#: p4d.24xlarge's rate, the cheapest profile the ceiling gates. The cheapest rather than the
#: most expensive on purpose: a test that passes at $55.04 and fails at $21.96 would be
#: testing that the ceiling is somewhere below the H100 rather than where it is.
GATED_RATE: Final = Decimal("21.9576")
