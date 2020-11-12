import abc
import copy
import typing
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

import attr

from .error import DecompFailure
from .parse_file import Function, Label, Rodata
from .parse_instruction import (
    AsmAddressMode,
    AsmGlobalSymbol,
    AsmLiteral,
    Instruction,
    JumpTarget,
    Macro,
    Register,
    parse_instruction,
)


@attr.s(eq=False)
class Block:
    index: int = attr.ib()
    label: Optional[Label] = attr.ib()
    approx_label_name: str = attr.ib()
    instructions: List[Instruction] = attr.ib()

    # TODO: fix "Any" to be "BlockInfo" (currently annoying due to circular imports)
    block_info: Optional[Any] = None

    def add_block_info(self, block_info: Any) -> None:
        assert self.block_info is None
        self.block_info = block_info

    def clone(self) -> "Block":
        return copy.deepcopy(self)

    def __str__(self) -> str:
        name = f"{self.index} ({self.approx_label_name})"
        inst_str = "\n".join("    " + str(instr) for instr in self.instructions)
        return f"# {name}\n{inst_str}\n"


@attr.s(eq=False)
class BlockBuilder:
    curr_index: int = attr.ib(default=0)
    curr_label: Optional[Label] = attr.ib(default=None)
    last_label_name: str = attr.ib(default="initial")
    label_counter: int = attr.ib(default=0)
    curr_instructions: List[Instruction] = attr.ib(factory=list)
    blocks: List[Block] = attr.ib(factory=list)

    def new_block(self) -> Optional[Block]:
        if len(self.curr_instructions) == 0:
            return None

        label_name = self.last_label_name
        if self.label_counter > 0:
            label_name += f".{self.label_counter}"
        block = Block(
            self.curr_index, self.curr_label, label_name, self.curr_instructions
        )
        self.blocks.append(block)

        self.curr_index += 1
        self.curr_label = None
        self.label_counter += 1
        self.curr_instructions = []

        return block

    def add_instruction(self, instruction: Instruction) -> None:
        self.curr_instructions.append(instruction)

    def set_label(self, label: Label) -> None:
        if label == self.curr_label:
            # It's okay to repeat a label (e.g. once with glabel, once as a
            # standard label -- this often occurs for switches).
            return
        # We could support multiple labels at the same position, and output
        # empty blocks. For now we don't, however.
        if self.curr_label:
            raise DecompFailure(
                "A block is currently not allowed to have more than one label,\n"
                f"but {self.curr_label.name}/{label.name} is given two."
            )
        self.curr_label = label
        self.last_label_name = label.name
        self.label_counter = 0

    def get_blocks(self) -> List[Block]:
        return self.blocks


def invert_branch_mnemonic(mnemonic: str) -> str:
    inverses = {
        "beq": "bne",
        "bne": "beq",
        "beqz": "bnez",
        "bnez": "beqz",
        "bgez": "bltz",
        "bgtz": "blez",
        "blez": "bgtz",
        "bltz": "bgez",
        "bc1t": "bc1f",
        "bc1f": "bc1t",
    }
    return inverses[mnemonic]


