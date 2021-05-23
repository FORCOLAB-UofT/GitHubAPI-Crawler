"""Portable, NFS-safe file locking with timeouts for POSIX systems.

This code implements an NFS-safe file-based locking algorithm influenced by
the GNU/Linux open(2) manpage, under the description of the O_EXCL option:

    [...] O_EXCL is broken on NFS file systems, programs which rely on it
    for performing locking tasks will contain a race condition.  The
    solution for performing atomic file locking using a lockfile is to
    create a unique file on the same fs (e.g., incorporating hostname and
    pid), use link(2) to make a link to the lockfile.  If link() returns
    0, the lock is successful.  Otherwise, use stat(2) on the unique file
    to check if its link count has increased to 2, in which case the lock
    is also successful.

The assumption made here is that there will be no 'outside interference',
e.g. no agent external to this code will ever link() to the specific lock
files used.

Lock objects support lock-breaking so that you can't wedge a process forever.
This is especially helpful in a web environment, but may not be appropriate
for all applications.

Locks have a 'lifetime', which is the maximum length of time the process
expects to retain the lock.  It is important to pick a good number here
because other processes will not break an existing lock until the expected
lifetime has expired.  Too long and other processes will hang; too short and
you'll end up trampling on existing process locks -- and possibly corrupting
data.  In a distributed (NFS) environment, you also need to make sure that
your clocks are properly synchronized.
"""

import os
import sys
import time
import errno
import random
import socket
import logging
import datetime

from logging import NullHandler
from public import public


DEFAULT_LOCK_LIFETIME = datetime.timedelta(seconds=15)
# Allowable a bit of clock skew.
CLOCK_SLOP = datetime.timedelta(seconds=10)
MAXINT = sys.maxsize

# Details separator; also used in calculating the claim file path.  Lock files
# should not include this character.  We do it like this so flake8 won't
# complain about SEP.
SEP = ('^' if sys.platform == 'win32' else '|')
public(SEP=SEP)

# LP: #977999 - catch both ENOENT and ESTALE.  The latter is what an NFS
# server should return, but some Linux versions return ENOENT.
ERRORS = (errno.ENOENT, errno.ESTALE)


log = logging.getLogger('flufl.lock')

# Install a null handler to avoid warnings when applications don't set their
# own flufl.lock logger.  See http://docs.python.org/library/logging.html
logging.getLogger('flufl.lock').addHandler(NullHandler())


@public
class LockError(Exception):
    """Base class for all exceptions in this module."""


@public
class AlreadyLockedError(LockError):
    """An attempt is made to lock an already locked object."""


@public
class NotLockedError(LockError):
    """An attempt is made to unlock an object that isn't locked."""


@public
class TimeOutError(LockError):
    """The timeout interval elapsed before the lock succeeded."""


