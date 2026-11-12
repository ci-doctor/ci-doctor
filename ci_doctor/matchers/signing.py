"""The official registry's signed index: proof it came from the matchers repo's CI.

A hash check proves each file matches the index; it can't prove the index is
genuine, since the index comes from the same server as the files. So the official
registry's deploy signs it: `index.yaml.sig`, written next to `index.yaml`, is

    {"key_id": "...", "payload": "<base64>", "signature": "<base64>"}

where `payload` is the JSON `{"index_sha256", "version", "expires"}` and
`signature` is Ed25519 over the payload's exact bytes, by the key `key_id` names.
ci-doctor ships the public keys. `version` only goes up, so an older signed index
is refused (rollback), and `expires` makes a frozen copy stop working (freeze).

Only the registry under the `official` alias is checked: a private registry lives
in its owner's network and is trusted as served.
"""

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from nacl.exceptions import BadSignatureError, CryptoError
from nacl.signing import SigningKey, VerifyKey

from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import INDEX

#: The signature file, next to `index.yaml`.
SIGNATURE = "index.yaml.sig"
#: The most bytes a signature file may hold.
SIGNATURE_CAP = 4096
#: The alias whose index must be signed by one of :data:`OFFICIAL_KEYS`.
OFFICIAL = "official"
#: key_id -> the base64 Ed25519 public key the official registry's deploy signs with.
#: The current key and the next one, so a rotation never leaves a gap.
OFFICIAL_KEYS: dict[str, str] = {
    "official-1": "ZHE4h4GX9cXT1vpgZVGOHb+x4JP7smXwVQjYn7Md94M=",
}


@dataclass(frozen=True)
class Signed:
    """What a verified signature vouches for.

    Attributes:
        version: The signed index's version; never lower than the last one seen.
        expires: When the signature stops being accepted.
    """

    version: int
    expires: datetime


def verify(index: bytes, signature: bytes, keys: Mapping[str, str], now: datetime) -> Signed:
    """Check that `signature` vouches for exactly these index bytes, and still holds.

    Args:
        index: `index.yaml`'s bytes, as fetched.
        signature: `index.yaml.sig`'s bytes.
        keys: key_id -> base64 public key, the keys trusted.
        now: The current time, timezone-aware.

    Returns:
        The signed version and expiry.

    Raises:
        SetupError: On a malformed or unverifiable signature, an untrusted key, an
            index that isn't the one signed, or an expired signature.
    """
    try:
        envelope = json.loads(signature)
        key_id, payload = envelope["key_id"], base64.b64decode(envelope["payload"], validate=True)
        sig = base64.b64decode(envelope["signature"], validate=True)
        claims = json.loads(payload)
        version, expires = int(claims["version"]), datetime.fromisoformat(claims["expires"])
        signed_sha = str(claims["index_sha256"])
    except (ValueError, KeyError, TypeError, binascii.Error) as err:
        raise SetupError(f"{SIGNATURE} is malformed ({type(err).__name__})") from None
    if key_id not in keys:
        raise SetupError(
            f"{SIGNATURE} is signed with key {key_id!r}, which this ci-doctor doesn't trust; upgrade it"
        )
    try:
        VerifyKey(base64.b64decode(keys[key_id])).verify(payload, sig)
    except (BadSignatureError, CryptoError, ValueError):
        raise SetupError(
            f"{SIGNATURE} doesn't verify: this index wasn't signed by the official registry"
        ) from None
    if (actual := hashlib.sha256(index).hexdigest()) != signed_sha:
        raise SetupError(f"{INDEX} has sha256 {actual}, but its signature is for {signed_sha}")
    if expires.tzinfo is None or now >= expires:
        raise SetupError(
            f"the signed index expired at {expires.isoformat()}: a stale copy, or the registry stopped re-signing"
        )
    return Signed(version, expires)


def sign(index: bytes, *, version: int, expires: datetime, key_id: str, private_key: str) -> bytes:
    """Write the signature :func:`verify` accepts. The registry's deploy runs the same steps.

    Args:
        index: `index.yaml`'s bytes.
        version: Its version, higher than any signed before.
        expires: When the signature stops being accepted, timezone-aware.
        key_id: Which key signs, as ci-doctor's key list names it.
        private_key: The base64 Ed25519 seed.

    Returns:
        `index.yaml.sig`'s bytes.
    """
    claims = {
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "version": version,
        "expires": expires.isoformat(),
    }
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    sig = SigningKey(base64.b64decode(private_key)).sign(payload).signature
    envelope = {
        "key_id": key_id,
        "payload": base64.b64encode(payload).decode(),
        "signature": base64.b64encode(sig).decode(),
    }
    return json.dumps(envelope, indent=2).encode() + b"\n"
