from typing import Literal
from pydantic import Field, StrictBool, model_validator
from app.agent_schemas import StrictModel
from app.workflows.actions.send_graph.data import validate_source
from app.workflows.templates import TOKEN

GraphType = Literal['bar', 'grouped_bar', 'stacked_bar', 'line', 'multi_line', 'pie', 'combo']
ColorScheme = Literal['default', 'blue', 'green', 'grey', 'orange', 'rose', 'violet', 'yellow', 'neutral', 'distinct', 'vivid', 'mild']
InputMode = Literal['template', 'expression']


class SendGraphConfig(StrictModel):
    graph_type: GraphType = 'bar'
    data: str = Field(default='', max_length=32000)
    data_mode: InputMode = 'template'
    bar_data: str = Field(default='', max_length=32000)
    bar_data_mode: InputMode = 'template'
    line_data: str = Field(default='', max_length=32000)
    line_data_mode: InputMode = 'template'
    title: str = Field(default='', max_length=200)
    x_axis_label: str = Field(default='', max_length=100)
    y_axis_label: str = Field(default='', max_length=100)
    bar_label: str = Field(default='', max_length=100)
    line_label: str = Field(default='', max_length=100)
    color_scheme: ColorScheme = 'default'
    show_grid: StrictBool = True
    show_legend: StrictBool = True

    @model_validator(mode='after')
    def valid_chart(self):
        if self.graph_type == 'combo':
            for field, mode in [('bar_data', self.bar_data_mode), ('line_data', self.line_data_mode)]:
                try: validate_source(getattr(self, field), mode, 'bar')
                except ValueError as error:
                    raise ValueError('{}: {}'.format('Bar data input' if field=='bar_data' else 'Line data input', error)) from None
            if not TOKEN.search(self.bar_label) and self.bar_label.casefold() == 'x':
                raise ValueError('Bar data Y-axis label cannot be x; that name is reserved for categories.')
            self.data = ''; self.data_mode = 'template'; self.y_axis_label = ''
        else:
            try: validate_source(self.data, self.data_mode, self.graph_type)
            except ValueError as error: raise ValueError('Chart data input: {}'.format(error)) from None
            self.bar_data = ''; self.line_data = ''; self.bar_data_mode = 'template'; self.line_data_mode = 'template'
            self.bar_label = ''; self.line_label = ''
        return self