@public
class Lock:
    """A portable way to lock resources by way of the file system."""
    def __init__(self, lockfile, lifetime=None, separator=SEP):
        """Create the resource lock using the given file name and lifetime.

        Each process laying claim to this resource lock will create their own
        temporary lock file based on the path specified.  An optional lifetime
        is the length of time that the process expects to hold the lock.

        :param lockfile: The full path to the lock file.
        :param lifetime: The expected maximum lifetime of the lock, as a
            timedelta.  Defaults to 15 seconds.
        :param separator: The separator character to use in the lock file's
            file name.  Defaults to the vertical bar (`|`) on POSIX systems
            and caret (`^`) on Windows.
        """
        # This has to be defined before we call _set_claimfile().
        self._hostname = socket.getfqdn()
        if lifetime is None:
            lifetime = DEFAULT_LOCK_LIFETIME
        self._lockfile = lockfile
        self._lifetime = lifetime
        # The separator must be set before we claim the lock.
        self._separator = separator
        self._set_claimfile()
        # For transferring ownership across a fork.
        self._owned = True
        # For extending the set of NFS errnos that are retried in _read().
        self._retry_errnos = []

    def __repr__(self):
        return '<%s %s [%s: %s] pid=%s at %#xx>' % (
            self.__class__.__name__,
            self._lockfile,
            ('locked' if self.is_locked else 'unlocked'),
            self._lifetime, os.getpid(), id(self))

    @property
    def hostname(self):
        """The current machine's host name.

        :return: The current machine's hostname, as used in the `.details`
            property.
        :rtype: str
        """
        return self._hostname

    @property
    def details(self):
        """Details as read from the lock file.

        :return: A 3-tuple of hostname, process id, file name.
        :rtype: (str, int, str)
        :raises NotLockedError: if the lock is not acquired.
        """
        try:
            with open(self._lockfile) as fp:
                filename = fp.read().strip()
        except IOError as error:
            if error.errno in ERRORS:
                raise NotLockedError('Details are unavailable')
            raise
        # Rearrange for signature.
        try:
            lockfile, hostname, pid, random_ignored = filename.split(
                self._separator)
        except ValueError:
            raise NotLockedError('Details are unavailable')
        return hostname, int(pid), lockfile

    @property
    def lifetime(self):
        return self._lifetime

    @lifetime.setter
    def lifetime(self, lifetime):
        self._lifetime = lifetime

    def refresh(self, lifetime=None, unconditionally=False):
        """Refreshes the lifetime of a locked file.

        Use this if you realize that you need to keep a resource locked longer
        than you thought.

        :param lifetime: If given, this sets the lock's new lifetime.  This
            must be a datetime.timedelta.
        :param unconditionally: When False (the default), a `NotLockedError`
            is raised if an unlocked lock is refreshed.
        :raises NotLockedError: if the lock is not set, unless optional
            `unconditionally` flag is set to True.
        """
        if lifetime is not None:
            self._lifetime = lifetime
        # Do we have the lock?  As a side effect, this refreshes the lock!
        if not self.is_locked and not unconditionally:
            raise NotLockedError('{}: {}'.format(repr(self), self._read()))

    def lock(self, timeout=None):
        """Acquire the lock.

        This blocks until the lock is acquired unless optional timeout is not
        None, in which case a `TimeOutError` is raised when the timeout
        expires without lock acquisition.

        :param timeout: A datetime.timedelta indicating approximately how long
            the lock acquisition attempt should be made.  None (the default)
            means keep trying forever.
        :raises AlreadyLockedError: if the lock is already acquired.
        :raises TimeOutError: if `timeout` is not None and the indicated time
            interval expires without a lock acquisition.
        """
        if timeout is not None:
            timeout_time = datetime.datetime.now() + timeout
        # Make sure the claim file exists, and that its contents are current.
        self._write()
        # XXX This next call can fail with an EPERM.  I have no idea why, but
        # I'm nervous about wrapping this in a try/except.  It seems to be a
        # very rare occurrence, only happens from cron, and has only(?) been
        # observed on Solaris 2.6.
        self._touch()
        log.debug('laying claim: {}'.format(self._lockfile))
        # For quieting the logging output.
        loopcount = -1
        while True:
            loopcount += 1
            # Create the hard link and test for exactly 2 links to the file.
            try:
                os.link(self._claimfile, self._lockfile)
                # If we got here, we know we got the lock, and never
                # had it before, so we're done.  Just touch it again for the
                # fun of it.
                log.debug('got the lock: {}'.format(self._lockfile))
                self._touch()
                break
            except OSError as error:
                # The link failed for some reason, possibly because someone
                # else already has the lock (i.e. we got an EEXIST), or for
                # some other bizarre reason.
                if error.errno in ERRORS:
                    # XXX in some Linux environments, it is possible to get an
                    # ENOENT, which is truly strange, because this means that
                    # self._claimfile didn't exist at the time of the
                    # os.link(), but self._write() is supposed to guarantee
                    # that this happens!  I don't honestly know why this
                    # happens -- possibly due to weird caching file systems?
                    # -- but for now we just say we didn't acquire the lock
                    # and try again next time.
                    pass
                elif error.errno != errno.EEXIST:
                    # Something very bizarre happened.  Clean up our state and
                    # pass the error on up.
                    log.exception('unexpected link')
                    os.unlink(self._claimfile)
                    raise
                elif self._linkcount != 2:
                    # Somebody's messin' with us!  Log this, and try again
                    # later.  XXX should we raise an exception?
                    log.error('unexpected linkcount: {0:d}'.format(
                        self._linkcount))
                elif self._read() == self._claimfile:
                    # It was us that already had the link.
                    log.debug('already locked: {}'.format(self._lockfile))
                    raise AlreadyLockedError('We already had the lock')
                # Otherwise, someone else has the lock
                pass
            # We did not acquire the lock, because someone else already has
            # it.  Have we timed out in our quest for the lock?
            if timeout is not None and timeout_time < datetime.datetime.now():
                os.unlink(self._claimfile)
                log.error('timed out')
                raise TimeOutError('Could not acquire the lock')
            # Okay, we haven't timed out, but we didn't get the lock.  Let's
            # find if the lock lifetime has expired.  Cache the release time
            # to avoid race conditions.  (LP: #827052)
            release_time = self._releasetime
            if (release_time != -1 and
                    datetime.datetime.now() > release_time + CLOCK_SLOP):
                # Yes, so break the lock.
                self._break()
                log.error('lifetime has expired, breaking')
            # Okay, someone else has the lock, our claim hasn't timed out yet,
            # and the expected lock lifetime hasn't expired yet either.  So
            # let's wait a while for the owner of the lock to give it up.
            elif not loopcount % 100:
                log.debug('waiting for claim: {}'.format(self._lockfile))
            self._sleep()

    def unlock(self, unconditionally=False):
        """Release the lock.

        :param unconditionally: When False (the default), a `NotLockedError`
            is raised if this is called on an unlocked lock.
        :raises NotLockedError: if we don't own the lock, either because of
            unbalanced unlock calls, or because the lock was stolen out from
            under us, unless optional `unconditionally` is True.
        """
        is_locked = self.is_locked
        if not is_locked and not unconditionally:
            raise NotLockedError('Already unlocked')
        # If we owned the lock, remove the lockfile, relinquishing the lock.
        if is_locked:
            try:
                os.unlink(self._lockfile)
            except OSError as error:
                if error.errno not in ERRORS:
                    raise
        # Remove our claim file.
        try:
            os.unlink(self._claimfile)
        except OSError as error:
            if error.errno not in ERRORS:
                raise
        log.debug('unlocked: {}'.format(self._lockfile))

    @property
    def is_locked(self):
        """True if we own the lock, False if we do not.

        Checking the status of the lock resets the lock's lifetime, which
        helps avoid race conditions during the lock status test.
        """
        # Discourage breaking the lock for a while.
        try:
            self._touch()
        except PermissionError:
            # We can't touch the file because we're not the owner.  I don't see
            # how we can own the lock if we're not the owner.
            log.error('No permission to refresh the log')
            return False
        # XXX Can the link count ever be > 2?
        if self._linkcount != 2:
            return False
        return self._read() == self._claimfile

    def finalize(self):
        """Unconditionally unlock the file."""
        log.debug('finalize: {}'.format(self._lockfile))
        self.unlock(unconditionally=True)

    def __del__(self):
        log.debug('__del__: {}'.format(self._lockfile))
        if self._owned:
            self.finalize()

    def __enter__(self):
        self.lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.unlock()
        # Don't suppress any exception that might have occurred.
        return False

    def transfer_to(self, pid):
        """Transfer ownership of the lock to another process.

        Use this only if you're transfering ownership to a child process across
        a fork.  Use at your own risk, but it should be race-condition safe.
        transfer_to() is called in the parent, passing in the pid of the child.
        take_possession() is called in the child, and blocks until the parent
        has transferred possession to the child.  disown() is used to set the
        'owned' flag to False, and it is a disgusting wart necessary to make
        forced lock acquisition work. :(

        :param pid: The process id of the child process that will take
            possession of the lock.
        """
        # First touch it so it won't get broken while we're fiddling about.
        self._touch()
        # Find out current claim's file name
        winner = self._read()
        # Now twiddle ours to the given pid.
        self._set_claimfile(pid)
        # Create a hard link from the global lock file to the claim file.
        # This actually does things in reverse order of normal operation
        # because we know that lockfile exists, and claimfile better not!
        os.link(self._lockfile, self._claimfile)
        # Now update the lock file to contain a reference to the new owner
        self._write()
        # Toggle off our ownership of the file so we don't try to finalize it
        # in our __del__()
        self._owned = False
        # Unlink the old winner, completing the transfer.
        os.unlink(winner)
        # And do some sanity checks
        assert self._linkcount == 2, (
            'Unexpected link count: wanted 2, got {0:d}'.format(
                self._linkcount))
        assert self.is_locked, 'Expected to be locked'
        log.debug('transferred the lock: {}'.format(self._lockfile))

    def take_possession(self):
        """Take possession of a lock from another process.

        See `transfer_to()` for more information.
        """
        self._set_claimfile()
        # Wait until the linkcount is 2, indicating the parent has completed
        # the transfer.
        while self._linkcount != 2 or self._read() != self._claimfile:
            time.sleep(0.25)
        log.debug('took possession of the lock: {}'.format(self._lockfile))

    def disown(self):
        """Disown this lock.

        See `transfer_to()` for more information.
        """
        self._owned = False

    def _set_claimfile(self, pid=None):
        """Set the _claimfile private variable."""
        if pid is None:
            pid = os.getpid()
        # Calculate a hard link file name that will be used to lay claim to
        # the lock.  We need to watch out for two Lock objects in the same
        # process pointing to the same lock file.  Without this, if you lock
        # lf1 and do not lock lf2, lf2.locked() will still return True.
        self._claimfile = self._separator.join((
            self._lockfile,
            self.hostname,
            str(pid),
            str(random.randint(0, MAXINT)),
            ))

    def _write(self):
        """Write our claim file's name to the claim file."""
        # Make sure it's group writable
        with open(self._claimfile, 'w') as fp:
            fp.write(self._claimfile)

    @property
    def retry_errnos(self):
        """The set of errno values that cause a read retry."""
        return self._retry_errnos[:]

    @retry_errnos.setter
    def retry_errnos(self, errnos):
        self._retry_errnos = []
        self._retry_errnos.extend(errnos)

    @retry_errnos.deleter
    def retry_errnos(self):
        self._retry_errnos = []

    def _read(self):
        """Read the contents of our lock file.

        :return: The contents of the lock file or None if the lock file does
            not exist.
        """
        while True:
            try:
                with open(self._lockfile) as fp:
                    return fp.read()
            except EnvironmentError as error:
                if error.errno in self._retry_errnos:
                    self._sleep()
                elif error.errno not in ERRORS:
                    raise
                else:
                    return None

    def _touch(self, filename=None):
        """Touch the claim file into the future.

        :param filename: If given, the file to touch, otherwise our claim file
            is touched.
        """
        expiration_date = datetime.datetime.now() + self._lifetime
        t = time.mktime(expiration_date.timetuple())
        try:
            # XXX We probably don't need to modify atime, but this is easier.
            os.utime(filename or self._claimfile, (t, t))
        except OSError as error:
            if error.errno not in ERRORS:
                raise

    @property
    def _releasetime(self):
        """The time when the lock should be released.

        :return: The mtime of the file, which is when the lock should be
            released, or -1 if the lockfile doesn't exist.
        """
        try:
            return datetime.datetime.fromtimestamp(
                os.stat(self._lockfile).st_mtime)
        except OSError as error:
            if error.errno in ERRORS:
                return -1
            raise

    @property
    def _linkcount(self):
        """The number of hard links to the lock file.

        :return: the number of hard links to the lock file, or -1 if the lock
            file doesn't exist.
        """
        try:
            return os.stat(self._lockfile).st_nlink
        except OSError as error:
            if error.errno in ERRORS:
                return -1
            raise

    def _break(self):
        """Break the lock."""
        # First, touch the lock file.  This reduces but does not eliminate the
        # chance for a race condition during breaking.  Two processes could
        # both pass the test for lock expiry in lock() before one of them gets
        # to touch the lock file.  This shouldn't be too bad because all
        # they'll do in that function is delete the lock files, not claim the
        # lock, and we can be defensive for ENOENTs here.
        #
        # Touching the lock could fail if the process breaking the lock and
        # the process that claimed the lock have different owners.  Don't do
        # that.
        try:
            self._touch(self._lockfile)
        except OSError as error:
            if error.errno != errno.EPERM:
                raise
        # Get the name of the old winner's temp file.
        winner = self._read()
        # Remove the global lockfile, which actually breaks the lock.
        try:
            os.unlink(self._lockfile)
        except OSError as error:
            if error.errno not in ERRORS:
                raise
        # Try to remove the old winner's claim file, since we're assuming the
        # winner process has hung or died.  Don't worry too much if we can't
        # unlink their claim file -- this doesn't wreck the locking algorithm,
        # but will leave claim file turds laying around, a minor inconvenience.
        try:
            if winner:
                os.unlink(winner)
        except OSError as error:
            if error.errno not in ERRORS:
                raise

    def _sleep(self):
        """Snooze for a random amount of time."""
        interval = random.random() * 2.0 + 0.01
        time.sleep(interval)
