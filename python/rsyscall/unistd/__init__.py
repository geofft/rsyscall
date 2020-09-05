"`#include <unistd.h>`"
from __future__ import annotations
from rsyscall._raw import ffi, lib # type: ignore
import enum
from rsyscall.struct import Serializer, FixedSerializer, Serializable
import struct
import typing as t
import rsyscall.near.types as near
import os
if t.TYPE_CHECKING:
    from rsyscall.handle import Pointer, Task, FileDescriptor
else:
    Pointer = t.Optional

__all__ = [
    "SEEK",
    "OK",
    "Arg",
    "ArgList",
    "Pipe",
]

# re-exported
from rsyscall.unistd.io import SEEK
from rsyscall.unistd.pipe import Pipe

class OK(enum.IntFlag):
    "The mode argument to access, faccessat."
    R = lib.R_OK
    W = lib.W_OK
    X = lib.X_OK
    F = lib.F_OK

class Arg(str, Serializable):
    "A null-terminated string, as passed to execve."
    def to_bytes(self) -> bytes:
        return os.fsencode(self) + b'\0'

    T = t.TypeVar('T', bound='Arg')
    @classmethod
    def from_bytes(cls: t.Type[T], data: bytes) -> T:
        try:
            nullidx = data.index(b'\0')
        except ValueError:
            return cls(os.fsdecode(data))
        else:
            return cls(os.fsdecode(data[0:nullidx]))

T_arglist = t.TypeVar('T_arglist', bound='ArgList')
class ArgList(t.List[Pointer[Arg]], FixedSerializer):
    "A null-terminated list of null-terminated strings, as passed to execve."
    @classmethod
    def get_serializer(cls, task: Task) -> Serializer[T_arglist]:
        return ArgListSerializer()

import struct
class ArgListSerializer(Serializer[T_arglist]):
    def to_bytes(self, arglist: T_arglist) -> bytes:
        ret = b""
        for ptr in arglist:
            ret += struct.Struct("Q").pack(int(ptr.near))
        ret += struct.Struct("Q").pack(0)
        return ret

    def from_bytes(self, data: bytes) -> T_arglist:
        raise Exception("can't get pointer handles from raw bytes")

#### Classes ####
from rsyscall.handle.fd import BaseFileDescriptor, FileDescriptorTask
from rsyscall.handle.pointer import Pointer, WrittenPointer
from rsyscall.path import Path, EmptyPath

from rsyscall.fcntl import AT, O
T_fd = t.TypeVar('T_fd', bound='FSFileDescriptor')
class FSFileDescriptor(BaseFileDescriptor):
    async def readlinkat(self, path: t.Union[WrittenPointer[Path], WrittenPointer[EmptyPath]],
                         buf: Pointer) -> t.Tuple[Pointer, Pointer]:
        self._validate()
        with path.borrow(self.task):
            with buf.borrow(self.task):
                ret = await _readlinkat(self.task.sysif, self.near, path.near, buf.near, buf.size())
                return buf.split(ret)

    async def faccessat(self, ptr: WrittenPointer[Path], mode: OK, flags: AT=AT.NONE) -> None:
        self._validate()
        with ptr.borrow(self.task):
            await _faccessat(self.task.sysif, self.near, ptr.near, mode, flags)

    async def openat(self: T_fd, path: WrittenPointer[Path], flags: O, mode=0o644) -> T_fd:
        self._validate()
        with path.borrow(self.task) as path_n:
            fd = await _openat(self.task.sysif, self.near, path_n, flags|O.CLOEXEC, mode)
            return self.task.make_fd_handle(fd)

    async def fchmod(self, mode: int) -> None:
        self._validate()
        await _fchmod(self.task.sysif, self.near, mode)

    async def ftruncate(self, length: int) -> None:
        self._validate()
        await _ftruncate(self.task.sysif, self.near, length)

