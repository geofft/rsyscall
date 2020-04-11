from __future__ import annotations
import typing as t
import time
from rsyscall._raw import lib # type: ignore
from rsyscall._raw import ffi # type: ignore
import os
import abc
import trio
import rsyscall.handle as handle
import rsyscall.near
from rsyscall.trio_test_case import TrioTestCase
from rsyscall.thread import Thread, ChildThread
from rsyscall.command import Command
from rsyscall.handle import FileDescriptor, Path
from dataclasses import dataclass
from rsyscall.struct import Int32
from rsyscall.monitor import AsyncChildProcess
from rsyscall.memory.ram import RAMThread

import rsyscall.tasks.local as local
from rsyscall.sys.capability import CAP, CapHeader, CapData
from rsyscall.sys.socket import AF, SOCK, SOL
from rsyscall.sys.prctl import PR, PR_CAP_AMBIENT
from rsyscall.fcntl import O
from rsyscall.sched import CLONE
from rsyscall.netinet.in_ import SockaddrIn
from rsyscall.netinet.ip import IP, IPPROTO
from rsyscall.linux.netlink import SockaddrNl, NETLINK
from rsyscall.linux.rtnetlink import RTMGRP
from rsyscall.handle import Socketpair
import rsyscall.net.if_ as netif

import rsyscall.nix as nix

miredo_nixdep = nix.import_nix_dep("miredo")

@dataclass
class MiredoExecutables:
    run_client: Command
    privproc: Command

    @classmethod
    async def from_store(cls, store: nix.Store) -> MiredoExecutables:
        miredo_path = await store.realise(miredo_nixdep)
        return MiredoExecutables(
            run_client=Command(miredo_path/"libexec"/"miredo"/"miredo-run-client", ["miredo-run-client"], {}),
            privproc=Command(miredo_path/"libexec"/"miredo"/"miredo-privproc", ["miredo-privproc"], {}),
        )

@dataclass
class Miredo:
    # we could use setns instead of keeping a thread around inside the namespace.
    # that would certainly be more lightweight.
    # but, the hassle with setns is that it seems you must setns to
    # the owning userns before you can setns to the netns.
    # you can't just do an unshare(USER) to get caps then setns to wherever.
    # I don't get why this is the case, and I'm not sure it can't be worked around.
    # So, I'll just use a thread, which I do understand.
    # Hopefully we can get a more lightweight setns-based approach later?
    ns_thread: ChildThread

async def exec_miredo_privproc(
        miredo_exec: MiredoExecutables,
        thread: ChildThread,
        privproc_side: FileDescriptor, tun_index: int) -> AsyncChildProcess:
    await thread.unshare(CLONE.FILES)
    privproc_side = thread.task.inherit_fd(privproc_side)
    await privproc_side.dup2(thread.stdin)
    await privproc_side.dup2(thread.stdout)
    child = await thread.exec(miredo_exec.privproc.args(str(tun_index)))
    return child

async def exec_miredo_run_client(
        miredo_exec: MiredoExecutables,
        thread: ChildThread,
        inet_sock: FileDescriptor,
        tun_fd: FileDescriptor,
        reqsock: FileDescriptor,
        icmp6_fd: FileDescriptor,
        client_side: FileDescriptor,
        server_name: str) -> AsyncChildProcess:
    await thread.unshare(CLONE.FILES)
    child = await thread.exec(miredo_exec.run_client.args(
        *[str(await fd.inherit(thread.task).as_argument())
          for fd in [inet_sock, tun_fd, reqsock, icmp6_fd, client_side]],
        server_name, server_name))
    return child

async def add_to_ambient(thr: RAMThread, capset: t.Set[CAP]) -> None:
    hdr_ptr = await thr.ram.ptr(CapHeader())
    data_ptr = await thr.ram.malloc(CapData)
    await thr.task.capget(hdr_ptr, data_ptr)
    data = await data_ptr.read()
    data.inheritable.update(capset)
    data_ptr = await data_ptr.write(data)
    await thr.task.capset(hdr_ptr, data_ptr)
    for cap in capset:
        await thr.task.prctl(PR.CAP_AMBIENT, PR_CAP_AMBIENT.RAISE, cap)

