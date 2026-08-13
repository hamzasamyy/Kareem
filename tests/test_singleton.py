import time
import unittest
from unittest import mock

from kareem import singleton


class SingletonTests(unittest.TestCase):
    def test_second_acquire_fails_while_first_holds_lock(self):
        name = "Local\\KareemSingleInstanceMutexTest1"
        self.assertTrue(singleton.acquire(retry_seconds=0, name=name))
        self.assertFalse(singleton.acquire(retry_seconds=0, name=name))

    def test_acquire_retries_until_deadline(self):
        name = "Local\\KareemSingleInstanceMutexTest2"
        self.assertTrue(singleton.acquire(retry_seconds=0, name=name))
        start = time.monotonic()
        ok = singleton.acquire(retry_seconds=0.5, name=name)
        elapsed = time.monotonic() - start
        self.assertFalse(ok)
        self.assertGreaterEqual(elapsed, 0.5)

    def test_non_windows_degrades_instead_of_crashing(self):
        # ctypes.windll doesn't exist on macOS/Linux — without the platform
        # guard, acquire() would raise AttributeError and take Kareem's
        # startup down with it. It should instead treat the lock as acquired
        # and let Kareem run (just without duplicate-instance protection).
        with mock.patch.object(singleton.sys, "platform", "linux"):
            self.assertTrue(singleton.acquire(retry_seconds=0, name="unused-on-this-path"))


if __name__ == "__main__":
    unittest.main()
