from typing import Optional, Set, Tuple, Union

import attr
import pycparser.c_ast as ca

from .c_types import (
    CType,
    TypeMap,
    StructField,
    equal_types,
    get_struct,
    primitive_size,
    resolve_typedefs,
    type_to_string,
    var_size_align,
)


@attr.s(eq=False, repr=False)
class Type:
    """
    Type information for an expression, which may improve over time. The least
    specific type is any (initially the case for e.g. arguments); this might
    get refined into intish if the value gets used for e.g. an integer add
    operation, or into u32 if it participates in a logical right shift.
    Types cannot change except for improvements of this kind -- thus concrete
    types like u32 can never change into anything else, and e.g. ints can't
    become floats.
    """

    K_INT = 1
    K_PTR = 2
    K_FLOAT = 4
    K_INTPTR = 3
    K_ANY = 7
    SIGNED = 1
    UNSIGNED = 2
    ANY_SIGN = 3

    kind: int = attr.ib()
    size: Optional[int] = attr.ib()
    sign: int = attr.ib()
    uf_parent: Optional["Type"] = attr.ib(default=None)
    ptr_to: Optional[Union["Type", CType]] = attr.ib(default=None)

    def unify(self, other: "Type") -> bool:
        """
        Try to set this type equal to another. Returns true on success.
        Once set equal, the types will always be equal (we use a union-find
        structure to ensure this).
        """
        x = self.get_representative()
        y = other.get_representative()
        if x is y:
            return True
        if x.size is not None and y.size is not None and x.size != y.size:
            return False
        size = x.size if x.size is not None else y.size
        ptr_to = x.ptr_to if x.ptr_to is not None else y.ptr_to
        kind = x.kind & y.kind
        sign = x.sign & y.sign
        if size in [8, 16]:
            kind &= ~Type.K_FLOAT
        if size in [8, 16, 64]:
            kind &= ~Type.K_PTR
        if kind == 0 or sign == 0:
            return False
        if kind == Type.K_PTR:
            size = 32
        if sign != Type.ANY_SIGN:
            assert kind == Type.K_INT
        if x.ptr_to is not None and y.ptr_to is not None:
            if isinstance(x.ptr_to, Type) and isinstance(y.ptr_to, Type):
                if not x.ptr_to.unify(y.ptr_to):
                    return False
            elif not isinstance(x.ptr_to, Type) and not isinstance(y.ptr_to, Type):
                # TODO: deep resolve_typedefs (needs a typemap)
                if not equal_types(x.ptr_to, y.ptr_to):
                    return False
            else:
                # TODO: unify Type and CType (needs a typemap)
                # Until we get that, let's handle some easy cases though:
                if isinstance(x.ptr_to, Type) and not isinstance(y.ptr_to, Type):
                    type = x.ptr_to
                    ctype = y.ptr_to
                elif isinstance(y.ptr_to, Type) and not isinstance(x.ptr_to, Type):
                    type = y.ptr_to
                    ctype = x.ptr_to
                else:
                    assert False, "unreachable"
                if not (
                    isinstance(ctype, ca.PtrDecl) and Type.ptr(ctype.type).unify(type)
                ):
                    return False
        x.kind = kind
        x.size = size
        x.sign = sign
        x.ptr_to = ptr_to
        y.uf_parent = x
        return True

    def get_representative(self) -> "Type":
        if self.uf_parent is None:
            return self
        self.uf_parent = self.uf_parent.get_representative()
        return self.uf_parent

    def is_float(self) -> bool:
        return self.get_representative().kind == Type.K_FLOAT

    def is_pointer(self) -> bool:
        return self.get_representative().kind == Type.K_PTR

    def is_int(self) -> bool:
        return self.get_representative().kind == Type.K_INT

    def is_unsigned(self) -> bool:
        return self.get_representative().sign == Type.UNSIGNED

    def get_size_bits(self) -> int:
        return self.get_representative().size or 32

    def to_decl(self, var: str) -> str:
        ret = str(self)
        prefix = ret if ret.endswith("*") else ret + " "
        return prefix + var

    def _stringify(self, seen: Set["Type"]) -> str:
        if self in seen:
            return "?"
        seen.add(self)
        type = self.get_representative()
        size = type.size or 32
        sign = "s" if type.sign & Type.SIGNED else "u"
        if type.kind == Type.K_ANY:
            if type.size is not None:
                return f"?{size}"
            return "?"
        if type.kind == Type.K_PTR:
            if type.ptr_to is not None:
                if isinstance(type.ptr_to, Type):
                    return (type.ptr_to._stringify(seen) + " *").replace("* *", "**")
                return type_to_string(ca.PtrDecl([], type.ptr_to))
            return "void *"
        if type.kind == Type.K_FLOAT:
            return f"f{size}"
        return f"{sign}{size}"

    def __str__(self) -> str:
        return self._stringify(set())

    def __repr__(self) -> str:
        type = self.get_representative()
        signstr = ("+" if type.sign & Type.SIGNED else "") + (
            "-" if type.sign & Type.UNSIGNED else ""
        )
        kindstr = (
            ("I" if type.kind & Type.K_INT else "")
            + ("P" if type.kind & Type.K_PTR else "")
            + ("F" if type.kind & Type.K_FLOAT else "")
        )
        sizestr = str(type.size) if type.size is not None else "?"
        return f"Type({signstr + kindstr + sizestr})"

    @staticmethod
    def any() -> "Type":
        return Type(kind=Type.K_ANY, size=None, sign=Type.ANY_SIGN)

    @staticmethod
    def intish() -> "Type":
        return Type(kind=Type.K_INT, size=None, sign=Type.ANY_SIGN)

    @staticmethod
    def intptr() -> "Type":
        return Type(kind=Type.K_INTPTR, size=None, sign=Type.ANY_SIGN)

    @staticmethod
    def intptr32() -> "Type":
        return Type(kind=Type.K_INTPTR, size=32, sign=Type.ANY_SIGN)

    @staticmethod
    def ptr(type: Optional[Union["Type", CType]] = None) -> "Type":
        return Type(kind=Type.K_PTR, size=32, sign=Type.ANY_SIGN, ptr_to=type)

    @staticmethod
    def f32() -> "Type":
        return Type(kind=Type.K_FLOAT, size=32, sign=Type.ANY_SIGN)

    @staticmethod
    def floatish() -> "Type":
        return Type(kind=Type.K_FLOAT, size=None, sign=Type.ANY_SIGN)

    @staticmethod
    def f64() -> "Type":
        return Type(kind=Type.K_FLOAT, size=64, sign=Type.ANY_SIGN)

    @staticmethod
    def s8() -> "Type":
        return Type(kind=Type.K_INT, size=8, sign=Type.SIGNED)

    @staticmethod
    def u8() -> "Type":
        return Type(kind=Type.K_INT, size=8, sign=Type.UNSIGNED)

    @staticmethod
    def s16() -> "Type":
        return Type(kind=Type.K_INT, size=16, sign=Type.SIGNED)

    @staticmethod
    def u16() -> "Type":
        return Type(kind=Type.K_INT, size=16, sign=Type.UNSIGNED)

    @staticmethod
    def s32() -> "Type":
        return Type(kind=Type.K_INT, size=32, sign=Type.SIGNED)

    @staticmethod
    def u32() -> "Type":
        return Type(kind=Type.K_INT, size=32, sign=Type.UNSIGNED)

    @staticmethod
    def s64() -> "Type":
        return Type(kind=Type.K_INT, size=64, sign=Type.SIGNED)

    @staticmethod
    def u64() -> "Type":
        return Type(kind=Type.K_INT, size=64, sign=Type.UNSIGNED)

    @staticmethod
    def int64() -> "Type":
        return Type(kind=Type.K_INT, size=64, sign=Type.ANY_SIGN)

    @staticmethod
    def of_size(size: int) -> "Type":
        return Type(kind=Type.K_ANY, size=size, sign=Type.ANY_SIGN)

    @staticmethod
    def bool() -> "Type":
        return Type.intish()


