"""Opt-in T6/T7. Give GOGOGO_TEST_MONGOD a local executable, never a URI.

The runner creates each process and DB itself. It can neither attach to an
existing deployment nor accept an address, data directory or credentials.
"""

import asyncio
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.monitoring import CommandListener

import member_auth
from test_member_login import LEGACY_KEY, PASSWORD, RoomCase, room


class Concerns(CommandListener):
    def __init__(self):
        self.writes = []

    def started(self, event):
        # Keep only concern options, never documents or credential values.
        if event.command_name == "update":
            self.writes.append(dict(event.command.get("writeConcern", {})))

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


@contextmanager
def private_server(binary, replica):
    with tempfile.TemporaryDirectory(prefix="gogogo-test-mongo-") as directory:
        root = Path(directory).resolve()
        data = root / "db"
        data.mkdir()
        log = root / "mongod.log"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        args = [str(binary), "--bind_ip", "127.0.0.1", "--port", str(port),
                "--dbpath", str(data), "--logpath", str(log),
                "--wiredTigerCacheSizeGB", "0.25", "--setParameter", "enableTestCommands=1",
                "--setParameter", "diagnosticDataCollectionEnabled=false"]
        if replica:
            args += ["--replSet", "gogogo_test", "--oplogSize", "96"]
        env = {"TMP": str(root), "TEMP": str(root), "TMPDIR": str(root), "HOME": str(root)}
        if os.name == "nt":
            env["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")
            env["PATH"] = str(Path(env["SystemRoot"]) / "System32")
        child = subprocess.Popen(args, env=env, cwd=root, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        client = None
        verified = False
        try:
            end = time.monotonic() + 45
            while time.monotonic() < end:
                if child.poll() is not None:
                    raise RuntimeError(f"Dedicated mongod exited during startup: {child.returncode}")
                if log.is_file() and '"Waiting for connections"' in log.read_text(
                        encoding="utf-8", errors="replace"):
                    break
                time.sleep(0.15)
            else:
                raise RuntimeError("Dedicated mongod did not open its listener")
            listener = Concerns()
            client = MongoClient("127.0.0.1", port, directConnection=True,
                                 retryWrites=False, retryReads=False, appname="gogogo-local-test",
                                 serverSelectionTimeoutMS=3000, socketTimeoutMS=10000,
                                 event_listeners=[listener])
            options = client.admin.command("getCmdLineOpts")["parsed"]
            if (Path(options["storage"]["dbPath"]).resolve() != data or
                    int(options["net"]["port"]) != port or
                    client.admin.command("serverStatus")["pid"] != child.pid):
                raise RuntimeError("Local server identity check failed; refusing to write")
            verified = True
            if replica:
                client.admin.command("replSetInitiate", {
                    "_id": "gogogo_test", "members": [{"_id": 0, "host": f"127.0.0.1:{port}"}]})
                end = time.monotonic() + 45
                while not client.admin.command("hello").get("isWritablePrimary"):
                    if time.monotonic() >= end:
                        raise RuntimeError("Dedicated replica set did not elect its primary")
                    time.sleep(0.2)
            yield client, listener
        finally:
            if verified and child.poll() is None:
                try:
                    client.admin.command("shutdown", force=True, timeoutSecs=1)
                except PyMongoError:
                    pass
            if client is not None:
                client.close()
            if child.poll() is None:
                try:
                    child.wait(timeout=12 if verified else 0)
                except subprocess.TimeoutExpired:
                    child.kill()  # Only this test's child process handle.
            child.wait(timeout=15)


class SimultaneousWrites:
    """Rendezvous immediately before two real driver calls. MongoDB decides the winner."""

    def __init__(self, collection):
        self.collection = collection
        self.barrier = threading.Barrier(2, timeout=15)

    def find_one(self, *args, **kwargs):
        return self.collection.find_one(*args, **kwargs)

    def update_one(self, *args, **kwargs):
        self.barrier.wait()
        return self.collection.update_one(*args, **kwargs)


@unittest.skipUnless(os.environ.get("GOGOGO_TEST_MONGOD"), "requires a disposable mongod executable")
class LocalMongo(unittest.TestCase):
    def test_standalone(self):
        self.run_topology(False)

    def test_replica_set(self):
        self.run_topology(True)

    def run_topology(self, replica):
        binary = Path(os.environ["GOGOGO_TEST_MONGOD"])
        if (not binary.is_absolute() or binary.name.lower() not in ("mongod", "mongod.exe")
                or str(binary).startswith(("//", "\\\\")) or not binary.is_file()):
            self.fail("GOGOGO_TEST_MONGOD must name a local absolute mongod executable")
        with private_server(binary, replica) as (client, listener):
            with self.subTest(scenario="T6 real DB"):
                asyncio.run(self.compare_and_set(client, listener))
            with self.subTest(scenario="T7 baseline on new credentials"):
                asyncio.run(self.old_app(client))

    @staticmethod
    def seed(collection):
        state = {"_id": "main_state", "identity_keys": {}, "identity_enroll": {}}
        collection.insert_one(state)

    async def compare_and_set(self, client, listener):
        collection = client.gogogo_test.auth_cas
        self.seed(collection)
        with patch.object(room, "collection", collection):
            durable = room.durable_collection()
            self.assertEqual(durable.write_concern.document["w"], "majority")
            self.assertIs(durable.write_concern.document["j"], True)
            self.assertEqual(durable.read_preference.name, "Primary")
            nid = room.name_id("FixtureMember")

            async def run(action, password=PASSWORD, factory=room.durable_collection, **fields):
                return await member_auth.authenticate_member(
                    factory, nid, action, password, **fields)

            async def race(action, **fields):
                pair = SimultaneousWrites(durable)
                results = await asyncio.gather(run(action, factory=lambda: pair, **fields),
                                               run(action, factory=lambda: pair, **fields))
                self.assertCountEqual([reason for reason, _ in results], [None, "credentials"], action)
                self.assertTrue(all(record is None for reason, record in results if reason), action)

            await race("signup")
            self.assertIsNone((await run("login"))[0])
            self.assertEqual((await run("signup"))[0], "credentials")
            self.assertEqual((await run("login", "wrong fixture password"))[0], "credentials")

            nid = room.name_id("MigrationFixture")
            durable.update_one({"_id": "main_state"}, {"$set": {
                f"identity_keys.{nid}": room.identity_hash(LEGACY_KEY)}})
            self.assertEqual((await run("migrate", identity_key="different-fixture-key"))[0],
                             "credentials")
            await race("migrate", identity_key=LEGACY_KEY)
            self.assertEqual((await run("legacy", identity_key=LEGACY_KEY))[0], "credentials")

            code = room.identity_hash("fixture-recovery-code")
            original = room.read_identity_snapshot(nid)
            self.assertTrue(room.db_issue_enrollment(nid, code, time.time() - 60, [original]))
            self.assertEqual((await run("recover", code_hash=code))[0], "credentials")
            self.assertFalse(room.db_issue_enrollment(nid, code, time.time() + 120, [original]))
            self.assertTrue(room.db_issue_enrollment(nid, code, time.time() + 120,
                                                    [room.read_identity_snapshot(nid)]))
            self.assertEqual((await run("recover", code_hash=room.identity_hash("wrong")))[0],
                             "credentials")
            await race("recover", code_hash=code)
            self.assertEqual((await run("recover", code_hash=code))[0], "credentials")
            self.assertIsNone((await run("login"))[0])

            # failCommand is available only on our new mongod with test commands enabled.
            # errorCode prevents the write. writeConcernError returns a server-side
            # acknowledgement failure after MongoDB has applied the mutation.
            for name, fault, committed in (
                ("Rejected", {"errorCode": 121}, False),
                ("Uncertain", {"writeConcernError": {
                    "code": 64, "codeName": "WriteConcernFailed", "errmsg": "synthetic timeout",
                    "errInfo": {"wtimeout": True}}}, True),
            ):
                nid = room.name_id(name)
                client.admin.command("configureFailPoint", "failCommand", mode={"times": 1},
                                     data={"failCommands": ["update"], **fault})
                reason, record = await member_auth.authenticate_member(
                    room.durable_collection, nid, "signup", PASSWORD)
                self.assertEqual((reason, record), ("storage", None), name)
                stored = {"_id": "main_state", f"identity_enroll.{nid}.pw": {"$exists": True}}
                self.assertEqual(collection.find_one(stored, {"_id": 1}) is not None,
                                 committed, f"{name} local read")
                # An injected wtimeout can return before the replica's majority
                # commit point advances. Await visibility, not a successful login.
                if committed:
                    deadline = time.monotonic() + 10
                    while durable.find_one(stored, {"_id": 1}) is None:
                        self.assertLess(time.monotonic(), deadline, "majority visibility timeout")
                        await asyncio.sleep(0.1)
                reason, _ = await run("login")
                self.assertEqual(reason, None if committed else "credentials", name)
            self.assertTrue(any(c.get("w") == "majority" and c.get("j") is True
                                for c in listener.writes))

        # Confirm the WebSocket does not publish a session or room state on either error.
        harness = RoomCase()
        fixture = ExitStack()
        harness.addCleanup = fixture.callback
        await harness.asyncSetUp()
        try:
            websocket_db = client.gogogo_test.ws_failure
            websocket_db.insert_one(room.server_state)
            with patch.object(room, "collection", websocket_db):
                from test_member_login import ROOM_PASSWORD
                for i, fault in enumerate((
                        {"errorCode": 121},
                        {"writeConcernError": {"code": 64, "errmsg": "synthetic timeout",
                                               "errInfo": {"wtimeout": True}}},
                )):
                    client.admin.command("configureFailPoint", "failCommand", mode={"times": 1},
                                         data={"failCommands": ["update"], **fault})
                    sock, answer = await harness.attempt(nickname=f"WebFixture{i}",
                                                         mode="signup", room_password=ROOM_PASSWORD)
                    self.assertEqual(answer.get("reason"), "storage")
                    self.assertNotIn(sock, room.manager.authed_connections)
                    self.assertFalse(room.manager.sessions)
                    self.assertLessEqual({event["type"] for event in sock.history},
                                         {"welcome", "auth_failed"})
        finally:
            await harness.asyncTearDown()
            fixture.close()

    async def old_app(self, client):
        from test_legacy_rollback import SPENT_CODE, check_rollback

        collection = client.gogogo_test.legacy
        state = room.load_data()
        mid, rid = room.name_id("Migrating"), room.name_id("Recovered")
        state["identity_keys"] = {mid: room.identity_hash(LEGACY_KEY)}
        state["identity_enroll"] = {rid: {"h": room.identity_hash(SPENT_CODE),
                                         "exp": time.time() + 300}}
        collection.insert_one(state)
        with patch.object(room, "collection", collection):
            for name, action, proof in (
                    ("Migrating", "migrate", {"identity_key": LEGACY_KEY}),
                    ("NewWriter", "signup", {}),
                    ("Recovered", "recover", {"code_hash": room.identity_hash(SPENT_CODE)})):
                reason, _ = await member_auth.authenticate_member(
                    room.durable_collection, room.name_id(name), action, PASSWORD, **proof)
                self.assertIsNone(reason, action)
            await check_rollback(self, collection)
