r"""
Apex Meta-Injector — Win32 I/O Handler.

Provides Windows-specific file operations using the Win32 API:
- Long path support (\\?\ prefix for paths > MAX_PATH)
- File locking via LockFileEx / UnlockFileEx
- Atomic file replacement via ReplaceFileW
- Staging file creation on the same volume
- File permission and lock detection

All Win32 calls use ctypes to avoid C++ build dependencies.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import logging
import os
import shutil
import tempfile
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Win32 Constants
# ─────────────────────────────────────────────────────────────────────

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
GENERIC_READ_WRITE = GENERIC_READ | GENERIC_WRITE

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
FILE_SHARE_NONE = 0x00000000

CREATE_NEW = 1
CREATE_ALWAYS = 2
OPEN_EXISTING = 3
OPEN_ALWAYS = 4

FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_READONLY = 0x01
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000

INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value

LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
LOCKFILE_FAIL_IMMEDIATELY = 0x00000001

MOVEFILE_REPLACE_EXISTING = 0x00000001
MOVEFILE_WRITE_THROUGH = 0x00000008

REPLACEFILE_IGNORE_MERGE_ERRORS = 0x00000002

ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
ERROR_ACCESS_DENIED = 5

# ─────────────────────────────────────────────────────────────────────
# Win32 Structures
# ─────────────────────────────────────────────────────────────────────


class OVERLAPPED(ctypes.Structure):
    """Win32 OVERLAPPED structure for async I/O and file locking."""

    _fields_ = [
        ("Internal", ctypes.POINTER(ctypes.c_ulong)),
        ("InternalHigh", ctypes.POINTER(ctypes.c_ulong)),
        ("Offset", ctypes.wintypes.DWORD),
        ("OffsetHigh", ctypes.wintypes.DWORD),
        ("hEvent", ctypes.wintypes.HANDLE),
    ]


# ─────────────────────────────────────────────────────────────────────
# Win32 API Bindings
# ─────────────────────────────────────────────────────────────────────

kernel32 = None
if os.name == "nt":
    kernel32 = ctypes.windll.kernel32

    # CreateFileW
    kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        ctypes.wintypes.LPCWSTR,  # lpFileName
        ctypes.wintypes.DWORD,  # dwDesiredAccess
        ctypes.wintypes.DWORD,  # dwShareMode
        ctypes.c_void_p,  # lpSecurityAttributes
        ctypes.wintypes.DWORD,  # dwCreationDisposition
        ctypes.wintypes.DWORD,  # dwFlagsAndAttributes
        ctypes.wintypes.HANDLE,  # hTemplateFile
    ]

    # CloseHandle
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

    # ReplaceFileW
    kernel32.ReplaceFileW.restype = ctypes.wintypes.BOOL
    kernel32.ReplaceFileW.argtypes = [
        ctypes.wintypes.LPCWSTR,  # lpReplacedFileName
        ctypes.wintypes.LPCWSTR,  # lpReplacementFileName
        ctypes.wintypes.LPCWSTR,  # lpBackupFileName (can be NULL)
        ctypes.wintypes.DWORD,  # dwReplaceFlags
        ctypes.c_void_p,  # lpExclude (reserved)
        ctypes.c_void_p,  # lpReserved
    ]

    # MoveFileExW
    kernel32.MoveFileExW.restype = ctypes.wintypes.BOOL
    kernel32.MoveFileExW.argtypes = [
        ctypes.wintypes.LPCWSTR,  # lpExistingFileName
        ctypes.wintypes.LPCWSTR,  # lpNewFileName
        ctypes.wintypes.DWORD,  # dwFlags
    ]

    # GetFileAttributesW
    kernel32.GetFileAttributesW.restype = ctypes.wintypes.DWORD
    kernel32.GetFileAttributesW.argtypes = [ctypes.wintypes.LPCWSTR]

    # GetLastError
    kernel32.GetLastError.restype = ctypes.wintypes.DWORD

    # LockFileEx / UnlockFileEx
    kernel32.LockFileEx.restype = ctypes.wintypes.BOOL
    kernel32.LockFileEx.argtypes = [
        ctypes.wintypes.HANDLE,  # hFile
        ctypes.wintypes.DWORD,  # dwFlags
        ctypes.wintypes.DWORD,  # dwReserved
        ctypes.wintypes.DWORD,  # nNumberOfBytesToLockLow
        ctypes.wintypes.DWORD,  # nNumberOfBytesToLockHigh
        ctypes.POINTER(OVERLAPPED),  # lpOverlapped
    ]

    kernel32.UnlockFileEx.restype = ctypes.wintypes.BOOL
    kernel32.UnlockFileEx.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(OVERLAPPED),
    ]

    # GetFileSizeEx
    kernel32.GetFileSizeEx.restype = ctypes.wintypes.BOOL
    kernel32.GetFileSizeEx.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.LARGE_INTEGER),
    ]


# ─────────────────────────────────────────────────────────────────────
# Custom Exceptions
# ─────────────────────────────────────────────────────────────────────


class Win32IOError(OSError):
    """Base exception for Win32 I/O operations."""

    def __init__(self, message: str, error_code: int = 0):
        self.error_code = error_code
        super().__init__(f"{message} (Win32 error {error_code})")


class FileInUseError(Win32IOError):
    """File is locked by another process."""

    pass


class FilePermissionError(Win32IOError):
    """Insufficient permissions to access file."""

    pass


class AtomicReplaceError(Win32IOError):
    """Failed to atomically replace a file."""

    pass


# ─────────────────────────────────────────────────────────────────────
# Win32IO Class
# ─────────────────────────────────────────────────────────────────────


class Win32IO:
    """
    Windows-specific file I/O operations using Win32 API.

    Provides long path support, file locking, atomic replacement,
    and permission checking — all via ctypes to kernel32.
    """

    @staticmethod
    def normalize_path(path: str | Path) -> str:
        r"""
        Normalize a path for Win32 API calls.

        Prepends the \\?\ extended-length prefix for paths that
        approach or exceed MAX_PATH (260 chars). This allows paths
        up to ~32,767 characters.
        """
        path_str = str(Path(path).resolve())

        # Already has the prefix
        if path_str.startswith("\\\\?\\"):
            return path_str

        # UNC paths: \\server\share → \\?\UNC\server\share
        if path_str.startswith("\\\\"):
            return "\\\\?\\UNC\\" + path_str[2:]

        # Regular paths: prepend \\?\
        if len(path_str) > 240 or True:  # Always use for safety
            return "\\\\?\\" + path_str

        return path_str

    @staticmethod
    def is_file_locked(path: str | Path) -> bool:
        """
        Check if a file is locked by another process.

        Attempts to open the file with exclusive access. If it fails
        with a sharing violation, the file is in use.
        """
        if kernel32 is None:
            return False
        norm_path = Win32IO.normalize_path(path)

        handle = kernel32.CreateFileW(
            norm_path,
            GENERIC_READ_WRITE,
            FILE_SHARE_NONE,  # Exclusive — no sharing
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )

        if handle == INVALID_HANDLE_VALUE:
            error_code = kernel32.GetLastError()
            return error_code in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION)

        kernel32.CloseHandle(handle)
        return False

    @staticmethod
    def check_write_access(path: str | Path) -> bool:
        """
        Check if the file is writable.

        Verifies the file is not read-only and can be opened for writing.
        """
        if kernel32 is None:
            return os.access(path, os.W_OK)
        norm_path = Win32IO.normalize_path(path)

        # Check read-only attribute
        attrs = kernel32.GetFileAttributesW(norm_path)
        if attrs == 0xFFFFFFFF:  # INVALID_FILE_ATTRIBUTES
            return False

        if attrs & FILE_ATTRIBUTE_READONLY:
            return False

        # Try opening for write with shared access
        handle = kernel32.CreateFileW(
            norm_path,
            GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )

        if handle == INVALID_HANDLE_VALUE:
            return False

        kernel32.CloseHandle(handle)
        return True

    @staticmethod
    def create_staging_file(target_path: str | Path, suffix: str = ".apex_tmp") -> Path:
        """
        Create a staging (temp) file in the same directory as the target.

        This ensures the staging file is on the same volume as the target,
        which is required for atomic replace operations.
        """
        target = Path(target_path)
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(
            suffix=suffix,
            prefix=f".{target.stem}_",
            dir=str(parent),
        )
        os.close(fd)
        return Path(tmp_path)

    @staticmethod
    def atomic_replace(
        target: str | Path,
        replacement: str | Path,
        backup: bool = True,
        backup_suffix: str = ".apex_backup",
    ) -> Path | None:
        """
        Atomically replace target with replacement using Win32 ReplaceFileW.

        If backup is True, creates a backup of the original file.
        Returns the backup path if created, None otherwise.

        Backup creation must succeed before replacement. Existing backups are
        retained, and a failed replacement is never retried with a weaker API.
        """
        target = Path(target)
        replacement = Path(replacement)
        backup_path = None
        if backup:
            # Never overwrite an earlier recovery copy. A failed backup aborts commit.
            import uuid

            backup_path = Path(str(target) + backup_suffix)
            if backup_path.exists():
                backup_path = Path(str(backup_path) + "." + uuid.uuid4().hex)
            created = False
            try:
                with open(target, "rb") as src, open(backup_path, "xb") as dst:
                    created = True
                    shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                shutil.copystat(target, backup_path)
            except Exception:
                if created:
                    backup_path.unlink(missing_ok=True)
                raise
        if kernel32 is None:
            os.replace(replacement, target)
        else:
            success = kernel32.ReplaceFileW(
                Win32IO.normalize_path(target),
                Win32IO.normalize_path(replacement),
                None,
                0,
                None,
                None,
            )
            if not success:
                code = kernel32.GetLastError()
                if code in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
                    raise FileInUseError("Target is in use", code)
                # Do not fall back to a less strict operation on an existing target.
                raise AtomicReplaceError(f"Failed to replace {target}; backup: {backup_path}", code)
        return backup_path

    @staticmethod
    @contextmanager
    def locked_file(
        path: str | Path,
        shared: bool = False,
        write: bool = False,
    ) -> Generator:
        """
        Context manager that opens a file with Win32 file locking.

        Parameters:
            path: File path to lock
            shared: If True, acquire a shared (read) lock. If False, exclusive lock.
            write: If True, open for read/write access.

        Yields:
            The file handle (Python file object)

        Raises:
            FileInUseError: If the file is locked by another process
            FilePermissionError: If insufficient permissions
        """
        if kernel32 is None:
            import fcntl

            with open(path, "r+b" if write else "rb") as f:
                try:
                    fcntl.flock(f, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                except OSError as e:
                    raise FileInUseError(f"File is locked: {path}") from e
                try:
                    yield f
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            return
        norm_path = Win32IO.normalize_path(path)
        access = GENERIC_READ_WRITE if write else GENERIC_READ
        share_mode = FILE_SHARE_READ | FILE_SHARE_DELETE if shared else FILE_SHARE_NONE

        handle = kernel32.CreateFileW(
            norm_path,
            access,
            share_mode,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN,
            None,
        )

        if handle == INVALID_HANDLE_VALUE:
            error_code = kernel32.GetLastError()
            if error_code in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
                raise FileInUseError(f"File is locked: {path}", error_code)
            elif error_code == ERROR_ACCESS_DENIED:
                raise FilePermissionError(f"Access denied: {path}", error_code)
            else:
                raise Win32IOError(f"Cannot open file: {path}", error_code)

        # Apply advisory lock
        overlapped = OVERLAPPED()
        lock_flags = 0 if shared else LOCKFILE_EXCLUSIVE_LOCK
        lock_flags |= LOCKFILE_FAIL_IMMEDIATELY

        locked = kernel32.LockFileEx(
            handle,
            lock_flags,
            0,
            0xFFFFFFFF,  # Lock entire file
            0xFFFFFFFF,
            ctypes.byref(overlapped),
        )

        if not locked:
            error_code = kernel32.GetLastError()
            kernel32.CloseHandle(handle)
            raise FileInUseError(f"Cannot acquire lock on: {path}", error_code)

        try:
            # Convert Win32 handle to Python file descriptor
            import msvcrt

            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY if not write else os.O_RDWR)
            mode = "r+b" if write else "rb"
            f = os.fdopen(fd, mode, closefd=False)
            try:
                yield f
            finally:
                f.flush() if write else None
                f.close()
        finally:
            # Release lock and close handle
            kernel32.UnlockFileEx(
                handle,
                0,
                0xFFFFFFFF,
                0xFFFFFFFF,
                ctypes.byref(overlapped),
            )
            if "fd" in locals():
                os.close(fd)
            else:
                kernel32.CloseHandle(handle)


# ─────────────────────────────────────────────────────────────────────
# Hash Utilities
# ─────────────────────────────────────────────────────────────────────


def compute_file_hash(
    path: str | Path,
    algorithm: str = "xxhash",
    chunk_size: int = 8 * 1024 * 1024,  # 8MB chunks for NVMe throughput
) -> str:
    """
    Compute a hash of a file for integrity verification.

    Uses xxhash by default for speed, sha256 for maximum safety.
    """
    if algorithm == "xxhash":
        try:
            import xxhash

            hasher = xxhash.xxh3_128()
        except ImportError:
            logger.warning("xxhash not available, falling back to sha256")
            hasher = hashlib.sha256()
    elif algorithm == "sha256":
        hasher = hashlib.sha256()
    else:
        hasher = hashlib.new(algorithm)

    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)

    return hasher.hexdigest()


def hash_regions(path, regions, algorithm=None):
    """Hash bounded, validated regions, including their lengths and order."""
    import struct

    if algorithm is None:
        from apex_injector.config import get_config

        algorithm = get_config().engine.hash_algorithm
    hasher = hashlib.sha256()
    if algorithm == "xxhash":
        import xxhash

        hasher = xxhash.xxh3_128()
    size = Path(path).stat().st_size
    if not regions:
        raise ValueError("No essence regions found")
    with open(path, "rb") as f:
        for offset, length in regions:
            if offset < 0 or length <= 0 or offset + length > size:
                raise ValueError("Invalid or truncated essence region")
            hasher.update(struct.pack(">Q", length))
            f.seek(offset)
            remaining = length
            while remaining:
                data = f.read(min(8 * 1024 * 1024, remaining))
                if not data:
                    raise ValueError("Truncated essence")
                hasher.update(data)
                remaining -= len(data)
    return hasher.hexdigest()


def verify_bitstream_integrity(
    original_path, modified_path, essence_offset, essence_length, algorithm="xxhash", chunk_size=8 * 1024 * 1024
):
    regions = [(essence_offset, essence_length)]
    return hash_regions(original_path, regions, algorithm) == hash_regions(modified_path, regions, algorithm)


class VerificationError(RuntimeError):
    pass


@contextmanager
def transaction_lock(path):
    """Cross-process lock by canonical path, held through replacement.

    Persistent lock files live in the system temporary directory: deleting a lock
    file would let another process acquire a different inode for the same path.
    """
    name = hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode()).hexdigest()
    directory = Path(tempfile.gettempdir()) / "apex-meta-injector-locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    with open(directory / name, "a+b") as f:
        if os.name == "nt":
            import msvcrt

            if f.tell() == 0:
                f.write(b"0")
                f.flush()
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e:
                raise FileInUseError(f"Another injection is running: {path}") from e
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                raise FileInUseError(f"Another injection is running: {path}") from e
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


class StagedTransaction:
    """Copy/edit/validate/commit transaction with explicit terminal states."""

    def __init__(
        self,
        target_path,
        backup=True,
        verify_essence=True,
        hash_algorithm="xxhash",
        fingerprint=None,
        validate=None,
        backup_suffix=".apex_backup",
        staging_suffix=".apex_tmp",
    ):
        self.target_path = Path(target_path).resolve()
        self.backup = backup
        self.verify_essence = verify_essence
        self.hash_algorithm = hash_algorithm
        self.fingerprint = fingerprint
        self.validate = validate
        self.backup_suffix = backup_suffix
        self.staging_suffix = staging_suffix
        self.staging_path = None
        self.backup_path = None
        self.original_hash = None
        self.essence_offset = None
        self.essence_length = None
        self._state = "new"
        self._stack = ExitStack()

    def __enter__(self):
        try:
            self._stack.enter_context(transaction_lock(self.target_path))
            self._stack.enter_context(Win32IO.locked_file(self.target_path, shared=True))
            self._identity = self.target_path.stat()
            if self.validate:
                self.validate(self.target_path)
            self.original_hash = compute_file_hash(self.target_path, self.hash_algorithm)
            self._essence_hash = (
                self.fingerprint(self.target_path) if self.verify_essence and self.fingerprint else None
            )
            self.staging_path = Win32IO.create_staging_file(self.target_path, self.staging_suffix)
            self._state = "staged"
            return self
        except Exception:
            self._stack.close()
            raise

    def write_staged(self, data):
        self.staging_path.write_bytes(data)

    def copy_to_staging(self):
        shutil.copy2(self.target_path, self.staging_path)

    def set_essence_region(self, offset, length):
        self.essence_offset, self.essence_length = offset, length
        self._essence_hash = hash_regions(self.target_path, [(offset, length)], self.hash_algorithm)

    def verify(self):
        if self.validate:
            self.validate(self.staging_path)
        if not self.verify_essence:
            return True
        if self.fingerprint:
            return self._essence_hash == self.fingerprint(self.staging_path)
        if self.essence_offset is None or self.essence_length is None:
            raise VerificationError("No essence verifier configured; refusing commit")
        return self._essence_hash == hash_regions(
            self.staging_path, [(self.essence_offset, self.essence_length)], self.hash_algorithm
        )

    def commit(self):
        if self._state != "staged":
            raise RuntimeError(f"Cannot commit transaction in state {self._state}")
        if not self.verify():
            raise VerificationError("Bitstream verification failed; original preserved")
        current = self.target_path.stat()
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
            self._identity.st_dev,
            self._identity.st_ino,
            self._identity.st_size,
            self._identity.st_mtime_ns,
        ) or compute_file_hash(self.target_path, self.hash_algorithm) != self.original_hash:
            raise VerificationError("Source changed during injection; refusing to overwrite it")
        with open(self.staging_path, "r+b") as f:
            f.flush()
            os.fsync(f.fileno())
        self.backup_path = Win32IO.atomic_replace(
            self.target_path, self.staging_path, backup=self.backup, backup_suffix=self.backup_suffix
        )
        self._state = "committed"
        return self.backup_path

    def rollback(self):
        if self._state == "committed":
            return
        if self.staging_path:
            self.staging_path.unlink(missing_ok=True)
        self._state = "aborted"

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                self.rollback()
            elif self._state == "staged":
                try:
                    self.commit()
                except Exception:
                    self.rollback()
                    raise
        finally:
            self._stack.close()
        return False