# Branch-likely instructions only evaluate their delay slots when they are
# taken, making control flow more complex. However, on the IRIX compiler they
# only occur in a very specific pattern:
#
# ...
# <branch likely instr> .label
#  X
# ...
# X
# .label:
# ...
#
# which this function transforms back into a regular branch pattern by moving
# the label one step back and replacing the delay slot by a nop.
#
# Branch-likely instructions that do not appear in this pattern are kept.
#
# We also do this for b instructions, which sometimes occur in the same pattern,
# and also fix up the pattern
#
# <branch likely instr> .label
#  X
# .label:
#
# which GCC emits.
def normalize_likely_branches(function: Function) -> Function:
    label_prev_instr: Dict[str, Optional[Instruction]] = {}
    label_before_instr: Dict[int, str] = {}
    instr_before_instr: Dict[int, Instruction] = {}
    prev_instr: Optional[Instruction] = None
    prev_label: Optional[Label] = None
    prev_item: Union[Instruction, Label, None] = None
    for item in function.body:
        if isinstance(item, Instruction):
            if prev_label is not None:
                label_before_instr[id(item)] = prev_label.name
                prev_label = None
            if isinstance(prev_item, Instruction):
                instr_before_instr[id(item)] = prev_item
            prev_instr = item
        elif isinstance(item, Label):
            label_prev_instr[item.name] = prev_instr
            prev_label = item
            prev_instr = None
        prev_item = item

    insert_label_before: Dict[int, str] = {}
    new_body: List[Tuple[Union[Instruction, Label], Union[Instruction, Label]]] = []

    body_iter: Iterator[Union[Instruction, Label]] = iter(function.body)
    for item in body_iter:
        orig_item = item
        if isinstance(item, Instruction) and (
            item.is_branch_likely_instruction() or item.mnemonic == "b"
        ):
            old_label = item.get_branch_target().target
            before_target = label_prev_instr[old_label]
            before_before_target = (
                instr_before_instr.get(id(before_target))
                if before_target is not None
                else None
            )
            next_item = next(body_iter)
            orig_next_item = next_item
            if (
                item.mnemonic == "b"
                and before_before_target is not None
                and before_before_target.is_branch_instruction()
            ):
                # Don't treat 'b' instructions as branch likelies if doing so would
                # introduce a label in a delay slot.
                new_body.append((item, item))
                new_body.append((next_item, next_item))
            elif (
                isinstance(next_item, Instruction)
                and before_target is next_item
                and item.mnemonic != "b"
            ):
                mn_inverted = invert_branch_mnemonic(item.mnemonic[:-1])
                item = Instruction(mn_inverted, item.args, item.emit_goto)
                new_body.append((orig_item, item))
                new_body.append((Instruction("dummy", []), Instruction("nop", [])))
                new_body.append((orig_next_item, next_item))
            elif (
                isinstance(next_item, Instruction)
                and before_target is not None
                and before_target is not next_item
                and str(before_target) == str(next_item)
                and (item.mnemonic != "b" or next_item.mnemonic != "nop")
            ):
                if id(before_target) not in label_before_instr:
                    new_label = old_label + "_before"
                    label_before_instr[id(before_target)] = new_label
                    insert_label_before[id(before_target)] = new_label
                new_target = JumpTarget(label_before_instr[id(before_target)])
                mn_unlikely = item.mnemonic[:-1] or "b"
                item = Instruction(
                    mn_unlikely, item.args[:-1] + [new_target], item.emit_goto
                )
                next_item = Instruction("nop", [])
                new_body.append((orig_item, item))
                new_body.append((orig_next_item, next_item))
            else:
                new_body.append((item, item))
                new_body.append((next_item, next_item))
        else:
            new_body.append((orig_item, item))

    new_function = function.bodyless_copy()
    for (orig_item, new_item) in new_body:
        if id(orig_item) in insert_label_before:
            new_function.new_label(insert_label_before[id(orig_item)])
        new_function.body.append(new_item)

    return new_function


def prune_unreferenced_labels(function: Function, rodata: Rodata) -> Function:
    labels_used: Set[str] = {
        label.name
        for label in function.body
        if isinstance(label, Label) and label.name in rodata.mentioned_labels
    }
    for item in function.body:
        if isinstance(item, Instruction) and item.is_branch_instruction():
            labels_used.add(item.get_branch_target().target)

    new_function = function.bodyless_copy()
    for item in function.body:
        if not (isinstance(item, Label) and item.name not in labels_used):
            new_function.body.append(item)

    return new_function


