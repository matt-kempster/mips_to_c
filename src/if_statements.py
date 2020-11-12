from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple, Union

import attr

from .flow_graph import (
    BasicNode,
    Block,
    ConditionalNode,
    FlowGraph,
    Node,
    ReturnNode,
    SwitchNode,
)
from .options import CodingStyle, Options
from .translate import (
    BinaryOp,
    BlockInfo,
    CommaConditionExpr,
    Condition,
    Formatter,
    FunctionInfo,
    Statement as TrStatement,
    Type,
    simplify_condition,
    format_expr,
)


@attr.s
class Context:
    flow_graph: FlowGraph = attr.ib()
    fmt: Formatter = attr.ib()
    options: Options = attr.ib()
    reachable_without: Dict[Tuple[Node, Node], Set[Node]] = attr.ib(factory=dict)
    is_void: bool = attr.ib(default=True)
    switch_nodes: Dict[SwitchNode, int] = attr.ib(factory=dict)
    case_nodes: Dict[Node, List[Tuple[int, int]]] = attr.ib(
        factory=lambda: defaultdict(list)
    )
    goto_nodes: Set[Node] = attr.ib(factory=set)
    loop_nodes: Set[Node] = attr.ib(factory=set)
    emitted_nodes: Set[Node] = attr.ib(factory=set)
    has_warned: bool = attr.ib(default=False)


@attr.s
class IfElseStatement:
    condition: Condition = attr.ib()
    indent: int = attr.ib()
    if_body: "Body" = attr.ib()
    else_body: Optional["Body"] = attr.ib(default=None)

    def should_write(self) -> bool:
        return True

    def format(self, fmt: Formatter) -> str:
        space = fmt.indent(self.indent, "")
        condition = simplify_condition(self.condition)
        cond_str = format_expr(condition, fmt)
        after_ifelse = f"\n{space}" if fmt.coding_style.newline_after_if else " "
        before_else = f"\n{space}" if fmt.coding_style.newline_before_else else " "
        if_str = "\n".join(
            [
                f"{space}if ({cond_str}){after_ifelse}{{",
                self.if_body.format(fmt),  # has its own indentation
                f"{space}}}",
            ]
        )
        if self.else_body is not None and not self.else_body.is_empty():
            sub_if = self.else_body.get_lone_if_statement()
            if sub_if:
                fmt.extra_indent -= 1
                sub_if_str = sub_if.format(fmt).lstrip()
                fmt.extra_indent += 1
                else_str = f"{before_else}else {sub_if_str}"
            else:
                else_str = "\n".join(
                    [
                        f"{before_else}else{after_ifelse}{{",
                        self.else_body.format(fmt),
                        f"{space}}}",
                    ]
                )
            if_str = if_str + else_str
        return if_str


@attr.s
class SimpleStatement:
    indent: int = attr.ib()
    contents: Union[str, TrStatement] = attr.ib()

    def should_write(self) -> bool:
        return True

    def format(self, fmt: Formatter) -> str:
        if isinstance(self.contents, str):
            return fmt.indent(self.indent, self.contents)
        else:
            return fmt.indent(self.indent, self.contents.format(fmt))


@attr.s
class LabelStatement:
    indent: int = attr.ib()
    context: Context = attr.ib()
    node: Node = attr.ib()

    def should_write(self) -> bool:
        return (
            self.node in self.context.goto_nodes or self.node in self.context.case_nodes
        )

    def format(self, fmt: Formatter) -> str:
        lines = []
        if self.node in self.context.case_nodes:
            for (switch, case) in self.context.case_nodes[self.node]:
                case_str = f"case {case}" if case != -1 else "default"
                switch_str = f" // switch {switch}" if switch != 0 else ""
                lines.append(fmt.indent(self.indent, f"{case_str}:{switch_str}"))
        if self.node in self.context.goto_nodes:
            lines.append(f"{label_for_node(self.context, self.node)}:")
        return "\n".join(lines)