def type_from_ctype(ctype: CType, typemap: TypeMap) -> Type:
    ctype = resolve_typedefs(ctype, typemap)
    if isinstance(ctype, (ca.PtrDecl, ca.ArrayDecl)):
        return Type.ptr(ctype.type)
    if isinstance(ctype, ca.FuncDecl):
        return Type.ptr(ctype)
    if isinstance(ctype, ca.TypeDecl):
        if isinstance(ctype.type, (ca.Struct, ca.Union)):
            return Type.any()
        names = ["int"] if isinstance(ctype.type, ca.Enum) else ctype.type.names
        if "double" in names:
            return Type.f64()
        if "float" in names:
            return Type.f32()
        size = 8 * primitive_size(ctype.type)
        if not size:
            return Type.any()
        sign = Type.UNSIGNED if "unsigned" in names else Type.SIGNED
        return Type(kind=Type.K_INT, size=size, sign=sign)


def ptr_type_from_ctype(ctype: CType, typemap: TypeMap) -> Tuple[Type, bool]:
    real_ctype = resolve_typedefs(ctype, typemap)
    if isinstance(real_ctype, ca.ArrayDecl):
        return Type.ptr(real_ctype.type), True
    if isinstance(real_ctype, ca.FuncDecl):
        return Type.ptr(real_ctype), True
    return Type.ptr(ctype), False


