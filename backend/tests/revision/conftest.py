"""Deep review (docs/revision/revision-profunda-2026-11.md): reuses the integration fixtures.

Only the dedicated *_test database is used (the integration fixtures refuse anything else).
A test that demonstrates a defect is marked xfail(strict=True): the suite stays green while the
defect exists, and the test fails (XPASS) as soon as it is fixed, so the marker must be removed.
"""

from tests.integration.conftest import *