# Detect and simplify various standard patterns emitted by the IRIX compiler.
# Currently handled:
# - checks for x/0 and INT_MIN/-1 after division (removed)
# - unsigned to float conversion (converted to a made-up instruction)
# - float/double to unsigned conversion (converted to a made-up instruction)
def simplify_standard_patterns(function: Function) -> Function:
    BodyPart = Union[Instruction, Label]

    div_pattern: List[str] = [
        "bnez",
        "nop",
        "break",
        "",
        "li $at, -1",
        "bne",
        "li $at, 0x80000000",
        "bne",
        "nop",
        "break",
        "",
    ]

    divu_pattern: List[str] = [
        "bnez",
        "nop",
        "break",
        "",
    ]

    div_p2_pattern_1: List[str] = [
        "bgez",
        "sra",
        "addiu",
        "sra",
        "",
    ]

    div_p2_pattern_2: List[str] = [
        "bgez",
        "move",
        "addiu",
        "",
        "sra",
    ]

    utf_pattern: List[str] = [
        "bgez",
        "cvt.s.w",
        "li $at, 0x4f800000",
        "mtc1",
        "nop",
        "add.s",
        "",
    ]

    ftu_pattern: List[str] = [
        "cfc1",  # cfc1 Y, $31
        "nop",
        "andi",
        "andi*",  # (skippable)
        "?",  # bnez or bneql
        "?",
        "li*",
        "mtc1",
        "mtc1*",
        "li",
        "?",  # sub.fmt ?, X, ?
        "ctc1",
        "nop",
        "?",  # cvt.w.fmt ?, ?
        "cfc1",
        "nop",
        "andi",
        "andi*",
        "bnez",
        "nop",
        "mfc1",
        "li",
        "b",
        "or",
        "",
        "b",
        "li",
        "?",  # label: (moved one step down if bneql)
        "?",  # mfc1
        "nop",
        "bltz",
        "nop",
    ]

    def get_li_imm(ins: Instruction) -> Optional[int]:
        if ins.mnemonic == "li" and isinstance(ins.args[1], AsmLiteral):
            return ins.args[1].value & 0xFFFFFFFF
        return None

    def matches_pattern(actual: List[BodyPart], pattern: List[str]) -> int:
        def match_one(actual: BodyPart, expected: str) -> bool:
            if expected == "?":
                return True
            if not isinstance(actual, Instruction):
                return expected == ""
            ins = actual
            exp = parse_instruction(expected, emit_goto=False)
            if not exp.args:
                if exp.mnemonic == "li" and ins.mnemonic in ["lui", "addiu"]:
                    return True
                return ins.mnemonic == exp.mnemonic
            if str(ins) == str(exp):
                return True
            # A bit of an ugly hack, but since 'li' can be spelled many ways...
            return (
                exp.mnemonic == "li"
                and exp.args[0] == ins.args[0]
                and isinstance(exp.args[1], AsmLiteral)
                and (exp.args[1].value & 0xFFFFFFFF) == get_li_imm(ins)
            )

        actuali = 0
        for pat in pattern:
            matches = actuali < len(actual) and match_one(
                actual[actuali], pat.rstrip("*")
            )
            if matches:
                actuali += 1
            elif not pat.endswith("*"):
                return 0
        return actuali

    def create_div_p2(bgez: Instruction, sra: Instruction) -> Instruction:
        assert isinstance(sra.args[2], AsmLiteral)
        shift = sra.args[2].value & 0x1F
        return Instruction(
            "div.fictive", [sra.args[0], bgez.args[0], AsmLiteral(2 ** shift)]
        )

    def try_replace_div(i: int) -> Optional[Tuple[List[BodyPart], int]]:
        actual = function.body[i : i + len(div_pattern)]
        if not matches_pattern(actual, div_pattern):
            return None
        label1 = typing.cast(Label, actual[3])
        label2 = typing.cast(Label, actual[10])
        bnez = typing.cast(Instruction, actual[0])
        bne1 = typing.cast(Instruction, actual[5])
        bne2 = typing.cast(Instruction, actual[7])
        if (
            bnez.get_branch_target().target != label1.name
            or bne1.get_branch_target().target != label2.name
            and bne2.get_branch_target().target != label2.name
        ):
            return None
        return ([], i + len(div_pattern) - 1)

    def try_replace_divu(i: int) -> Optional[Tuple[List[BodyPart], int]]:
        actual = function.body[i : i + len(divu_pattern)]
        if not matches_pattern(actual, divu_pattern):
            return None
        label = typing.cast(Label, actual[3])
        bnez = typing.cast(Instruction, actual[0])
        if bnez.get_branch_target().target != label.name:
            return None
        return ([], i + len(divu_pattern) - 1)

    def try_replace_div_p2_1(i: int) -> Optional[Tuple[List[BodyPart], int]]:
        actual = function.body[i : i + len(div_p2_pattern_1)]
        if not matches_pattern(actual, div_p2_pattern_1):
            return None
        if typing.cast(Instruction, actual[2]).args[0] != Register("at"):
            return None
        label = typing.cast(Label, actual[4])
        bnez = typing.cast(Instruction, actual[0])
        if bnez.get_branch_target().target != label.name:
            return None
        div = create_div_p2(bnez, typing.cast(Instruction, actual[3]))
        return ([div], i + len(div_p2_pattern_1) - 1)

    def try_replace_div_p2_2(i: int) -> Optional[Tuple[List[BodyPart], int]]:
        actual = function.body[i : i + len(div_p2_pattern_2)]
        if not matches_pattern(actual, div_p2_pattern_2):
            return None
        if typing.cast(Instruction, actual[1]).args[0] != Register("at"):
            return None
        if typing.cast(Instruction, actual[2]).args[0] != Register("at"):
            return None
        label = typing.cast(Label, actual[3])
        bnez = typing.cast(Instruction, actual[0])
        if bnez.get_branch_target().target != label.name:
            return None
        div = create_div_p2(bnez, typing.cast(Instruction, actual[4]))
        return ([div], i + len(div_p2_pattern_2))

    def try_replace_utf_conv(i: int) -> Optional[Tuple[List[BodyPart], int]]:
        actual = function.body[i : i + len(utf_pattern)]
        if not matches_pattern(actual, utf_pattern):
            return None
        label = typing.cast(Label, actual[6])
        bgez = typing.cast(Instruction, actual[0])
        if bgez.get_branch_target().target != label.name:
            return None
        cvt_instr = typing.cast(Instruction, actual[1])
        new_instr = Instruction(mnemonic="cvt.s.u.fictive", args=cvt_instr.args)
        return ([new_instr], i + len(utf_pattern) - 1)

    def try_replace_ftu_conv(i: int) -> Optional[Tuple[List[BodyPart], int]]:
        actual = function.body[i : i + len(ftu_pattern)]
        consumed = matches_pattern(actual, ftu_pattern)
        if not consumed:
            return None
        sub = next(
            x
            for x in actual
            if isinstance(x, Instruction) and x.mnemonic.startswith("sub")
        )
        cfc = actual[0]
        assert isinstance(cfc, Instruction)
        fmt = sub.mnemonic.split(".")[-1]
        args = [cfc.args[0], sub.args[1]]
        if fmt == "s":
            new_instr = Instruction(mnemonic="cvt.u.s.fictive", args=args)
        else:
            new_instr = Instruction(mnemonic="cvt.u.d.fictive", args=args)
        return ([new_instr], i + consumed)

    def try_replace_mips1_double_load_store(
        i: int,
    ) -> Optional[Tuple[List[BodyPart], int]]:
        # TODO: sometimes the instructions aren't consecutive.
        actual = function.body[i : i + 2]
        if not matches_pattern(actual, ["lwc1", "lwc1"]) and not matches_pattern(
            actual, ["swc1", "swc1"]
        ):
            return None
        a, b = actual
        assert isinstance(a, Instruction)
        assert isinstance(b, Instruction)
        ra, rb = a.args[0], b.args[0]
        ma, mb = a.args[1], b.args[1]
        # TODO: verify that the memory locations are consecutive as well (a bit
        # annoying with macros...)
        if not (
            isinstance(ra, Register)
            and ra.is_float()
            and ra.other_f64_reg() == rb
            and isinstance(ma, AsmAddressMode)
            and isinstance(mb, AsmAddressMode)
            and ma.rhs == mb.rhs
        ):
            return None
        num = int(ra.register_name[1:])
        if num % 2 == 1:
            ra, rb = rb, ra
            ma, mb = mb, ma
        # Store the even-numbered register (ra) into the low address (mb).
        new_args = [ra, mb]
        new_mn = "ldc1" if a.mnemonic == "lwc1" else "sdc1"
        new_instr = Instruction(mnemonic=new_mn, args=new_args)
        return ([new_instr], i + 2)

    def no_replacement(i: int) -> Tuple[List[BodyPart], int]:
        return ([function.body[i]], i + 1)

    new_function = function.bodyless_copy()
    i = 0
    while i < len(function.body):
        repl, i = (
            try_replace_div(i)
            or try_replace_divu(i)
            or try_replace_div_p2_1(i)
            or try_replace_div_p2_2(i)
            or try_replace_utf_conv(i)
            or try_replace_ftu_conv(i)
            or try_replace_mips1_double_load_store(i)
            or no_replacement(i)
        )
        new_function.body.extend(repl)
    return new_function


