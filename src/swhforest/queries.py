"""Built-in structural queries, as swhforest patterns and as ast-grep rules."""

from .query import Pattern as P, q_nested_function, q_open, q_print_in_loop, q_train_fit


def _call_attr(name, obj=None):
    f = {"attribute": P(type="identifier", text=name)}
    if obj:
        f["object"] = P(type="identifier", text=obj)
    return P(type="call", fields={"function": P(type="attribute", fields=f)})


def _js_member_call(prop, obj=None):
    f = {"property": P(type="property_identifier", text=prop)}
    if obj is not None:
        f["object"] = obj
    return P(type="call_expression", fields={"function": P(type="member_expression", fields=f)})


# name -> (language, pattern, description)
QUERIES = {
    "train_fit": ("python", q_train_fit(), "function named train that calls x.fit()"),
    "py_open": ("python", q_open(), "function that calls open()"),
    "with_open_json": ("python", P(type="with_statement",
        contains=P(type="call", fields={"function": P(type="identifier", text="open")}),
        fields={"body": P(type="block", contains=_call_attr("load", "json"))}),
        "with open(...) whose body calls json.load()"),
    "init_self_assign": ("python", P(type="function_definition",
        fields={"name": P(type="identifier", text="__init__")},
        contains=P(type="assignment", fields={"left": P(type="attribute",
            fields={"object": P(type="identifier", text="self")})})),
        "__init__ assigning an attribute of self"),
    "print_in_loop": ("python", q_print_in_loop(), "function with print() inside a for loop"),
    "loop_append": ("python", P(type="for_statement", fields={"body": P(type="block",
        contains=P(type="for_statement", contains=_call_attr("append")))}),
        "for loop containing a for loop that calls x.append()"),
    "nested_fn": ("python", q_nested_function(), "function defined inside a function"),
    "py_eval": ("python", P(type="call", fields={"function": P(type="identifier", text="eval")}),
        "call of eval()"),
    "except_pass": ("python", P(type="except_clause", contains=P(type="pass_statement")),
        "except clause containing pass"),
    "js_fetch": ("javascript", P(type="function_declaration", contains=P(
        type="call_expression", fields={"function": P(type="identifier", text="fetch")})),
        "function declaration that calls fetch()"),
    "then_catch": ("javascript", _js_member_call("catch", _js_member_call("then")),
        "x.then(...).catch(...)"),
    "listener_arrow": ("javascript", P(type="call_expression", fields={
        "function": P(type="member_expression", fields={
            "property": P(type="property_identifier", text="addEventListener")}),
        "arguments": P(type="arguments", contains=P(type="arrow_function"))}),
        "addEventListener with an arrow-function argument"),
    "await_in_loop": ("javascript", P(type="for_statement", contains=P(type="await_expression")),
        "for loop containing await"),
}


def _has(kind=None, field=None, regex=None, deep=False, **more):
    r = {}
    if deep:
        r["stopBy"] = "end"
    if field:
        r["field"] = field
    if kind:
        r["kind"] = kind
    if regex:
        r["regex"] = regex
    r.update(more)
    return r


_PY_CALL_ATTR = lambda name, obj=None: _has("call", has=_has("attribute", "function", all=[
    {"has": _has(field="attribute", regex=f"^{name}$")}] + ([{"has": _has(field="object", regex=f"^{obj}$")}] if obj else [])))

# ast-grep rules (kind + relational constraints); counts are compared, not assumed equal
AST_GREP = {
    "train_fit": {"kind": "function_definition", "all": [
        {"has": _has(field="name", regex="^train$")},
        {"has": dict(_PY_CALL_ATTR("fit"), stopBy="end")}]},
    "py_open": {"kind": "function_definition", "has": _has("call", deep=True, has=_has("identifier", "function", "^open$"))},
    "with_open_json": {"kind": "with_statement", "all": [
        {"has": _has("call", deep=True, has=_has("identifier", "function", "^open$"))},
        {"has": _has("block", "body", has=dict(_PY_CALL_ATTR("load", "json"), stopBy="end"))}]},
    "init_self_assign": {"kind": "function_definition", "all": [
        {"has": _has(field="name", regex="^__init__$")},
        {"has": _has("assignment", deep=True, has=_has("attribute", "left", has=_has(field="object", regex="^self$")))}]},
    "print_in_loop": {"kind": "function_definition", "has": _has("for_statement", deep=True,
        has=_has("call", deep=True, has=_has("identifier", "function", "^print$")))},
    "loop_append": {"kind": "for_statement", "has": _has("block", "body", has=_has("for_statement", deep=True,
        has=dict(_PY_CALL_ATTR("append"), stopBy="end")))},
    "nested_fn": {"kind": "function_definition", "has": _has("block", "body", has=_has("function_definition", deep=True))},
    "py_eval": {"kind": "call", "has": _has("identifier", "function", "^eval$")},
    "except_pass": {"kind": "except_clause", "has": _has("pass_statement", deep=True)},
    "js_fetch": {"kind": "function_declaration", "has": _has("call_expression", deep=True,
        has=_has("identifier", "function", "^fetch$"))},
    "then_catch": {"kind": "call_expression", "has": _has("member_expression", "function", all=[
        {"has": _has(field="property", regex="^catch$")},
        {"has": _has("call_expression", "object", has=_has("member_expression", "function",
            has=_has(field="property", regex="^then$")))}])},
    "listener_arrow": {"kind": "call_expression", "all": [
        {"has": _has("member_expression", "function", has=_has(field="property", regex="^addEventListener$"))},
        {"has": _has("arguments", "arguments", has=_has("arrow_function", deep=True))}]},
    "await_in_loop": {"kind": "for_statement", "has": _has("await_expression", deep=True)},
}
