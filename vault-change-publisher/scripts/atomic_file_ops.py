#!/usr/bin/env python3
"""Provide descriptor-relative atomic filesystem operations used by publication."""

from __future__ import annotations

import ctypes
import errno
import os
import sys


def _rename_function() -> tuple[object, int]:
    """Return the platform no-replace rename function and flag."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        name = "renameatx_np"
        flag = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        name = "renameat2"
        flag = 0x00000001  # RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    function = getattr(libc, name, None)
    if function is None:
        raise OSError(errno.ENOSYS, "atomic no-replace rename is unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    return function, flag


def rename_no_replace(
    source_directory_fd: int,
    source_name: str,
    destination_directory_fd: int,
    destination_name: str,
) -> None:
    """Atomically rename any entry type without replacing the destination."""
    function, flag = _rename_function()
    result = function(
        source_directory_fd,
        os.fsencode(source_name),
        destination_directory_fd,
        os.fsencode(destination_name),
        flag,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            destination_name,
        )


def verify_rename_no_replace(directory_fd: int) -> None:
    """Verify no-replace support inside a private directory before moving data."""
    source_name = ".rename-no-replace-source"
    destination_name = ".rename-no-replace-destination"
    descriptor = os.open(
        source_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    os.close(descriptor)
    current_name = source_name
    try:
        rename_no_replace(
            directory_fd,
            source_name,
            directory_fd,
            destination_name,
        )
        current_name = destination_name
    finally:
        try:
            os.unlink(current_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
