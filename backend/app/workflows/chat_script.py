"""A bounded interpreter for a documented subset of Python expressions.

Workflow input is parsed into an AST, never passed to eval, exec, or compile.
Only JSON values and the operations below enter the interpreter.
"""
import ast
import json
import math
import operator

MAX_STRING = 8192
MAX_ITEMS = 100
MAX_VALUE_BYTES = 16 * 1024
FUNCTIONS = {"str", "int", "float", "bool", "len", "round", "abs", "min", "max", "sum"}
METHODS = {"lower", "upper", "strip", "lstrip", "rstrip", "replace", "split", "join", "startswith", "endswith", "get"}


class ScriptError(ValueError):
    """A useful error without variable contents or Python internals."""


def json_value(value):
    remaining = [1000]

    def visit(item, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 8:
            raise ScriptError("The value is too deeply nested or contains too many items.")
        if item is None or type(item) is bool:
            return item
        if type(item) is int:
            if item.bit_length() > 256:
                raise ScriptError("The number is too large.")
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise ScriptError("Numbers must be finite.")
            return item
        if type(item) is str:
            if len(item) > MAX_STRING:
                raise ScriptError("Text values can contain up to 8,192 characters.")
            return item
        if type(item) in {list, tuple}:
            if len(item) > MAX_ITEMS:
                raise ScriptError("Lists can contain up to 100 items.")
            return [visit(child, depth + 1) for child in item]
        if type(item) is dict:
            if len(item) > MAX_ITEMS or any(type(key) is not str or len(key) > 128 for key in item):
                raise ScriptError("Objects support up to 100 string keys of 128 characters each.")
            return {key: visit(child, depth + 1) for key, child in item.items()}
        raise ScriptError("Use a text, number, Boolean, null, list, or object value.")

    result = visit(value)
    try:
        size = len(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    except (UnicodeError, ValueError):
        raise ScriptError("Use valid Unicode text and finite numbers.") from None
    if size > MAX_VALUE_BYTES:
        raise ScriptError("A variable value can use up to 16 KB.")
    return result


def parse_expression(source):
    if not isinstance(source, str) or not source.strip() or len(source) > 2000:
        raise ScriptError("Enter a Python expression of up to 2,000 characters.")
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        raise ScriptError("Enter a valid Python expression, without assignments or statements.") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > 200:
        raise ScriptError("The expression is too complex.")
    allowed = (ast.Expression, ast.Constant, ast.Name, ast.Load, ast.List, ast.Tuple, ast.Dict,
        ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
        ast.UnaryOp, ast.UAdd, ast.USub, ast.Not, ast.BoolOp, ast.And, ast.Or,
        ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
        ast.IfExp, ast.Subscript, ast.Slice, ast.Call, ast.Attribute, ast.JoinedStr, ast.FormattedValue)
    for node in nodes:
        if not isinstance(node, allowed):
            raise ScriptError("This expression uses an unsupported operation.")
        if isinstance(node, ast.Constant):
            json_value(node.value)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            raise ScriptError("Use text concatenation instead of percent formatting.")
        if isinstance(node, ast.Name) and node.id.startswith("_") and node.id != "_transferred_context":
            raise ScriptError("Private names are not available in chat scripts.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ScriptError("Private attributes are not available in chat scripts.")
        if isinstance(node, ast.Call):
            valid = isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS or isinstance(node.func, ast.Attribute) and node.func.attr in METHODS
            if not valid or node.keywords or len(node.args) > 16:
                raise ScriptError("Use a supported function with positional arguments.")
        if isinstance(node, ast.Dict) and any(key is None for key in node.keys):
            raise ScriptError("Object unpacking is not supported.")
        if isinstance(node, ast.FormattedValue) and (node.format_spec is not None or node.conversion not in {-1, 115}):
            raise ScriptError("Use a plain f-string or round() instead of format specifications.")

    def depth(node, level=0):
        if level > 20:
            raise ScriptError("The expression is too deeply nested.")
        for child in ast.iter_child_nodes(node):
            depth(child, level + 1)
    depth(tree)
    return tree


def number(value):
    if type(value) not in {int, float}:
        raise ScriptError("This operation requires numbers.")
    return value


def argument_count(values, minimum, maximum):
    if not minimum <= len(values) <= maximum:
        raise ScriptError("Check the number of function arguments.")


def data_only(value):
    """Validate namespace data before an operation can invoke Python protocols.

    Namespace dictionaries may contain the full session; result-size limits are
    applied separately to the value ultimately assigned to one variable.
    """
    remaining = [10000]
    def visit(item, depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 10:
            raise ScriptError("The variable data is too complex.")
        if type(item) in {dict, list}:
            if len(item) > MAX_ITEMS:
                raise ScriptError("The variable contains too many items.")
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise ScriptError("Object keys must be strings.")
                values = item.values()
            else:
                values = item
            for child in values: visit(child, depth + 1)
        else:
            json_value(item)
    visit(value)
    return value


class Interpreter:
    def __init__(self, environment):
        self.environment = environment
        self.remaining = 1000

    def tick(self):
        self.remaining -= 1
        if self.remaining < 0:
            raise ScriptError("The expression needs too many operations.")

    def binary(self, operation, left, right):
        if isinstance(operation, ast.Add):
            if type(left) is type(right) and type(left) in {str, list}:
                maximum = MAX_STRING if isinstance(left, str) else MAX_ITEMS
                if len(left) + len(right) > maximum:
                    raise ScriptError("The combined value is too large.")
            else:
                number(left); number(right)
            result = left + right
        elif isinstance(operation, ast.Mult) and (type(left) in {str, list} or type(right) in {str, list}):
            value, count = (left, right) if type(left) in {str, list} else (right, left)
            maximum = MAX_STRING if isinstance(value, str) else MAX_ITEMS
            if type(count) is not int or len(value) * max(count, 0) > maximum:
                raise ScriptError("The repeated value is too large or the count is invalid.")
            # Avoid converting an arbitrarily large repetition count for an empty value.
            result = value * max(count, 0) if value and count > 0 else value[:0]
        else:
            number(left); number(right)
            functions = {ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
                ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
            result = functions[type(operation)](left, right)
        return json_value(result)

    def function(self, name, args):
        if name in {"str", "int", "float", "bool", "len", "abs"}:
            argument_count(args, 1, 1)
            if name in {"int", "float"} and isinstance(args[0], str) and len(args[0].strip()) > 100:
                raise ScriptError("The number is too large.")
            if name == "abs": number(args[0])
            return {"str": str, "int": int, "float": float, "bool": bool, "len": len, "abs": abs}[name](*args)
        if name == "round":
            argument_count(args, 1, 2); number(args[0])
            if len(args) == 2 and (type(args[1]) is not int or not -100 <= args[1] <= 100):
                raise ScriptError("Use round() with a precision from -100 to 100.")
            return round(*args)
        if name == "sum":
            argument_count(args, 1, 2)
            if not isinstance(args[0], list):
                raise ScriptError("sum() requires a list of numbers.")
            result = number(args[1]) if len(args) == 2 else 0
            for value in args[0]:
                self.tick(); result = self.binary(ast.Add(), result, number(value))
            return result
        if name in {"min", "max"}:
            argument_count(args, 1, 16)
            values = args[0] if len(args) == 1 else args
            if not isinstance(values, list) or not values:
                raise ScriptError("min() and max() require values or a non-empty list.")
            return min(values) if name == "min" else max(values)
        raise ScriptError("This function is not supported.")

    def method(self, value, name, args):
        if name == "get" and type(value) is dict:
            argument_count(args, 1, 2)
            if type(args[0]) is not str:
                raise ScriptError("Object keys must be strings.")
            return value.get(*args)
        if type(value) is not str:
            raise ScriptError("This method requires text, or an object for get().")
        if name in {"lower", "upper"}:
            argument_count(args, 0, 0)
            return str.lower(value) if name == "lower" else str.upper(value)
        if name in {"strip", "lstrip", "rstrip"}:
            argument_count(args, 0, 1)
            if args and args[0] is not None and type(args[0]) is not str:
                raise ScriptError("strip() requires text characters.")
            return {"strip": str.strip, "lstrip": str.lstrip, "rstrip": str.rstrip}[name](value, *args)
        if name in {"startswith", "endswith"}:
            argument_count(args, 1, 1)
            if type(args[0]) is not str: raise ScriptError("The prefix or suffix must be text.")
            return str.startswith(value, args[0]) if name == "startswith" else str.endswith(value, args[0])
        if name == "replace":
            argument_count(args, 2, 3)
            if any(type(item) is not str for item in args[:2]) or len(args) == 3 and type(args[2]) is not int:
                raise ScriptError("replace() requires old text, new text, and an optional integer count.")
            occurrences = value.count(args[0]) if args[0] else len(value) + 1
            if len(args) == 3 and args[2] >= 0: occurrences = min(occurrences, args[2])
            if len(value) + occurrences * (len(args[1]) - len(args[0])) > MAX_STRING:
                raise ScriptError("The replacement text is too large.")
            return str.replace(value, *args)
        if name == "split":
            argument_count(args, 0, 2)
            if args and args[0] is not None and type(args[0]) is not str or len(args) == 2 and type(args[1]) is not int:
                raise ScriptError("split() requires a separator and an optional integer count.")
            return str.split(value, *args)
        if name == "join":
            argument_count(args, 1, 1)
            if type(args[0]) is not list or any(type(item) is not str for item in args[0]):
                raise ScriptError("join() requires a list of text values.")
            if sum(len(item) for item in args[0]) + len(value) * max(len(args[0]) - 1, 0) > MAX_STRING:
                raise ScriptError("The joined text is too large.")
            return str.join(value, args[0])
        raise ScriptError("This method is not supported for this value.")

    def evaluate(self, node):
        self.tick()
        if isinstance(node, ast.Expression): return self.evaluate(node.body)
        if isinstance(node, ast.Constant): return json_value(node.value)
        if isinstance(node, ast.Name):
            if node.id not in self.environment:
                raise ScriptError("Variable '{}' is not set.".format(node.id))
            return data_only(self.environment[node.id])
        if isinstance(node, ast.Attribute):
            # Resolve JSON fields explicitly. Never access Python attributes.
            value = self.evaluate(node.value)
            if type(value) is dict and node.attr in value:
                return value[node.attr]
            # Survey values remain plain JSON for existing scripts/templates.
            # These read-only views also work for previously collected answers.
            if type(value) is str and node.attr == "text":
                return value
            if type(value) in {int, float} and node.attr == "norm":
                return {"value": value}
            raise ScriptError("This field is missing or is not supported for the value's type.")
        if isinstance(node, (ast.List, ast.Tuple)): return json_value([self.evaluate(item) for item in node.elts])
        if isinstance(node, ast.Dict): return json_value({self.evaluate(key): self.evaluate(value) for key, value in zip(node.keys, node.values)})
        if isinstance(node, ast.BinOp): return self.binary(node.op, self.evaluate(node.left), self.evaluate(node.right))
        if isinstance(node, ast.UnaryOp):
            value = self.evaluate(node.operand)
            if isinstance(node.op, ast.Not): return not value
            number(value)
            return -value if isinstance(node.op, ast.USub) else +value
        if isinstance(node, ast.BoolOp):
            result = None
            for item in node.values:
                result = self.evaluate(item)
                if isinstance(node.op, ast.And) and not result or isinstance(node.op, ast.Or) and result: break
            return result
        if isinstance(node, ast.IfExp): return self.evaluate(node.body if self.evaluate(node.test) else node.orelse)
        if isinstance(node, ast.Compare):
            left = self.evaluate(node.left)
            operations = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
                ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
                ast.Is: operator.is_, ast.IsNot: operator.is_not}
            for operation, expression in zip(node.ops, node.comparators):
                right = self.evaluate(expression)
                if not operations[type(operation)](left, right): return False
                left = right
            return True
        if isinstance(node, ast.Subscript):
            value = self.evaluate(node.value)
            if isinstance(node.slice, ast.Slice):
                if type(value) not in {str, list}: raise ScriptError("Only text and lists support slices.")
                parts = [self.evaluate(item) if item else None for item in (node.slice.lower, node.slice.upper, node.slice.step)]
                if any(item is not None and type(item) is not int for item in parts): raise ScriptError("Slice positions must be integers.")
                return value[slice(*parts)]
            key = self.evaluate(node.slice)
            if type(value) is dict and type(key) is str or type(value) in {list, str} and type(key) is int:
                return value[key]
            raise ScriptError("Use a string object key or integer list/text index.")
        if isinstance(node, ast.Call):
            args = [self.evaluate(item) for item in node.args]
            result = self.function(node.func.id, args) if isinstance(node.func, ast.Name) else self.method(self.evaluate(node.func.value), node.func.attr, args)
            return json_value(result)
        if isinstance(node, ast.JoinedStr):
            values = [str(self.evaluate(item.value)) if isinstance(item, ast.FormattedValue) else item.value for item in node.values]
            if sum(map(len, values)) > MAX_STRING: raise ScriptError("The formatted text is too large.")
            return "".join(values)
        raise ScriptError("This expression is not supported.")


def evaluate(source, environment):
    try:
        return json_value(Interpreter(environment).evaluate(parse_expression(source)))
    except ScriptError:
        raise
    except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError, OverflowError, RecursionError):
        raise ScriptError("Check value types, object keys, list indexes, and arithmetic in the expression.") from None