@attr.s
class DoWhileLoop:
    indent: int = attr.ib()
    coding_style: CodingStyle = attr.ib()

    body: "Body" = attr.ib()
    condition: Optional[Condition] = attr.ib(default=None)

    def should_write(self) -> bool:
        return True

    def format(self, fmt: Formatter) -> str:
        space = fmt.indent(self.indent, "")
        after_do = f"\n{space}" if self.coding_style.newline_after_if else " "
        cond = format_expr(self.condition, fmt) if self.condition else ""
        return "\n".join(
            [
                f"{space}do{after_do}{{",
                self.body.format(fmt),
                f"{space}}} while ({cond});",
            ]
        )


Statement = Union[SimpleStatement, IfElseStatement, LabelStatement, DoWhileLoop]


@attr.s
class Body:
    print_node_comment: bool = attr.ib()
    statements: List[Statement] = attr.ib(factory=list)

    def add_node(self, node: Node, indent: int, comment_empty: bool) -> None:
        assert isinstance(node.block.block_info, BlockInfo)
        to_write = node.block.block_info.to_write
        any_to_write = any(item.should_write() for item in to_write)

        # Add node header comment
        if self.print_node_comment and (any_to_write or comment_empty):
            self.add_comment(indent, f"Node {node.name()}")
        # Add node contents
        for item in node.block.block_info.to_write:
            if item.should_write():
                self.statements.append(SimpleStatement(indent, item))

    def add_statement(self, statement: Statement) -> None:
        self.statements.append(statement)

    def add_comment(self, indent: int, contents: str) -> None:
        self.add_statement(SimpleStatement(indent, f"// {contents}"))

    def add_if_else(self, if_else: IfElseStatement) -> None:
        self.statements.append(if_else)

    def add_do_while_loop(self, do_while_loop: DoWhileLoop) -> None:
        self.statements.append(do_while_loop)

    def is_empty(self) -> bool:
        return not any(statement.should_write() for statement in self.statements)

    def get_lone_if_statement(self) -> Optional[IfElseStatement]:
        """If the body consists solely of one IfElseStatement, return it, else None."""
        ret: Optional[IfElseStatement] = None
        for statement in self.statements:
            if statement.should_write():
                if not isinstance(statement, IfElseStatement) or ret:
                    return None
                ret = statement
        return ret

    def format(self, fmt: Formatter) -> str:
        return "\n".join(
            statement.format(fmt)
            for statement in self.statements
            if statement.should_write()
        )


def label_for_node(context: Context, node: Node) -> str:
    if node in context.loop_nodes:
        return f"loop_{node.block.index}"
    else:
        return f"block_{node.block.index}"


def emit_node(context: Context, node: Node, body: Body, indent: int) -> None:
    """Emit a node, together with a label for it (which is only printed if
    something jumps to it, e.g. currently for loops)."""
    if isinstance(node, ReturnNode) and not node.is_real():
        body.add_node(node, indent, comment_empty=False)
    else:
        body.add_statement(LabelStatement(max(indent - 1, 0), context, node))
        body.add_node(node, indent, comment_empty=True)


def emit_goto(context: Context, target: Node, body: Body, indent: int) -> None:
    label = label_for_node(context, target)
    context.goto_nodes.add(target)
    body.add_statement(SimpleStatement(indent, f"goto {label};"))


def create_goto(context: Context, target: Node, indent: int) -> SimpleStatement:
    label = label_for_node(context, target)
    context.goto_nodes.add(target)
    return SimpleStatement(indent, f"goto {label};")


def emit_switch_jump(
    context: Context, node: SwitchNode, body: Body, indent: int
) -> None:
    block_info = node.block.block_info
    assert isinstance(block_info, BlockInfo)
    expr = block_info.switch_value
    assert expr is not None
    switch_index = context.switch_nodes.get(node, 0)
    comment = f" // switch {switch_index}" if switch_index else ""
    body.add_statement(
        SimpleStatement(indent, f"goto *{format_expr(expr, context.fmt)};{comment}")
    )


