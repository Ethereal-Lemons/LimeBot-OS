"""Bounded arithmetic evaluation for the native calculate tool."""

from __future__ import annotations

import ast
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext


def calculate_expression(expression: str) -> str:
    """Evaluate a small arithmetic expression without executing Python code."""
    expression = str(expression or "").strip()
    if not expression:
        return "Error: An arithmetic expression is required."
    if len(expression) > 500:
        return "Error: Expression is too long (maximum 500 characters)."

    binary_ops = {
        ast.Add: lambda a, b: a + b,
        ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b,
        ast.Div: lambda a, b: a / b,
        ast.FloorDiv: lambda a, b: a // b,
        ast.Mod: lambda a, b: a % b,
        ast.Pow: lambda a, b: a**int(b),
    }
    unary_ops = {ast.UAdd: lambda value: value, ast.USub: lambda value: -value}

    def evaluate(node: ast.AST, depth: int = 0) -> Decimal:
        if depth > 30:
            raise ValueError("expression nesting is too deep")
        if isinstance(node, ast.Expression):
            return evaluate(node.body, depth + 1)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary_ops:
            return unary_ops[type(node.op)](evaluate(node.operand, depth + 1))
        if isinstance(node, ast.BinOp) and type(node.op) in binary_ops:
            left = evaluate(node.left, depth + 1)
            right = evaluate(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                if right != right.to_integral_value() or abs(right) > 20:
                    raise ValueError("power must be an integer between -20 and 20")
            value = binary_ops[type(node.op)](left, right)
            if abs(value) > Decimal("1e100"):
                raise ValueError("result magnitude is too large")
            return value
        raise ValueError(f"unsupported expression element: {type(node).__name__}")

    try:
        parsed = ast.parse(expression, mode="eval")
        with localcontext() as context:
            context.prec = 28
            value = evaluate(parsed)
    except (SyntaxError, ValueError, InvalidOperation, DivisionByZero, ZeroDivisionError) as error:
        return f"Error: Invalid arithmetic expression ({error})."

    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in {"", "-0"}:
        rendered = "0"
    return f"Result: {rendered}"
