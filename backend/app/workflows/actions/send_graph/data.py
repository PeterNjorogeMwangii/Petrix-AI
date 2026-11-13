"""Bounded chart rows and typed JSON template substitution."""
import json
import math
from app.workflows.templates import TOKEN, render, resolve

MULTI_SERIES = {'grouped_bar', 'stacked_bar', 'multi_line'}
MAX_ROWS = 100
MAX_CATEGORIES = 100
MAX_SERIES = 20
MAX_NUMBER = 10**15


def load_json(source):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON field: {}.'.format(key))
            value[key] = item
        return value
    def constant(_):
        raise ValueError('Chart values must be finite JSON numbers.')
    try:
        return json.loads(source, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError('Enter a JSON array of chart rows, or use a variable or Script.') from None


def replace_unquoted_tokens(source, replacement):
    """Replace unquoted placeholders without putting customer text into JSON syntax."""
    parts, position, quoted, escaped = [], 0, False, False
    while position < len(source):
        char = source[position]
        if not quoted:
            match = TOKEN.match(source, position)
            if match:
                parts.append(replacement(match)); position = match.end(); continue
        parts.append(char)
        if escaped:
            escaped = False
        elif quoted and char == '\\':
            escaped = True
        elif char == '"':
            quoted = not quoted
        position += 1
    return ''.join(parts)


def label(value, row, field, maximum):
    if field == 'x' and type(value) in {int, float}:
        if abs(value) > MAX_NUMBER or not math.isfinite(value):
            raise ValueError('Row {} is invalid (x: use a finite category within ±10^15).'.format(row))
        value = str(int(value)) if int(value) == value else str(value)
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError('Row {} is invalid ({}: use 1–{} characters).'.format(row, field, maximum))
    return value.strip()


def validate_rows(value, graph_type, allow_empty=False):
    if type(value) is not list or len(value) > MAX_ROWS:
        raise ValueError('Chart data must be an array of up to 100 rows.')
    if not value and not allow_empty:
        raise ValueError('Chart data cannot be empty.')
    multi = graph_type in MULTI_SERIES
    rows, seen, categories, series = [], set(), set(), set()
    for index, item in enumerate(value, 1):
        if type(item) is not dict:
            raise ValueError('Row {} is invalid (use an object with x and y).'.format(index))
        required = {'x', 'y', 'series'} if multi else {'x', 'y'}
        missing = sorted(required - item.keys())
        if missing:
            raise ValueError('Row {} is invalid ({}: Field required).'.format(index, missing[0]))
        extra = set(item) - required
        if extra:
            raise ValueError('Row {} is invalid ({}: unexpected field).'.format(index, sorted(extra)[0]))
        x = label(item['x'], index, 'x', 120)
        y = item['y']
        if type(y) not in {int, float} or abs(y) > MAX_NUMBER or not math.isfinite(y):
            raise ValueError('Row {} is invalid (y: use a finite number within ±10^15).'.format(index))
        if graph_type == 'pie' and y < 0:
            raise ValueError('Row {} is invalid (y: Pie values cannot be negative).'.format(index))
        name = label(item['series'], index, 'series', 80) if multi else ''
        key = (x, name)
        if key in seen:
            raise ValueError('Row {} is invalid (x{}: duplicate category{}).'.format(index,
                '/series' if multi else '', ' and series' if multi else ''))
        seen.add(key); categories.add(x)
        if multi: series.add(name)
        rows.append({'x': x, 'y': y, **({'series': name} if multi else {})})
    if len(categories) > MAX_CATEGORIES or len(series) > MAX_SERIES:
        raise ValueError('Use up to 100 categories and 20 series per chart.')
    return rows


def validate_source(source, mode, graph_type):
    from app.workflows.chat_script import parse_expression
    if not source.strip():
        raise ValueError('Enter chart data, a chat variable, or a Python expression.')
    if mode == 'expression':
        parse_expression(source)
        return
    if TOKEN.fullmatch(source.strip()):
        return
    dynamic = bool(TOKEN.search(source))
    value = load_json(replace_unquoted_tokens(source, lambda _: 'null') if dynamic else source)
    if not dynamic:
        validate_rows(value, graph_type)


def evaluate_source(source, mode, context):
    from app.workflows.chat_script import evaluate
    if mode == 'expression':
        value = evaluate(source, context.environment())
    else:
        match = TOKEN.fullmatch(source.strip())
        if match:
            value = resolve(match.group(1) or match.group(2), context)
        else:
            parts, position, decoder = [], 0, json.JSONDecoder()
            while position < len(source):
                token = TOKEN.match(source, position)
                if token:
                    parts.append(json.dumps(resolve(token.group(1) or token.group(2), context), ensure_ascii=False, allow_nan=False))
                    position = token.end()
                elif source[position] == '"':
                    try: item, end = decoder.raw_decode(source, position)
                    except json.JSONDecodeError:
                        raise ValueError('Enter valid chart JSON.') from None
                    # Resolve original string values only, never text inserted by another variable.
                    is_key = source[end:].lstrip().startswith(':')
                    match = TOKEN.fullmatch(item)
                    if not is_key:
                        item = resolve(match.group(1) or match.group(2), context) if match else render(item, context, maximum=16000, allow_empty=True)
                    parts.append(json.dumps(item, ensure_ascii=False, allow_nan=False)); position = end
                else:
                    parts.append(source[position]); position += 1
            value = load_json(''.join(parts))
    # API responses may contain JSON text; SQL rows already arrive as a list.
    if type(value) is str and value.strip():
        value = load_json(value)
    return value
