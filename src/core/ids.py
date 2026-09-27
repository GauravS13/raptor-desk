"""Identifiers of the form ``<prefix>_<ulid>``.

Fixture ids such as ``evt_01`` or ``jdg_26`` are kept verbatim; every record the
portal creates itself gets a prefixed ULID. ULIDs sort by creation time, which
keeps listings and exports in a stable, meaningful order.
"""

import os
import re
import time

_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"
_PREFIX = re.compile(r"^[a-z]{2,5}$")


def ulid() -> str:
    """A 26-character, lowercase, time-sortable identifier (48-bit ms + 80 random bits)."""
    millis = int(time.time() * 1000) & ((1 << 48) - 1)
    value = (millis << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def new_id(prefix: str) -> str:
    if not _PREFIX.match(prefix):
        raise ValueError(f"invalid id prefix: {prefix!r}")
    return f"{prefix}_{ulid()}"
