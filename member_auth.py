"""Member authentication invariants.

Exclude authentication fields from general state writes and all room payloads.
Confirm each credential mutation with one conditional, durable document update.
Run one application worker for session revocation.
The public /user_count response contains only the connection count.
Keep an impossible key hash in each password account's legacy device-key slot.
"""

import asyncio
import hashlib
import hmac
import re
import secrets
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor


# The one place that sets the minimum length of a NEW personal password.
# The page text and checks follow it. Existing passwords are never
# re-checked against it, so changing it never locks anyone out.
MIN_PASSWORD_CHARS = 8
MAX_PASSWORD_CHARS = 128
MAX_PASSWORD_BYTES = 512
LEGACY_KEY_TOMBSTONE = "personal-password:no-device-key"

# Server-wide protection, not a per-member limit. Every authentication request
# (member, room or admin password, password change) takes one slot when it is
# admitted, before waiting for any lock. Each scrypt call uses about 32 MiB, so
# two threads compute and the rest wait. A full server, or a request that waits
# longer than the deadline (lock waits included), is answered "busy" so the
# member can press the button again. A slot is released only after the request
# and every worker it started have really ended.
AUTH_WORKERS = 2
AUTH_MAX_WAITING = 16
# Deadline for admission, lock waits and the START of computation. A calculation
# that has started runs to the end (about a second), so this is not a bound on
# the total response time.
AUTH_MAX_WAIT_SECONDS = 20
_executor = ThreadPoolExecutor(max_workers=AUTH_WORKERS, thread_name_prefix="member-auth")
_slots = threading.BoundedSemaphore(AUTH_WORKERS + AUTH_MAX_WAITING)
_dummy_salt = secrets.token_bytes(16)
_hex_hash = re.compile(r"[0-9a-f]{64}")


def normalize_password(value):
    # A single NFC code point decomposes to at most four code points.
    # Bound work on an untrusted frame before normalizing it.
    if not isinstance(value, str) or len(value) > 4 * MAX_PASSWORD_CHARS:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return unicodedata.normalize("NFC", value)


def _password_bytes(value, *, new=True):
    # Only a new password must meet the minimum length. A login proof is
    # bounded above to cap work but is otherwise just compared.
    value = normalize_password(value)
    minimum = MIN_PASSWORD_CHARS if new else 1
    if not isinstance(value, str) or not minimum <= len(value) <= MAX_PASSWORD_CHARS:
        return None
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return encoded if len(encoded) <= MAX_PASSWORD_BYTES else None


def _derive(password, salt):
    return hashlib.scrypt(password, salt=salt, n=2**15, r=8, p=3,
                          dklen=32, maxmem=64 * 1024 * 1024)


def _new_record(password):
    salt = secrets.token_bytes(16)
    return {"v": 2, "pw": {"kdf": "scrypt-v1", "salt": salt.hex(),
                            "digest": _derive(password, salt).hex()}}


def _matches(record, password):
    # Missing accounts and recovery-only accounts perform the same KDF.
    salt, expected = _dummy_salt, bytes(32)
    valid = False
    if isinstance(record, dict) and record.get("v") == 2:
        verifier = record.get("pw")
        if isinstance(verifier, dict) and verifier.get("kdf") == "scrypt-v1":
            salt_hex, digest = verifier.get("salt"), verifier.get("digest")
            if (isinstance(salt_hex, str) and re.fullmatch(r"[0-9a-f]{32}", salt_hex)
                    and isinstance(digest, str) and _hex_hash.fullmatch(digest)):
                salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest)
                valid = True
    return hmac.compare_digest(_derive(password, salt), expected) and valid


def _legacy_hash(key):
    if not isinstance(key, str) or not key or len(key) > 128:
        return None
    try:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return None


