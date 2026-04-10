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
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

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

kernel32 = ctypes.windll.kernel32

# CreateFileW
kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    ctypes.wintypes.LPCWSTR,  # lpFileName
    ctypes.wintypes.DWORD,    # dwDesiredAccess
    ctypes.wintypes.DWORD,    # dwShareMode
    ctypes.c_void_p,          # lpSecurityAttributes
    ctypes.wintypes.DWORD,    # dwCreationDisposition
    ctypes.wintypes.DWORD,    # dwFlagsAndAttributes
    ctypes.wintypes.HANDLE,   # hTemplateFile
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
    ctypes.wintypes.DWORD,    # dwReplaceFlags
    ctypes.c_void_p,          # lpExclude (reserved)
    ctypes.c_void_p,          # lpReserved
]

# MoveFileExW
kernel32.MoveFileExW.restype = ctypes.wintypes.BOOL
kernel32.MoveFileExW.argtypes = [
    ctypes.wintypes.LPCWSTR,  # lpExistingFileName
    ctypes.wintypes.LPCWSTR,  # lpNewFileName
    ctypes.wintypes.DWORD,    # dwFlags
]

# GetFileAttributesW
kernel32.GetFileAttributesW.restype = ctypes.wintypes.DWORD
kernel32.GetFileAttributesW.argtypes = [ctypes.wintypes.LPCWSTR]

# GetLastError
kernel32.GetLastError.restype = ctypes.wintypes.DWORD

# LockFileEx / UnlockFileEx
kernel32.LockFileEx.restype = ctypes.wintypes.BOOL
kernel32.LockFileEx.argtypes = [
    ctypes.wintypes.HANDLE,    # hFile
    ctypes.wintypes.DWORD,     # dwFlags
    ctypes.wintypes.DWORD,     # dwReserved
    ctypes.wintypes.DWORD,     # nNumberOfBytesToLockLow
    ctypes.wintypes.DWORD,     # nNumberOfBytesToLockHigh
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
    ) -> Optional[Path]:
        """
        Atomically replace target with replacement using Win32 ReplaceFileW.

        If backup is True, creates a backup of the original file.
        Returns the backup path if created, None otherwise.

        Falls back to MoveFileExW if ReplaceFileW fails (e.g., target
        doesn't exist yet).
        """
        target_norm = Win32IO.normalize_path(target)
        replacement_norm = Win32IO.normalize_path(replacement)

        backup_path = None
        backup_norm = None

        if backup:
            backup_path = Path(str(target) + backup_suffix)
            backup_norm = Win32IO.normalize_path(backup_path)

        # Try ReplaceFileW first (preserves ACLs, timestamps)
        if Path(str(target)).exists():
            success = kernel32.ReplaceFileW(
                target_norm,
                replacement_norm,
                backup_norm,
                REPLACEFILE_IGNORE_MERGE_ERRORS,
                None,
                None,
            )

            if success:
                logger.debug("Atomic replace succeeded: %s → %s", replacement, target)
                return backup_path

            error_code = kernel32.GetLastError()
            logger.warning(
                "ReplaceFileW failed (error %d), falling back to MoveFileExW",
                error_code,
            )

        # Fallback: MoveFileExW (for new files or when ReplaceFileW fails)
        if backup and Path(str(target)).exists():
            # Manually create backup first
            try:
                shutil.copy2(str(target), str(backup_path))
            except OSError as e:
                logger.warning("Backup creation failed: %s", e)
                backup_path = None

        success = kernel32.MoveFileExW(
            replacement_norm,
            target_norm,
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )

        if not success:
            error_code = kernel32.GetLastError()
            raise AtomicReplaceError(
                f"Failed to replace {target} with {replacement}",
                error_code,
            )

        logger.debug("MoveFileExW replace succeeded: %s → %s", replacement, target)
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
        norm_path = Win32IO.normalize_path(path)
        access = GENERIC_READ_WRITE if write else GENERIC_READ
        share_mode = FILE_SHARE_READ if shared else FILE_SHARE_NONE

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