class FSTask(t.Generic[T_fd], FileDescriptorTask[T_fd]):
    async def readlink(self, path: WrittenPointer[Path], buf: Pointer) -> t.Tuple[Pointer, Pointer]:
        with path.borrow(self) as path_n:
            with buf.borrow(self) as buf_n:
                ret = await _readlinkat(self.sysif, None, path_n, buf_n, buf.size())
                return buf.split(ret)

    async def access(self, path: WrittenPointer[Path], mode: int, flags: int=0) -> None:
        with path.borrow(self) as path_n:
            try:
                await _faccessat(self.sysif, None, path_n, mode, flags)
            except FileNotFoundError as exn:
                exn.filename = path.value
                raise

    async def open(self, path: WrittenPointer[Path], flags: O, mode=0o644) -> T_fd:
        with path.borrow(self) as path_n:
            try:
                fd = await _openat(self.sysif, None, path_n, flags|O.CLOEXEC, mode)
            except FileNotFoundError as exn:
                exn.filename = path.value
                raise
            return self.make_fd_handle(fd)

    async def mkdir(self, path: WrittenPointer[Path], mode=0o755) -> None:
        with path.borrow(self) as path_n:
            await _mkdirat(self.sysif, None, path_n, mode)

    async def unlink(self, path: WrittenPointer[Path]) -> None:
        with path.borrow(self) as path_n:
            await _unlinkat(self.sysif, None, path_n, 0)

    async def rmdir(self, path: WrittenPointer[Path]) -> None:
        with path.borrow(self) as path_n:
            await _unlinkat(self.sysif, None, path_n, AT.REMOVEDIR)

    async def link(self, oldpath: WrittenPointer[Path], newpath: WrittenPointer[Path]) -> None:
        with oldpath.borrow(self) as oldpath_n:
            with newpath.borrow(self) as newpath_n:
                await _linkat(self.sysif, None, oldpath_n, None, newpath_n, 0)

    async def rename(self, oldpath: WrittenPointer[Path], newpath: WrittenPointer[Path]) -> None:
        with oldpath.borrow(self) as oldpath_n:
            with newpath.borrow(self) as newpath_n:
                await _renameat2(self.sysif, None, oldpath_n, None, newpath_n, 0)

    async def symlink(self, target: WrittenPointer, linkpath: WrittenPointer[Path]) -> None:
        with target.borrow(self) as target_n:
            with linkpath.borrow(self) as linkpath_n:
                await _symlinkat(self.sysif, target_n, None, linkpath_n)

#### Raw syscalls ####
import rsyscall.near.types as near
from rsyscall.near.sysif import SyscallInterface
from rsyscall.sys.syscall import SYS

async def _readlinkat(sysif: SyscallInterface, dirfd: t.Optional[near.FileDescriptor],
                      path: near.Address, buf: near.Address, bufsiz: int) -> int:
    if dirfd is None:
        dirfd = AT.FDCWD # type: ignore
    return (await sysif.syscall(SYS.readlinkat, dirfd, path, buf, bufsiz))

async def _faccessat(sysif: SyscallInterface, dirfd: t.Optional[near.FileDescriptor],
                     path: near.Address, flags: int, mode: int) -> None:
    if dirfd is None:
        dirfd = AT.FDCWD # type: ignore
    await sysif.syscall(SYS.faccessat, dirfd, path, flags, mode)

async def _openat(sysif: SyscallInterface, dirfd: t.Optional[near.FileDescriptor],
                  path: near.Address, flags: int, mode: int) -> near.FileDescriptor:
    if dirfd is None:
        dirfd = AT.FDCWD # type: ignore
    return near.FileDescriptor(await sysif.syscall(SYS.openat, dirfd, path, flags, mode))

async def _fchmod(sysif: SyscallInterface, fd: near.FileDescriptor, mode: int) -> None:
    await sysif.syscall(SYS.fchmod, fd, mode)

async def _ftruncate(sysif: SyscallInterface, fd: near.FileDescriptor, length: int) -> None:
    await sysif.syscall(SYS.ftruncate, fd, length)

async def _mkdirat(sysif: SyscallInterface,
                   dirfd: t.Optional[near.FileDescriptor], path: near.Address, mode: int) -> None:
    if dirfd is None:
        dirfd = AT.FDCWD # type: ignore
    await sysif.syscall(SYS.mkdirat, dirfd, path, mode)

async def _unlinkat(sysif: SyscallInterface,
                    dirfd: t.Optional[near.FileDescriptor], path: near.Address, flags: int) -> None:
    if dirfd is None:
        dirfd = AT.FDCWD # type: ignore
    await sysif.syscall(SYS.unlinkat, dirfd, path, flags)

async def _linkat(sysif: SyscallInterface,
                  olddirfd: t.Optional[near.FileDescriptor], oldpath: near.Address,
                  newdirfd: t.Optional[near.FileDescriptor], newpath: near.Address,
                  flags: int) -> None:
    if olddirfd is None:
        olddirfd = AT.FDCWD # type: ignore
    if newdirfd is None:
        newdirfd = AT.FDCWD # type: ignore
    await sysif.syscall(SYS.linkat, olddirfd, oldpath, newdirfd, newpath, flags)

async def _renameat2(sysif: SyscallInterface,
                     olddirfd: t.Optional[near.FileDescriptor], oldpath: near.Address,
                     newdirfd: t.Optional[near.FileDescriptor], newpath: near.Address,
                     flags: int) -> None:
    if olddirfd is None:
        olddirfd = AT.FDCWD # type: ignore
    if newdirfd is None:
        newdirfd = AT.FDCWD # type: ignore
    await sysif.syscall(SYS.renameat2, olddirfd, oldpath, newdirfd, newpath, flags)

async def _symlinkat(sysif: SyscallInterface,
                     target: near.Address, newdirfd: t.Optional[near.FileDescriptor], linkpath: near.Address) -> None:
    if newdirfd is None:
        newdirfd = AT.FDCWD # type: ignore
    await sysif.syscall(SYS.symlinkat, target, newdirfd, linkpath)