def emit_goto_or_early_return(
    context: Context, target: Node, body: Body, indent: int
) -> None:
    """Emit a goto to a node, *unless* that node is an early return, which we
    can't goto to since it's not a real node and won't ever be emitted."""
    if isinstance(target, ReturnNode) and not target.is_real():
        add_return_statement(context, body, target, indent, last=False)
    else:
        emit_goto(context, target, body, indent)


def end_reachable_without(
    context: Context, start: Node, end: Node, without: Node
) -> bool:
    """Return whether "end" is reachable from "start" if "without" were removed."""
    key = (start, without)
    if key in context.reachable_without:
        return end in context.reachable_without[key]

    reachable: Set[Node] = set()
    stack: List[Node] = [start]

    while stack:
        node = stack.pop()
        if node == without or node in reachable:
            continue
        reachable.add(node)
        if isinstance(node, BasicNode):
            stack.append(node.successor)
        elif isinstance(node, ConditionalNode):
            stack.append(node.fallthrough_edge)
            if not node.is_loop():
                # For compatibility with older code, don't add back edges.
                # (It would cause infinite loops before this was rewritten
                # iteratively, with a 'node in reachable' check avoiding
                # loops.) TODO: revisit this?
                stack.append(node.conditional_edge)
        elif isinstance(node, SwitchNode):
            stack.extend(node.cases)
        else:
            _: ReturnNode = node

    context.reachable_without[key] = reachable
    return end in reachable


def get_reachable_nodes(start: Node) -> List[Node]:
    reachable_nodes_set: Set[Node] = set()
    reachable_nodes: List[Node] = []
    stack: List[Node] = [start]
    while stack:
        node = stack.pop()
        if node in reachable_nodes_set:
            continue
        reachable_nodes_set.add(node)
        reachable_nodes.append(node)
        if isinstance(node, BasicNode):
            stack.append(node.successor)
        elif isinstance(node, ConditionalNode):
            if not node.is_loop():
                # This check is wonky, see end_reachable_without.
                # It should be kept the same as in immediate_postdominator.
                stack.append(node.conditional_edge)
            stack.append(node.fallthrough_edge)
        elif isinstance(node, SwitchNode):
            stack.extend(node.cases)
        else:
            _: ReturnNode = node
    return reachable_nodes


def immediate_postdominator(context: Context, start: Node, end: Node) -> Node:
    """
    Find the immediate postdominator of "start", where "end" is an exit node
    from the control flow graph.
    """
    # If the end is unreachable, we are computing immediate postdominators
    # of a subflow where every path ends in an early return. In this case we
    # need to replace our end node, or else every node will be treated as a
    # postdominator, and the earliest one might be within a conditional
    # expression. That in turn can result in nodes emitted multiple times.
    # (TODO: this is rather ad hoc, we should probably come up with a more
    # principled approach to early returns...)
    #
    # Note that we use a List instead of a Set here, since duplicated return
    # nodes may result in multiple nodes with the same block index, and sets
    # have non-deterministic iteration order.
    reachable_nodes: List[Node] = get_reachable_nodes(start)
    if end not in reachable_nodes:
        end = max(reachable_nodes, key=lambda n: n.block.index)

    stack: List[Node] = [start]
    seen: Set[Node] = set()
    postdominators: List[Node] = []
    while stack:
        # Get potential postdominator.
        node = stack.pop()
        if node in seen:
            # Don't revisit nodes.
            continue
        seen.add(node)
        # If removing the node means the end becomes unreachable,
        # the node is a postdominator.
        if node != start and not end_reachable_without(context, start, end, node):
            postdominators.append(node)
        else:
            # Otherwise, add the children of the node and continue the search.
            assert node != end
            if isinstance(node, BasicNode):
                stack.append(node.successor)
            elif isinstance(node, ConditionalNode):
                if not node.is_loop():
                    # This check is wonky, see end_reachable_without.
                    # It should be kept the same as in get_reachable_nodes.
                    stack.append(node.conditional_edge)
                stack.append(node.fallthrough_edge)
            elif isinstance(node, SwitchNode):
                stack.extend(node.cases)
            else:
                _: ReturnNode = node
    assert len(postdominators) == 1, "we should always find exactly one postdominator"
    return postdominators[0]


