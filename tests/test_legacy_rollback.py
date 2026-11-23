"""T7: run the actual November 9 handlers against the database left by this app."""

import asyncio
import hashlib
import json
import os
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

import member_auth
from test_member_login import (ADMIN_PASSWORD, LEGACY_KEY, PASSWORD, ROOM_PASSWORD,
                               RoomCase, Socket, room)


LEGACY_COMMIT = "ebeead5d7fff74f1599f3e76e33e73e0e737855d"
SPENT_CODE = "ABCDEFGHJKLM"


def pinned_module(commit, filename, blob_sha, fixture_var):
    fixture = os.environ.get(fixture_var)
    if fixture and fixture != "git":
        source = Path(fixture).read_bytes()
    else:
        repo = os.environ.get("GOGOGO_TEST_GIT_REPO", Path(__file__).resolve().parents[1])
        source = subprocess.check_output(["git", "-C", str(repo), "show", f"{commit}:{filename}"])
    digest = hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest()
    if digest != blob_sha:
        raise AssertionError(f"Incorrect Git blob for {commit}:{filename}: {digest}")
    module = ModuleType("snapshot_" + commit[:7] + "_" + Path(filename).stem)
    module.__file__ = filename
    with patch.object(os, "environ", {
        "MONGO_URI": "", "GOGOGO_ROOM_PASSWORD": ROOM_PASSWORD,
        "GOGOGO_ADMIN_PASSWORD": ADMIN_PASSWORD,
    }), patch("pymongo.MongoClient") as client:
        exec(compile(source, f"{commit[:7]}:{filename}", "exec"), module.__dict__)
        client.assert_not_called()
    return module


async def http_count(app):
    events = []

    async def send(event):
        events.append(event)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
               "method": "GET", "scheme": "http", "path": "/user_count",
               "query_string": b"", "headers": []}, receive, send)
    return events[0]["status"], json.loads(b"".join(e.get("body", b"") for e in events[1:]))


@asynccontextmanager
async def old_room(collection):
    old = pinned_module(LEGACY_COMMIT, "main.py", "2be801b908b9de7e72332b66f05e583b2de2fb00",
                        "GOGOGO_LEGACY_MAIN")
    old.collection = collection
    old.server_state = old.load_data()
    sockets, tasks = [], []

    async def login(nick, password, **credentials):
        socket = Socket(f"192.0.2.{len(sockets) + 1}")
        sockets.append(socket)
        tasks.append(asyncio.create_task(old.websocket_endpoint(socket)))
        await socket.expect("welcome")
        await socket.incoming.put(dict(type="set_nickname", nickname=nick, password=password,
                                       **credentials))
        result = await socket.expect("auth_ok", "auth_failed")
        if result["type"] == "auth_failed":
            assert {p["type"] for p in socket.history} == {"welcome", "auth_failed"}
            await tasks[-1]
        return socket, result

    # Keep the no-activity fixture intact; credential writes still use the
    # historical Mongo helpers and unmodified journaled conditional updates.
    with patch.object(old, "persist_state", AsyncMock()):
        try:
            yield old, login
        finally:
            for socket in sockets:
                await socket.close()
            await asyncio.gather(*tasks)
            pending = list(old.pending_presence_leaves.values()) + list(old.revoke_tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


async def check_rollback(test, collection):
    """Reusable for the memory document and the isolated real Mongo profiles."""
    async with old_room(collection) as (old, login):
        test.assertNotIn("NewWriter", old.member_names())
        test.assertEqual(await http_count(old.app), (200, {"count": 0}))
        for nick in ("Migrating", "NewWriter", "Recovered"):
            nid = old.name_id(nick)
            test.assertEqual(old.server_state["identity_keys"][nid], member_auth.LEGACY_KEY_TOMBSTONE)
            test.assertEqual(old.server_state["identity_enroll"][nid]["v"], 2)
            for password, fields, reason in (
                (PASSWORD, {}, "password"),
                (ROOM_PASSWORD, {"identity_key": LEGACY_KEY}, "identity"),
                (ROOM_PASSWORD, {"auth_mode": "signup"}, "identity"),
                (ROOM_PASSWORD, {"enroll_code": SPENT_CODE}, "identity"),
            ):
                _, reply = await login(nick, password, **fields)
                test.assertEqual(reply, {"type": "auth_failed", "reason": reason}, nick)
        test.assertEqual(await http_count(old.app), (200, {"count": 0}))
        before = collection.find_one({"_id": "main_state"}, {"identity_keys": 1, "identity_enroll": 1})
        old.save_data({"cards": [], "identity_keys": {}, "identity_enroll": {},
                       "global_notice": "old build saved its unrelated notice"})
        after = collection.find_one({"_id": "main_state"}, {"identity_keys": 1, "identity_enroll": 1})
        test.assertEqual(before, after)
        admin, result = await login(old.ADMIN_NICKNAME, ADMIN_PASSWORD)
        test.assertTrue(result["is_admin"])
        state = (await admin.expect("init_state"))["state"]
        for field in ("identity_keys", "identity_enroll"):
            test.assertNotIn(field, state)
        test.assertEqual(await http_count(old.app), (200, {"count": 1}))
        # The historical administrator retains the power to replace all proofs.
        await admin.incoming.put({"type": "issue_enrollment", "target_nick": "Migrating"})
        issued = await admin.expect("enrollment_issued")
        test.assertTrue(issued["ok"])
        _, replaced = await login("Migrating", ROOM_PASSWORD, enroll_code=issued["code"])
        test.assertEqual(replaced["type"], "auth_ok")
        test.assertIn("identity_key", replaced)
        test.assertFalse(replaced["is_admin"])
        _, used = await login("Migrating", ROOM_PASSWORD, enroll_code=issued["code"])
        test.assertEqual(used["reason"], "identity")
        test.assertEqual(await http_count(old.app), (200, {"count": 2}))


class LegacyRollbackTests(RoomCase):
    async def test_three_current_password_writes_on_the_real_legacy_build(self):
        self.seed("Migrating", key=LEGACY_KEY)
        await self.enter(nickname="Migrating", mode="migrate", identity_key=LEGACY_KEY,
                         room_password=ROOM_PASSWORD)
        await self.enter(nickname="NewWriter", mode="signup", room_password=ROOM_PASSWORD)
        self.seed("Recovered", code=SPENT_CODE, expires=time.time() + 600)
        await self.enter(nickname="Recovered", mode="recover", enroll_code=SPENT_CODE)
        await check_rollback(self, self.db)
