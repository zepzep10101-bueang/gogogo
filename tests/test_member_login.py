"""Offline regression tests for the room's real WebSocket login handler."""

import asyncio
import copy
import io
import json
import os
import threading
import time
import unicodedata
import unittest
from contextlib import ExitStack, redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, WebSocketDisconnect
import member_auth


ROOM_PASSWORD = "test-only-room-invitation"
ADMIN_PASSWORD = "test-only-independent-admin"
PASSWORD = "a new personal test password"
NEXT_PASSWORD = "a different personal test password"
LEGACY_KEY = "independent-test-browser-key"

# Replace the environment mapping before importing the application, which opens
# MongoDB at import time. The additional guard detects any connection attempt.
with patch.object(os, "environ", {
    "MONGO_URI": "",
    "GOGOGO_ROOM_PASSWORD": ROOM_PASSWORD,
    "GOGOGO_ADMIN_PASSWORD": ADMIN_PASSWORD,
}), patch("pymongo.MongoClient") as mongo_client:
    import main as room
    mongo_client.assert_not_called()


MISSING = object()


def read_path(document, path):
    for name in path.split("."):
        if not isinstance(document, dict) or name not in document:
            return MISSING
        document = document[name]
    return document


def write_path(document, path, value):
    names = path.split(".")
    for name in names[:-1]:
        if value is MISSING and name not in document:
            return
        document = document.setdefault(name, {})
    if value is MISSING:
        document.pop(names[-1], None)
    else:
        document[names[-1]] = copy.deepcopy(value)


def matches(document, query):
    for name, expected in query.items():
        if name == "$or":
            if not any(matches(document, branch) for branch in expected):
                return False
            continue
        actual = read_path(document, name)
        if isinstance(expected, dict) and any(k.startswith("$") for k in expected):
            for op, value in expected.items():
                if op == "$exists":
                    if (actual is not MISSING) != value:
                        return False
                elif op == "$gt":
                    if actual is MISSING or actual <= value:
                        return False
                else:
                    raise AssertionError(f"Unsupported test query operator {op}")
        elif actual != expected:
            return False
    return True


class MemoryCollection:
    """A single atomic document, with the Mongo operators used by auth."""

    def __init__(self, document):
        self.document = copy.deepcopy(document)
        self.lock = threading.Lock()
        self.fail_writes = False
        self.fail_after_write = False
        self.before_update = None
        self.miss_writes = False

    def with_options(self, *, write_concern, **options):
        assert write_concern.document.get("w") == "majority"
        assert write_concern.document.get("j") is True
        return self

    def find_one(self, query, projection=None):
        with self.lock:
            if not matches(self.document, query):
                return None
            if not projection or not any(projection.values()):
                result = copy.deepcopy(self.document)
                for key in projection or ():
                    if key == "cards.card_bg":
                        for card in result.get("cards", []):
                            card.pop("card_bg", None)
                    else:
                        write_path(result, key, MISSING)
                return result
            projected = {}
            for key, include in projection.items():
                value = read_path(self.document, key)
                if include and value is not MISSING:
                    write_path(projected, key, value)
            return projected

    def update_one(self, query, change, **options):
        with self.lock:
            if self.fail_writes:
                raise OSError("test storage failure")
            if self.before_update is not None:
                callback, self.before_update = self.before_update, None
                callback(self.document)
            if self.miss_writes or not matches(self.document, query):
                return SimpleNamespace(matched_count=0, modified_count=0)
            before = copy.deepcopy(self.document)
            for op, fields in change.items():
                if op not in ("$set", "$unset"):
                    raise AssertionError(f"Unsupported test update operator {op}")
                for path, value in fields.items():
                    write_path(self.document, path, value if op == "$set" else MISSING)
            if self.fail_after_write:
                raise TimeoutError("test write acknowledgement lost")
            return SimpleNamespace(matched_count=1, modified_count=int(before != self.document))


class Socket:
    """Exchange JSON with websocket_endpoint without a network listener."""

    def __init__(self, source):
        self.headers = {"x-forwarded-for": source}
        self.client = SimpleNamespace(host=source)
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.history = []
        self.closed = asyncio.Event()
        self.close_code = None

    async def accept(self):
        pass

    async def close(self, code=1000, reason=None):
        if not self.closed.is_set():
            self.close_code = code
            self.closed.set()
        await self.incoming.put(None)

    async def receive_text(self):
        packet = await self.incoming.get()
        if packet is None:
            raise WebSocketDisconnect()
        return json.dumps(packet)

    async def send_text(self, text):
        packet = json.loads(text)
        self.history.append(packet)
        await self.outgoing.put(packet)

    async def expect(self, *types):
        async def receive():
            while True:
                packet = await self.outgoing.get()
                if packet["type"] in types:
                    return packet
        return await asyncio.wait_for(receive(), timeout=12)


class RoomCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        state = room.load_data()
        patches = ExitStack()
        self.addCleanup(patches.close)
        state["global_notice"] = "private room notice sentinel"
        self.db = MemoryCollection(state)
        self.sockets = []
        self.tasks = []
        for attr, value in (
            ("collection", self.db),
            ("server_state", state),
            ("manager", room.ConnectionManager()),
            ("identity_locks", type(room.identity_locks)()),
            ("kicked_until", {}),
            ("pending_presence_leaves", {}),
            ("revoke_tasks", set()),
            ("admin_sockets", set()),
            ("pending_revocations", {}),
            ("revocation_task", None),
            ("signup_setting_lock", asyncio.Lock()),
            ("persist_state", AsyncMock()),
        ):
            patches.enter_context(patch.object(room, attr, value))
        for name, value in vars(room).copy().items():
            if name.startswith("auth_failures") and isinstance(value, (dict, list)):
                patches.enter_context(patch.object(room, name, type(value)()))

    async def asyncTearDown(self):
        for socket in self.sockets:
            await socket.close()
        await asyncio.gather(*self.tasks)
        tasks = list(room.pending_presence_leaves.values()) + list(room.revoke_tasks)
        if room.revocation_task is not None:
            tasks.append(room.revocation_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def connect(self, source=None):
        socket = Socket(source or f"192.0.2.{len(self.sockets) + 1}")
        self.sockets.append(socket)
        self.tasks.append(asyncio.create_task(room.websocket_endpoint(socket)))
        await socket.expect("welcome")
        return socket

    async def attempt(self, nickname="Writer", password=PASSWORD, mode="login", source=None, **extra):
        socket = await self.connect(source)
        await socket.incoming.put(dict(type="set_nickname", auth_mode=mode,
                                       nickname=nickname, password=password, **extra))
        result = await socket.expect("auth_ok", "auth_failed")
        if result["type"] == "auth_failed":
            self.assertLessEqual({p["type"] for p in socket.history}, {"welcome", "auth_failed"})
        return socket, result

    async def enter(self, **options):
        socket, result = await self.attempt(**options)
        self.assertEqual(result["type"], "auth_ok", result)
        state = (await socket.expect("init_state"))["state"]
        self.assertEqual(state["global_notice"], "private room notice sentinel")
        self.assertNotIn("identity_keys", state)
        self.assertNotIn("identity_enroll", state)
        self.assertNotIn("identity_key", result)
        return socket, result["session"]

    async def denied(self, **options):
        _, result = await self.attempt(**options)
        self.assertEqual(result["type"], "auth_failed", result)
        return result["reason"]

    def seed(self, nickname, *, key=None, code=None, expires=None):
        nid = room.name_id(nickname)
        for document in (room.server_state, self.db.document):
            if key:
                document.setdefault("identity_keys", {})[nid] = room.identity_hash(key)
            if code:
                document.setdefault("identity_enroll", {})[nid] = {
                    "h": room.identity_hash(code), "exp": expires,
                }

    async def revoke(self, nickname="Writer"):
        # The server path used by kick and recovery-code issue.
        for conn, name in list(room.manager.active_users.items()):
            if name == nickname:
                await room.revoke_connection(conn, {"type": "identity_revoked"})

    def assert_session_rejected(self, token):
        with self.assertRaises(HTTPException) as error:
            room.require_session(SimpleNamespace(headers={"x-gogogo-session": token}))
        self.assertEqual(error.exception.status_code, 403)


class MemberLoginTests(RoomCase):

    async def test_two_browsers_use_only_name_and_password(self):
        first, first_token = await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        self.assert_session_rejected("anonymous")
        second, second_token = await self.enter()
        # Phone and computer stay signed in together; neither session is revoked.
        for socket, token in ((first, first_token), (second, second_token)):
            room.require_session(SimpleNamespace(headers={"x-gogogo-session": token}))
            self.assertIn(socket, room.manager.authed_connections)
            self.assertFalse(socket.closed.is_set())
        self.assertNotIn("duplicate_kicked", {p["type"] for p in first.history})
        await self.denied(password=ROOM_PASSWORD)
        await self.denied(mode="signup", room_password=ROOM_PASSWORD, password=NEXT_PASSWORD)
        third, third_token = await self.enter()
        self.assertEqual(len({first_token, second_token, third_token}), 3)
        self.assertEqual(sum(name == "Writer" for name in room.manager.active_users.values()), 3)

    async def test_migration_requires_legacy_key_and_preserves_records(self):
        self.seed("Writer", key=LEGACY_KEY)
        room.server_state["attendance"] = {"2026-11": {"Writer": [1, 8]}}
        room.server_state["cards"][2]["reserved_by"] = "Writer"
        room.server_state["chat_history"] = [{"id": "keep", "senderName": "Writer", "msg": "previous work"}]
        room.server_state["trackers"]["Writer"] = {"todos": [{"text": "keep my task"}]}
        await self.denied(mode="signup", room_password=ROOM_PASSWORD)
        await self.denied(mode="migrate", identity_key="invalid", room_password=ROOM_PASSWORD)
        await self.denied(mode="migrate", identity_key=LEGACY_KEY, room_password="incorrect")
        reason = await self.denied(mode="legacy", identity_key=LEGACY_KEY, password=ROOM_PASSWORD)
        self.assertEqual(reason, "setup_required")
        await self.enter(mode="migrate", identity_key=LEGACY_KEY, room_password=ROOM_PASSWORD)
        self.assertEqual(self.db.document["identity_keys"][room.name_id("Writer")],
                         member_auth.LEGACY_KEY_TOMBSTONE)
        await self.denied(mode="migrate", identity_key=LEGACY_KEY, room_password=ROOM_PASSWORD)
        await self.enter()
        self.assertEqual(room.server_state["attendance"]["2026-11"]["Writer"], [1, 8])
        self.assertEqual(room.server_state["cards"][2]["reserved_by"], "Writer")
        self.assertIn("previous work", [m["msg"] for m in room.server_state["chat_history"]])
        self.assertEqual(room.server_state["trackers"]["Writer"]["todos"], [{"text": "keep my task"}])

    async def test_two_simultaneous_signups_have_exactly_one_winner(self):
        passwords = (PASSWORD, NEXT_PASSWORD)
        attempts = await asyncio.gather(*(self.attempt(mode="signup", password=p, room_password=ROOM_PASSWORD)
                                          for p in passwords))
        outcomes = [p["type"] for _, p in attempts]
        self.assertCountEqual(outcomes, ["auth_ok", "auth_failed"])
        await self.enter(password=passwords[outcomes.index("auth_ok")])
        await self.denied(password=passwords[outcomes.index("auth_failed")])

    async def test_open_signup_needs_no_admin_and_optional_room_password(self):
        self.assertFalse(room.SIGNUP_NEEDS_ROOM_PASSWORD)
        self.assertIn('"__SIGNUP_ROOM_PASSWORD__" === "1"', room.PAGE_HTML)
        self.assertIn('const SIGNUP_ROOM_PASSWORD = "0" === "1"', room.read_root())
        # Default: the room address is enough; no room password or code is given out.
        await self.enter(mode="signup", nickname="Newcomer")
        with patch.object(room, "SIGNUP_NEEDS_ROOM_PASSWORD", True):
            self.assertIn('const SIGNUP_ROOM_PASSWORD = "1" === "1"', room.read_root())
            await self.denied(mode="signup", nickname="Invited")
            await self.denied(mode="signup", nickname="Invited", room_password="wrong room password")
            await self.enter(mode="signup", nickname="Invited", room_password=ROOM_PASSWORD)

    async def test_admin_switches_signup_room_password_with_one_click(self):
        admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        member, _ = await self.enter(mode="signup", nickname="Member")
        await member.incoming.put({"type": "set_signup_room_password", "on": True})
        await member.incoming.put({"type": "ping"})
        await member.expect("pong")
        self.assertFalse(room.signup_needs_room_password())
        await admin.incoming.put({"type": "set_signup_room_password", "on": True})
        reply = await admin.expect("signup_room_password")
        self.assertEqual((reply["ok"], reply["on"]), (True, True))
        self.assertIn('const SIGNUP_ROOM_PASSWORD = "1" === "1"', room.read_root())
        await self.denied(mode="signup", nickname="Invited")
        await self.enter(mode="signup", nickname="Invited", room_password=ROOM_PASSWORD)
        await admin.incoming.put({"type": "set_signup_room_password", "on": False})
        self.assertEqual((await admin.expect("signup_room_password"))["on"], False)
        await self.enter(mode="signup", nickname="Open")
        # Without a room password on the server the switch cannot lock signup shut.
        with patch.object(room, "ROOM_PASSWORD_SERVER", ""):
            await admin.incoming.put({"type": "set_signup_room_password", "on": True})
            reply = await admin.expect("signup_room_password")
        self.assertEqual((reply["ok"], reply["on"]), (False, False))

    async def test_signup_switch_reports_success_only_after_a_durable_write(self):
        admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        self.db.fail_writes = True
        # Requiring the room password applies at once even when the save fails.
        await admin.incoming.put({"type": "set_signup_room_password", "on": True})
        reply = await admin.expect("signup_room_password")
        self.assertEqual((reply["ok"], reply["reason"], reply["on"]), (False, "storage", True))
        await self.denied(mode="signup", nickname="Blocked")
        self.db.fail_writes = False
        await admin.incoming.put({"type": "set_signup_room_password", "on": True})
        reply = await admin.expect("signup_room_password")
        self.assertEqual((reply["ok"], reply["on"]), (True, True))
        self.assertIs(self.db.document["signup_room_password"], True)
        # Opening signup waits for the save: a failed save keeps it restricted.
        self.db.fail_writes = True
        await admin.incoming.put({"type": "set_signup_room_password", "on": False})
        reply = await admin.expect("signup_room_password")
        self.assertEqual((reply["ok"], reply["reason"], reply["on"]), (False, "storage", True))
        self.db.fail_writes = False
        await self.denied(mode="signup", nickname="Blocked")
        self.assertIs(self.db.document["signup_room_password"], True)
        await admin.incoming.put({"type": "set_signup_room_password", "on": False})
        reply = await admin.expect("signup_room_password")
        self.assertEqual((reply["ok"], reply["on"]), (True, False))
        self.assertIs(self.db.document["signup_room_password"], False)
        await self.enter(mode="signup", nickname="Opened")

    async def test_room_state_save_never_writes_the_signup_policy(self):
        admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        stale = copy.deepcopy(room.server_state)  # a room snapshot taken before the switch
        stale["signup_room_password"] = False
        stale.pop("cards", None)  # card patches are not part of this check
        await admin.incoming.put({"type": "set_signup_room_password", "on": True})
        reply = await admin.expect("signup_room_password")
        self.assertEqual((reply["ok"], reply["on"]), (True, True))
        # The general save of the older snapshot lands afterwards.
        with patch.object(room, "collection", self.db):
            room.save_data(stale)
        self.assertIs(self.db.document["signup_room_password"], True)
        # After a restart the policy comes back from the DB as the admin chose it.
        with patch.object(room, "collection", self.db):
            reloaded = room.load_data()
        with patch.object(room, "server_state", reloaded):
            self.assertTrue(room.signup_needs_room_password())

    async def test_signup_policy_changes_are_saved_then_applied_one_at_a_time(self):
        admins = []
        for _ in range(2):
            admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
            admins.append(admin)
        writes = []
        real = room.db_set_signup_room_password

        def slow_write(on):
            writes.append(("start", on))
            time.sleep(0.2 if on else 0)
            real(on)
            writes.append(("end", on))

        with patch.object(room, "db_set_signup_room_password", slow_write):
            await admins[0].incoming.put({"type": "set_signup_room_password", "on": True})
            await asyncio.sleep(0.05)
            await admins[1].incoming.put({"type": "set_signup_room_password", "on": False})
            await admins[0].expect("signup_room_password")
            await admins[1].expect("signup_room_password")
        # The second change waited for the first save, so memory and DB end the same.
        self.assertEqual(writes, [("start", True), ("end", True), ("start", False), ("end", False)])
        self.assertIs(self.db.document["signup_room_password"], False)
        self.assertFalse(room.signup_needs_room_password())

    async def test_pending_signin_connections_have_one_high_server_wide_cap(self):
        await self.enter(mode="signup")
        with patch.object(room, "MAX_PENDING_SIGNIN", 1):
            await self.connect(None)  # one connection still waiting to sign in
            refused = Socket("192.0.2.200")
            self.sockets.append(refused)
            self.tasks.append(asyncio.create_task(room.websocket_endpoint(refused)))
            result = await refused.expect("auth_ok", "auth_failed")
            self.assertEqual((result["type"], result["reason"]), ("auth_failed", "busy"))
            self.assertEqual([p["type"] for p in refused.history], ["auth_failed"])
            self.assertEqual(refused.close_code, 1013)
        await self.enter()
        self.assertEqual(room.manager.pending_signin, 1)  # the first connection still waits

    async def test_pending_slot_is_taken_before_the_first_await_and_returned_once(self):
        gate = asyncio.Event()

        class SlowAccept(Socket):
            async def accept(self):
                await gate.wait()

        with patch.object(room, "MAX_PENDING_SIGNIN", 2):
            slow = [SlowAccept(f"192.0.2.{30 + i}") for i in range(3)]
            self.sockets.extend(slow)
            tasks = [asyncio.create_task(room.websocket_endpoint(s)) for s in slow]
            self.tasks.extend(tasks)
            await asyncio.sleep(0)
            # All three reached accept() together; only two may hold a slot.
            self.assertEqual(room.manager.pending_signin, 2)
            gate.set()
            results = [await s.expect("welcome", "auth_failed") for s in slow]
            self.assertEqual([r["type"] for r in results].count("auth_failed"), 1)
            self.assertEqual(sum(s.close_code == 1013 for s in slow), 1)
            entered = [s for s, r in zip(slow, results) if r["type"] == "welcome"]
            # Signing in returns the slot at once; closing does not return it twice.
            await entered[0].incoming.put(dict(type="set_nickname", auth_mode="signup",
                                               nickname="Writer", password=PASSWORD))
            await entered[0].expect("auth_ok")
            self.assertEqual(room.manager.pending_signin, 1)
            await entered[0].close()
            await entered[1].close()
            await asyncio.gather(*tasks)
            self.assertEqual(room.manager.pending_signin, 0)

        class BrokenAccept(Socket):
            async def accept(self):
                raise RuntimeError("handshake failed")

        with self.assertRaises(RuntimeError):
            await room.websocket_endpoint(BrokenAccept("192.0.2.40"))
        self.assertEqual(room.manager.pending_signin, 0)

        class Cancelled(Socket):
            async def accept(self):
                await asyncio.Event().wait()

        task = asyncio.create_task(room.websocket_endpoint(Cancelled("192.0.2.41")))
        await asyncio.sleep(0)
        self.assertEqual(room.manager.pending_signin, 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(room.manager.pending_signin, 0)

    async def test_refusing_an_over_cap_connection_ends_in_bounded_time(self):
        class Stuck(Socket):
            async def send_text(self, text):
                await asyncio.Event().wait()

        with patch.object(room, "MAX_PENDING_SIGNIN", 0), \
                patch.object(room, "PENDING_REFUSE_SECONDS", 0.05):
            started = time.monotonic()
            await asyncio.wait_for(room.websocket_endpoint(Stuck("192.0.2.50")), 2)
            self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(room.manager.pending_signin, 0)

    async def test_invitation_historical_name_and_admin_boundaries(self):
        room.server_state["attendance"] = {"2026-11": {"Writer": [1]}}
        # Open signup still never takes an existing or look-alike name.
        await self.denied(mode="signup")
        await self.denied(mode="signup", nickname="Wri-ter")
        await self.denied(mode="signup", nickname=room.ADMIN_NICKNAME)
        await self.denied(mode="signup", room_password=ROOM_PASSWORD)
        await self.denied(mode="signup", nickname="Wri-ter", room_password=ROOM_PASSWORD)
        await self.denied(mode="signup", nickname=room.ADMIN_NICKNAME, room_password=ROOM_PASSWORD)
        await self.denied(mode="recover", nickname=room.ADMIN_NICKNAME, enroll_code="ABCDEFGHJKLM")
        await self.denied(mode="admin", nickname=room.ADMIN_NICKNAME, password=ROOM_PASSWORD)
        admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        self.assertIn(admin, room.admin_sockets)
        member, _ = await self.enter(mode="signup", nickname="Reader", room_password=ROOM_PASSWORD)
        await member.incoming.put({"type": "issue_enrollment", "target_nick": "Writer"})
        await member.incoming.put({"type": "ping"})
        await member.expect("pong")
        self.assertFalse(any(p["type"] == "enrollment_issued" for p in member.history))

    async def test_legacy_recovery_rejects_expired_and_accepts_only_one_claim(self):
        code = "ABCDEFGHJKLM"
        self.seed("Writer", code=code, expires=time.time() - 1)
        await self.denied(mode="recover", enroll_code=code)
        self.seed("Writer", code=code, expires=time.time() + 600)
        candidates = [PASSWORD, NEXT_PASSWORD]
        attempts = await asyncio.gather(*(self.attempt(mode="recover", password=pw, enroll_code=code)
                                          for pw in candidates))
        results = [packet["type"] for _, packet in attempts]
        self.assertCountEqual(results, ["auth_ok", "auth_failed"])
        winner = results.index("auth_ok")
        await self.enter(password=candidates[winner])
        await self.denied(password=candidates[1 - winner])
        await self.denied(mode="recover", enroll_code=code)
        self.assertNotIn(room.identity_hash(code), str(self.db.document))

    async def test_admin_recovery_revokes_old_password_and_session(self):
        member, token = await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        await admin.incoming.put({"type": "issue_enrollment", "target_nick": "Writer"})
        reply = await admin.expect("enrollment_issued")
        self.assertTrue(reply["ok"])
        code = reply["code"]
        self.assertNotIn(code, str(self.db.document))
        self.assertNotIn(code, str(room.server_state))
        await member.expect("identity_revoked")
        self.assert_session_rejected(token)
        await self.denied()
        await self.enter(mode="recover", password=NEXT_PASSWORD, enroll_code=code)
        await self.denied(mode="recover", password=PASSWORD, enroll_code=code)
        await self.denied()
        await self.enter(password=NEXT_PASSWORD)

    async def test_kick_clears_sessions_and_blocks_password_entry_during_cooldown(self):
        member, token = await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        await admin.incoming.put({"type": "kick", "target_nick": "Writer"})
        await member.expect("kicked")
        self.assert_session_rejected(token)
        self.assertEqual(await self.denied(), "kicked")
        room.kicked_until["Writer"] = time.time() - 1
        await self.enter()

    async def test_failed_admin_recovery_blocks_old_access_and_protects_new_code_from_retry(self):
        _, token = await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        request = {"type": "issue_enrollment", "target_nick": "Writer"}
        self.db.fail_writes = True
        await admin.incoming.put(request)
        failure = await admin.expect("enrollment_issued")
        self.assertFalse(failure["ok"])
        self.assertNotIn("code", failure)
        self.assert_session_rejected(token)
        self.assertEqual(await self.denied(), "storage")
        nid = room.name_id("Writer")
        _, failed_generation = room.pending_revocations[nid]
        self.db.fail_writes = False
        await admin.incoming.put(request)
        replacement = await admin.expect("enrollment_issued")
        self.assertTrue(replacement["ok"])
        self.assertFalse(room.db_issue_enrollment(nid, "", 0, failed_generation))
        await self.enter(mode="recover", password=NEXT_PASSWORD, enroll_code=replacement["code"])

    async def test_no_password_or_room_state_on_storage_failure(self):
        self.db.fail_writes = True
        await self.denied(mode="signup", room_password=ROOM_PASSWORD)
        self.assertFalse(self.db.document.get("identity_enroll"))
        self.assertFalse(room.manager.sessions)
        self.db.fail_writes = False
        await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        self.assertNotIn(PASSWORD, str(self.db.document))

    async def test_ambiguous_write_never_grants_session(self):
        self.db.fail_after_write = True
        await self.denied(mode="signup", room_password=ROOM_PASSWORD)
        self.assertFalse(room.manager.sessions)
        self.assertIn(room.name_id("Writer"), self.db.document["identity_enroll"])
        # The write committed but never reached the app's credential cache.
        self.db.fail_after_write = False
        await self.enter()

    async def test_room_snapshot_cannot_overwrite_private_credentials(self):
        self.seed("Legacy", key=LEGACY_KEY)
        await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        private_fields = {name: copy.deepcopy(self.db.document[name]) for name in room.IDENTITY_FIELDS}
        room.save_data({"_id": "main_state", "cards": [], "global_notice": "later notice",
                        "identity_keys": {}, "identity_enroll": {}})
        self.assertEqual(self.db.document["global_notice"], "later notice")
        for name, expected in private_fields.items():
            self.assertEqual(self.db.document[name], expected)

    async def test_recovery_write_checks_current_code_generation(self):
        code, replacement = "ABCDEFGHJKLM", "NPQRSTUVWXYZ"
        self.seed("Writer", code=code, expires=time.time() + 600)
        nid = room.name_id("Writer")

        def rotate_during_claim(document):
            document["identity_enroll"][nid] = {
                "h": room.identity_hash(replacement), "exp": time.time() + 600,
            }

        self.db.before_update = rotate_during_claim
        await self.denied(mode="recover", enroll_code=code)
        self.assertEqual(self.db.document["identity_enroll"][nid]["h"], room.identity_hash(replacement))
        self.assertFalse(room.manager.sessions)
        await self.enter(mode="recover", enroll_code=replacement)

    async def test_public_count_reports_only_the_number_of_open_sockets(self):
        def assert_count(expected):
            # The deployment checker calls GET /user_count without a session.
            body = room.get_user_count()
            self.assertEqual(body, {"count": expected})
            self.assertIs(type(body["count"]), int)

        assert_count(0)
        anonymous = await self.connect()
        anonymous_task = self.tasks[-1]
        assert_count(1)
        authenticated, _ = await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        authenticated_task = self.tasks[-1]
        room.manager.active_shares[0] = str(id(authenticated))
        assert_count(2)
        await anonymous.close()
        await asyncio.wait_for(anonymous_task, timeout=5)
        assert_count(1)
        await authenticated.close()
        await asyncio.wait_for(authenticated_task, timeout=5)
        assert_count(0)

    async def test_anonymous_socket_and_http_receive_no_private_state(self):
        self.assertEqual(room.healthz(), {"ok": True})
        authenticated, _ = await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        anonymous = await self.connect()
        await authenticated.incoming.put({"type": "chat", "msg": "private chat sentinel"})
        await authenticated.expect("chat")
        await anonymous.incoming.put({"type": "ping"})
        await anonymous.expect("pong")
        self.assertEqual([p["type"] for p in anonymous.history], ["welcome", "pong"])
        request = SimpleNamespace(headers={})
        with patch.object(room, "load_pomo_url") as read_config:
            with self.assertRaises(HTTPException) as error:
                room.get_pomo_config(request)
            self.assertEqual(error.exception.status_code, 403)
            read_config.assert_not_called()
        with patch.object(room, "read_card_background") as read_background:
            with self.assertRaises(HTTPException) as error:
                await room.card_background(0, request)
            self.assertEqual(error.exception.status_code, 403)
            read_background.assert_not_called()
        request.json = AsyncMock(return_value={"url": "https://docs.google.com/spreadsheets/d/test"})
        with patch.object(room, "save_pomo_url") as save:
            with self.assertRaises(HTTPException) as error:
                await room.update_pomo_config(request)
            self.assertEqual(error.exception.status_code, 403)
            request.json.assert_not_awaited()
            save.assert_not_called()

    async def test_password_length_checked_by_server(self):
        await self.denied(mode="signup", password="tiny", room_password=ROOM_PASSWORD)
        await self.denied(mode="signup", password="z" * 513, room_password=ROOM_PASSWORD)
        await self.denied(mode="signup", password=["invalid type"], room_password=ROOM_PASSWORD)
        self.assertFalse(self.db.document.get("identity_enroll"))

    async def test_timeouts_and_failed_login_send_terminal_close_code(self):
        self.assertEqual(room.AUTH_HANDSHAKE_TIMEOUT, 60)
        with patch.object(room, "AUTH_HANDSHAKE_TIMEOUT", 0.15):
            waiting = await self.connect()
            await waiting.incoming.put({"type": "ping"})
            await waiting.expect("pong")
            await asyncio.wait_for(waiting.closed.wait(), timeout=3)
        self.assertEqual(waiting.close_code, 4001)
        bad, reply = await self.attempt(password="wrong test password")
        self.assertEqual(reply["type"], "auth_failed")
        await asyncio.wait_for(bad.closed.wait(), timeout=3)
        self.assertEqual(bad.close_code, 4001)

    async def test_missing_user_and_wrong_password_share_one_verification_and_error(self):
        await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        results = []
        for nickname in ("Writer", "NeverJoined"):
            with patch.object(member_auth, "_derive", wraps=member_auth._derive) as derive:
                results.append(await self.denied(nickname=nickname, password=NEXT_PASSWORD))
                self.assertEqual(derive.call_count, 1)
        self.assertEqual(results, ["credentials", "credentials"])

    async def test_unicode_passwords_on_the_login_handler(self):
        self.assertEqual(await self.denied(mode="signup", password="하" * (member_auth.MIN_PASSWORD_CHARS - 1),
                                           room_password=ROOM_PASSWORD), "password_policy")
        await self.enter(mode="signup", password=" 글 쓰는 일상과 하루 기록을 남깁니다 ", room_password=ROOM_PASSWORD)
        await self.enter(password=unicodedata.normalize("NFD", " 글 쓰는 일상과 하루 기록을 남깁니다 "))
        await self.denied(password="글 쓰는 일상과 하루 기록을 남깁니다")

    async def test_private_background_rechecks_session_after_await(self):
        _, token = await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        request = SimpleNamespace(headers={"x-gogogo-session": token})
        room.server_state["cards"][0].pop("card_bg", None)
        started, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def read(_):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(timeout=12):
                raise AssertionError("background test was never released")
            return "data:image/png;base64,QUJD"

        with patch.object(room, "read_card_background", side_effect=read):
            response = asyncio.create_task(room.card_background(0, request))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                await self.revoke()  # kick / recovery-code issue path
            finally:
                release.set()
            with self.assertRaises(HTTPException) as error:
                await response
            self.assertEqual(error.exception.status_code, 403)

    async def test_cached_background_waiter_cannot_use_a_replaced_session(self):
        _, token = await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        request = SimpleNamespace(headers={"x-gogogo-session": token})
        room.server_state["cards"][0]["card_bg"] = "data:image/png;base64,QUJD"
        checked = asyncio.Event()
        require = room.require_session

        def checked_session(candidate):
            require(candidate)
            checked.set()

        with patch.object(room, "require_session", side_effect=checked_session):
            async with room.background_read_lock:
                response = asyncio.create_task(room.card_background(0, request))
                await asyncio.wait_for(checked.wait(), timeout=5)
                await self.revoke()  # kick / recovery-code issue path
            with self.assertRaises(HTTPException) as error:
                await response
            self.assertEqual(error.exception.status_code, 403)

    async def test_admin_config_rechecks_session_after_request_body(self):
        _, token = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        started, release = asyncio.Event(), asyncio.Event()

        async def read_body():
            started.set()
            await release.wait()
            return {"url": "https://docs.google.com/spreadsheets/d/test-only"}

        with patch.object(room, "save_pomo_url") as save:
            request = SimpleNamespace(headers={"x-gogogo-session": token}, json=read_body)
            response = asyncio.create_task(room.update_pomo_config(request))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                await self.revoke(room.ADMIN_NICKNAME)
            finally:
                release.set()
            with self.assertRaises(HTTPException) as error:
                await response
            self.assertEqual(error.exception.status_code, 403)
            save.assert_not_called()

    async def test_config_read_discards_value_when_session_is_revoked(self):
        _, token = await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        request = SimpleNamespace(headers={"x-gogogo-session": token})
        started, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def read():
            loop.call_soon_threadsafe(started.set)
            if not release.wait(timeout=12):
                raise AssertionError("config test was never released")
            return "https://docs.google.com/spreadsheets/d/private-test"

        with patch.object(room, "load_pomo_url", side_effect=read):
            response = asyncio.create_task(asyncio.to_thread(room.get_pomo_config, request))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                await self.revoke()  # kick / recovery-code issue path
            finally:
                release.set()
            with self.assertRaises(HTTPException) as error:
                await response
            self.assertEqual(error.exception.status_code, 403)

    async def test_each_credential_write_rejects_conflict_failure_and_lost_ack(self):
        admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        code = "ABCDEFGHJKLM"
        for fault in ("miss_writes", "fail_writes", "fail_after_write"):
            for mode in ("signup", "migrate", "recover", "issue"):
                with self.subTest(fault=fault, mode=mode):
                    nickname = f"Member{mode}{('miss_writes', 'fail_writes', 'fail_after_write').index(fault)}"
                    if mode == "migrate":
                        self.seed(nickname, key=LEGACY_KEY)
                    if mode == "recover":
                        self.seed(nickname, code=code, expires=time.time() + 600)
                    if mode == "issue":
                        member, token = await self.enter(nickname=nickname, mode="signup",
                                                         room_password=ROOM_PASSWORD)
                    with patch.object(self.db, fault, True):
                        if mode == "issue":
                            await admin.incoming.put({"type": "issue_enrollment", "target_nick": nickname})
                            reply = await admin.expect("enrollment_issued")
                            self.assertFalse(reply["ok"])
                            self.assertNotIn("code", reply)
                            self.assert_session_rejected(token)
                            # Each fault injection is independent. Recovery retries
                            # have their own test.
                            if room.revocation_task is not None:
                                room.revocation_task.cancel()
                                await asyncio.gather(room.revocation_task, return_exceptions=True)
                            room.pending_revocations.pop(room.name_id(nickname), None)
                        else:
                            socket, reply = await self.attempt(nickname=nickname, mode=mode, identity_key=LEGACY_KEY,
                                                               room_password=ROOM_PASSWORD, enroll_code=code)
                            self.assertEqual(reply["type"], "auth_failed")
                            await asyncio.wait_for(socket.closed.wait(), timeout=3)
                            self.assertEqual(socket.close_code, 4001)

    async def test_logs_and_client_messages_exclude_credential_material(self):
        output = io.StringIO()
        nick, migrant = "SecretWriter", "OldWriter"
        self.seed(migrant, key=LEGACY_KEY)
        with redirect_stdout(output):
            admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
            await self.enter(mode="signup", nickname=nick, room_password=ROOM_PASSWORD)
            await self.enter(mode="migrate", nickname=migrant, room_password=ROOM_PASSWORD, identity_key=LEGACY_KEY)
            await admin.incoming.put({"type": "issue_enrollment", "target_nick": nick})
            issued = await admin.expect("enrollment_issued")
            code = issued["code"]
            await self.enter(mode="recover", nickname=nick, password=NEXT_PASSWORD, enroll_code=code)
            await self.denied(nickname=nick, password="incorrect attempt for logs")

        lines = output.getvalue()
        # auth_ok intentionally delivers the short-lived HTTP session and the
        # administrator alone receives the raw one-use code in its success reply.
        client_packets = []
        tokens = []
        for socket in self.sockets:
            for original in socket.history:
                packet = original.copy()
                if packet["type"] == "auth_ok":
                    tokens.append(packet.pop("session"))
                if packet["type"] == "enrollment_issued" and packet["ok"]:
                    self.assertIs(socket, admin)
                    self.assertEqual(packet.pop("code"), code)
                client_packets.append(packet)
        wire = json.dumps(client_packets)
        nid = room.name_id(migrant)
        private_record = self.db.document["identity_enroll"][nid]["pw"]
        secret_values = (PASSWORD, NEXT_PASSWORD, LEGACY_KEY, ROOM_PASSWORD, ADMIN_PASSWORD, code,
                         room.identity_hash(code), room.identity_hash(LEGACY_KEY),
                         private_record["salt"], private_record["digest"], member_auth.LEGACY_KEY_TOMBSTONE, *tokens)
        for value in secret_values:
            self.assertNotIn(value, wire)
            self.assertNotIn(value, lines)
        for private_field in room.IDENTITY_FIELDS:
            self.assertNotIn(private_field, wire)
        for name in (nick, migrant):
            self.assertNotIn(name, lines)

    async def test_no_attempt_limit_blocks_the_right_password(self):
        await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        for name in ("AUTH_FAIL_GLOBAL_LIMIT", "AUTH_FAIL_SOURCE_LIMIT", "AUTH_FAIL_ACCOUNT_LIMIT", "auth_slots"):
            self.assertFalse(hasattr(room, name), name)
        shared = "198.51.100.7"  # e.g. every member behind one proxy address
        for i in range(25):
            self.assertEqual(await self.denied(password=f"incorrect attempt {i:02d}", source=shared), "credentials")
        await self.enter(source=shared)
        await self.enter(nickname=room.ADMIN_NICKNAME, mode="admin", password=ADMIN_PASSWORD, source=shared)

    async def test_member_changes_own_password_from_a_signed_in_device(self):
        phone, _ = await self.enter(mode="signup")
        computer, _ = await self.enter()
        await phone.incoming.put({"type": "change_password", "new_password": "short"})
        reply = await phone.expect("password_changed")
        self.assertEqual((reply["ok"], reply["reason"]), (False, "password_policy"))
        # No current password is asked: this is also the forgotten-password path.
        await phone.incoming.put({"type": "change_password", "new_password": NEXT_PASSWORD})
        reply = await phone.expect("password_changed")
        self.assertEqual((reply["ok"], reply["reason"]), (True, None))
        self.assertNotIn(NEXT_PASSWORD, json.dumps(phone.history))
        # Both devices stay connected.
        for socket in (phone, computer):
            await socket.incoming.put({"type": "ping"})
            await socket.expect("pong")
        self.assertEqual(await self.denied(), "credentials")
        await self.enter(password=NEXT_PASSWORD)

    async def test_change_right_follows_the_password_generation(self):
        phone, _ = await self.enter(mode="signup")
        computer, _ = await self.enter()
        await phone.incoming.put({"type": "change_password", "new_password": NEXT_PASSWORD})
        self.assertTrue((await phone.expect("password_changed"))["ok"])
        # The computer stays connected but its sign-in predates the change.
        await computer.incoming.put({"type": "change_password", "new_password": "taken back by an old session"})
        reply = await computer.expect("password_changed")
        self.assertEqual((reply["ok"], reply["reason"]), (False, "stale"))
        await computer.incoming.put({"type": "ping"})
        await computer.expect("pong")
        # The changing device may change again; a fresh sign-in with the new password may too.
        await phone.incoming.put({"type": "change_password", "new_password": PASSWORD})
        self.assertTrue((await phone.expect("password_changed"))["ok"])
        fresh, _ = await self.enter()
        await fresh.incoming.put({"type": "change_password", "new_password": NEXT_PASSWORD})
        self.assertTrue((await fresh.expect("password_changed"))["ok"])
        await self.enter(password=NEXT_PASSWORD)

    async def test_admission_precedes_lock_waits_and_covers_admin_and_room_proofs(self):
        await self.enter(mode="signup")
        # A request stuck behind the name lock is answered busy within the deadline.
        lock = room.identity_lock("Writer")
        await lock.acquire()
        try:
            with patch.object(member_auth, "AUTH_MAX_WAIT_SECONDS", 0.2):
                _, result = await self.attempt()
            self.assertEqual((result["type"], result["reason"]), ("auth_failed", "busy"))
        finally:
            lock.release()
        # Admin and room-password proofs use the same server-wide capacity.
        full = member_auth.threading.BoundedSemaphore(1)
        full.acquire()
        with patch.object(member_auth, "_slots", full):
            self.assertEqual(await self.denied(nickname=room.ADMIN_NICKNAME, mode="admin",
                                               password=ADMIN_PASSWORD), "busy")
            self.assertEqual(await self.denied(nickname="Elder", mode="legacy", password=ROOM_PASSWORD,
                                               identity_key=LEGACY_KEY), "busy")
        full.release()
        await self.enter()

    async def test_password_change_needs_a_signed_in_member_session(self):
        await self.enter(mode="signup")
        stranger = await self.connect(None)
        await stranger.incoming.put({"type": "change_password", "new_password": NEXT_PASSWORD})
        await stranger.incoming.put({"type": "set_nickname", "auth_mode": "login", "nickname": "Writer",
                                     "password": PASSWORD})
        await stranger.expect("auth_ok", "auth_failed")
        self.assertFalse(any(p["type"] == "password_changed" for p in stranger.history))
        admin, _ = await self.enter(mode="admin", nickname=room.ADMIN_NICKNAME, password=ADMIN_PASSWORD)
        await admin.incoming.put({"type": "change_password", "new_password": NEXT_PASSWORD})
        await admin.incoming.put({"type": "ping"})
        await admin.expect("pong")
        self.assertFalse(any(p["type"] == "password_changed" for p in admin.history))
        await self.enter()

    async def test_server_capacity_answers_busy_and_admits_after_retry(self):
        await self.enter(mode="signup", room_password=ROOM_PASSWORD)
        full = member_auth.threading.BoundedSemaphore(1)
        full.acquire()
        with patch.object(member_auth, "_slots", full):
            socket, result = await self.attempt()
        self.assertEqual((result["type"], result["reason"]), ("auth_failed", "busy"))
        self.assertNotIn("session", result)
        full.release()
        await self.enter()


if __name__ == "__main__":
    unittest.main()
