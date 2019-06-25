from __future__ import annotations
from dataclasses import dataclass
import rsyscall.far
import rsyscall.near
import typing as t
import logging
import contextlib
from rsyscall.struct import Serializer
from rsyscall.memory.allocation_interface import AllocationInterface
from rsyscall.memory.transport import MemoryGateway
from rsyscall.sys.mman import MemoryMapping
logger = logging.getLogger(__name__)


T = t.TypeVar('T')
T_co = t.TypeVar('T_co', covariant=True)
U = t.TypeVar('U')
T_pointer = t.TypeVar('T_pointer', bound='Pointer')
@dataclass(eq=False)
class Pointer(t.Generic[T]):
    """An owning handle for some piece of memory.

    More precisely, this is an owning handle for an allocation in some memory mapping.  We're
    explicitly representing memory mappings, rather than glossing over them and pretending that the
    address space is flat and uniform. If we have two mappings for the same file, we can translate
    this Pointer between them.

    As an implication of owning an allocation, we also know the length of that allocation, which is
    the length of the range of memory that it's valid to operate on through this pointer. We
    retrieve this through Pointer.size and use it in many places; anywhere we take a Pointer, if
    there's some question about what size to operate on, we operate on the full size of the
    pointer. Reducing the amount of memory to operate on can be done through Pointer.split.

    We also know the type of the region of memory; that is, how to interpret this region of
    memory. This is useful at type-checking time to check that we aren't passing pointers to memory
    of the wrong type. At runtime, the type is reified as a serializer, which allows us to translate
    a value of the type to and from bytes.

    We also hold a transport which will allow us to read and write the memory we own. Combined with
    the serializer, this allows us to write and read values of the appropriate type to and from
    memory using the Pointer.write and Pointer.read methods.

    Finally, pointers have a "valid" bit which says whether the Pointer can be used. We say that a
    method "consumes" a pointer if it will invalidate that pointer.

    Most of the methods manipulating the pointer are "linear". That is, they consume the pointer
    object they're called on and return a new pointer object to use. This forces the user to be more
    careful with tracking the state of the pointer; and also allows us to represent some state
    changes with by changing the type of the pointer, in particular Pointer.write.

    See also the inheriting class WrittenPointer

    """
    mapping: MemoryMapping
    transport: MemoryGateway
    serializer: Serializer[T]
    allocation: AllocationInterface
    valid: bool = True

    async def write(self, value: T) -> WrittenPointer[T]:
        "Write this value to this pointer, consuming it and returning a new WrittenPointer"
        self._validate()
        value_bytes = self.serializer.to_bytes(value)
        if len(value_bytes) > self.size():
            raise Exception("value_bytes is too long", len(value_bytes),
                            "for this typed pointer of size", self.size())
        await self.transport.write(self, value_bytes)
        return self._wrote(value)

    async def read(self) -> T:
        "Read the value pointed to by this pointer"
        self._validate()
        value = await self.transport.read(self)
        return self.serializer.from_bytes(value)

    def size(self) -> int:
        """Return the size of this pointer's allocation in bytes

        This is mostly used by syscalls and passed to the kernel, so that the kernel knows the size
        of the buffer that it's been passed. To reduce the size of a buffer passed to the kernel,
        use Pointer.split.

        """
        return self.allocation.size()

    def split(self, size: int) -> t.Tuple[Pointer, Pointer]:
        """Invalidate this pointer and split it into two adjacent pointers

        This is primarily used by syscalls that write to one contiguous part of a buffer and leave
        the rest unused.  They split the pointer into a "used" part and an "unused" part, and return
        both parts.

        """
        self._validate()
        # TODO uhhhh if split throws an exception... don't we need to free... or something...
        self.valid = False
        # TODO we should only allow split if we are the only reference to this allocation
        alloc1, alloc2 = self.allocation.split(size)
        first = self._with_alloc(alloc1)
        # TODO should degrade this pointer to raw bytes or something, or maybe no type at all
        second = self._with_alloc(alloc2)
        return first, second

    def merge(self, ptr: Pointer) -> Pointer:
        """Merge two pointers produced by split back into a single pointer

        The two pointers passed in are invalidated.

        This is primarily used by the user to re-assemble a buffer that was split by a syscall.

        """
        self._validate()
        ptr._validate()
        # TODO should assert that these two pointers both serialize the same thing
        # although they could be different types of serializers...
        self.valid = False
        ptr.valid = False
        # TODO we should only allow merge if we are the only reference to this allocation
        alloc = self.allocation.merge(ptr.allocation)
        return self._with_alloc(alloc)

    def __add__(self, right: Pointer[T]) -> Pointer[T]:
        "left + right desugars to left.merge(right)"
        return self.merge(right)

    def __radd__(self, left: t.Optional[Pointer[T]]) -> Pointer[T]:
        """"left += right" desugars to "left = (left + right) if left is not None else right"

        With this, you can initialize a variable to None, then merge pointers into it in a
        loop. This is especially useful when trying to write an entire buffer, or fill an
        entire buffer by reading.

        """
        if left is None:
            return self
        else:
            return left + self

    @property
    def near(self) -> rsyscall.near.Address:
        """Return the raw memory address referred to by this Pointer

        This is mostly used by syscalls and passed to the kernel, so that the kernel knows the start
        of the buffer to read to or write from.

        """
        # TODO hmm should maybe validate that this fits in the bounds of the mapping I guess
        self._validate()
        return self._get_raw_near()

    @contextlib.contextmanager
    def borrow(self, task: rsyscall.far.Task) -> t.Iterator[rsyscall.near.Address]:
        """Pin the address of this pointer, and yield the pointer's raw memory address

        We validate this pointer, and pin it in memory so that it can't be moved or deleted while
        it's being used.

        This is mostly used by syscalls and passed to the kernel, so that the kernel knows the start
        of the buffer to read to or write from.

        """
        # TODO actual tracking of pointer references is not yet implemented
        # we should have a flag or lock to indicate that this pointer shouldn't be moved or deleted,
        # while it's being borrowed.
        # TODO rename this to pinned
        # TODO make this the only way to get .near
        self._validate()
        if task.address_space != self.mapping.task.address_space:
            raise rsyscall.far.AddressSpaceMismatchError(task.address_space, self.mapping.task.address_space)
        yield self.near

    def _validate(self) -> None:
        if not self.valid:
            raise Exception("handle is no longer valid")

    def free(self) -> None:
        """Free this pointer, invalidating it and releasing the underlying allocation.

        It isn't necessary to explicitly call this, because the pointer will be freed on
        GC. But you can call it anyway if, for example, the pointer will be referenced for
        long after it is done being used.

        """
        if self.valid:
            self.valid = False
            self.allocation.free()

    def __del__(self) -> None:
        # This isn't strictly necessary because the allocation will free itself on __del__.
        # But, that will only happen when *all* pointers referring to the allocation are collected;
        # not just the valid one.
        # So, this ensures GC is a bit more prompt.
        # Oh, wait. The real reason we need this is because the Arena stores references to the allocation.
        # TODO We should fix that.
        self.free()

    def split_from_end(self, size: int, alignment: int) -> t.Tuple[Pointer, Pointer]:
        """Split from the end of this pointer, such that the right pointer is aligned to `alignment`

        Used by write_to_end; mostly only useful for preparing stacks.

        """
        extra_to_remove = (int(self.near) + size) % alignment
        return self.split(self.size() - size - extra_to_remove)

    async def write_to_end(self, value: T, alignment: int) -> t.Tuple[Pointer[T], WrittenPointer[T]]:
        """Write a value to the end of the range of this pointer

        Splits the pointer, and returns both parts.  This function is only useful for preparing
        stacks. Would be nice to figure out either a more generic way to prep stacks, or to figure
        out more things that write_to_end could be used for.

        """
        value_bytes = self.serializer.to_bytes(value)
        rest, write_buf = self.split_from_end(len(value_bytes), alignment)
        written = await write_buf.write(value)
        return rest, written

    def _get_raw_near(self) -> rsyscall.near.Address:
        # only for printing purposes
        return self.mapping.near.as_address() + self.allocation.offset()

    def __repr__(self) -> str:
        if self.valid:
            return f"Pointer({self.near}, {self.serializer})"
        else:
            return f"Pointer(invalid, {self._get_raw_near()}, {self.serializer})"

    #### Various ways to create new Pointers by changing one thing about the old pointer. 
    def _with_mapping(self: T_pointer, mapping: MemoryMapping) -> T_pointer:
        if type(self) is not Pointer:
            raise Exception("subclasses of Pointer must override _with_mapping")
        if mapping.file is not self.mapping.file:
            raise Exception("can only move pointer between two mappings of the same file")
        # we don't have a clean model for referring to the same object through multiple mappings.
        # this is a major TODO.
        # at least two ways to achieve it:
        # - have Pointers become multi-mapping super-pointers, which can be valid in multiple address spaces
        # - break our linearity constraint on pointers, allowing multiple pointers for the same allocation;
        #   this is difficult because split() is only easy to implement due to linearity.
        # right here, we just linearly move the pointer to a new mapping
        self._validate()
        self.valid = False
        return type(self)(mapping, self.transport, self.serializer, self.allocation)

    def _with_alloc(self, allocation: AllocationInterface) -> Pointer:
        return Pointer(self.mapping, self.transport, self.serializer, allocation)

    def _reinterpret(self, serializer: Serializer[U]) -> Pointer[U]:
        # TODO how can we check to make sure we don't reinterpret in wacky ways?
        # maybe we should only be able to reinterpret in ways that are allowed by the serializer?
        # so maybe it's a method on the Serializer? cast_to(Type)?
        self._validate()
        self.valid = False
        return Pointer(self.mapping, self.transport, serializer, self.allocation)

    def _wrote(self, value: T) -> WrittenPointer[T]:
        "Assert we wrote this value to this pointer, and return the appropriate new WrittenPointer"
        self.valid = False
        return WrittenPointer(self.mapping, self.transport, value, self.serializer, self.allocation)