def build_conditional_subgraph(
    context: Context, start: ConditionalNode, end: Node, indent: int
) -> IfElseStatement:
    """
    Output the subgraph between "start" and "end" at indent level "indent",
    given that "start" is a ConditionalNode; this program will intelligently
    output if/else relationships.
    """
    if_block_info = start.block.block_info
    assert isinstance(if_block_info, BlockInfo)
    assert if_block_info.branch_condition is not None

    else_body: Optional[Body] = None
    fallthrough_node: Node = start.fallthrough_edge
    conditional_node: Node = start.conditional_edge
    if fallthrough_node is end:
        # This case is quite rare, and either indicates an early return, an
        # empty if, or some sort of loop. In the loop case, we expect
        # build_flowgraph_between to end up noticing that the label has already
        # been seen and emit a goto, but in rare cases this might not happen.
        # If so it seems fine to emit the loop here.
        if_condition = if_block_info.branch_condition
        if_body = build_flowgraph_between(context, conditional_node, end, indent + 1)
    elif (
        isinstance(fallthrough_node, ConditionalNode)
        and (
            fallthrough_node.conditional_edge is conditional_node
            or fallthrough_node.fallthrough_edge is conditional_node
        )
        and context.options.andor_detection
    ):
        # The fallthrough node is also a conditional, with an edge pointing to
        # the same target as our conditional edge. This case comes up for
        # &&-statements and ||-statements, but also sometimes for regular
        # if-statements (a degenerate case of an &&/|| statement).
        return get_andor_if_statement(context, start, end, indent)
    else:
        # This case is the most common. Since we missed the if above, we will
        # assume that taking the conditional edge does not perform any other
        # ||/&&-chain checks, but instead represents skipping the if body.
        # Thus, we split into an if-body and an else-body, though the latter
        # (for one reason or another) can still be empty.
        assert start.block.block_info
        assert start.block.block_info.branch_condition
        if_condition = start.block.block_info.branch_condition.negated()

        if_body = build_flowgraph_between(context, fallthrough_node, end, indent + 1)
        else_body = build_flowgraph_between(context, conditional_node, end, indent + 1)

    return IfElseStatement(if_condition, indent, if_body, else_body)


def gather_any_comma_conditions(block_info: BlockInfo) -> Condition:
    branch_condition = block_info.branch_condition
    assert branch_condition is not None
    comma_statements = [
        statement for statement in block_info.to_write if statement.should_write()
    ]
    if comma_statements:
        assert not isinstance(branch_condition, CommaConditionExpr)
        return CommaConditionExpr(comma_statements, branch_condition)
    else:
        return branch_condition