def build_blocks(function: Function, rodata: Rodata) -> List[Block]:
    function = normalize_likely_branches(function)
    function = prune_unreferenced_labels(function, rodata)
    function = simplify_standard_patterns(function)
    function = prune_unreferenced_labels(function, rodata)

    block_builder = BlockBuilder()

    body_iter: Iterator[Union[Instruction, Label]] = iter(function.body)

    def process(item: Union[Instruction, Label]) -> None:
        if isinstance(item, Label):
            # Split blocks at labels.
            block_builder.new_block()
            block_builder.set_label(item)
            return

        if not item.is_delay_slot_instruction():
            block_builder.add_instruction(item)
            return

        process_after: List[Union[Instruction, Label]] = []
        next_item = next(body_iter)
        if isinstance(next_item, Label):
            # Delay slot is a jump target, so we need the delay slot
            # instruction to be in two blocks at once... In most cases,
            # we can just duplicate it. (This happens from time to time
            # in -O2-compiled code.)
            label = next_item
            next_item = next(body_iter)

            assert isinstance(next_item, Instruction), "Cannot have two labels in a row"

            # (Best-effort check for whether the instruction can be
            # executed twice in a row.)
            r = next_item.args[0] if next_item.args else None
            if all(a != r for a in next_item.args[1:]):
                process_after.append(label)
                process_after.append(next_item)
            else:
                msg = [
                    f"Label {label.name} refers to a delay slot; this is currently not supported.",
                    "Please modify the assembly to work around it (e.g. copy the instruction",
                    "to all jump sources and move the label, or add a nop to the delay slot).",
                ]
                if "_before" in label.name:
                    msg += [
                        "",
                        "This label was auto-generated as the target for a branch-likely",
                        "instruction (e.g. beql); you can also try to turn that into a non-likely",
                        "branch if that's equivalent in this context, i.e., if it's okay to",
                        "execute its delay slot unconditionally.",
                    ]
                raise DecompFailure("\n".join(msg))

        if next_item.is_delay_slot_instruction():
            raise DecompFailure(
                f"Two delay slot instructions in a row is not supported:\n{item}\n{next_item}"
            )

        if item.is_branch_likely_instruction():
            target = item.get_branch_target()
            mn_inverted = invert_branch_mnemonic(item.mnemonic[:-1])
            temp_label = JumpTarget(target.target + "_branchlikelyskip")
            branch_not = Instruction(mn_inverted, item.args[:-1] + [temp_label], False)
            block_builder.add_instruction(branch_not)
            block_builder.add_instruction(Instruction("nop", []))
            block_builder.new_block()
            block_builder.add_instruction(next_item)
            block_builder.add_instruction(Instruction("b", [target], item.emit_goto))
            block_builder.add_instruction(Instruction("nop", []))
            block_builder.new_block()
            block_builder.set_label(Label(temp_label.target))

        elif item.mnemonic in ["jal", "jalr"]:
            # Move the delay slot instruction to before the call so it
            # passes correct arguments.
            if next_item.args and next_item.args[0] == item.args[0]:
                raise DecompFailure(
                    f"Instruction after {item.mnemonic} clobbers its source\n"
                    "register, which is currently not supported.\n\n"
                    "Try rewriting the assembly to avoid that."
                )
            block_builder.add_instruction(next_item)
            block_builder.add_instruction(item)
        else:
            block_builder.add_instruction(item)
            block_builder.add_instruction(next_item)

        if item.is_jump_instruction():
            # Split blocks at jumps, after the next instruction.
            block_builder.new_block()

        for item in process_after:
            process(item)

    for item in body_iter:
        process(item)

    if block_builder.curr_label:
        label = block_builder.curr_label.name
        print(f'Warning: missing "jr $ra" in last block (.{label}).\n')
        block_builder.add_instruction(Instruction("jr", [Register("ra")]))
        block_builder.add_instruction(Instruction("nop", []))
        block_builder.new_block()

    # Throw away whatever is past the last "jr $ra" and return what we have.
    return block_builder.get_blocks()