class WrittenPointer(Pointer[T_co]):
    """A Pointer with some known value written to it

    We have all the normal functionality of a Pointer (see that class for more information), but we
    also know that we've had some value written to us, and we know what that value is, and it's
    immediately accessible in Python.

    We can also view this with an emphasis on the value: This is some known value, that has been
    written to some memory location. The value and the pointer are equally important in this class,
    and both are used by most uses of this class.

    We use inheritance so that a WrittenPointer gracefully degrades back to a Pointer, and is
    invalidated whenever a pointer is invalidated. Specifically, we want anything that writes to a
    pointer to invalidate this pointer. The invalidation lets us know that this value is no longer
    necessarily written to this pointer.

    For example, syscalls that write to pointers will typically call split. A call to
    WrittenPointer.split will invalidate the WrittenPointer and return regular Pointers; that's
    desirable because the syscall likely overwrote whatever value was previously written here.

    TODO: We should fix syscalls that write to memory but don't call split so that they invalidate
    the WrittenPointer. That's mostly syscalls using Sockbufs...

    """
    def __init__(self,
                 mapping: MemoryMapping,
                 transport: MemoryGateway,
                 value: T_co,
                 serializer: Serializer[T_co],
                 allocation: AllocationInterface,
    ) -> None:
        super().__init__(mapping, transport, serializer, allocation)
        self.value = value

    def __repr__(self) -> str:
        return f"WrittenPointer({self.near}, {self.value})"

    def _with_mapping(self, mapping: MemoryMapping) -> WrittenPointer:
        if type(self) is not WrittenPointer:
            raise Exception("subclasses of WrittenPointer must override _with_mapping")
        if mapping.file is not self.mapping.file:
            raise Exception("can only move pointer between two mappings of the same file")
        # see notes in Pointer._with_mapping
        self._validate()
        self.valid = False
        return type(self)(mapping, self.transport, self.value, self.serializer, self.allocation)