def get_andor_if_statement(
    context: Context, start: ConditionalNode, end: Node, indent: int
) -> IfElseStatement:
    """
    This function detects &&-statements, ||-statements, and
    degenerate forms of those - i.e. singular if-statements.

    As generated by IDO, &&-statements and ||-statements are
    emitted in a very particular way. In the below ASCII art,
    if (X) is a "COND", then the subgraph is an &&-statement,
    and 'alternative' represents the if body and 'bottom'
    the else body. Otherwise, i.e. if (X) is "FALL", then it
    is an ||-statement, and 'bottom' is the if body and
    'alternative' the else.

          +-------+      COND
          | start |---------------------+
          +-------+                     |
              | FALL                    |
              v                         |
          +-------+      COND           |
          |   1   |-------------------+ |
          +-------+                   | |
              | FALL                  | |
              v                       | |
    ___________________________________________
                                    . . .
                                    . . .
                                    . . .
    ___________________________________________
              |                     | | |
              | FALL                | | |
              v                     | | |
          +-------+    COND         | | |
          |  N-1  |---------------+ | | |
          +-------+               | | | |
              | FALL              | | | |
              v                   v v v v
          +-------+    (X)    +--------------+
          |   N   |---------->|    bottom    |
          +-------+           +--------------+
              |                   |
              | (Y)               |
              v                   |
     +-----------------+          |
     |   alternative   |          |
     +-----------------+          |
              |                   |
              v                   v
          +---------------------------+
          |            end            |
          +---------------------------+

    """
    conditions: List[Condition] = []
    condition_nodes: List[ConditionalNode] = []
    bottom = start.conditional_edge
    curr_node: ConditionalNode = start
    while True:
        # Collect conditions as we accumulate them:
        block_info = curr_node.block.block_info
        assert isinstance(block_info, BlockInfo)
        branch_condition = block_info.branch_condition
        assert branch_condition is not None
        if not conditions:
            # The first condition in an if-statement will have unrelated
            # statements in its to_write list, which our caller will already
            # have emitted. Avoid emitting them twice.
            conditions.append(branch_condition)
        else:
            # Include the statements in the condition by using a comma expression.
            conditions.append(gather_any_comma_conditions(block_info))
        condition_nodes.append(curr_node)

        # The next node will tell us whether we are in an &&/|| statement.
        # Our strategy will be:
        #   - Check if we have reached the end of an &&-statement
        #   - Check if we have reached the end of an ||-statement
        #   - Otherwise, assert we still fit the criteria of an &&/|| statement.
        next_node = curr_node.fallthrough_edge

        if (
            not isinstance(next_node, ConditionalNode)
            or (
                next_node.conditional_edge is not bottom
                and next_node.fallthrough_edge is not bottom
            )
            # An edge-case of our pattern-matching technology: without
            # this, self-loops match the pattern indefinitely, since a
            # self-loop node's conditional edge points to itself.
            or next_node.is_loop()
            or len(next_node.parents) > 1
        ):
            # We reached the end of an && statement.

            if bottom is end:
                # If we don't need to emit an 'else', only emit && conditions up
                # to the first comma-statement condition, to avoid too much of
                # the output being sucked into if conditions.
                index = next(
                    (
                        i
                        for i, cond in enumerate(conditions)
                        if isinstance(cond, CommaConditionExpr)
                    ),
                    None,
                )

                if index is not None:
                    if_body = build_flowgraph_between(
                        context, condition_nodes[index], end, indent + 1
                    )
                    return IfElseStatement(
                        join_conditions(
                            [cond.negated() for cond in conditions[:index]], "&&"
                        ),
                        indent,
                        if_body=if_body,
                    )

            if_body = build_flowgraph_between(context, next_node, end, indent + 1)
            else_body = build_flowgraph_between(context, bottom, end, indent + 1)
            return IfElseStatement(
                # We negate everything, because the conditional edges will jump
                # OVER the if body.
                join_conditions([cond.negated() for cond in conditions], "&&"),
                indent,
                if_body=if_body,
                else_body=else_body,
            )

        if next_node.fallthrough_edge is bottom:
            # End of || statement.
            assert next_node.block.block_info
            next_node_condition = gather_any_comma_conditions(
                next_node.block.block_info
            )
            if_body = build_flowgraph_between(context, bottom, end, indent + 1)
            else_body = build_flowgraph_between(
                context, next_node.conditional_edge, end, indent + 1
            )
            return IfElseStatement(
                # Negate the last condition, for it must fall-through to the
                # body instead of jumping to it, hence it must jump OVER the body.
                join_conditions(conditions + [next_node_condition.negated()], "||"),
                indent,
                if_body=if_body,
                # The else-body is wherever the code jumps to instead of the
                # fallthrough (i.e. if-body).
                else_body=else_body,
            )

        # Otherwise, still in the middle of an &&/|| condition.
        assert next_node.conditional_edge is bottom
        curr_node = next_node
    assert False