def is_self_loop_edge(node: "Node", edge: "Node") -> bool:
    return node is edge


def is_loop_edge(node: "Node", edge: "Node") -> bool:
    # Loops are represented by backwards jumps.
    return edge.block.index <= node.block.index


# TODO: Nodes need to be frozen.
# We use dictionaries and sets of nodes throughout the codebase,
# and it would be helpful to define __eq__ using `self.block.index`.
# This would also keep code from silently modifying Nodes.
# It just makes more sense.
@attr.s(eq=False)
class BaseNode(abc.ABC):
    block: Block = attr.ib()
    emit_goto: bool = attr.ib()
    parents: Set["Node"] = attr.ib(init=False, factory=set)
    dominators: Set["Node"] = attr.ib(init=False, factory=set)
    immediate_dominator: Optional["Node"] = attr.ib(init=False, default=None)
    immediately_dominates: List["Node"] = attr.ib(init=False, factory=list)

    def to_basic_node(self, successor: "Node") -> "BasicNode":
        new_node = BasicNode(self.block, self.emit_goto, successor)
        new_node.parents = self.parents
        new_node.dominators = self.dominators
        new_node.immediate_dominator = self.immediate_dominator
        new_node.immediately_dominates = self.immediately_dominates
        return new_node

    def add_parent(self, parent: "Node") -> None:
        self.parents.add(parent)

    def replace_parent(self, replace_this: "Node", with_this: "Node") -> None:
        if replace_this in self.parents:
            self.parents.remove(replace_this)
            self.parents.add(with_this)

    @abc.abstractmethod
    def children(self) -> List["Node"]:
        ...

    @abc.abstractmethod
    def replace_any_children(self, replace_this: "Node", with_this: "Node") -> None:
        ...

    def name(self) -> str:
        return str(self.block.index)


@attr.s(eq=False)
class BasicNode(BaseNode):
    successor: "Node" = attr.ib()

    def replace_any_children(self, replace_this: "Node", with_this: "Node") -> None:
        if self.successor is replace_this:
            self.successor = with_this

    def is_loop(self) -> bool:
        return is_loop_edge(self, self.successor)

    def children(self) -> List["Node"]:
        return [self.successor]

    def __str__(self) -> str:
        return "".join(
            [
                f"{self.block}\n",
                f"# {self.block.index} -> {self.successor.block.index}",
                " (loop)" if self.is_loop() else "",
                " (goto)" if self.emit_goto else "",
            ]
        )


