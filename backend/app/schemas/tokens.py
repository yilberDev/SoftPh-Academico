"""Types shared by the requests that carry a one-time token (HU-019, HU-016)."""

from typing import Annotated

from pydantic import StringConstraints

# A real token has 43 characters; the cap only stops absurd payloads (422).
# The format itself is checked by the services (hash_well_formed_token), which
# answer a malformed token exactly like an unknown one (404).
OneTimeToken = Annotated[str, StringConstraints(strict=True, max_length=512)]