def join_conditions(conditions: List[Condition], op: str) -> Condition:
    assert op in ["&&", "||"]
    assert conditions
    ret: Condition = conditions[0]
    for cond in conditions[1:]:
        ret = BinaryOp(ret, op, cond, type=Type.bool())
    return ret


def add_return_statement(
    context: Context, body: Body, node: ReturnNode, indent: int, last: bool
) -> None:
    emit_node(context, node, body, indent)

    ret_info = node.block.block_info
    assert isinstance(ret_info, BlockInfo)

    ret = ret_info.return_value
    if ret is not None:
        ret_str = format_expr(ret, context.fmt)
        body.add_statement(SimpleStatement(indent, f"return {ret_str};"))
        context.is_void = False
    elif not last:
        body.add_statement(SimpleStatement(indent, "return;"))


def detect_loop(
    context: Context, start: ConditionalNode, end: Node, indent: int
) -> Optional[DoWhileLoop]:
    # We detect edges that are accompanied by their reverse as loops.
    imm_pdom: Node
    if start.is_self_loop():
        imm_pdom = start
    else:
        imm_pdom = immediate_postdominator(context, start, end)

    if not (
        isinstance(imm_pdom, ConditionalNode) and imm_pdom.conditional_edge is start
    ):
        return None

    loop_body = Body(False, [])
    emit_node(context, start, loop_body, indent + 1)

    if not start.is_self_loop():
        # There are more nodes to emit, "between" the start node
        # and the loop edge that it connects to:
        loop_body = build_flowgraph_between(
            context,
            start,
            imm_pdom,
            indent + 1,
            skip_loop_detection=True,
        )
        emit_node(context, imm_pdom, loop_body, indent + 1)

    assert imm_pdom.block.block_info
    assert imm_pdom.block.block_info.branch_condition
    return DoWhileLoop(
        indent,
        context.options.coding_style,
        loop_body,
        imm_pdom.block.block_info.branch_condition,
    )