@attr.s(eq=False)
class ConditionalNode(BaseNode):
    conditional_edge: "Node" = attr.ib()
    fallthrough_edge: "Node" = attr.ib()

    def replace_any_children(self, replace_this: "Node", with_this: "Node") -> None:
        if self.conditional_edge is replace_this:
            self.conditional_edge = with_this
        if self.fallthrough_edge is replace_this:
            self.fallthrough_edge = with_this

    def is_self_loop(self) -> bool:
        return is_self_loop_edge(self, self.conditional_edge)

    def is_loop(self) -> bool:
        return is_loop_edge(self, self.conditional_edge)

    def children(self) -> List["Node"]:
        return [self.conditional_edge, self.fallthrough_edge]

    def __str__(self) -> str:
        return "".join(
            [
                f"{self.block}\n",
                f"# {self.block.index} -> ",
                f"cond: {self.conditional_edge.block.index}",
                " (loop)" if self.is_loop() else "",
                ", ",
                f"def: {self.fallthrough_edge.block.index}",
                " (goto)" if self.emit_goto else "",
            ]
        )


@attr.s(eq=False)
class ReturnNode(BaseNode):
    index: int = attr.ib()

    def replace_any_children(self, replace_this: "Node", with_this: "Node") -> None:
        pass

    def name(self) -> str:
        name = super().name()
        return name if self.is_real() else f"{name}.{self.index}"

    def children(self) -> List["Node"]:
        return []

    def is_real(self) -> bool:
        return self.index == 0

    def __str__(self) -> str:
        return "".join(
            [
                f"{self.block}\n",
                f"# {self.block.index} -> ret",
                " (goto)" if self.emit_goto else "",
            ]
        )


@attr.s(eq=False)
class SwitchNode(BaseNode):
    cases: List["Node"] = attr.ib()

    def replace_any_children(self, replace_this: "Node", with_this: "Node") -> None:
        new_cases: List["Node"] = []
        for case in self.cases:
            if case is replace_this:
                new_cases.append(with_this)
                with_this.add_parent(self)
            else:
                new_cases.append(case)
        self.cases = new_cases

    def children(self) -> List["Node"]:
        return self.cases

    def __str__(self) -> str:
        targets = ", ".join(str(c.block.index) for c in self.cases)
        return f"{self.block}\n# {self.block.index} -> {targets}"


Node = Union[BasicNode, ConditionalNode, ReturnNode, SwitchNode]


def build_graph_from_block(
    block: Block, blocks: List[Block], nodes: List[Node], rodata: Rodata
) -> Node:
    # Don't reanalyze blocks.
    for node in nodes:
        if node.block == block:
            return node

    new_node: Node
    dummy_node: Any = None

    def find_block_by_label(label: str) -> Optional[Block]:
        for block in blocks:
            if block.label and block.label.name == label:
                return block
        return None

    # Extract branching instructions from this block.
    jumps: List[Instruction] = [
        inst for inst in block.instructions if inst.is_jump_instruction()
    ]
    assert len(jumps) in [0, 1], "too many jump instructions in one block"

    if len(jumps) == 0:
        # No jumps, i.e. the next block is this node's successor block.
        new_node = BasicNode(block, False, dummy_node)
        nodes.append(new_node)

        # Recursively analyze.
        next_block = blocks[block.index + 1]
        new_node.successor = build_graph_from_block(next_block, blocks, nodes, rodata)

        # Keep track of parents.
        new_node.successor.add_parent(new_node)
    elif len(jumps) == 1:
        # There is a jump. This is either:
        # - a ReturnNode, if it's "jr $ra",
        # - a SwitchNode, if it's "jr $something_else",
        # - a BasicNode, if it's an unconditional branch, or
        # - a ConditionalNode.
        jump = jumps[0]

        if jump.mnemonic == "jr" and jump.args[0] == Register("ra"):
            new_node = ReturnNode(block, False, index=0)
            nodes.append(new_node)
            return new_node

        if jump.mnemonic == "jr":
            new_node = SwitchNode(block, True, [])
            nodes.append(new_node)

            jtbl_names = []
            for ins in block.instructions:
                for arg in ins.args:
                    if (
                        isinstance(arg, AsmAddressMode)
                        and isinstance(arg.lhs, Macro)
                        and arg.lhs.macro_name == "lo"
                        and isinstance(arg.lhs.argument, AsmGlobalSymbol)
                        and any(
                            arg.lhs.argument.symbol_name.startswith(prefix)
                            for prefix in ("jtbl", "jpt_")
                        )
                    ):
                        jtbl_names.append(arg.lhs.argument.symbol_name)
            if len(jtbl_names) != 1:
                raise DecompFailure(
                    "Unable to determine jump table for jr instruction.\n\n"
                    "There must be a read of a variable in the same block as\n"
                    'the instruction, which has a name starting with "jtbl"/"jpt_".'
                )

            jtbl_name = jtbl_names[0]
            if jtbl_name not in rodata.values:
                raise DecompFailure(
                    "Found jr instruction, but the corresponding jump table is not provided.\n\n"
                    "Please pass a --rodata flag to mips_to_c, pointing to the right .s file.\n\n"
                    "(You might need to pass --goto and --no-andor flags as well,\n"
                    "to get correct control flow for non-jtbl switch jumps.)"
                )

            jtbl_entries = rodata.values[jtbl_name].data
            for entry in jtbl_entries:
                if isinstance(entry, bytes):
                    # We have entered padding, stop reading.
                    break
                entry = entry.lstrip(".")
                case_block = find_block_by_label(entry)
                if case_block is None:
                    raise DecompFailure(f"Cannot find jtbl target {entry}")
                case_node = build_graph_from_block(case_block, blocks, nodes, rodata)
                new_node.cases.append(case_node)
                if new_node not in case_node.parents:
                    case_node.add_parent(new_node)

            return new_node

        assert jump.is_branch_instruction()

        # Get the block associated with the jump target.
        branch_label = jump.get_branch_target()
        branch_block = find_block_by_label(branch_label.target)
        if branch_block is None:
            target = branch_label.target
            raise DecompFailure(f"Cannot find branch target {target}")

        is_constant_branch = jump.mnemonic in ["b", "j"]
        if is_constant_branch:
            # A constant branch becomes a basic edge to our branch target.
            new_node = BasicNode(block, jump.emit_goto, dummy_node)
            nodes.append(new_node)
            # Recursively analyze.
            new_node.successor = build_graph_from_block(
                branch_block, blocks, nodes, rodata
            )
            # Keep track of parents.
            new_node.successor.add_parent(new_node)
        else:
            # A conditional branch means the fallthrough block is the next
            # block if the branch isn't.
            new_node = ConditionalNode(block, jump.emit_goto, dummy_node, dummy_node)
            nodes.append(new_node)
            # Recursively analyze this too.
            next_block = blocks[block.index + 1]
            new_node.conditional_edge = build_graph_from_block(
                branch_block, blocks, nodes, rodata
            )
            new_node.fallthrough_edge = build_graph_from_block(
                next_block, blocks, nodes, rodata
            )
            # Keep track of parents.
            new_node.conditional_edge.add_parent(new_node)
            new_node.fallthrough_edge.add_parent(new_node)

    return new_node


