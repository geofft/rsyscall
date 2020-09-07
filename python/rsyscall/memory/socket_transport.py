"""Memory transport to a remote address space based on reading/writing file descriptors.

We need to be able to read and write to the address spaces of our
threads. We start with the ability to read and write to the address
space of the local thread; we need to bootstrap that into an ability
to read and write to other address spaces.

We could have taken a dependency on some means of RDMA, or used
process_vm_readv/writev, or used other techniques, but these only work
in certain circumstances and places additional dependencies on
us. Nevertheless, they might be useful optimizations in the future.

Instead, we make sure that whenever we want to read and write to any
address space, we have a file descriptor owned by a task in that
address space, which is connected to a file descriptor owned by a task
in an address space that we already can read and write.

Then, the problem is reduced to copying bytes between memory and the
connection underlying the pair of file descriptors. But that's easy:
We just use the "read" and "write" system calls.

So, to write to memory in some address space B, we write to some
memory in some address space A that we already can access; then call
`write` on A's file descriptor to copy the bytes from memory in A into
the connection; then call `read` on B's file descriptor to copy the
bytes from the connection to memory in B.

To read memory in address space B, we just perform this in reverse; we
call `write` on B's file descriptor, and `read` on A's file descriptor.

"""
from __future__ import annotations
from dataclasses import dataclass, field
from rsyscall.concurrency import SuspendableCoroutine, Future, Promise, make_future, run_all
from rsyscall.memory.ram import RAM
from rsyscall.epoller import AsyncFileDescriptor
from rsyscall.memory.transport import MemoryTransport
from rsyscall.memory.allocation_interface import AllocationInterface
import rsyscall.handle as handle
import typing as t
import trio
import math

from rsyscall.handle import Pointer, FileDescriptor
from rsyscall.sys.uio import IovecList
from rsyscall.memory.allocator import AllocatorInterface

__all__ = [
    "SocketMemoryTransport",
]

@dataclass
class ReadOp:
    src: Pointer
    done: t.Optional[bytes] = None
    cancelled: bool = False

    @property
    def data(self) -> bytes:
        if self.done is None:
            raise Exception("not done yet")
        return self.done

@dataclass
class WriteOp:
    dest: Pointer
    data: bytes
    done: bool = False
    cancelled: bool = False

    def assert_done(self) -> None:
        if not self.done:
            raise Exception("not done yet")

@dataclass
class SpanAllocation(AllocationInterface):
    """An allocation which is a subspan of some other allocation, and can be split freely

    This should be built into our allocation system. In fact, it is: This is what split is
    for. But the ownership is tricky: Splitting an allocation consumes it. We aren't
    supposed to take ownership of the pointers passed to us for batch_write/batch_read, so
    we can't naively split the pointers.  Instead, we use to_span, below, to make them use
    SpanAllocation, so we can split them freely without taking ownership.

    We should make it possible to split an allocation without consuming it, or otherwise
    have multiple references to the same allocation, then we can get rid of this.

    """
    alloc: AllocationInterface
    _offset: int
    _size: int

    def __post_init__(self) -> None:
        if self._offset + self._size > self.alloc.size():
            raise Exception("span falls off the end of the underlying allocation",
                            self._offset, self._size, self.alloc.size())

    def offset(self) -> int:
        return self.alloc.offset() + self._offset

    def size(self) -> int:
        return self._size

    def split(self, size: int) -> t.Tuple[AllocationInterface, AllocationInterface]:
        if size > self.size():
            raise Exception("called split with size", size, "greater than this allocation's total size", self.size())
        return (SpanAllocation(self.alloc, self._offset, size),
                SpanAllocation(self.alloc, self._offset + size, self._size - size))

    def merge(self, other: AllocationInterface) -> AllocationInterface:
        if not isinstance(other, SpanAllocation):
            raise Exception("can only merge SpanAllocation with SpanAllocation, not", other)
        if self.alloc == other.alloc:
            if self._offset + self._size == other._offset:
                return SpanAllocation(self.alloc, self._offset, self._size + other._size)
            else:
                raise Exception("spans are not adjacent")
        else:
            raise Exception("can't merge spans over two different allocations")

    def free(self) -> None:
        pass