def build_flowgraph_between(
    context: Context,
    start: Node,
    end: Node,
    indent: int,
    skip_loop_detection: bool = False,
) -> Body:
    """
    Output a section of a flow graph that has already been translated to our
    symbolic AST. All nodes between start and end, including start but NOT end,
    will be printed out using if-else statements and block info at the given
    level of indentation.
    """
    curr_start = start
    body = Body(print_node_comment=context.options.debug)

    # We will split this graph into subgraphs, where the entrance and exit nodes
    # of that subgraph are at the same indentation level. "curr_start" will
    # iterate through these nodes, which are commonly referred to as
    # articulation nodes.
    while curr_start != end:
        # Write the current node (but return nodes are handled specially).
        if not isinstance(curr_start, ReturnNode):
            # Before we do anything else, we pattern-match the subgraph
            # rooted at curr_start against certain predefined subgraphs
            # that emit do-while-loops:
            if not skip_loop_detection and isinstance(curr_start, ConditionalNode):
                do_while_loop = detect_loop(context, curr_start, end, indent)
                if do_while_loop:
                    body.add_do_while_loop(do_while_loop)
                    # Move past the loop:
                    if curr_start.is_self_loop():
                        curr_start = curr_start.fallthrough_edge
                    else:
                        imm_pdom = immediate_postdominator(context, curr_start, end)
                        assert isinstance(imm_pdom, ConditionalNode)
                        curr_start = imm_pdom.fallthrough_edge
                    continue

            # If a node is ever encountered twice, we can emit a goto to the
            # first place we emitted it. Since nodes represent positions in the
            # assembly, and we use phi's for preserved variable contents, this
            # will end up semantically equivalent. This can happen sometimes
            # when early returns/continues/|| are not detected correctly, and
            # hints at that situation better than if we just blindly duplicate
            # the block.
            if curr_start in context.emitted_nodes:
                emit_goto(context, curr_start, body, indent)
                break
            context.emitted_nodes.add(curr_start)

            # Emit the node, and possibly a label for it.
            emit_node(context, curr_start, body, indent)

        if curr_start.emit_goto:
            # If we have decided to emit a goto here, then we should just fall
            # through to the next node, after writing a goto/conditional goto/
            # return.
            block_info = curr_start.block.block_info
            assert isinstance(block_info, BlockInfo)
            if isinstance(curr_start, BasicNode):
                emit_goto_or_early_return(context, curr_start.successor, body, indent)
            elif isinstance(curr_start, ConditionalNode):
                target = curr_start.conditional_edge
                if_body = Body(print_node_comment=False)
                emit_goto_or_early_return(context, target, if_body, indent + 1)
                assert block_info.branch_condition is not None
                body.add_if_else(
                    IfElseStatement(
                        block_info.branch_condition,
                        indent,
                        if_body=if_body,
                        else_body=None,
                    )
                )
            elif isinstance(curr_start, SwitchNode):
                emit_switch_jump(context, curr_start, body, indent)
            else:  # ReturnNode
                add_return_statement(context, body, curr_start, indent, last=False)

            # Advance to the next node in block order. This may skip over
            # unreachable blocks -- hopefully none too important.
            index = context.flow_graph.nodes.index(curr_start)
            fallthrough = context.flow_graph.nodes[index + 1]
            if isinstance(curr_start, ConditionalNode):
                assert fallthrough == curr_start.fallthrough_edge
            curr_start = fallthrough
            continue

        # Switch nodes are always marked emit_goto.
        assert not isinstance(curr_start, SwitchNode)

        if isinstance(curr_start, BasicNode):
            # In a BasicNode, the successor is the next articulation node.
            curr_start = curr_start.successor
        elif isinstance(curr_start, ConditionalNode):
            # A ConditionalNode means we need to find the next articulation
            # node. This means we need to find the "immediate postdominator"
            # of the current node, where "postdominator" means we have to go
            # through it, and "immediate" means we aren't skipping any.
            curr_end = immediate_postdominator(context, curr_start, end)
            # We also need to handle the if-else block here; this does the
            # outputting of the subgraph between curr_start and the next
            # articulation node.
            body.add_if_else(
                build_conditional_subgraph(context, curr_start, curr_end, indent)
            )
            # Move on.
            curr_start = curr_end
        else:  # ReturnNode
            # Write the return node, and break, because there is nothing more
            # to process.
            add_return_statement(context, body, curr_start, indent, last=False)
            break

    return body


def build_naive(context: Context, nodes: List[Node], return_node: ReturnNode) -> Body:
    """Naive procedure for generating output with only gotos for control flow.

    Used for --no-ifs, when the regular if_statements code fails."""

    body = Body(print_node_comment=context.options.debug)

    def emit_successor(node: Node, cur_index: int) -> None:
        if (
            cur_index + 1 < len(nodes)
            and nodes[cur_index + 1] == node
            and not (isinstance(node, ReturnNode) and not node.is_real())
        ):
            # Fallthrough is fine
            return
        emit_goto_or_early_return(context, node, body, 1)

    for i, node in enumerate(nodes):
        block_info = node.block.block_info
        assert isinstance(block_info, BlockInfo)
        if isinstance(node, ReturnNode):
            # Do not emit duplicated (non-real) return nodes; they don't have
            # a well-defined position, so we emit them next to where they are
            # jumped to instead. Also don't emit the final return node; that's
            # our caller's responsibility.
            if node.is_real() and node is not return_node:
                add_return_statement(context, body, node, 1, last=False)
        elif isinstance(node, BasicNode):
            emit_node(context, node, body, 1)
            emit_successor(node.successor, i)
        elif isinstance(node, SwitchNode):
            emit_node(context, node, body, 1)
            emit_switch_jump(context, node, body, 1)
        else:  # ConditionalNode
            emit_node(context, node, body, 1)
            if_body = Body(print_node_comment=False)
            emit_goto_or_early_return(context, node.conditional_edge, if_body, 2)
            assert block_info.branch_condition is not None
            body.add_if_else(
                IfElseStatement(
                    block_info.branch_condition,
                    1,
                    if_body=if_body,
                    else_body=None,
                )
            )
            emit_successor(node.fallthrough_edge, i)

    return body


