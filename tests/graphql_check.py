"""A small GraphQL reader for the tests.

It parses an operation (a query or a mutation) and checks every field, argument,
inline fragment and literal input value against an excerpt of GitHub's public
schema, so an operation that names a field GitHub does not have fails here
instead of on GitHub. Used by the tests and by fake_gh.py, which refuses
operations the same way the real API would.

The excerpt holds `type`, `interface`, `input`, `enum` and `union` definitions. A selection may hold inline
fragments (`... on Issue { number }`) and `__typename`. Arguments are literals: strings, numbers, booleans, enum
names, lists and objects; an object given to an argument of an input type is checked field by field (unknown
fields, missing required fields, enum values, nested inputs). Variables are not checked.
"""

import os
import re

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "github-schema-excerpt.graphql")
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\.\.\.|\$?[A-Za-z_]\w*|-?\d+(?:\.\d+)?|[{}():!=,\[\]@]')


class Schema(dict):
    """{type name: {field name: (set of argument names, return type name)}}, with the parts of the excerpt that a
    plain dict of fields cannot hold: `raw` ({(type, field): the declared type, as written}), `args` ({(type, field):
    {argument: declared type}}), `kinds` ({name: "type", "interface", "input", "enum" or "union"}), `values`
    ({enum name: set of values}), `members` ({union name: set of type names}) and `implements` ({type name: set of
    interface names})."""

    def __init__(self):
        super().__init__()
        self.raw, self.args, self.kinds, self.values, self.members, self.implements = {}, {}, {}, {}, {}, {}


def load_schema(path=SCHEMA_FILE):
    with open(path, encoding="utf-8") as handle:
        text = "\n".join(line for line in handle.read().split("\n") if not line.lstrip().startswith("#"))
    schema = Schema()
    for kind, name, rest, body in re.findall(r"^(type|interface|input) (\w+)([^{\n]*) \{\n(.*?)\n\}", text, re.S | re.M):
        fields = {}
        for field, args, returns in re.findall(r"^\s+(\w+)(?:\(([^)]*)\))?: (.+?)\s*$", body, re.M):
            returns = returns.strip()
            declared = {}
            for arg, declared_type in re.findall(r"(\w+): ([^\s=]+)", args or ""):
                declared[arg] = declared_type
            fields[field] = (set(declared) | set(re.findall(r"(\w+):", args or "")), returns.strip("[]!"))
            schema.raw[(name, field)] = returns
            schema.args[(name, field)] = declared
        schema[name] = fields
        schema.kinds[name] = kind
        schema.implements[name] = set(re.findall(r"\w+", rest.replace("implements", "")))
    for name, body in re.findall(r"^enum (\w+) \{\n(.*?)\n\}", text, re.S | re.M):
        schema.kinds[name] = "enum"
        schema.values[name] = set(re.findall(r"^\s+(\w+)\s*$", body, re.M))
    for name, members in re.findall(r"^union (\w+) = (.+)$", text, re.M):
        schema.kinds[name] = "union"
        schema.members[name] = {part.strip() for part in members.split("|")}
    return schema


def operation(query):
    """"query" or "mutation": the operation type of a document (a bare selection set is a query)."""
    tokens = TOKEN.findall(query)
    return tokens[0] if tokens and tokens[0] in ("query", "mutation") else "query"


def parse_value(tokens, pos):
    """One literal value at tokens[pos]: (value, next position). Scalars, enum names and variables stay as the raw
    token (a string keeps its quotes); an object is a dict, a list is a list."""
    token = tokens[pos]
    if token == "{":
        pos += 1
        obj = {}
        while tokens[pos] != "}":
            if tokens[pos] == ",":
                pos += 1
                continue
            assert tokens[pos + 1] == ":", "expected a field of the object at token %d" % pos
            key = tokens[pos]
            obj[key], pos = parse_value(tokens, pos + 2)
        return obj, pos + 1
    if token == "[":
        pos += 1
        items = []
        while tokens[pos] != "]":
            if tokens[pos] == ",":
                pos += 1
                continue
            value, pos = parse_value(tokens, pos)
            items.append(value)
        return items, pos + 1
    return token, pos + 1


def parse_query(query):
    """Return the top selection set as a list of (alias, name, {argument: value}, children). An inline fragment is
    ("...", "...", {"on": type name}, children)."""
    tokens = TOKEN.findall(query)
    pos = 0
    if tokens and tokens[0] in ("query", "mutation"):
        pos = 1
        if tokens[pos] != "{" and tokens[pos] != "(":
            pos += 1  # operation name
        if tokens[pos] == "(":
            while tokens[pos] != ")":
                pos += 1
            pos += 1

    def selection(pos):
        assert tokens[pos] == "{", "expected { at token %d" % pos
        pos += 1
        fields = []
        while tokens[pos] != "}":
            if tokens[pos] == ",":
                pos += 1
                continue
            if tokens[pos] == "...":
                assert tokens[pos + 1] == "on", "only inline fragments are supported (token %d)" % pos
                on = tokens[pos + 2]
                children, pos = selection(pos + 3)
                fields.append(("...", "...", {"on": on}, children))
                continue
            alias = name = tokens[pos]
            pos += 1
            if tokens[pos] == ":":
                name = tokens[pos + 1]
                pos += 2
            args = {}
            if tokens[pos] == "(":
                pos += 1
                while tokens[pos] != ")":
                    if tokens[pos] == ",":
                        pos += 1
                        continue
                    assert tokens[pos + 1] == ":", "expected argument at token %d" % pos
                    key = tokens[pos]
                    args[key], pos = parse_value(tokens, pos + 2)
                pos += 1
            children = []
            if tokens[pos] == "{":
                children, pos = selection(pos)
            fields.append((alias, name, args, children))
        return fields, pos + 1

    fields, _ = selection(pos)
    return fields


