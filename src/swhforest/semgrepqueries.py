"""External query workload: rules from the public Semgrep registry, translated.

Source: https://github.com/semgrep/semgrep-rules (branch ``develop``).  Each
entry translates the core pattern of one well-known Python or JavaScript rule
into the swhforest pattern language (node type, grammar field -> sub-pattern,
literal leaf text, ``contains`` = descendant-or-self).  The language has no
disjunction, negation, regex, taint or metavariable equality, so:

* ``pattern-either``: one representative branch is kept;
* ``pattern-not`` / ``pattern-not-inside`` / ``metavariable-regex`` /
  ``metavariable-comparison`` / ``focus-metavariable``: dropped;
* ``pattern-inside``: becomes an enclosing pattern with ``contains`` (the match
  is then reported at the enclosing node, not at the inner statement);
* ``$X`` metavariables: become untyped sub-patterns or are dropped, and
  repeated metavariables are not required to bind the same text;
* taint rules (source -> sink): approximated by syntactic nesting of the
  source inside the sink's arguments.

Every approximation is recorded in the last tuple element.  Rules whose meaning
would be lost (pure regex rules, metavariable-comparison on integer values,
taint with an arbitrary function parameter as source, metavariable-analysis)
were not included.  Node types and field names are those of
tree-sitter-python 0.25.0 and tree-sitter-javascript 0.25.0, checked by parsing
each rule's test file; scripts can validate a pattern against the annotated
``ruleid:`` / ``ok:`` lines of that file with treematch.find_in_tree.
"""

from .query import Pattern as P

_BASE = "https://github.com/semgrep/semgrep-rules/blob/develop/"


# ---------------------------------------------------------------- helpers
def _id(text):
    return P(type="identifier", text=text)


def _py_call(func, obj=None, arguments=None):
    """``obj.func(...)`` (or ``func(...)`` when obj is None) in Python."""
    if obj is None:
        f = _id(func)
    else:
        f = P(type="attribute", fields={"object": _id(obj), "attribute": _id(func)})
    fields = {"function": f}
    if arguments is not None:
        fields["arguments"] = arguments
    return P(type="call", fields=fields)


def _py_mod_call(obj):
    """``obj.$ANY(...)`` in Python."""
    return P(type="call", fields={"function": P(type="attribute", fields={"object": _id(obj)})})


def _py_kwarg(name, value=None):
    f = {"name": _id(name)}
    if value is not None:
        f["value"] = value
    return P(type="keyword_argument", fields=f)


def _py_args_with(sub):
    return P(type="argument_list", contains=sub)


def _py_attr(obj, attr):
    return P(type="attribute", fields={"object": _id(obj), "attribute": _id(attr)})


def _js_member(prop, obj=None):
    f = {"property": P(type="property_identifier", text=prop)}
    if obj is not None:
        f["object"] = obj if isinstance(obj, P) else _id(obj)
    return P(type="member_expression", fields=f)


def _js_call(func, obj=None, arguments=None):
    """``obj.func(...)`` (or ``func(...)`` when obj is None) in JavaScript."""
    f = _id(func) if obj is None else _js_member(func, obj)
    fields = {"function": f}
    if arguments is not None:
        fields["arguments"] = arguments
    return P(type="call_expression", fields=fields)


def _js_member_call(prop, arguments=None):
    """``$X.prop(...)`` in JavaScript."""
    fields = {"function": _js_member(prop)}
    if arguments is not None:
        fields["arguments"] = arguments
    return P(type="call_expression", fields=fields)


def _js_args_with(sub):
    return P(type="arguments", contains=sub)


def _js_str(text):
    return P(type="string_fragment", text=text)