def build_body(context: Context, function_info: FunctionInfo, options: Options) -> Body:
    start_node: Node = context.flow_graph.entry_node()
    return_node: Optional[ReturnNode] = context.flow_graph.return_node()
    if return_node is None:
        fictive_block = Block(-1, None, "", [])
        return_node = ReturnNode(fictive_block, False, index=-1)

    num_switches = len(
        [node for node in context.flow_graph.nodes if isinstance(node, SwitchNode)]
    )
    switch_index = 0
    for node in context.flow_graph.nodes:
        if isinstance(node, SwitchNode):
            assert node.cases, "jtbl list must not be empty"
            if num_switches > 1:
                switch_index += 1
            context.switch_nodes[node] = switch_index
            most_common = max(node.cases, key=node.cases.count)
            has_common = False
            if node.cases.count(most_common) > 1:
                context.case_nodes[most_common].append((switch_index, -1))
                has_common = True
            for index, target in enumerate(node.cases):
                if has_common and target == most_common:
                    continue
                context.case_nodes[target].append((switch_index, index))
        elif isinstance(node, ConditionalNode) and node.is_loop():
            context.loop_nodes.add(node.conditional_edge)
        elif isinstance(node, BasicNode) and node.is_loop():
            context.loop_nodes.add(node.successor)

    if options.debug:
        print("Here's the whole function!\n")
    body: Body
    if options.ifs:
        body = build_flowgraph_between(context, start_node, return_node, 1)
    else:
        body = build_naive(context, context.flow_graph.nodes, return_node)

    if return_node.index != -1:
        add_return_statement(context, body, return_node, 1, last=True)

    return body


def get_function_text(function_info: FunctionInfo, options: Options) -> str:
    fmt = Formatter(options.coding_style, skip_casts=options.skip_casts)
    context = Context(flow_graph=function_info.flow_graph, options=options, fmt=fmt)
    body: Body = build_body(context, function_info, options)

    function_lines: List[str] = []

    fn_name = function_info.stack_info.function.name
    arg_strs = []
    for arg in function_info.stack_info.arguments:
        arg_strs.append(arg.type.to_decl(arg.format(fmt)))
    if function_info.stack_info.is_variadic:
        arg_strs.append("...")
    arg_str = ", ".join(arg_strs) or "void"

    fn_header = f"{fn_name}({arg_str})"

    if context.is_void:
        fn_header = f"void {fn_header}"
    else:
        fn_header = function_info.return_type.to_decl(fn_header)
    whitespace = "\n" if fmt.coding_style.newline_after_function else " "
    function_lines.append(f"{fn_header}{whitespace}{{")

    any_decl = False
    for local_var in function_info.stack_info.local_vars[::-1]:
        type_decl = local_var.type.to_decl(local_var.format(fmt))
        function_lines.append(SimpleStatement(1, f"{type_decl};").format(fmt))
        any_decl = True
    temp_decls = set()
    for temp_var in function_info.stack_info.temp_vars:
        if temp_var.need_decl():
            expr = temp_var.expr
            type_decl = expr.type.to_decl(expr.var.format(fmt))
            temp_decls.add(f"{type_decl};")
            any_decl = True
    for decl in sorted(list(temp_decls)):
        function_lines.append(SimpleStatement(1, decl).format(fmt))
    for phi_var in function_info.stack_info.phi_vars:
        type_decl = phi_var.type.to_decl(phi_var.get_var_name())
        function_lines.append(SimpleStatement(1, f"{type_decl};").format(fmt))
        any_decl = True
    if any_decl:
        function_lines.append("")

    function_lines.append(body.format(fmt))
    function_lines.append("}")
    full_function_text: str = "\n".join(function_lines)
    return full_function_text