def literal(token):
    """The Python value of a scalar token: a string without its quotes, a number, a boolean, None, or the token."""
    if token.startswith('"'):
        return bytes(token[1:-1], "utf-8").decode("unicode_escape") if "\\" in token else token[1:-1]
    if token in ("true", "false"):
        return token == "true"
    if token == "null":
        return None
    if re.fullmatch(r"-?[0-9]+", token):
        return int(token)
    return token


def possible(schema, parent, on):
    """True when a fragment on `on` may appear inside a selection of `parent`."""
    if parent == on:
        return True
    if schema.kinds.get(parent) == "union":
        return on in schema.members[parent]
    if schema.kinds.get(parent) == "interface":
        return parent in schema.implements.get(on, ()) or on in schema.members.get(parent, ())
    return False


def check_input(schema, type_name, value, where, problems):
    """Check a literal against an input type, enum or scalar named `type_name` (without ! or [])."""
    kind = schema.kinds.get(type_name)
    if isinstance(value, str) and value.startswith("$"):
        return
    if kind == "input":
        if not isinstance(value, dict):
            problems.append("%s must be an object of type %s" % (where, type_name))
            return
        known = schema[type_name]
        for key, item in value.items():
            if key not in known:
                problems.append("Field '%s' doesn't exist on input type '%s'" % (key, type_name))
                continue
            declared = schema.raw[(type_name, key)]
            check_value(schema, declared, item, "%s.%s" % (where, key), problems)
        for key in known:
            declared = schema.raw[(type_name, key)]
            if declared.rstrip().endswith("!") and "=" not in declared and key not in value:
                problems.append("Argument '%s' on input type '%s' is required" % (key, type_name))
    elif kind == "enum":
        if not isinstance(value, str) or value not in schema.values[type_name]:
            problems.append("%s is not a value of enum %s" % (where, type_name))
    elif type_name in ("String", "ID", "URI", "DateTime", "Date"):
        if not (isinstance(value, str) and value.startswith('"')):
            problems.append("%s must be a string" % where)
    elif type_name == "Boolean":
        if value not in ("true", "false"):
            problems.append("%s must be a boolean" % where)
    elif type_name in ("Int", "Float"):
        if not (isinstance(value, str) and re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value)):
            problems.append("%s must be a number" % where)


def check_value(schema, declared, value, where, problems):
    declared = declared.strip().split("=")[0].strip()
    if declared.endswith("!"):
        declared = declared[:-1]
    if declared.startswith("["):
        inner = declared[1:-1]
        if not isinstance(value, list):
            value = [value]  # GraphQL coerces a single value into a list
        for item in value:
            check_value(schema, inner, item, where, problems)
        return
    check_input(schema, declared, value, where, problems)


def check(query, schema=None):
    """Return a list of problems; empty when every field, argument, fragment and literal input exists and fits."""
    schema = schema or load_schema()
    problems = []

    def walk(fields, type_name):
        known = schema.get(type_name)
        if known is None and schema.kinds.get(type_name) != "union":
            problems.append("type %s is not in the schema excerpt" % type_name)
            return
        known = known or {}
        for _, name, args, children in fields:
            if name == "...":
                on = args["on"]
                if on not in schema and schema.kinds.get(on) != "union":
                    problems.append("Fragment cannot be spread here: type %s is not in the schema excerpt" % on)
                elif not possible(schema, type_name, on):
                    problems.append("Fragment on %s can't be spread inside %s" % (on, type_name))
                else:
                    walk(children, on)
                continue
            if name == "__typename":
                continue
            if name not in known:
                problems.append("Field '%s' doesn't exist on type '%s'" % (name, type_name))
                continue
            arg_names, returns = known[name]
            for arg, value in args.items():
                if arg not in arg_names:
                    problems.append("Field '%s' doesn't accept argument '%s'" % (name, arg))
                    continue
                declared = schema.args.get((type_name, name), {}).get(arg)
                if declared:
                    check_value(schema, declared, value, "argument '%s' of '%s'" % (arg, name), problems)
            for arg, declared in schema.args.get((type_name, name), {}).items():
                if declared.endswith("!") and arg not in args:
                    problems.append("Field '%s' argument '%s' is required" % (name, arg))
            if children:
                walk(children, returns)
            elif returns in schema or schema.kinds.get(returns) == "union":
                problems.append("Field '%s' of type '%s' must have a selection of subfields" % (name, returns))

    walk(parse_query(query), "Mutation" if operation(query) == "mutation" else "Query")
    return problems