async def add_to_ambient_caps(thr: RAMThread, capset: t.Set[CAP]) -> None:
    hdr = await thr.ptr(CapHeader())
    data_ptr = await thr.ram.malloc(CapData)
    data = thr.task.capget(hdr, data_ptr).read()
    data.inheritable.update(capset)
    data_ptr = await data_ptr.write(data)
    await thr.task.capset(hdr, data_ptr)
    for cap in capset:
        await thr.task.prctl(PR.CAP_AMBIENT, PR_CAP_AMBIENT.RAISE, cap)

async def start_miredo(nursery, miredo_exec: MiredoExecutables, thread: Thread) -> Miredo:
    inet_sock = await thread.task.socket(AF.INET, SOCK.DGRAM)
    await inet_sock.bind(await thread.ram.ptr(SockaddrIn(0, 0)))
    # set a bunch of sockopts
    one = await thread.ram.ptr(Int32(1))
    await inet_sock.setsockopt(SOL.IP, IP.RECVERR, one)
    await inet_sock.setsockopt(SOL.IP, IP.PKTINFO, one)
    await inet_sock.setsockopt(SOL.IP, IP.MULTICAST_TTL, one)
    # hello fragments my old friend
    await inet_sock.setsockopt(SOL.IP, IP.MTU_DISCOVER, await thread.ram.ptr(Int32(IP.PMTUDISC_DONT)))
    ns_thread = await thread.clone()
    await ns_thread.unshare(CLONE.NEWNET|CLONE.NEWUSER)
    # create icmp6 fd so miredo can relay pings
    icmp6_fd = await ns_thread.task.socket(AF.INET6, SOCK.RAW, IPPROTO.ICMPV6)

    # create the TUN interface
    tun_fd = await ns_thread.task.open(await ns_thread.ram.ptr(Path("/dev/net/tun")), O.RDWR)
    ptr = await thread.ram.ptr(netif.Ifreq(b'teredo', flags=netif.IFF_TUN))
    await tun_fd.ioctl(netif.TUNSETIFF, ptr)
    # create reqsock for ifreq operations in this network namespace
    reqsock = await ns_thread.task.socket(AF.INET, SOCK.STREAM)
    await reqsock.ioctl(netif.SIOCGIFINDEX, ptr)
    tun_index = (await ptr.read()).ifindex
    # create socketpair for communication between privileged process and teredo client
    privproc_pair = await (await ns_thread.task.socketpair(
        AF.UNIX, SOCK.STREAM, 0, await ns_thread.ram.malloc(Socketpair))).read()

    privproc_thread = await ns_thread.clone()
    await add_to_ambient(privproc_thread, {CAP.NET_ADMIN})
    privproc_child = await exec_miredo_privproc(miredo_exec, privproc_thread, privproc_pair.first, tun_index)
    nursery.start_soon(privproc_child.check)

    # TODO lock down the client thread, it's talking on the network and isn't audited...
    # should clear out the mount namespace
    # iterate through / and umount(MNT_DETACH) everything that isn't /nix
    # ummm and let's use UMOUNT_NOFOLLOW too
    # ummm no let's just only umount directories
    client_thread = await ns_thread.clone(CLONE.NEWPID)
    await client_thread.unshare(CLONE.NEWNET|CLONE.NEWNS)
    await client_thread.unshare_user()
    client_child = await exec_miredo_run_client(
        miredo_exec, client_thread, inet_sock, tun_fd, reqsock, icmp6_fd, privproc_pair.second, "teredo.remlab.net")
    nursery.start_soon(client_child.check)

    # we keep the ns thread around so we don't have to mess with setns
    return Miredo(ns_thread)