def _authenticate(collection_factory, nid, action, password, identity_key, code_hash,
                  expected_digest=None):
    if not isinstance(nid, str) or not _hex_hash.fullmatch(nid):
        return "credentials", None
    key_path = f"identity_keys.{nid}"
    record_path = f"identity_enroll.{nid}"
    predicate = {"_id": "main_state"}
    if action in ("signup", "migrate", "recover", "change"):
        encoded = _password_bytes(password)
        if encoded is None:
            return "password_policy", None
    if action == "change" and (not isinstance(expected_digest, str)
                               or not _hex_hash.fullmatch(expected_digest)):
        return "stale", None

    collection = collection_factory()
    if action == "login":
        row = collection.find_one(predicate, {"_id": 0, record_path: 1})
        record = (row or {}).get("identity_enroll", {}).get(nid)
        proof = _password_bytes(password, new=False)
        if proof is None:
            return "credentials", None
        if not _matches(record, proof):
            return "credentials", None
        # Recovery may have superseded this verifier while scrypt ran.
        # Recheck the same generation before admission; login never writes.
        predicate.update({record_path: record, key_path: LEGACY_KEY_TOMBSTONE})
        if collection.find_one(predicate, {"_id": 1}) is None:
            return "credentials", None
        return None, record
    elif action in ("migrate", "legacy"):
        old_hash = _legacy_hash(identity_key)
        if old_hash is None:
            return "credentials", None
        predicate.update({key_path: old_hash, record_path: {"$exists": False}})
        if collection.find_one(predicate, {"_id": 1}) is None:
            return "credentials", None
        if action == "legacy":
            return None, None
    elif action == "signup":
        # Main checks the invitation and the room history. The write succeeds
        # only while the name has neither a device key nor a password record.
        predicate.update({key_path: {"$exists": False}, record_path: {"$exists": False}})
    elif action == "recover":
        if not isinstance(code_hash, str) or not _hex_hash.fullmatch(code_hash):
            return "credentials", None
        predicate.update({"$or": [{key_path: {"$exists": False}}, {key_path: LEGACY_KEY_TOMBSTONE}],
                          record_path + ".h": code_hash,
                          record_path + ".exp": {"$gt": time.time()}})
        if collection.find_one(predicate, {"_id": 1}) is None:
            return "credentials", None
        # The one-use proof must still be current when the update is sent.
        predicate[record_path + ".exp"] = {"$gt": time.time()}
    elif action == "change":
        # The caller holds a signed-in session for this name, admitted with
        # the verifier generation in expected_digest. Only that generation may
        # change: after any change or recovery, older sessions lose this right.
        predicate.update({key_path: LEGACY_KEY_TOMBSTONE, record_path + ".v": 2,
                          record_path + ".pw.digest": expected_digest})
    else:
        return "credentials", None

    record = _new_record(encoded)
    result = collection.update_one(predicate, {
        "$set": {record_path: record, key_path: LEGACY_KEY_TOMBSTONE},
    })
    if result.modified_count != 1:
        return ("stale" if action == "change" else "credentials"), None
    return None, record


class Busy(Exception):
    """The server-wide authentication capacity or deadline was exceeded."""


class AuthTicket:
    """One admitted authentication request.

    It holds one server-wide slot from admission, before any lock wait, until
    the request has ended and every worker it started has really finished.
    Lock waits and queued work share one deadline from admission. Work that
    has already started is not cut off, so the deadline bounds waiting, not
    the total response time.
    """

    def __init__(self):
        self.started = time.monotonic()
        self.abandoned = threading.Event()
        self._guard = threading.Lock()
        self._workers = 0
        self._closed = False
        self._released = False

    def remaining(self):
        return AUTH_MAX_WAIT_SECONDS - (time.monotonic() - self.started)

    def _release_if_done(self):
        if self._closed and self._workers == 0 and not self._released:
            self._released = True
            _slots.release()

    def close(self):
        with self._guard:
            self._closed = True
            self._release_if_done()

    async def hold(self, lock):
        """Acquire an asyncio lock within the deadline, or raise Busy."""
        timeout = self.remaining()
        if timeout <= 0:
            raise Busy
        try:
            await asyncio.wait_for(lock.acquire(), timeout)
        except asyncio.TimeoutError:
            raise Busy from None

    async def run(self, fn, *args):
        """Run blocking work on the two auth threads; raise Busy past the deadline."""
        with self._guard:
            if self._closed:
                raise Busy
            self._workers += 1

        def work():
            try:
                if self.abandoned.is_set() or self.remaining() <= 0:
                    raise Busy
                return fn(*args)
            finally:
                with self._guard:
                    self._workers -= 1
                    self._release_if_done()

        try:
            future = asyncio.get_running_loop().run_in_executor(_executor, work)
        except RuntimeError:
            with self._guard:
                self._workers -= 1
                self._release_if_done()
            raise Busy from None
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # The caller went away: queued work is skipped without computing.
            # Running work finishes and only then frees the slot.
            self.abandoned.set()
            raise


def admit():
    """Return an AuthTicket, or None when the server-wide capacity is full."""
    if not _slots.acquire(blocking=False):
        return None
    return AuthTicket()


async def authenticate_member(collection_factory, nid, action, password, *,
                              identity_key=None, code_hash=None, expected_digest=None,
                              ticket=None):
    """Return (reason, private record); a None reason means successful authentication.

    The factory must supply the authoritative primary collection and durable
    writes. The caller holds its nickname lock through session publication and
    passes the ticket it was admitted with; without one, this call admits itself.
    """
    own = ticket is None
    if own:
        ticket = admit()
        if ticket is None:
            return "busy", None

    def run():
        try:
            return _authenticate(collection_factory, nid, action, password, identity_key,
                                 code_hash, expected_digest)
        except Exception:
            # A DB timeout can be ambiguous. Never issue a session from it.
            return "storage", None

    try:
        return await ticket.run(run)
    except Busy:
        return "busy", None
    finally:
        if own:
            ticket.close()