def is_trivial_return_block(block: Block) -> bool:
    # A heuristic for when a block is a simple "early-return" block.
    # This could be improved.
    stores = ["sb", "sh", "sw", "swc1", "sdc1", "swr", "swl", "jal"]
    return_regs = [Register("v0"), Register("f0")]
    for instr in block.instructions:
        if instr.mnemonic in stores:
            return False
        if any(reg in instr.args for reg in return_regs):
            return False
    return True


def build_nodes(function: Function, blocks: List[Block], rodata: Rodata) -> List[Node]:
    graph: List[Node] = []

    # Traverse through the block tree.
    entry_block = blocks[0]
    build_graph_from_block(entry_block, blocks, graph, rodata)

    # Sort the nodes by index.
    graph.sort(key=lambda node: node.block.index)
    return graph


def is_premature_return(node: Node, edge: Node, nodes: List[Node]) -> bool:
    """Check whether a given edge in the flow graph is an early return."""
    if not isinstance(edge, ReturnNode) or edge != nodes[-1]:
        return False
    if not is_trivial_return_block(edge.block):
        # Only trivial return blocks can be used for premature returns,
        # hopefully.
        return False
    if not isinstance(node, BasicNode):
        # We only treat BasicNode's as being able to return early right now.
        # (Handling ConditionalNode's seems to cause assertion failures --
        # might need changes to build_flowgraph_between.)
        return False
    # The only node that is allowed to point to the return node is the node
    # before it in the flow graph list. (You'd think it would be the node
    # with index = return_node.index - 1, but that's not necessarily the
    # case -- some functions have a dead penultimate block with a
    # superfluous unreachable return.)
    return node != nodes[-2]


def duplicate_premature_returns(nodes: List[Node]) -> List[Node]:
    """For each jump to an early return node, create a duplicate return node
    for it to jump to. This ensures nice nesting for the if_statements code,
    and avoids a phi node for the return value."""
    extra_nodes: List[Node] = []
    index = 0
    for node in nodes:
        if (
            isinstance(node, BasicNode)
            and not node.emit_goto
            and is_premature_return(node, node.successor, nodes)
        ):
            assert isinstance(node.successor, ReturnNode)
            node.successor.parents.remove(node)
            index += 1
            n = ReturnNode(node.successor.block.clone(), False, index=index)
            node.successor = n
            n.add_parent(node)
            extra_nodes.append(n)

    nodes += extra_nodes
    nodes.sort(key=lambda node: node.block.index)
    return [n for n in nodes if n.parents or n == nodes[0]]