def to_span(ptr: Pointer) -> Pointer:
    "Wraps the pointer's allocation in SpanAllocation so it can be split freely"
    return Pointer(
        ptr.mapping,
        ptr.transport,
        ptr.serializer,
        SpanAllocation(ptr.allocation, 0, ptr.allocation.size()),
        ptr.typ)

@dataclass
class MergedAllocation(AllocationInterface):
    """One big allocation created from zero or more smaller allocations, which can be merged freely

    This is the same issue as with SpanAllocation. Our allocation system allows us to
    merge adjacent allocations, but that consumes the allocation, which we aren't allowed
    to do with the pointers passed to us for batch_write/batch_read. So we wrap the
    allocations in MergedAllocation to merge them together.

    We should make it possible to merge an allocation without consuming it, or otherwise
    have multiple references to the same allocation, then we can get rid of this.

    """
    allocs: t.List[AllocationInterface]

    def __post_init__(self) -> None:
        if len(self.allocs) == 0:
            return
        cur = self.allocs[0].offset()
        for alloc in self.allocs:
            if alloc.offset() != cur:
                raise Exception("allocation is not contiguous with previous allocation")
            cur += alloc.size()

    def offset(self) -> int:
        if len(self.allocs) == 0:
            return 0
        return self.allocs[0].offset()

    def size(self) -> int:
        return sum(alloc.size() for alloc in self.allocs)

    def split(self, size: int) -> t.Tuple[AllocationInterface, AllocationInterface]:
        reached = 0
        for i, alloc in enumerate(self.allocs):
            reached += alloc.size()
            if reached >= size:
                break
        else:
            raise Exception("called split with size", size, "greater than this allocation's total size", reached)
        split_idx = i
        first, second = self.allocs[:split_idx], self.allocs[split_idx+1:]
        if reached == size:
            first = first + [self.allocs[split_idx]]
        else:
            overhang = reached - size
            first_part, second_part = alloc.split(alloc.size() - overhang)
            first = first + [first_part]
            second = [second_part] + second
        return MergedAllocation(first), MergedAllocation(second)

    def merge(self, other: AllocationInterface) -> AllocationInterface:
        raise Exception("doesn't support merge")

    def free(self) -> None:
        pass

def merge_adjacent_pointers(ptrs: t.List[Pointer]) -> Pointer:
    "Merges these pointers together by wrapping the allocation in a MergedAllocation"
    return Pointer(
        ptrs[0].mapping,
        ptrs[0].transport,
        ptrs[0].serializer,
        MergedAllocation([ptr.allocation for ptr in ptrs]),
        ptrs[0].typ)

def merge_adjacent_writes(write_ops: t.List[t.Tuple[Pointer, bytes]]) -> t.List[t.Tuple[Pointer, bytes]]:
    "Combine writes to adjacent memory, to reduce the number of operations needed"
    if len(write_ops) == 0:
        return []
    write_ops = sorted(write_ops, key=lambda op: int(op[0].near))
    groupings: t.List[t.List[t.Tuple[Pointer, bytes]]] = []
    ops_to_merge = [write_ops[0]]
    for (prev_op, prev_data), (op, data) in zip(write_ops, write_ops[1:]):
        if prev_op.mapping is op.mapping:
            if int(prev_op.near + len(prev_data)) == int(op.near):
                # the current op is adjacent to the previous op, append it to
                # the list of pending ops to merge together.
                ops_to_merge.append((op, data))
                continue
            elif int(prev_op.near + len(prev_data)) > int(op.near):
                raise Exception("pointers passed to memcpy are overlapping!")
        # the current op isn't adjacent to the previous op, so
        # flush the list of ops_to_merge and start a new one.
        groupings.append(ops_to_merge)
        ops_to_merge = [(op, data)]
    groupings.append(ops_to_merge)

    outputs: t.List[t.Tuple[Pointer, bytes]] = []
    for group in groupings:
        merged_data = b''.join([op[1] for op in group])
        merged_ptr = merge_adjacent_pointers([op[0] for op in group])
        outputs.append((merged_ptr, merged_data))
    return outputs