def verify_bitstream_integrity(
    original_path: str | Path,
    modified_path: str | Path,
    essence_offset: int,
    essence_length: int,
    algorithm: str = "xxhash",
    chunk_size: int = 8 * 1024 * 1024,
) -> bool:
    """
    Verify that the essence (bitstream) data has not been altered.

    Compares hashes of the essence region in original and modified files.
    This is the core safety check ensuring we never re-encode.
    """
    def hash_region(path, offset, length):
        if algorithm == "xxhash":
            try:
                import xxhash
                hasher = xxhash.xxh3_128()
            except ImportError:
                hasher = hashlib.sha256()
        else:
            hasher = hashlib.sha256()

        with open(path, "rb") as f:
            f.seek(offset)
            remaining = length
            while remaining > 0:
                to_read = min(chunk_size, remaining)
                chunk = f.read(to_read)
                if not chunk:
                    break
                hasher.update(chunk)
                remaining -= len(chunk)

        return hasher.hexdigest()

    original_hash = hash_region(original_path, essence_offset, essence_length)
    modified_hash = hash_region(modified_path, essence_offset, essence_length)

    match = original_hash == modified_hash
    if not match:
        logger.error(
            "BITSTREAM INTEGRITY FAILURE: %s vs %s",
            original_hash,
            modified_hash,
        )
    return match


class StagedTransaction:
    """
    Implements the Stage-Verify-Commit workflow for safe file modification.

    Usage:
        with StagedTransaction(target_path) as txn:
            txn.write_staged(modified_data)
            # Verify is automatic on commit
        # File is atomically replaced on successful exit
    """

    def __init__(
        self,
        target_path: str | Path,
        backup: bool = True,
        verify_essence: bool = True,
        hash_algorithm: str = "xxhash",
    ):
        self.target_path = Path(target_path)
        self.backup = backup
        self.verify_essence = verify_essence
        self.hash_algorithm = hash_algorithm
        self.staging_path: Optional[Path] = None
        self.backup_path: Optional[Path] = None
        self.original_hash: Optional[str] = None
        self.essence_offset: Optional[int] = None
        self.essence_length: Optional[int] = None
        self._committed = False

    def __enter__(self):
        # Hash the original file
        self.original_hash = compute_file_hash(
            self.target_path,
            self.hash_algorithm,
        )
        # Create staging file
        self.staging_path = Win32IO.create_staging_file(self.target_path)
        logger.debug("Staged transaction: %s → %s", self.target_path, self.staging_path)
        return self

    def write_staged(self, data: bytes):
        """Write data to the staging file."""
        with open(self.staging_path, "wb") as f:
            f.write(data)

    def copy_to_staging(self):
        """Copy the original file to staging for in-place modification."""
        shutil.copy2(str(self.target_path), str(self.staging_path))

    def set_essence_region(self, offset: int, length: int):
        """Set the essence (bitstream) region for integrity verification."""
        self.essence_offset = offset
        self.essence_length = length

    def verify(self) -> bool:
        """
        Verify the staged file's essence matches the original.

        Returns True if verification passes or is not applicable.
        """
        if not self.verify_essence:
            return True

        if self.essence_offset is None or self.essence_length is None:
            logger.warning("No essence region set; skipping bitstream verification")
            return True

        return verify_bitstream_integrity(
            self.target_path,
            self.staging_path,
            self.essence_offset,
            self.essence_length,
            self.hash_algorithm,
        )

    def commit(self) -> Optional[Path]:
        """
        Commit the staged file by atomically replacing the target.

        Returns the backup path if created.

        Raises:
            AtomicReplaceError: If replacement fails
            RuntimeError: If bitstream verification fails
        """
        if not self.verify():
            # Clean up staging file
            self.staging_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Bitstream verification FAILED for {self.target_path}. "
                "Staged file has been deleted to prevent data corruption."
            )

        self.backup_path = Win32IO.atomic_replace(
            self.target_path,
            self.staging_path,
            backup=self.backup,
        )
        self._committed = True
        logger.info("Committed: %s", self.target_path)
        return self.backup_path

    def rollback(self):
        """Clean up staging file without committing."""
        if self.staging_path and self.staging_path.exists():
            self.staging_path.unlink(missing_ok=True)
            logger.debug("Rolled back: %s", self.staging_path)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
            return False

        if not self._committed:
            try:
                self.commit()
            except Exception:
                self.rollback()
                raise

        return False
