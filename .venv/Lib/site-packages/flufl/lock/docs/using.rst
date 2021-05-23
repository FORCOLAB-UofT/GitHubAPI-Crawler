============================
Using the flufl.lock library
============================

The ``flufl.lock`` package provides NFS-safe file locking with timeouts for
POSIX systems.  The implementation is influenced by the GNU/Linux `open(2)`_
manpage, under the description of the ``O_EXCL`` option:

    [...] O_EXCL is broken on NFS file systems, programs which rely on it for
    performing locking tasks will contain a race condition.  The solution for
    performing atomic file locking using a lockfile is to create a unique file
    on the same fs (e.g., incorporating hostname and pid), use link(2) to make
    a link to the lockfile.  If link() returns 0, the lock is successful.
    Otherwise, use stat(2) on the unique file to check if its link count has
    increased to 2, in which case the lock is also successful.

The assumption made here is that there will be no *outside interference*,
e.g. no agent external to this code will ever ``link()`` to the specific lock
files used.

Lock objects support lock-breaking so that you can't wedge a process forever.

Locks have a *lifetime*, which is the maximum length of time the process
expects to retain the lock.  It is important to pick a good number here
because other processes will not break an existing lock until the expected
lifetime has expired.  Too long and other processes will hang; too short and
you'll end up trampling on existing process locks -- and possibly corrupting
data.  In a distributed (NFS) environment, you also need to make sure that
your clocks are properly synchronized.


Creating a lock
===============

To create a lock, you must first instantiate a ``Lock`` object, specifying the
path to a file that will be used to synchronize the lock.  This file should
not exist.
::

    # This function comes from the test infrastructure.
    >>> filename = temporary_lockfile()

    >>> from flufl.lock import Lock
    >>> lock = Lock(filename)
    >>> lock
    <Lock ... [unlocked: 0:00:15] pid=... at ...>

Locks have a default lifetime...

    >>> lock.lifetime
    datetime.timedelta(0, 15)

...which you can change.

    >>> from datetime import timedelta
    >>> lock.lifetime = timedelta(seconds=30)
    >>> lock.lifetime
    datetime.timedelta(0, 30)
    >>> lock.lifetime = timedelta(seconds=15)

You can ask whether the lock is acquired or not.

    >>> lock.is_locked
    False

Acquiring the lock is easy if no other process has already acquired it.

    >>> lock.lock()
    >>> lock.is_locked
    True

Once you have the lock, it's easy to release it.

    >>> lock.unlock()
    >>> lock.is_locked
    False

It is an error to attempt to acquire the lock more than once in the same
process.
::

    >>> from flufl.lock import AlreadyLockedError
    >>> lock.lock()
    >>> try:
    ...     lock.lock()
    ... except AlreadyLockedError as error:
    ...     print(error)
    We already had the lock

    >>> lock.unlock()

Lock objects also support the context manager protocol.

    >>> lock.is_locked
    False
    >>> with lock:
    ...     lock.is_locked
    True
    >>> lock.is_locked
    False


Lock acquisition blocks
=======================

When trying to lock the file when the lock is unavailable (because another
process has already acquired it), the lock call will block.
::

    >>> import time
    >>> t0 = time.time()

    # This function comes from the test infrastructure.
    >>> acquire(filename, timedelta(seconds=5))
    >>> lock.lock()
    >>> t1 = time.time()
    >>> lock.unlock()

    >>> t1 - t0 > 4
    True


Refreshing a lock
=================

A process can *refresh* a lock if it realizes that it needs to hold the lock
for a little longer.  You cannot refresh an unlocked lock.

    >>> from flufl.lock import NotLockedError
    >>> try:
    ...     lock.refresh()
    ... except NotLockedError as error:
    ...     print(error)
    <Lock ...

To refresh a lock, first acquire it with your best guess as to the length of
time you'll need it.

    >>> from datetime import datetime
    >>> lock.lifetime = timedelta(seconds=2)
    >>> lock.lock()
    >>> lock.is_locked
    True

After the current lifetime expires, the lock is stolen from the parent process
even if the parent never unlocks it.
::

    # This function comes from the test infrastructure.
    >>> t_broken = waitfor(filename, lock.lifetime)
    >>> lock.is_locked
    False

However, if the process holding the lock refreshes it, it will hold it can
hold it for as long as it needs.

    >>> lock.lock()
    >>> lock.refresh(timedelta(seconds=5))
    >>> t_broken = waitfor(filename, lock.lifetime)
    >>> lock.is_locked
    False


Lock details
============

Lock files are written with unique contents that can be queried for
information about the host name the lock was acquired on, the id of the
process that acquired the lock, and the path to the lock file.

    >>> import os
    >>> lock.lock()
    >>> hostname, pid, lockfile = lock.details
    >>> hostname == lock.hostname
    True
    >>> pid == os.getpid()
    True
    >>> lockfile == filename
    True
    >>> lock.unlock()

Even if another process has acquired the lock, the details can be queried.

    >>> acquire(filename, timedelta(seconds=3))
    >>> lock.is_locked
    False
    >>> hostname, pid, lockfile = lock.details
    >>> hostname == lock.hostname
    True
    >>> pid == os.getpid()
    False
    >>> lockfile == filename
    True

However, if no process has acquired the lock, the details are unavailable.

    >>> lock.lock()
    >>> lock.unlock()
    >>> try:
    ...     lock.details
    ... except NotLockedError as error:
    ...     print(error)
    Details are unavailable


Lock file separator
===================

Lock claim file names contain useful bits of information concatenated by a
*separator character*.  This character is the caret (``^``) by default on
Windows and the vertical bar (``|``) by default everywhere else.  You can
change this character.  There are some restrictions:

* It cannot be an alphanumeric;
* It cannot appear in the host machine's fully qualified domain name
  (e.g. the value of ``lock.hostname``);
* It cannot appear in the lock's file name (the argument passed to the
  ``Lock`` constructor)

It may also be helpful to avoid `any reserved characters
<https://en.wikipedia.org/wiki/Filename#Reserved_characters_and_words>`_ on
the file systems where you intend to run the code.

    >>> lock = Lock(filename, separator='+')
    >>> lock.lock()
    >>> hostname, pid, lockfile = lock.details
    >>> hostname == lock.hostname
    True
    >>> pid == os.getpid()
    True
    >>> lockfile == filename
    True
    >>> with open(filename) as fp:
    ...     claim_file = fp.read().strip()
    ...     '+' in claim_file
    True
    >>> lock.unlock()


.. _`open(2)`: http://manpages.ubuntu.com/manpages/dapper/en/man2/open.2.html