def get_field(
    type: Type, offset: int, typemap: TypeMap, *, target_size: Optional[int]
) -> Tuple[Optional[str], Type, Type, bool]:
    """Returns field name, target type, target pointer type, and whether the field is an array."""
    if target_size is None and offset == 0:
        # We might as well take a pointer to the whole struct
        target = get_pointer_target(type, typemap)
        target_type = target[1] if target else Type.any()
        return None, target_type, type, False
    type = type.get_representative()
    if not type.ptr_to or isinstance(type.ptr_to, Type):
        return None, Type.any(), Type.ptr(), False
    ctype = resolve_typedefs(type.ptr_to, typemap)
    if isinstance(ctype, ca.TypeDecl) and isinstance(ctype.type, (ca.Struct, ca.Union)):
        struct = get_struct(ctype.type, typemap)
        if struct:
            fields = struct.fields.get(offset)
            if fields:
                # Ideally, we should use target_size and the target pointer type to
                # determine which struct field to use if there are multiple at the
                # same offset (e.g. if a struct starts here, or we have a union).
                # For now though, we just use target_size as a boolean signal -- if
                # it's known we take an arbitrary subfield that's as concrete as
                # possible, if unknown we prefer a whole substruct. (The latter case
                # happens when taking pointers to fields -- pointers to substructs are
                # more common and can later be converted to concrete field pointers.)
                if target_size is None:
                    # Structs will be placed first in the field list.
                    field = fields[0]
                else:
                    # Pick the first subfield in case of unions.
                    correct_size_fields = [f for f in fields if f.size == target_size]
                    if len(correct_size_fields) == 1:
                        field = correct_size_fields[0]
                    else:
                        ind = 0
                        while ind + 1 < len(fields) and fields[ind + 1].name.startswith(
                            fields[ind].name + "."
                        ):
                            ind += 1
                        field = fields[ind]
                return (
                    field.name,
                    type_from_ctype(field.type, typemap),
                    *ptr_type_from_ctype(field.type, typemap),
                )
            # if the struct has no usable field, make one
            else:
                return get_field_do_struct_edit(type, offset, typemap, ctype, struct, target_size=target_size)
    return None, Type.any(), Type.ptr(), False

