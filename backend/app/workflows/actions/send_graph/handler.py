from dataclasses import dataclass
import json
from app.workflows.actions.send_graph.data import evaluate_source, validate_rows
from app.workflows.chat_script import ScriptError
from app.workflows.templates import render


@dataclass(frozen=True)
class GraphSkipped:
    reason: str = 'empty_combo_data'


def execute(config, context):
    kind = config['graph_type']
    values = {}
    inputs = [('bar_data', 'bar'), ('line_data', 'line')] if kind == 'combo' else [('data', kind)]
    try:
        failures = []
        for field, chart_type in inputs:
            try:
                values[field] = evaluate_source(config[field], config[field+'_mode'], context)
            except (ValueError, TypeError, RecursionError, OverflowError):
                failures.append(field)
        def empty(value):
            return value is None or value == [] or (type(value) is str and not value.strip())
        if kind == 'combo' and any(empty(value) for value in values.values()):
            return GraphSkipped()
        if failures:
            field = failures[0]
            raise ScriptError('{} could not be resolved. Check its JSON, variable path, or Python expression.'.format(
                'Chart data input' if field=='data' else field.replace('_',' ').capitalize()))
        for field, chart_type in inputs:
            try: values[field] = validate_rows(values[field], chart_type)
            except ValueError as error:
                raise ScriptError('{}: {}'.format('Chart data input' if field=='data' else field.replace('_',' ').capitalize(), error)) from None
        if kind=='combo' and len({row['x'] for rows in values.values() for row in rows}) > 100:
            raise ScriptError('Use up to 100 shared categories for a Combo chart.')
        def text(field, maximum):
            return render(config[field], context, maximum=maximum, allow_empty=True) if config[field] else ''
        title, x_label = text('title', 200), text('x_axis_label', 100)
        y_label = text('y_axis_label', 100) if kind!='combo' else ''
        bar_label = (text('bar_label', 100) or 'Bar') if kind=='combo' else ''
        line_label = (text('line_label', 100) or 'Line') if kind=='combo' else ''
        if kind=='combo' and bar_label.casefold()=='x':
            raise ScriptError('Bar data Y-axis label cannot be x; that name is reserved for categories.')
        graph = {'version': 1, 'graph_type': kind, 'title': title, 'x_axis_label': x_label, 'y_axis_label': y_label,
            'bar_label': bar_label, 'line_label': line_label, 'color_scheme': config['color_scheme'],
            'show_grid': config['show_grid'] and kind!='pie',
            'show_legend': config['show_legend'] and kind not in {'bar','line'},
            'data': values.get('data', []), 'bar_data': values.get('bar_data', []), 'line_data': values.get('line_data', [])}
        if len(json.dumps(graph, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 64*1024:
            raise ScriptError('Keep the resolved graph within 64 KiB.')
    except (TypeError, RecursionError, OverflowError):
        raise ScriptError('The chart data or labels could not be processed.') from None
    if context.graph_calls >= 10:
        raise ScriptError('A turn can send up to 10 graphs.')
    context.graph_calls += 1
    return {'content': '', 'quick_replies': [], 'graph': graph}