# short_id -> (language, pattern, semgrep rule id, rule url, approximation note)
SEMGREP_QUERIES = {
    # ------------------------------------------------------------ Python
    "sg_py_subprocess_shell": ("python",
        P(type="call",
          fields={"function": P(type="attribute", fields={"object": _id("subprocess")}),
                  "arguments": _py_args_with(_py_kwarg("shell", P(type="true")))}),
        "subprocess-shell-true",
        _BASE + "python/lang/security/audit/subprocess-shell-true.yaml",
        "subprocess.$FUNC(..., shell=True, ...); dropped pattern-not for a literal-string "
        "command (subprocess.$FUNC(\"...\", shell=True)); metavariable-pattern on $TRUE "
        "reduced to the literal True; keyword is searched anywhere in the argument list"),
    "sg_py_pyyaml_load": ("python",
        _py_call("load", "yaml", _py_args_with(
            _py_kwarg("Loader", P(type="attribute", fields={"object": _id("yaml"),
                                                             "attribute": _id("Loader")})))),
        "avoid-pyyaml-load",
        _BASE + "python/lang/security/deserialization/avoid-pyyaml-load.yaml",
        "kept branch yaml.load(..., Loader=yaml.Loader, ...); dropped the other 6 branches "
        "(unsafe_load, UnsafeLoader, CLoader, load_all); dropped pattern-inside 'import yaml' "
        "and pattern-not-inside ruamel.yaml.YAML(...)"),
    "sg_py_exec": ("python", _py_call("exec"),
        "exec-detected",
        _BASE + "python/lang/security/audit/exec-detected.yaml",
        "exec(...); dropped pattern-not exec(\"...\") (literal-string argument)"),
    "sg_py_md5": ("python", _py_call("md5", "hashlib"),
        "insecure-hash-algorithm-md5",
        _BASE + "python/lang/security/insecure-hash-algorithms-md5.yaml",
        "hashlib.md5(...); dropped pattern-not hashlib.md5(..., usedforsecurity=False, ...)"),
    "sg_py_mktemp": ("python", _py_call("mktemp", "tempfile"),
        "tempfile-insecure",
        _BASE + "python/lang/correctness/tempfile/mktemp.yaml",
        "exact: tempfile.mktemp(...)"),
    "sg_py_pdb": ("python", _py_call("set_trace", "pdb"),
        "python-debugger-found",
        _BASE + "python/lang/best-practice/pdb.yaml",
        "kept branch pdb.set_trace(); dropped branch 'import pdb'"),
    "sg_py_mutable_default_list": ("python",
        P(type="function_definition", fields={
            "parameters": P(type="parameters", contains=P(type="default_parameter",
                                                          fields={"value": P(type="list")})),
            "body": P(type="block", contains=P(type="call", fields={
                "function": P(type="attribute", fields={"attribute": _id("append")})}))}),
        "default-mutable-list",
        _BASE + "python/lang/correctness/common-mistakes/default-mutable-list.yaml",
        "def $F(..., $D=[...], ...) whose body calls $X.append(...); metavariable equality "
        "($X == $D) dropped, so any append in the body counts; kept the append branch only "
        "(not extend/insert); dropped the 9 pattern-not-inside re-assignments of $D and the "
        "nested-def exclusion; matches the default only as untyped default_parameter (not "
        "typed_default_parameter) and accepts non-empty list literals; reported at the def"),
    "sg_py_mutable_default_dict": ("python",
        P(type="function_definition", fields={
            "parameters": P(type="parameters", contains=P(type="default_parameter",
                                                          fields={"value": P(type="dictionary")})),
            "body": P(type="block", contains=P(type="assignment", fields={
                "left": P(type="subscript")}))}),
        "default-mutable-dict",
        _BASE + "python/lang/correctness/common-mistakes/default-mutable-dict.yaml",
        "def $F(..., $D={...}, ...) whose body assigns $X[...] = ...; metavariable equality "
        "($X == $D) dropped; kept the subscript-assignment branch only (not update/"
        "setdefault); dropped all pattern-not-inside re-assignments and the nested-def "
        "exclusion; untyped default_parameter only; reported at the def"),
    "sg_py_is_string": ("python",
        P(type="comparison_operator", fields={"operators": P(type="is", text="is")},
          contains=P(type="string")),
        "string-is-comparison",
        _BASE + "python/lang/correctness/common-mistakes/is-comparison-string.yaml",
        "$S is \"...\" (either side); string may be anywhere in the comparison (also nested); "
        "dropped pattern-not for $S is \"\" and the None/True/False/type() exclusions "
        "(those have no string operand anyway); uses the anonymous 'is' operator token, "
        "which the structural index cannot prune on"),
    "sg_py_yield_in_init": ("python",
        P(type="function_definition", fields={"name": _id("__init__")},
          contains=P(type="yield")),
        "yield-in-init",
        _BASE + "python/lang/correctness/return-in-init.yaml",
        "def __init__(...) containing yield; dropped pattern-inside class $A and the "
        "pattern-not-inside for nested defs (a yield in a nested function also counts); "
        "reported at the def"),
    "sg_py_cert_none": ("python",
        P(type="call", fields={"arguments": _py_args_with(
            _py_kwarg("cert_reqs", _py_attr("ssl", "CERT_NONE")))}),
        "disabled-cert-validation",
        _BASE + "python/lang/security/audit/network/disabled-cert-validation.yaml",
        "any call with keyword cert_reqs=ssl.CERT_NONE; dropped the restriction to the 8 "
        "urllib3/ssl callee names; metavariable-regex on $REQS reduced to ssl.CERT_NONE "
        "(not CERT_OPTIONAL, 'NONE', 'OPTIONAL')"),
    "sg_py_bind_all": ("python",
        P(type="call", fields={
            "function": P(type="attribute", fields={"attribute": _id("bind")}),
            "arguments": _py_args_with(P(type="string_content", text="0.0.0.0"))}),
        "avoid-bind-to-all-interfaces",
        _BASE + "python/lang/security/audit/network/bind.yaml",
        "$S.bind((\"0.0.0.0\", ...)); dropped the preceding $S = socket.socket(...) "
        "statement and metavariable equality, so any .bind call qualifies; kept the "
        "'0.0.0.0' branch only (not '::' or ''); the literal may be anywhere in the "
        "arguments; reported at the bind call, not at the socket() line"),
    "sg_py_sleep_in_async": ("python",
        P(type="function_definition", contains=P(type="async", text="async"),
          fields={"body": P(type="block", contains=_py_call("sleep", "time"))}),
        "sync-sleep-in-async-code",
        _BASE + "python/lang/correctness/sync-sleep-in-async-code.yaml",
        "function whose body calls time.sleep(...) and that contains an 'async' token; "
        "'async' is an unlabelled child of function_definition, so the token may also "
        "come from a nested async def / async for / async with; dropped the "
        "pattern-not-inside for a nested sync def; reported at the def"),
    "sg_py_unverified_ssl": ("python", _py_call("_create_unverified_context", "ssl"),
        "unverified-ssl-context",
        _BASE + "python/lang/security/unverified-ssl-context.yaml",
        "kept branch ssl._create_unverified_context(...); dropped the assignment branch "
        "ssl._create_default_https_context = ssl._create_unverified_context"),
    "sg_py_hardcoded_password": ("python",
        P(type="function_definition", fields={"parameters": P(type="parameters",
            contains=P(type="default_parameter", fields={
                "name": _id("password"), "value": P(type="string")}))}),
        "hardcoded-password-default-argument",
        _BASE + "python/lang/security/audit/hardcoded-password-default-argument.yaml",
        "def $F(..., password=\"...\", ...); dropped pattern-not password=\"\" (empty "
        "string); f-strings also count as strings; untyped default_parameter only"),
    "sg_py_exit": ("python", _py_call("exit"),
        "use-sys-exit",
        _BASE + "python/lang/correctness/exit.yaml",
        "exit($X); pattern-not sys.exit($X) is implied (callee must be a bare identifier); "
        "any arity accepted instead of exactly one argument"),
    "sg_js_eval": ("javascript", _js_call("eval"),
        "eval-detected",
        _BASE + "javascript/browser/security/eval-detected.yaml",
        "eval(...); dropped pattern-not eval(\"...\") (literal-string argument)"),
    "sg_js_innerhtml": ("javascript",
        P(type="assignment_expression", fields={"left": _js_member("innerHTML")}),
        "insecure-innerhtml",
        _BASE + "javascript/browser/security/insecure-innerhtml.yaml",
        "$EL.innerHTML = $HTML; dropped pattern-not $EL.innerHTML = \"...\" (literal string)"),
    "sg_js_document_write": ("javascript", _js_call("write", "document"),
        "insecure-document-method",
        _BASE + "javascript/browser/security/insecure-document-method.yaml",
        "kept branch document.write(...); dropped the innerHTML/outerHTML assignment "
        "branches and the pattern-not literal-string exclusions"),
    "sg_js_postmessage_wildcard": ("javascript",
        _js_member_call("postMessage", _js_args_with(_js_str("*"))),
        "wildcard-postmessage-configuration",
        _BASE + "javascript/browser/security/wildcard-postmessage-configuration.yaml",
        "$OBJECT.postMessage(..., '*', ...); the '*' string may be anywhere inside the "
        "arguments, also nested; a template literal `*` also matches"),
    "sg_js_spawn_shell": ("javascript",
        _js_call("spawn", arguments=_js_args_with(P(type="pair", fields={
            "key": P(type="property_identifier", text="shell")}))),
        "spawn-shell-true",
        _BASE + "javascript/lang/security/audit/spawn-shell-true.yaml",
        "spawn(..., {shell: $SHELL}); kept the bare spawn branch (not spawnSync, "
        "$CP.spawn, $CP.spawnSync); dropped pattern-not {shell: false}; the option object "
        "may be in any argument position"),
    "sg_js_pseudorandom": ("javascript", _js_member("pseudoRandomBytes", "crypto"),
        "detect-pseudoRandomBytes",
        _BASE + "javascript/lang/security/detect-pseudoRandomBytes.yaml",
        "exact: crypto.pseudoRandomBytes"),
    "sg_js_buffer_noassert": ("javascript",
        _js_member_call("readUInt8", _js_args_with(P(type="true"))),
        "detect-buffer-noassert",
        _BASE + "javascript/lang/security/detect-buffer-noassert.yaml",
        "$OBJ.readUInt8(..., true); metavariable-regex on $API "
        "(read|write)(U?Int8|...(LE|BE)) reduced to readUInt8; 'true' may be any argument, "
        "not necessarily the last"),
    "sg_js_mustache_escape": ("javascript",
        P(type="assignment_expression", fields={"left": _js_member("escapeMarkup"),
                                                "right": P(type="false")}),
        "detect-disable-mustache-escape",
        _BASE + "javascript/lang/security/detect-disable-mustache-escape.yaml",
        "exact: $OBJ.escapeMarkup = false"),
    "sg_js_assigned_undefined": ("javascript",
        P(type="assignment_expression", fields={"left": P(type="undefined", text="undefined")}),
        "assigned-undefined",
        _BASE + "javascript/lang/best-practice/assigned-undefined.yaml",
        "kept branch undefined = $X; dropped the var/let/const undefined = $X declaration "
        "branches"),
    "sg_js_alert": ("javascript", _js_call("alert"),
        "javascript-alert",
        _BASE + "javascript/lang/best-practice/leftover_debugging.yaml",
        "alert(...) with any arity instead of alert() / alert($X)"),
    "sg_js_zlib_in_loop": ("javascript",
        P(type="for_statement", fields={"body": P(type="statement_block", contains=P(
            type="call_expression", fields={"function": P(type="member_expression",
                                                          fields={"object": _id("zlib")})}))}),
        "zlib-async-loop",
        _BASE + "javascript/lang/best-practice/zlib-async-loop.yaml",
        "for (...) { ... zlib.$METHOD(...) ... }; kept the C-style for branch only (not "
        "while, do-while, forEach, map, reduce, reduceRight); dropped metavariable-regex "
        "excluding *Sync methods; reported at the for loop"),
    "sg_js_replaceall": ("javascript",
        _js_member_call("replaceAll", _js_args_with(P(type="string"))),
        "no-replaceall",
        _BASE + "javascript/lang/correctness/no-replaceall.yaml",
        "$STRING.replaceAll(\"...\", $NEW); the string literal may be any argument, not "
        "necessarily the first"),
    "sg_js_proto_pollution_loop": ("javascript",
        P(type="while_statement", fields={"body": P(type="statement_block", contains=P(
            type="assignment_expression", fields={"left": P(type="identifier"),
                                                  "right": P(type="subscript_expression")}))}),
        "prototype-pollution-loop",
        _BASE + "javascript/lang/security/audit/prototype-pollution/prototype-pollution-loop.yaml",
        "while (...) { ... $SMTH = $X[$A] ... }; metavariable equality ($X == $SMTH) dropped; "
        "kept the while branch and the plain $SMTH = $SMTH[$A] form only (not for/forEach, "
        "'= ... || $Z', '= ... && $Z', chained assignment); dropped pattern-not-inside for "
        "counter loops and the metavariable-pattern on $A; reported at the loop"),
    "sg_js_spawn_git_clone": ("javascript",
        _js_call("spawn", arguments=_js_args_with(P(type="array", contains=_js_str("clone")))),
        "spawn-git-clone",
        _BASE + "javascript/lang/security/spawn-git-clone.yaml",
        "spawn(..., [..., 'clone', ...]); kept the bare spawn branch (not $X.spawn); dropped "
        "the 'git' first-argument check, the position of 'clone', and the pattern-not "
        "for a literal URL"),
    "sg_js_object_assign_json": ("javascript",
        _js_call("assign", "Object", _js_args_with(_js_call("parse", "JSON"))),
        "insecure-object-assign",
        _BASE + "javascript/lang/security/insecure-object-assign.yaml",
        "taint rule (source JSON.parse(...), sink Object.assign(...)) approximated by "
        "syntactic nesting: Object.assign(..., JSON.parse(...), ...); flows through "
        "variables are missed; pattern-not JSON.parse(\"...\") dropped"),
    "sg_js_stringify_key": ("javascript",
        P(type="subscript_expression", fields={"index": _js_call("stringify", "JSON")}),
        "no-stringify-keys",
        _BASE + "javascript/lang/correctness/no-stringify-keys.yaml",
        "taint rule (source JSON.stringify(...), sink $OBJECT[...]) approximated by direct "
        "nesting $OBJECT[JSON.stringify(...)]; flows through variables and aliases "
        "($S = JSON.stringify; $S(...)) are missed"),
}