def ensure_fallthrough(nodes: List[Node]) -> None:
    """For any node which is only reachable through indirect jumps (switch
    labels, loop edges, emit_goto edges), mark its predecessor as emit_goto to
    ensure we continue to generate code after it."""
    last_reachable: Set[Node] = set()
    while True:
        # Traverse all edges other than indirect jumps, and collect the targets.
        reachable: Set[Node] = {nodes[0]}
        predecessors: Dict[Node, Node] = {}
        for i, node in enumerate(nodes):
            fallthrough: Optional[Node]
            if i + 1 < len(nodes):
                fallthrough = nodes[i + 1]
                predecessors[fallthrough] = node
            else:
                fallthrough = None
            if isinstance(node, BasicNode):
                if node.emit_goto and fallthrough is not None:
                    reachable.add(fallthrough)
                if not node.emit_goto and not node.is_loop():
                    reachable.add(node.successor)
            elif isinstance(node, ConditionalNode):
                assert fallthrough is not None
                assert fallthrough == node.fallthrough_edge
                reachable.add(fallthrough)
                if not node.emit_goto and not node.is_loop():
                    reachable.add(node.conditional_edge)
            elif isinstance(node, SwitchNode):
                assert fallthrough is not None
                reachable.add(fallthrough)
            else:  # ReturnNode
                if node.emit_goto and fallthrough is not None:
                    reachable.add(fallthrough)

        # We can make the first node not in the set be included in the set by
        # making its predecessor fall through, and then repeat. For efficiency
        # we actually do this for all nodes not in the set, but note that we
        # still need the repeat, since marking nodes fallthrough can cause
        # edges to disappear.
        #
        # At each point we mark more and more nodes fallthrough, so eventually
        # this process will terminate.
        assert reachable != last_reachable, "Fallthrough process hit a cycle"
        last_reachable = reachable
        unreachable = set(nodes).difference(reachable)
        if not unreachable:
            break
        for node in unreachable:
            assert node in predecessors, "Start node is never unreachable"
            pre = predecessors[node]
            assert not isinstance(pre, ConditionalNode)
            pre.emit_goto = True


def compute_parents(nodes: List[Node]) -> None:
    for node in nodes:
        node.parents = set()
    for node in nodes:
        for child in node.children():
            child.parents.add(node)


def compute_dominators_and_parents(nodes: List[Node]) -> None:
    """Compute or recompute the dominators and parents of the given nodes."""
    for node in nodes:
        node.dominators = set()
        node.immediate_dominator = None
        node.immediately_dominates = []
    compute_parents(nodes)

    entry = nodes[0]
    entry.dominators = {entry}
    for n in nodes[1:]:
        n.dominators = set(nodes)

    changes = True
    while changes:
        changes = False
        for n in nodes[1:]:
            assert n.parents, f"no predecessors for node: {n}"
            nset = n.dominators
            for p in n.parents:
                nset = nset.intersection(p.dominators)
            nset = {n}.union(nset)
            if len(nset) < len(n.dominators):
                n.dominators = nset
                changes = True

    for n in nodes[1:]:
        doms = n.dominators.difference({n})
        n.immediate_dominator = max(doms, key=lambda d: len(d.dominators))
        n.immediate_dominator.immediately_dominates.append(n)
    for n in nodes:
        n.immediately_dominates.sort(key=lambda x: x.block.index)


@attr.s(frozen=True)
class FlowGraph:
    nodes: List[Node] = attr.ib()

    def entry_node(self) -> Node:
        return self.nodes[0]

    def return_node(self) -> Optional[ReturnNode]:
        candidates = [
            n for n in self.nodes if isinstance(n, ReturnNode) and n.is_real()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda n: n.block.index)


def build_flowgraph(function: Function, rodata: Rodata) -> FlowGraph:
    blocks = build_blocks(function, rodata)
    nodes = build_nodes(function, blocks, rodata)
    nodes = duplicate_premature_returns(nodes)
    ensure_fallthrough(nodes)
    compute_dominators_and_parents(nodes)
    return FlowGraph(nodes)


def visualize_flowgraph(flow_graph: FlowGraph) -> None:
    import graphviz as g

    dot = g.Digraph()
    for node in flow_graph.nodes:
        dot.node(node.name())
        if isinstance(node, BasicNode):
            dot.edge(node.name(), node.successor.name(), color="green")
        elif isinstance(node, ConditionalNode):
            dot.edge(node.name(), node.fallthrough_edge.name(), color="blue")
            dot.edge(node.name(), node.conditional_edge.name(), color="red")
        else:
            pass
    dot.render("graphviz_render.gv")
    print("Rendered to graphviz_render.gv.pdf")
    print("Key: green = successor, red = conditional edge, blue = fallthrough edge")