def merge_adjacent_reads(read_ops: t.List[ReadOp]) -> t.List[t.Tuple[ReadOp, t.List[ReadOp]]]:
    """Combine reads to adjacent memory, to reduce the number of operations needed

    We return a list of pairs: A combined ReadOp, paired with the list of ReadOps that
    went into it. We need to perform the combined ReadOp, then split the read data between
    the list of constituent ReadOps.

    """
    if len(read_ops) == 0:
        return []
    read_ops = sorted(read_ops, key=lambda op: int(op.src.near))

    groupings: t.List[t.List[ReadOp]] = []
    ops_to_merge = [read_ops[0]]
    for prev_op, op in zip(read_ops, read_ops[1:]):
        if int(prev_op.src.near + prev_op.src.size()) == int(op.src.near):
            # the current op is adjacent to the previous op, append it to
            # the list of pending ops to merge together.
            ops_to_merge.append(op)
        elif int(prev_op.src.near + prev_op.src.size()) > int(op.src.near):
            raise Exception("pointers passed to memcpy are overlapping!", prev_op.src, op.src)
        else:
            # the current op isn't adjacent to the previous op, so
            # flush the list of ops_to_merge and start a new one.
            groupings.append(ops_to_merge)
            ops_to_merge = [op]
    groupings.append(ops_to_merge)
    outputs: t.List[t.Tuple[ReadOp, t.List[ReadOp]]] = []
    for group in groupings:
        merged_ptr = merge_adjacent_pointers([op.src for op in group])
        outputs.append((ReadOp(merged_ptr), group))
    return outputs

@dataclass
class PrimitiveSocketMemoryTransport(MemoryTransport):
    """Like SocketMemoryTransport, but doesn't require a remote_allocator

    This just uses plain `read` and `write` rather than `readv` and
    `writev`, and thus is less efficient, but doesn't require
    allocating memory for an iovec on the remote side.

    We use this to transport the memory needed for the iovecs used in
    SocketMemoryTransport. We also use this as a fallback when using
    an iovec would be too much overhead.

    """
    local: AsyncFileDescriptor
    remote: FileDescriptor

    def __init__(self,
                 local: AsyncFileDescriptor,
                 remote: FileDescriptor,
    ) -> None:
        self.local = local
        self.remote = remote
        self.read_lock = trio.Lock()
        self.write_local_channel, self.pending_write_local = trio.open_memory_channel(math.inf)
        self.suspendable_write_local = SuspendableCoroutine(self._run_write_local)
        self.read_remote_channel, self.pending_read_remote = trio.open_memory_channel(math.inf)
        self.suspendable_read_remote = SuspendableCoroutine(self._run_read_remote)

    def inherit(self, task: handle.Task) -> PrimitiveSocketMemoryTransport:
        return PrimitiveSocketMemoryTransport(self.local, task.make_fd_handle(self.remote))

    async def _run_write_local(self, susp: SuspendableCoroutine) -> None:
        while True:
            # again, we could batch these, with good syscall pipelining...
            dest, to_write, promise = await susp.wait(self.pending_write_local.receive)
            while to_write.size() > 0:
                written, to_write = await susp.wait(lambda: self.local.write(to_write))
            self.read_remote_channel.send_nowait((dest, promise))

    async def _run_read_remote(self, susp: SuspendableCoroutine) -> None:
        while True:
            # TODO we could process these in batches, if we could pipeline syscalls, preserving order.
            dest, promise = await susp.wait(self.pending_read_remote.receive)
            rest = to_span(dest)
            while rest.size() > 0:
                read, rest = await susp.wait(lambda: self.remote.read(rest))
            promise.send(None)

    async def write(self, dest: Pointer, data: bytes) -> None:
        if dest.size() != len(data):
            raise Exception("mismatched pointer size", dest.size(), "and data size", len(data))
        src = await self.local.ram.ptr(data)
        future, promise = make_future()
        self.write_local_channel.send_nowait((dest, src, promise))
        async with self.suspendable_write_local.running():
            async with self.suspendable_read_remote.running():
                await future.get()

    async def batch_write(self, ops: t.List[t.Tuple[Pointer, bytes]]) -> None:
        raise Exception("batch write not supported")

    async def read(self, src: Pointer) -> bytes:
        async with self.read_lock:
            src = to_span(src)
            dest = await self.local.ram.malloc(bytes, src.size())
            async def write() -> None:
                rest = src
                while rest.size() > 0:
                    written, rest = await self.remote.write(rest)
            await trio.sleep(0)
            with trio.CancelScope(shield=True):
                async with trio.open_nursery() as nursery:
                    nursery.start_soon(write)
                    read: t.Optional[Pointer[bytes]] = None
                    rest = dest
                    while rest.size() > 0:
                        more_read, rest = await self.local.read(rest)
                        if read is None:
                            read = more_read
                        else:
                            read = read.merge(more_read)
            if read is None:
                return b''
            else:
                return await read.read()

    async def batch_read(self, ops: t.List[Pointer]) -> t.List[bytes]:
        raise Exception("batch read not supported")

