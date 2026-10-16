import pytest
from types import SimpleNamespace

from app.workflows.chat_script import ScriptError, evaluate, parse_expression
from app.workflows.context import ActionContext
from app.workflows.templates import render


@pytest.mark.parametrize("script,expected", [
    ('"PetrixBank"', "PetrixBank"),
    ('financial_institution + "-" + ticket_number', "PetrixBank-12345"),
    ('financial_institution + "-" + str(12345)', "PetrixBank-12345"),
    ('f"{financial_institution}-{ticket_number}"', "PetrixBank-12345"),
    ('round(1250 * 0.16, 2)', 200.0),
    ('10 // 3 + 10 % 3', 4),
    ('-(2 + 3)', -5), ('+5', 5),
    ('True and not False', True), ('False or "default"', "default"),
    ('"approved" if 750 >= 700 else "review"', "approved"),
    ('1 < 2 <= 3 and "loans" in ["loans", "cards"]', True),
    ('None is None and None is not False', True),
    ('["a", 2, True, None]', ["a", 2, True, None]),
    ('(1, 2)', [1, 2]), ('{"name": "Petrix", "count": 3}', {"name": "Petrix", "count": 3}),
    ('"  HELLO  ".strip().lower()', 'hello'), ('"petrix".upper()', 'PETRIX'),
    ('"--a--".lstrip("-").rstrip("-")', 'a'),
    ('"Petrix Bank".replace(" ", "-")', 'Petrix-Bank'),
    ('"a-b-c".split("-")[1]', 'b'), ('"a-b-c".split("-", 1)', ['a','b-c']),
    ('"-".join(["Petrix", "12345"])', 'Petrix-12345'),
    ('"Petrix".startswith("Pet") and "Bank".endswith("ank")', True),
    ('variables.get("attempts", 0) + 1', 1), ('{"a": 3}.get("a")', 3),
    ('[1, 2, 3][-1]', 3), ('"abcdef"[1:5:2]', 'bd'),
    ('len("Petrix")', 6), ('int("123") + 1', 124), ('float("1.5")', 1.5),
    ('bool(0)', False), ('abs(-5)', 5), ('min([8, 2, 4])', 2),
    ('max(1, 3, 2)', 3), ('sum([1, 2, 3], 4)', 10),
    ('"ab" * 3', 'ababab'), ('3 * [1, 2]', [1, 2, 1, 2, 1, 2]),
    ('"abc" * -2', ''), ('"" * (2 * 1000000000000000000000)', ''),
])
def test_supported_python_expressions(script, expected):
    assert evaluate(script, {"financial_institution": "PetrixBank", "ticket_number": "12345", "variables": {}}) == expected


@pytest.mark.parametrize("script", [
    '__import__("os").system("echo forbidden")', 'open("file.txt")', 'eval("2+2")',
    'exec("x=1")', 'getattr("x", "__class__")', '(lambda: 1)()',
    '"x".__class__', '"x".format()', '"x".lower.__globals__',
    '[x for x in [1,2]]', '(x for x in [1])', '{1, 2}', '(x := 1)',
    '2 ** 1000000000', '"%1000000000s" % "x"', 'str(*[1])',
    'dict(a=1)', 'round(1, ndigits=2)', '{**variables}',
    'f"{1:1000000000}"', 'f"{1!r}"', '_private', 'import os', 'x = 1',
])
def test_unsupported_code_is_rejected_before_execution(script):
    with pytest.raises(ScriptError): parse_expression(script)