# arguments follow get_field names, apart from target_offset (offset in get_field)
def get_field_do_struct_edit(
    type: Type, target_offset: int, typemap: TypeMap, ctype: ca.TypeDecl, struct: ca.Struct, *, target_size: Optional[int]
) -> Tuple[Optional[str], Type, Type, bool]:
    # find field with biggest offset which is below target offset
    offset = target_offset
    fields = None
    while not fields and offset > 0:
        offset -= 1
        fields = struct.fields.get(offset)
    if fields:
        #TODO understand target_size=None meaning
        if target_size is None:
            print('target_size is None')
        else:
            # find a field extending up to offset + target_size
            ind = 0
            field = None
            # for each fields[ind] which doesn't hold a struct
            while ind < len(fields) and not field:
                while ind + 1 < len(fields) and fields[ind + 1].name.startswith(
                    fields[ind].name + "."
                ):
                    ind += 1
                # fields[ind].size takes into account array size
                # if target offset range is inside fields[ind]
                if offset + fields[ind].size >= target_offset + target_size:
                    field = fields[ind]
                else:
                    ind += 1
            # we may want to loop more if field isn't right, but this should do for most cases
            # if field is an unk_* array
            if field and isinstance(field.type, ca.ArrayDecl) and field.name.startswith('unk_'):
                # split unk_* into a "before" array (field), the new target field (new_field),
                # and an "after" array (field2)
                length = field.type.dim.value
                # hoping unhandled cases will raise errors and not silently do weird stuff
                if length.startswith('0x'):
                    length = int(length, 16)
                elif length.startswith('0'):
                    length = int(length, 8)
                else:
                    length = int(length, 10)
                # TODO handle non-char arrays? requires more work because splitting is harder
                if length != field.size:
                    print('{} not a char array'.format(field.name))
                #TODO test more
                #TODO remove zero-length array fields
                # resize field so it ends at target_offset
                field.size = target_offset - offset
                field.type.dim.value = str(field.size)
                # new used target field from target_offset to target_offset+target_size
                new_field_name = 'unk_{}'.format(hex(target_offset)[2:].upper())
                #TODO better way to handle pycparser types?
                new_field_ctype = ca.TypeDecl(
                    declname = new_field_name,
                    quals = [],
                    #TODO because we write this stuff early we can't have better type infos
                    type = ca.IdentifierType(names=['?{}B'.format(target_size)])
                )
                new_field = StructField(type=new_field_ctype,size=target_size,name=new_field_name)
                struct.fields[target_offset].append(new_field)
                # new unk_* field from target_offset+target_size to initial array field end
                field2name = 'unk_{}'.format(hex(target_offset+target_size)[2:].upper())
                field2size = offset+length - (target_offset+target_size)
                field2 = StructField(
                    type=ca.ArrayDecl(
                        type=ca.TypeDecl(
                            declname=field2name,
                            quals=[],
                            type=ca.IdentifierType(names=['char'])
                        ),
                        dim=ca.Constant(type='int',value=hex(field2size)),
                        dim_quals=[]
                    ), size=field2size, name=field2name)
                struct.fields[target_offset+target_size].append(field2)
                print('## Struct Edit Start ##')
                print('In {} replaced char {}[{}] by:'.format(ctype.declname, field.name, length))
                print('char {}[{}];'.format(field.type.type.declname, field.type.dim.value))
                print('{} {};'.format(new_field.type.type.names[0], new_field.type.declname))
                print('char {}[{}];'.format(field2.type.type.declname, field2.type.dim.value))
                print('## Struct Edit End   ##')
                return (
                    new_field.name,
                    #TODO this may cause issues: we confirm what the instructions want
                    # by modifying the struct accordingly, this is not confirmation of
                    # type but likely is interpreted as such
                    # (Type.any() could be used, but what about is_array?)
                    Type.of_size(target_size),
                    Type.ptr(),
                    False
                )
            # target is overlapping with a field which isn't an array or isn't named unk_*
            # assuming the existing field has uses, there's a conflict. May be caused by a union?
            else:
                print('Conflicting?')

    return None, Type.any(), Type.ptr(), False

def find_substruct_array(
    type: Type, offset: int, scale: int, typemap: TypeMap
) -> Optional[Tuple[str, int, CType]]:
    if scale <= 0:
        return None
    type = type.get_representative()
    if not type.ptr_to or isinstance(type.ptr_to, Type):
        return None
    ctype = resolve_typedefs(type.ptr_to, typemap)
    if not isinstance(ctype, ca.TypeDecl):
        return None
    if not isinstance(ctype.type, (ca.Struct, ca.Union)):
        return None
    struct = get_struct(ctype.type, typemap)
    if not struct:
        return None
    for off, fields in struct.fields.items():
        if offset < off:
            continue
        for field in fields:
            if offset >= off + field.size:
                continue
            field_type = resolve_typedefs(field.type, typemap)
            if not isinstance(field_type, ca.ArrayDecl):
                continue
            size = var_size_align(field_type.type, typemap)[0]
            if size == scale:
                return field.name, off, field_type.type
    return None


def get_pointer_target(
    type: Type, typemap: Optional[TypeMap]
) -> Optional[Tuple[int, Type]]:
    type = type.get_representative()
    target = type.ptr_to
    if target is None:
        return None
    if isinstance(target, Type):
        if target.size is None:
            return None
        return target.size // 8, target
    if typemap is None:
        # (shouldn't happen, but might as well handle it)
        return None
    size, align = var_size_align(target, typemap)
    if align == 0:
        # void* or function pointer
        return None
    return size, type_from_ctype(target, typemap)
