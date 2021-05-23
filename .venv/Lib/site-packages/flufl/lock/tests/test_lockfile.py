"""Testing other aspects of the implementation and API."""

import os
import builtins
import unittest

from contextlib import ExitStack, suppress
from flufl.lock._lockfile import Lock, NotLockedError
from tempfile import TemporaryDirectory
from unittest.mock import patch


EMOCKEDFAILURE = 99
EOTHERMOCKEDFAILURE = 98


class TestableEnvironmentError(EnvironmentError):
    def __init__(self, errno):
        super().__init__()
        self.errno = errno


class ErrnoRetryTests(unittest.TestCase):
    def setUp(self):
        self._builtin_open = builtins.open
        self._failure_countdown = None
        self._retry_count = None
        self._errno = EMOCKEDFAILURE
        lock_dir = TemporaryDirectory()
        self.addCleanup(lock_dir.cleanup)
        self._lock = Lock(os.path.join(lock_dir.name, 'test.lck'))

    def tearDown(self):
        with suppress(NotLockedError):
            self._lock.unlock()

    def _testable_open(self, *args, **kws):
        if self._failure_countdown <= 0:
            return self._builtin_open(*args, **kws)
        self._failure_countdown -= 1
        self._retry_count += 1
        raise TestableEnvironmentError(self._errno)

    def test_retry_errno_api(self):
        self.assertEqual(self._lock.retry_errnos, [])
        self._lock.retry_errnos = [EMOCKEDFAILURE, EOTHERMOCKEDFAILURE]
        self.assertEqual(self._lock.retry_errnos,
                         [EMOCKEDFAILURE, EOTHERMOCKEDFAILURE])
        del self._lock.retry_errnos
        self.assertEqual(self._lock.retry_errnos, [])

    def test_retries(self):
        # Test that _read() will retry when a given errno is encountered.
        self._lock.lock()
        self._lock.retry_errnos = [self._errno]
        self._failure_countdown = 3
        self._retry_count = 0
        with patch('builtins.open', self._testable_open):
            self.assertTrue(self._lock.is_locked)
        # The _read() trigged by the .is_locked call should have been retried.
        self.assertEqual(self._retry_count, 3)


class LockTests(unittest.TestCase):
    def setUp(self):
        lock_dir = TemporaryDirectory()
        self.addCleanup(lock_dir.cleanup)
        self._lock = Lock(os.path.join(lock_dir.name, 'test.lck'))

    def test_is_locked_permission_error(self):
        with ExitStack() as resources:
            resources.enter_context(
                patch('os.utime', side_effect=PermissionError))
            log_mock = resources.enter_context(
                patch('flufl.lock._lockfile.log'))
            self.assertFalse(self._lock.is_locked)
            log_mock.error.assert_called_once_with(
                'No permission to refresh the log')