async def get_batch(chan: trio.abc.ReceiveChannel) -> t.List:
    requests = []
    requests.append(await chan.receive())
    # grab everything else in the channel
    try:
        while True:
            requests.append(chan.receive_nowait())
    except (trio.WouldBlock, trio.Cancelled):
        return requests


class SocketMemoryTransport(MemoryTransport):
    """Read and write bytes from a remote address space, using a connected socketpair

    We use the "ram" inside the "local" AsyncFileDescriptor to access
    pointers in the address space of the "local" fd. We use the
    socketpair to transport memory from the "local" address space to
    the address space of the "remote" fd.

    In this way, we turn a transport for the "local" address space,
    plus the connected socketpair, into a transport for the "remote"
    address space.

    We also take a remote_allocator; we use this to allocate memory
    for iovecs in the remote space so we can call `readv` and `writev`
    instead of regular `read` and `write`, which means less roundtrips
    for syscalls.

    We provide a batching interface, which is much more
    efficient. Internally, we also perform batching of multiple
    unrelated writes or reads happening at a time. This allows for the
    user to write more naive parallel code which tries to perform
    several writes or reads at once, and have that code be
    automatically batched together.

    """
    def __init__(self,
                 local: AsyncFileDescriptor,
                 remote: FileDescriptor,
                 remote_allocator: AllocatorInterface,
    ) -> None:
        self.local = local
        self.remote = remote
        self.remote_allocator = remote_allocator
        self.primitive = PrimitiveSocketMemoryTransport(local, remote)
        self.primitive_remote_ram = RAM(self.remote.task, self.primitive, self.remote_allocator)

    def inherit(self, task: handle.Task) -> SocketMemoryTransport:
        return SocketMemoryTransport(self.local, task.make_fd_handle(self.remote),
                                     self.remote_allocator.inherit(task))

    async def write(self, dest: Pointer, data: bytes) -> None:
        await self.primitive.write(dest, data)

    async def batch_write(self, ops: t.List[t.Tuple[Pointer, bytes]]) -> None:
        await run_all([(lambda dest=dest, data=data: self.write(dest, data)) for dest, data in ops])

    async def read(self, src: Pointer) -> bytes:
        return await self.primitive.read(src)
