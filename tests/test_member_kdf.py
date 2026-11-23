import asyncio
import threading
import time
import unittest
import unicodedata
from unittest.mock import Mock, patch

import member_auth

# Boundaries follow the single length setting.
MIN = member_auth.MIN_PASSWORD_CHARS


class PasswordHashTests(unittest.TestCase):
    def test_server_accepts_passphrases_without_trimming_or_truncating(self):
        for value in ("a" * MIN, "암" * 128, "🙂" * 128, " surrounding " + "s" * MIN + " "):
            self.assertEqual(member_auth._password_bytes(value), value.encode("utf-8"))
        for value in ("", "a" * (MIN - 1), "a" * 129, "🙂" * 129, None, 15, "a" * MIN + "\ud800"):
            self.assertIsNone(member_auth._password_bytes(value))

    def test_composed_length_decides_the_boundary(self):
        for char in ("각", "\u1f82"):
            self.assertEqual(member_auth._password_bytes(unicodedata.normalize("NFD", char * 128)),
                             (char * 128).encode())
            self.assertIsNone(member_auth._password_bytes(unicodedata.normalize("NFD", char * 129)))
        self.assertIsNone(member_auth._password_bytes(unicodedata.normalize("NFD", "각" * (MIN - 1))))
        self.assertNotEqual(member_auth._password_bytes("①" * MIN), member_auth._password_bytes("1" * MIN))

    def test_short_new_passwords_rejected_after_nfc_before_storage(self):
        storage = Mock()
        for action in ("signup", "migrate", "recover"):
            result = member_auth._authenticate(storage, "a" * 64, action,
                                               unicodedata.normalize("NFD", "각" * (MIN - 1)), None, None)
            self.assertEqual(result, ("password_policy", None))
        storage.assert_not_called()

    def test_length_rule_applies_only_to_new_passwords(self):
        short = "짧은 비번"
        self.assertIsNone(member_auth._password_bytes(short))
        self.assertEqual(member_auth._password_bytes(short, new=False), short.encode())
        self.assertIsNone(member_auth._password_bytes("", new=False))
        self.assertIsNone(member_auth._password_bytes("a" * 129, new=False))
        # A password set under one minimum still logs in after the setting changes.
        nid = "b" * 64
        raw = "a" * member_auth.MIN_PASSWORD_CHARS
        record = member_auth._new_record(raw.encode())
        collection = Mock()
        collection.find_one.return_value = {"identity_enroll": {nid: record}}
        with patch.object(member_auth, "MIN_PASSWORD_CHARS", member_auth.MIN_PASSWORD_CHARS + 5):
            self.assertEqual(member_auth._authenticate(lambda: collection, nid, "login", raw, None, None),
                             (None, record))

    def test_change_writes_only_over_a_current_password_account(self):
        nid = "c" * 64
        generation = "d" * 64
        collection = Mock()
        collection.update_one.return_value = Mock(modified_count=1)
        reason, record = member_auth._authenticate(lambda: collection, nid, "change", "a" * MIN, None, None,
                                                   generation)
        self.assertIsNone(reason)
        predicate = collection.update_one.call_args[0][0]
        self.assertEqual(predicate[f"identity_keys.{nid}"], member_auth.LEGACY_KEY_TOMBSTONE)
        self.assertEqual(predicate[f"identity_enroll.{nid}.v"], 2)
        # Only the generation the session signed in with may change.
        self.assertEqual(predicate[f"identity_enroll.{nid}.pw.digest"], generation)
        collection.find_one.assert_not_called()
        # Another change or a recovery code replaced the record: nothing is written.
        collection.update_one.return_value = Mock(modified_count=0)
        self.assertEqual(member_auth._authenticate(lambda: collection, nid, "change", "a" * MIN, None, None,
                                                   generation), ("stale", None))
        storage = Mock()
        self.assertEqual(member_auth._authenticate(storage, nid, "change", "a" * MIN, None, None, None),
                         ("stale", None))
        storage.assert_not_called()
        self.assertEqual(member_auth._authenticate(Mock(), nid, "change", "a" * (MIN - 1), None, None,
                                                   generation), ("password_policy", None))

    def test_login_accepts_composed_and_decomposed_input(self):
        nid = "a" * 64
        composed = "  기기 사이에서 사용하는 개인 비밀번호  "
        decomposed = unicodedata.normalize("NFD", composed)
        record = member_auth._new_record(member_auth._password_bytes(decomposed))
        self.assertEqual(record["v"], 2)
        collection = Mock()
        collection.find_one.return_value = {"identity_enroll": {nid: record}}
        for value in (composed, decomposed):
            self.assertEqual(member_auth._authenticate(lambda: collection, nid, "login", value, None, None),
                             (None, record))
        self.assertFalse(member_auth._matches(dict(record, v=1), composed.encode()))
        collection.update_one.assert_not_called()

    def test_distinct_salts_and_exact_verification(self):
        raw = b"a unique personal passphrase "
        first = member_auth._new_record(raw)
        second = member_auth._new_record(raw)
        self.assertNotEqual(first["pw"]["salt"], second["pw"]["salt"])
        self.assertNotEqual(first["pw"]["digest"], second["pw"]["digest"])
        self.assertTrue(member_auth._matches(first, raw))
        self.assertFalse(member_auth._matches(first, raw.strip()))
        self.assertFalse(member_auth._matches({"h": "old", "exp": 1}, raw))


class PasswordWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_queue_instead_of_being_refused(self):
        self.assertFalse(hasattr(member_auth, "_workers"))
        calls = 0
        lock = threading.Lock()

        def slow_authenticate(*args):
            nonlocal calls
            with lock:
                calls += 1
            time.sleep(0.05)
            return None, None

        with patch.object(member_auth, "_authenticate", slow_authenticate):
            results = await asyncio.gather(*(member_auth.authenticate_member(None, "a" * 64, "login", "a" * MIN)
                                             for _ in range(6)))
        self.assertEqual(results, [(None, None)] * 6)
        self.assertEqual(calls, 6)

    async def test_full_queue_answers_busy_and_frees_slots_when_workers_end(self):
        gate = threading.Event()

        def blocked_authenticate(*args):
            gate.wait(5)
            return None, None

        with patch.object(member_auth, "_slots", threading.BoundedSemaphore(2)),                 patch.object(member_auth, "_executor", member_auth.ThreadPoolExecutor(max_workers=1)),                 patch.object(member_auth, "_authenticate", blocked_authenticate):
            first = asyncio.ensure_future(member_auth.authenticate_member(None, "a" * 64, "login", "x"))
            second = asyncio.ensure_future(member_auth.authenticate_member(None, "a" * 64, "login", "x"))
            await asyncio.sleep(0.05)
            self.assertEqual(await member_auth.authenticate_member(None, "a" * 64, "login", "x"), ("busy", None))
            gate.set()
            self.assertEqual(await asyncio.gather(first, second), [(None, None)] * 2)
            # Both slots are free again, so the next member is served.
            self.assertEqual(await member_auth.authenticate_member(None, "a" * 64, "login", "x"), (None, None))

    async def test_request_waiting_too_long_is_skipped_without_computing(self):
        calls = []

        def slow_authenticate(*args):
            calls.append(args[2])
            time.sleep(0.2)
            return None, None

        with patch.object(member_auth, "AUTH_MAX_WAIT_SECONDS", 0.05),                 patch.object(member_auth, "_executor", member_auth.ThreadPoolExecutor(max_workers=1)),                 patch.object(member_auth, "_authenticate", slow_authenticate):
            results = await asyncio.gather(member_auth.authenticate_member(None, "a" * 64, "login", "x"),
                                           member_auth.authenticate_member(None, "a" * 64, "signup", "x"))
        self.assertEqual(results, [(None, None), ("busy", None)])
        self.assertEqual(calls, ["login"])

    async def test_lock_wait_counts_against_the_deadline_and_frees_the_slot(self):
        lock = asyncio.Lock()
        await lock.acquire()
        slots = threading.BoundedSemaphore(2)
        with patch.object(member_auth, "_slots", slots), patch.object(member_auth, "AUTH_MAX_WAIT_SECONDS", 0.1):
            ticket = member_auth.admit()
            self.assertEqual(slots._value, 1)  # admitted before waiting for the lock
            with self.assertRaises(member_auth.Busy):
                await ticket.hold(lock)
            ticket.close()
            self.assertEqual(slots._value, 2)
            # Work queued past the deadline is skipped too.
            late = member_auth.admit()
            await asyncio.sleep(0.15)
            with self.assertRaises(member_auth.Busy):
                await late.run(lambda: self.fail("computed past the deadline"))
            late.close()
        self.assertEqual(slots._value, 2)
        lock.release()

    async def test_slot_stays_taken_until_the_worker_really_ends(self):
        gate = threading.Event()
        slots = threading.BoundedSemaphore(1)
        with patch.object(member_auth, "_slots", slots), \
                patch.object(member_auth, "_executor", member_auth.ThreadPoolExecutor(max_workers=1)):
            ticket = member_auth.admit()
            task = asyncio.ensure_future(ticket.run(gate.wait, 5))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            ticket.close()
            # The request is gone, but its scrypt-sized work is still running.
            self.assertIsNone(member_auth.admit())
            gate.set()
            for _ in range(50):
                if slots._value == 1:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(slots._value, 1)

    async def test_abandoned_request_is_not_computed_and_releases_its_slot(self):
        gate = threading.Event()
        calls = []

        def blocked_authenticate(*args):
            calls.append(args[2])
            gate.wait(5)
            return None, None

        slots = threading.BoundedSemaphore(2)
        with patch.object(member_auth, "_slots", slots),                 patch.object(member_auth, "_executor", member_auth.ThreadPoolExecutor(max_workers=1)),                 patch.object(member_auth, "_authenticate", blocked_authenticate):
            running = asyncio.ensure_future(member_auth.authenticate_member(None, "a" * 64, "login", "x"))
            waiting = asyncio.ensure_future(member_auth.authenticate_member(None, "a" * 64, "signup", "x"))
            await asyncio.sleep(0.05)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            gate.set()
            self.assertEqual(await running, (None, None))
            for _ in range(50):
                if slots._value == 2:
                    break
                await asyncio.sleep(0.01)
        self.assertEqual(calls, ["login"])
        self.assertEqual(slots._value, 2)
