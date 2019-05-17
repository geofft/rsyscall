from __future__ import annotations

from rsyscall.trio_test_case import TrioTestCase
import rsyscall.io
from rsyscall.nix import local_store
from rsyscall.misc import bash_nixdep, coreutils_nixdep
from rsyscall.struct import Bytes
from rsyscall.tasks.ssh import *
import rsyscall.tasks.local as local

from rsyscall.unistd import SEEK
from rsyscall.signal import Sigset, HowSIG
from rsyscall.sys.memfd import MFD

import rsyscall.handle as handle
from rsyscall.path import Path
from rsyscall.io import StandardTask, Command
from rsyscall.monitor import AsyncChildProcess

# import logging
# logging.basicConfig(level=logging.DEBUG)

async def start_cat(stdtask: StandardTask, cat: Command,
                    stdin: handle.FileDescriptor, stdout: handle.FileDescriptor) -> AsyncChildProcess:
    thread = await stdtask.fork()
    await thread.unshare_files_and_replace({
        thread.stdin: stdin,
        thread.stdout: stdout,
    }, going_to_exec=True)
    child = await thread.exec(cat)
    return child

class TestSSH(TrioTestCase):
    async def asyncSetUp(self) -> None:
        self.local = local.thread
        self.store = local_store
        self.host = await make_local_ssh(self.local, self.store)
        self.local_child, self.remote = await self.host.ssh(self.local)

    async def asyncTearDown(self) -> None:
        await self.local_child.kill()

    async def test_read(self) -> None:
        [(local_sock, remote_sock)] = await self.remote.open_channels(1)
        data = Bytes(b"hello world")
        await local_sock.write(await self.local.ram.to_pointer(data))
        valid, _ = await remote_sock.read(await self.remote.ram.malloc_type(Bytes, len(data)))
        self.assertEqual(len(data), valid.bytesize())
        self.assertEqual(data, await valid.read())

    async def test_exec_true(self) -> None:
        bash = await self.store.bin(bash_nixdep, "bash")
        await self.remote.run(bash.args('-c', 'true'))

    async def test_exec_pipe(self) -> None:
        [(local_sock, remote_sock)] = await self.remote.open_channels(1)
        cat = await self.store.bin(coreutils_nixdep, "cat")
        thread = await self.remote.fork()
        await thread.unshare_files_and_replace({
            thread.stdin: remote_sock,
            thread.stdout: remote_sock,
        })
        child_process = await thread.exec(cat)

        in_data = await self.local.ram.to_pointer(Bytes(b"hello"))
        written, _ = await local_sock.write(in_data)
        valid, _ = await local_sock.read(written)
        self.assertEqual(in_data.value, await valid.read())

    async def test_fork(self) -> None:
        thread1 = await self.remote.fork()
        async with thread1 as stdtask1:
            thread2 = await stdtask1.fork()
            await thread2.close()

    async def test_nest(self) -> None:
        local_child, remote = await self.host.ssh(self.remote)
        await local_child.kill()

    async def test_copy(self) -> None:
        cat = await self.store.bin(coreutils_nixdep, "cat")

        local_file = await self.local.task.memfd_create(await self.local.ram.to_pointer(Path("source")), MFD.CLOEXEC)
        remote_file = await self.remote.task.memfd_create(await self.remote.ram.to_pointer(Path("dest")), MFD.CLOEXEC)

        data = b'hello world'
        await local_file.write(await self.local.ram.to_pointer(Bytes(data)))
        await local_file.lseek(0, SEEK.SET)

        [(local_sock, remote_sock)] = await self.remote.open_channels(1)

        local_child = await start_cat(self.local, cat, local_file, local_sock)
        await local_sock.close()

        remote_child = await start_cat(self.remote, cat, remote_sock, remote_file)
        await remote_sock.close()

        await local_child.check()
        await remote_child.check()

        await remote_file.lseek(0, SEEK.SET)
        read, _ = await remote_file.read(await self.remote.ram.malloc_type(Bytes, len(data)))
        self.assertEqual(await read.read(), data)

    async def test_sigmask_bug(self) -> None:
        thread = await self.remote.fork()
        await thread.unshare_files(going_to_exec=True)
        await rsyscall.io.do_cloexec_except(
            thread, set([fd.near for fd in thread.task.fd_handles]))
        await self.remote.task.sigprocmask((HowSIG.SETMASK,
                                            await self.remote.ram.to_pointer(Sigset())),
                                           await self.remote.ram.malloc_struct(Sigset))
        await self.remote.task.read_oldset_and_check()