@pytest.mark.parametrize("script", [
    'missing', '1 / 0', '"x" + 1', 'int("not a number")', 'float("inf")',
    'float("nan")', '"x" * 1000000000', '[1] * 1000000',
    '"a".replace("a", "x" * 8192) + "x"',
    '("a" * 8192).replace("", "xxxx")',
    '"-".join(["x" * 8192, "y" * 8192])', '("x " * 101).split()',
    '2 * 999999999999999999999999999999999999999999999999999999999999999999999999999999999',
    '[1][9]', '{"a":1}["b"]', '"abc"[::0]',
    'len(12)', 'sum([1, "a"])', 'min([])', '{1: "a"}',
    '"text".get("a")', 'variables.get(["a"])', 'round(1.5, 1000000)',
    '"x".replace("x", 1)', '"a".split(1)', '("abc").strip(1)',
    '"a".startswith(["a"])', 'str(1, 2)', '1j', 'b"bytes"',
])
def test_type_errors_missing_data_and_resource_limits_are_controlled(script):
    with pytest.raises(ScriptError): evaluate(script, {"variables": {}})


def test_python_objects_cannot_enter_the_expression_runtime():
    class Unexpected:
        def __str__(self): raise AssertionError("Python object protocol was invoked")
    with pytest.raises(ScriptError): evaluate('str(value)', {"value": Unexpected()})


def test_short_circuit_and_optional_variables():
    assert evaluate('False and missing', {}) is False
    assert evaluate('"ready" if True else missing', {}) == 'ready'
    assert evaluate('variables.get("missing")', {"variables": {}}) is None
    # Reading one field still works when the namespace exceeds a single value's 16 KB limit.
    variables = {"a": "x" * 8000, "b": "y" * 8000, "c": "z" * 8000}
    assert evaluate('len(variables.get("c"))', {"variables": variables}) == 8000


def test_expressions_and_values_are_bounded():
    with pytest.raises(ScriptError): parse_expression('"' + 'a' * 2000 + '"')
    with pytest.raises(ScriptError): parse_expression('+'.join(['1'] * 100))
    with pytest.raises(ScriptError): evaluate('"' + '\\ud800' + '"', {})
    with pytest.raises(ScriptError): evaluate('"é" * 8192', {})


def context(variables=None):
    agent = SimpleNamespace(id="agent-1", name="Petrix", default_language="en", languages=["en", "sw"])
    return ActionContext(agent, "en", {"content": "message", "conversation_id": "chat-1"}, {"variables": variables or {}})


def test_template_variables_json_paths_and_legacy_aliases():
    ctx = context({"complaint_ref": "PetrixBank-12345", "customer": {"name": "Chris"}, "flags": [True, 3], "nothing": None})
    assert render('{complaint_ref}: {{customer.name}} / {flags.0}', ctx) == 'PetrixBank-12345: Chris / true'
    assert render('Hi {_chatbot.name}; {{agent.name}}; {{input.message}}', ctx) == 'Hi Petrix; Petrix; message'
    assert render('Object: {customer}', ctx) == 'Object: {"name":"Chris"}'
    assert render('Nullable: {nothing}', ctx) == 'Nullable: '
    ctx.variables["raw"] = '{agent_name}'
    assert render('{raw}', ctx) == '{agent_name}'
    with pytest.raises(ScriptError): render('{missing}', ctx)
    with pytest.raises(ScriptError): render('{customer.missing}', ctx)
    with pytest.raises(ScriptError): render('{nothing}', ctx)
    with pytest.raises(ScriptError): render('{complaint_ref}', ctx, maximum=5)


@pytest.mark.parametrize('name', ['_private','1name','a-b','two words','a.b','école','a'*65,'class','str','variables','system','agent_name','conversation_id'])
def test_variable_name_and_system_guards(name):
    with pytest.raises(ScriptError): context().assign(name, 'test')


def test_variable_value_and_session_limits_and_language_write():
    ctx = context(); ctx.assign('flag', True); ctx.assign('nullable', None)
    assert ctx.variables == {"flag": True, "nullable": None}
    ctx.assign('language', 'sw'); assert ctx.language == 'sw' and 'language' not in ctx.variables
    with pytest.raises(ScriptError): ctx.assign('language', 'fr')
    for index in range(98): ctx.assign('v' + str(index), index)
    with pytest.raises(ScriptError): ctx.assign('overflow', 1)
    big = context()
    for index in range(8): big.assign('v' + str(index), 'x'*8000)
    with pytest.raises(ScriptError): big.assign('too_big', 'x'*8000)