async def start_miredo_simple(nursery, miredo_exec: MiredoExecutables, thread: Thread) -> Miredo:
    ### create socket Miredo will use for internet access
    inet_sock = await thread.task.socket(AF.INET, SOCK.DGRAM)
    await inet_sock.bind(await thread.ram.ptr(SockaddrIn(0, 0)))
    # set_miredo_sockopts extracted to a helper function to preserve clarity
    await set_miredo_sockopts(inet_sock)
    ns_thread = await thread.clone()
    ### create main network namespace thread
    await ns_thread.unshare(CLONE.NEWNET|CLONE.NEWUSER)
    ### create in-namespace raw INET6 socket which Miredo will use to relay pings
    icmp6_fd = await ns_thread.task.socket(AF.INET6, SOCK.RAW, IPPROTO.ICMPV6)
    ### create and set up the TUN interface
    # open /dev/net/tun
    tun_fd = await ns_thread.task.open(await ns_thread.ptr(Path("/dev/net/tun")), O.RDWR)
    # create tun interface for this fd
    ifreq = await thread.ptr(Ifreq('teredo', flags=IFF_TUN))
    await tun_fd.ioctl(TUNSETIFF, ifreq)
    ### create socket which Miredo will use for Ifreq operations in this network namespace
    reqsock = await ns_thread.task.socket(AF.INET, SOCK.STREAM)
    # use reqsock to look up the interface index of the TUN interface by name (reusing the previous Ifreq)
    await reqsock.ioctl(SIOCGIFINDEX, ifreq)
    tun_index = (await ifreq.read()).ifindex
    # create socketpair which Miredo will use to communicate between privileged process and Teredo client
    privproc_pair = await (await ns_thread.task.socketpair(
        AF.UNIX, SOCK.STREAM, 0, await ns_thread.ram.malloc(Socketpair))).read()
    ### start up privileged process
    privproc_thread = await ns_thread.clone(unshare=CLONE.FILES)
    # preserve NET_ADMIN capability over exec so that privproc can manipulate the TUN interface
    # add_to_ambient_caps extracted to a helper function to preserve clarity
    await add_to_ambient_caps(privproc_thread, {CAP.NET_ADMIN})
    privproc_side = thread.task.inherit_fd(privproc_pair.first)
    await privproc_side.dup2(privproc_thread.stdin)
    await privproc_side.dup2(privproc_thread.stdout)
    privproc_child = await thread.exec(miredo_exec.privproc.args(str(tun_index)))
    ### start up Teredo client
    # we pass CLONE.NEWPID since miredo starts subprocesses and this will ensure they're killed on exit
    client_thread = await ns_thread.clone(flags=CLONE.NEWNET|CLONE.NEWNS|CLONE.NEWPID, unshare=CLONE.FILES)
    # a helper method
    async def pass_fd(fd: FileDescriptor) -> str:
        await fd.inherit(ns_thread.task).disable_cloexec()
        return str(int(fd.near))
    client_child = await thread.exec(miredo_exec.run_client.args(
        await pass_fd(inet_sock), await pass_fd(tun_fd), await pass_fd(reqsock),
        await pass_fd(icmp6_fd), await pass_fd(privproc_pair.second),
        "teredo.remlab.net", "teredo.remlab.net")
    return Miredo(ns_thread, client_child, privproc_child)

class TestMiredo(TrioTestCase):
    async def asyncSetUp(self) -> None:
        # TODO lmao stracing this stuff causes a bug,
        # what is even going on
        self.thread = local.thread
        print("a", time.time())
        self.exec = await MiredoExecutables.from_store(nix.local_store)
        print("a1", time.time())
        self.miredo = await start_miredo(self.nursery, self.exec, self.thread)
        print("b", time.time())
        self.netsock = await self.miredo.ns_thread.task.socket(AF.NETLINK, SOCK.DGRAM, NETLINK.ROUTE)
        print("b-1", time.time())
        print("b0", time.time())
        await self.netsock.bind(
            await self.miredo.ns_thread.ram.ptr(SockaddrNl(0, RTMGRP.IPV6_ROUTE)))
        print("b0.5", time.time())


    async def test_miredo(self) -> None:
        print("b1", time.time())
        ping6 = (await self.thread.environ.which("ping")).args('-6')
        print("b1.5", time.time())
        # TODO lol actually parse this, don't just read and throw it away
        await self.netsock.read(await self.miredo.ns_thread.ram.malloc(bytes, 4096))
        print("b2", time.time())
        thread = await self.miredo.ns_thread.clone()
        print("c", time.time())
        # bash = await rsc.which(self.thread, "bash")
        # await (await thread.exec(bash)).check()
        await add_to_ambient(thread, {CAP.NET_RAW})
        await (await thread.exec(ping6.args('-c', '1', 'google.com'))).check()
        print("d", time.time())

if __name__ == "__main__":
    import unittest
    unittest.main()
