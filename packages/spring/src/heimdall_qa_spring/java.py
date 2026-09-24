"""Just enough Java to read annotations, without a JVM in the room.

The reader's whole job is to answer questions a compiler could answer in
milliseconds and the harness must answer in a `pip install` that brings no JDK: is
this class a controller, what does it map, which record is the body, what does each
of its components declare. So this module is a scanner, not a parser. It knows
exactly three things about Java grammar — a string literal is opaque, brackets
nest, and a declaration is preceded by its own annotations — and it stays
deliberately ignorant of everything else.

The one non-negotiable is that nothing here guesses. A construct the scanner cannot
read is reported as `None`, and the caller turns that into a gap, because a contract
generated from a misread annotation is worse than no contract: it produces cases
that pass for the wrong reason.

Why a scanner and not a grammar: whoever has to fix this is not a Java compiler
author, and a recursive-descent parser for the whole language is a second project
with a second bug surface. The constructs this has to survive are enumerated by the
reference corpus — 30 controllers, 93 handlers, 23 handlers on one advice class —
and each of them is a shape a test names.
"""

import re
from dataclasses import dataclass

#: A Java identifier, or a dotted name of them.
_IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"
_DOTTED = rf"{_IDENT}(?:\.{_IDENT})*"

_ANNOTATION_HEAD = re.compile(rf"@({_DOTTED})\s*\(")
_ANNOTATION_BARE = re.compile(rf"@({_DOTTED})\b")
_TYPE_DECL = re.compile(rf"\b(class|interface|enum|record)\s+({_IDENT})")
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_MODIFIERS = (
    "public",
    "protected",
    "private",
    "static",
    "final",
    "abstract",
    "default",
    "synchronized",
    "native",
    "strictfp",
    "transient",
    "volatile",
)
_MODIFIER_RE = re.compile(r"\b(" + "|".join(_MODIFIERS) + r")\b")
_OPENERS = {"(": ")", "{": "}", "[": "]"}


@dataclass(frozen=True)
class Annotation:
    """One `@Name(...)`, kept as raw text plus the three reads that matter.

    The arguments stay a string because the forms are open — `max = 64`,
    `regexp = "^x$"`, `{"admin", "test"}`, a constant reference — and a typed parse
    would have to model every one of them to answer what the reader actually asks:
    what is the first string, what is the named string, what is the named number.
    """

    name: str
    arguments: str | None = None

    def value(self) -> str | None:
        """The first positional string literal, e.g. `@RequestMapping("/api/ingest")`."""
        if not self.arguments:
            return None
        parts = split_arguments(self.arguments)
        return _first_string(parts[0]) if parts else None

    def strings(self) -> tuple[str, ...]:
        """Every string literal in the arguments, in source order."""
        return () if self.arguments is None else tuple(_STRING.findall(self.arguments))

    def unquote(self) -> tuple[str, ...]:
        """`strings()`, with escapes resolved to the values Java would see."""
        return tuple(unquote(literal) for literal in self.strings())

    def named(self, key: str) -> str | None:
        """The raw text of `key = ...`, or `None` when the argument is positional."""
        return _named_argument(self.arguments, key)

    def named_string(self, key: str) -> str | None:
        raw = self.named(key)
        return None if raw is None else _first_string(raw)

    def named_strings(self, key: str) -> tuple[str, ...]:
        raw = self.named(key)
        if raw is None:
            return ()
        return tuple(unquote(literal) for literal in _STRING.findall(raw))

    def named_int(self, key: str) -> int | None:
        raw = self.named(key)
        if raw is None:
            return None
        found = re.search(r"^-?\d+", raw.strip())
        return int(found.group(0)) if found else None

    def named_bool(self, key: str) -> bool | None:
        raw = self.named(key)
        if raw is None:
            return None
        token = raw.strip()
        if token == "true":
            return True
        if token == "false":
            return False
        return None

    def raw(self) -> str:
        """The annotation as written, which is what a gap quotes back."""
        if self.arguments is None:
            return f"@{self.name}"
        return f"@{self.name}({self.arguments})"


@dataclass(frozen=True)
class Parameter:
    """One parameter or record component: its annotations, its type, its name."""

    name: str
    type: str
    annotations: tuple[Annotation, ...] = ()

    def annotation(self, name: str) -> Annotation | None:
        for candidate in self.annotations:
            if candidate.name == name:
                return candidate
        return None

    def has(self, name: str) -> bool:
        return self.annotation(name) is not None

    def names(self) -> tuple[str, ...]:
        return tuple(candidate.name for candidate in self.annotations)


@dataclass(frozen=True)
class Member:
    """A method, a field, a nested type or a compact constructor."""

    name: str
    kind: str
    annotations: tuple[Annotation, ...] = ()
    return_type: str | None = None
    parameters: tuple[Parameter, ...] = ()
    body: str = ""
    #: For `kind == "type"`: `record`, `class`, `interface` or `enum`.
    type_kind: str | None = None
    #: For a record, its components — parameters, with their annotations.
    components: tuple[Parameter, ...] = ()
    is_constructor: bool = False
    modifiers: tuple[str, ...] = ()

    def annotation(self, name: str) -> Annotation | None:
        for candidate in self.annotations:
            if candidate.name == name:
                return candidate
        return None

    def has(self, name: str) -> bool:
        return self.annotation(name) is not None

    def parameter(self, name: str) -> Parameter | None:
        for candidate in self.parameters:
            if candidate.name == name:
                return candidate
        return None

    def parameter_has(self, annotation: str) -> bool:
        """Whether any parameter carries `annotation`, e.g. `RequestBody`."""
        return any(candidate.has(annotation) for candidate in self.parameters)


@dataclass(frozen=True)
class JavaType:
    """One declared type, with its own members and its place in the file."""

    name: str
    kind: str
    package: str
    annotations: tuple[Annotation, ...] = ()
    components: tuple[Parameter, ...] = ()
    members: tuple[Member, ...] = ()
    body: str = ""
    #: Fully qualified as `package.Outer.Inner`, which is what `Contract.dto` wants.
    fqn: str = ""
    modifiers: tuple[str, ...] = ()

    def annotation(self, name: str) -> Annotation | None:
        for candidate in self.annotations:
            if candidate.name == name:
                return candidate
        return None

    def has(self, name: str) -> bool:
        return self.annotation(name) is not None

    def methods(self) -> tuple[Member, ...]:
        return tuple(member for member in self.members if member.kind == "method")

    def fields(self) -> tuple[Member, ...]:
        return tuple(member for member in self.members if member.kind == "field")

    def nested(self) -> tuple[Member, ...]:
        return tuple(member for member in self.members if member.kind == "type")


@dataclass(frozen=True)
class JavaFile:
    """One `.java` file: its package, and every type declared in it."""

    path: str
    package: str
    types: tuple[JavaType, ...] = ()

    def all_types(self) -> tuple[JavaType, ...]:
        """Every type, the nested ones included, each with its own fully qualified name."""
        found: list[JavaType] = []
        for declared in self.types:
            found.append(declared)
            found.extend(_flatten(declared, ()))
        return tuple(found)


def parse_java(path: str, text: str) -> JavaFile:
    """One file, as the types it declares."""
    clean = strip_comments(text)
    package = _package_of(clean)
    types = tuple(
        _as_type(member, package, ())
        for member in _scan(clean)
        if member.kind == "type"
    )
    return JavaFile(path=path, package=package, types=types)


def strip_comments(text: str) -> str:
    """Comments out, string literals intact, offsets preserved.

    Offsets matter: `_scan` reads a declaration head as "everything between the
    previous `;` and this `{`", so a comment removed by one character instead of
    blanked would shift a head into the previous member. Blanking keeps every index
    where it was, which also keeps a `//` inside a string from eating the line.
    """
    out = list(text)
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            index = _skip_string(text, index)
        elif char == "'":
            index = _skip_char_literal(text, index)
        elif char == "/" and text.startswith("//", index):
            while index < len(text) and text[index] != "\n":
                out[index] = " "
                index += 1
        elif char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end == -1 else end + 2
            for position in range(index, end):
                if text[position] != "\n":
                    out[position] = " "
            index = end
        else:
            index += 1
    return "".join(out)


def find_matching(text: str, open_index: int) -> int:
    """The index of the `)`, `}` or `]` that closes the bracket at `open_index`."""
    opener = text[open_index]
    closer = _OPENERS[opener]
    depth = 0
    index = open_index
    while index < len(text):
        char = text[index]
        if char == '"':
            index = _skip_string(text, index)
            continue
        if char == "'":
            index = _skip_char_literal(text, index)
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError(f"unbalanced '{opener}' at {open_index}")


def split_arguments(text: str) -> list[str]:
    """Split on top-level commas, so `Map<String, Object>` stays one argument."""
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            index = _skip_string(text, index)
            continue
        if char == "'":
            index = _skip_char_literal(text, index)
            continue
        if char in "(<{[":
            depth += 1
        elif char in ")>}]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def parse_parameter(raw: str) -> Parameter | None:
    """One parameter: `@Ann("x") final Map<String, Object> properties`.

    The name is the last identifier and the type is what precedes it once the
    annotations, a `final` and varargs are gone. A declaration with no name — valid
    nowhere, possible in text a scanner is reading — yields `None` rather than a
    parameter whose type is its own name.
    """
    text = raw.strip()
    if not text:
        return None
    annotations = _leading_annotations(text)
    rest = text[_annotations_length(text) :].strip()
    if rest.startswith("final "):
        rest = rest[len("final ") :].strip()
    varargs = ""
    if rest.endswith("..."):
        rest = rest[:-3].strip()
        varargs = "..."
    match = re.search(rf"({_IDENT})(\s*\[\])*\s*$", rest)
    if match is None:
        return None
    name = match.group(1)
    type_text = rest[: match.start()].strip()
    if not type_text:
        return None
    return Parameter(name=name, type=f"{type_text}{varargs}", annotations=annotations)


def type_head(declaration: str) -> str:
    """The erasure of a type: `Map<String, Object>` -> `Map`, `Foo[]` -> `Foo[]`."""
    text = declaration.strip()
    while "<" in text and ">" in text[text.index("<") :]:
        start = text.index("<")
        depth = 0
        end = start
        for index in range(start, len(text)):
            if text[index] == "<":
                depth += 1
            elif text[index] == ">":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        text = text[:start] + text[end + 1 :]
    return text.strip()


def generic_arguments(declaration: str) -> tuple[str, ...]:
    """The type arguments of a generic type, or `()` for a non-generic one."""
    text = declaration.strip()
    start = text.find("<")
    if start == -1:
        return ()
    end = text.rfind(">")
    if end < start:
        return ()
    return tuple(split_arguments(text[start + 1 : end]))


def simple_name(qualified: str) -> str:
    """`com.example.Foo` -> `Foo`, because annotations are matched by their last name."""
    return qualified.rsplit(".", 1)[-1].strip()


def parse_type_arguments(text: str, open_index: int) -> tuple[str, ...]:
    """The arguments inside a `( ... )`, as raw declaration texts."""
    close = find_matching(text, open_index)
    return tuple(split_arguments(text[open_index + 1 : close]))


def _scan(text: str) -> list[Member]:
    """Every member of an isolated region: a file's top level, or a type body.

    A member is closed by the `{` of its body or the `;` that ends it, and the head
    is everything since the previous close — so the walk never has to know how many
    braces deep it is. Method bodies are jumped over rather than scanned, which is
    what keeps this linear in the file instead of quadratic.

    A `{` whose head is not a declaration is an initializer block or something else
    the reader has no use for; it is skipped whole, and the scan resumes after it.
    """
    found: list[Member] = []
    index = 0
    head_start = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            index = _skip_string(text, index)
            continue
        if char == "'":
            index = _skip_char_literal(text, index)
            continue
        if char in "([":
            index = find_matching(text, index) + 1
            continue
        if char == "{":
            head = text[head_start:index]
            member = _parse_head(head)
            if member is None:
                index = find_matching(text, index) + 1
                head_start = index
                continue
            close = find_matching(text, index)
            found.append(
                Member(
                    name=member.name,
                    kind=member.kind,
                    annotations=member.annotations,
                    return_type=member.return_type,
                    parameters=member.parameters,
                    body=text[index + 1 : close],
                    type_kind=member.type_kind,
                    components=member.components,
                    is_constructor=member.is_constructor,
                    modifiers=member.modifiers,
                )
            )
            index = close + 1
            head_start = index
            continue
        if char == ";":
            member = _parse_field(text[head_start:index])
            if member is not None:
                found.append(member)
            index += 1
            head_start = index
            continue
        index += 1
    return found


def _parse_head(head: str) -> Member | None:
    """A declaration head, if it is one: a type, a method, a constructor."""
    stripped = _strip_annotations_and_modifiers(head)
    if not stripped:
        return None
    annotations = _leading_annotations(head)
    modifiers = tuple(match.group(1) for match in _MODIFIER_RE.finditer(head))
    type_match = _TYPE_DECL.search(stripped)
    if type_match is not None:
        kind, name = type_match.group(1), type_match.group(2)
        components: tuple[Parameter, ...] = ()
        if kind == "record":
            open_index = stripped.find("(", type_match.end())
            if open_index != -1:
                components = tuple(
                    parsed
                    for parsed in (
                        parse_parameter(raw)
                        for raw in parse_type_arguments(stripped, open_index)
                    )
                    if parsed is not None
                )
        return Member(
            name=name,
            kind="type",
            annotations=annotations,
            type_kind=kind,
            components=components,
            modifiers=modifiers,
        )
    open_index = stripped.find("(")
    if open_index == -1:
        # An identifier alone before a `{` is a record's *compact constructor*, the
        # one declaration whose body can reject a value before bean validation sees
        # it. It is kept because the reader reports what it throws.
        if re.fullmatch(_DOTTED, stripped):
            return Member(
                name=stripped,
                kind="method",
                annotations=annotations,
                is_constructor=True,
                modifiers=modifiers,
            )
        return None
    name = _identifier_ending_at(stripped, open_index - 1)
    if name is None:
        return None
    return_type = stripped[: open_index - len(name)].strip()
    if return_type.endswith(".") or "=" in return_type:
        return None
    parameters = tuple(
        parsed
        for parsed in (
            parse_parameter(raw) for raw in parse_type_arguments(stripped, open_index)
        )
        if parsed is not None
    )
    return Member(
        name=name,
        kind="method",
        annotations=annotations,
        return_type=return_type or None,
        parameters=parameters,
        is_constructor=not return_type,
        modifiers=modifiers,
    )


def _parse_field(head: str) -> Member | None:
    """A `;`-terminated head: a field, an import, a package or an enum constant.

    The initializer is kept in `body`, which is where a reader that wants the values
    a constant holds has to look: the PII denylist of the reference corpus is a
    `private static final Set<String> DENIED_KEYS = Set.of("cpf", …)`, and a field
    read without its right-hand side is a field whose values no one can see.
    """
    text = head.strip()
    if not text or text.startswith(("import", "package", "return", "throw")):
        return None
    annotations = _leading_annotations(text)
    declaration = text[_annotations_length(text) :].strip()
    stripped = _MODIFIER_RE.sub(" ", declaration).strip()
    if "=" not in stripped:
        return None
    before, _, value = stripped.partition("=")
    name = before.strip().split()[-1].strip()
    if not re.fullmatch(_IDENT, name):
        return None
    type_text = before.strip()[: -len(name)].strip()
    return Member(
        name=name,
        kind="field",
        annotations=annotations,
        return_type=type_text or None,
        body=value.strip(),
    )


def _strip_annotations_and_modifiers(head: str) -> str:
    """The declaration itself, with its decorators gone."""
    stripped = head[_annotations_length(head) :]
    return _MODIFIER_RE.sub(" ", stripped).strip()


def _leading_annotations(text: str) -> tuple[Annotation, ...]:
    """Annotations at the start of a head, in source order."""
    found: list[Annotation] = []
    index = 0
    while True:
        index = _skip_space(text, index)
        match = _ANNOTATION_HEAD.match(text, index)
        if match is not None:
            open_index = match.end() - 1
            try:
                close = find_matching(text, open_index)
            except ValueError:
                return tuple(found)
            found.append(
                Annotation(
                    name=simple_name(match.group(1)),
                    arguments=text[open_index + 1 : close],
                )
            )
            index = close + 1
            continue
        bare = _ANNOTATION_BARE.match(text, index)
        if bare is not None:
            found.append(Annotation(name=simple_name(bare.group(1))))
            index = bare.end()
            continue
        return tuple(found)


def _annotations_length(text: str) -> int:
    """Where the annotations end, so the declaration after them can be read."""
    index = 0
    while True:
        index = _skip_space(text, index)
        match = _ANNOTATION_HEAD.match(text, index)
        if match is not None:
            try:
                index = find_matching(text, match.end() - 1) + 1
            except ValueError:
                return index
            continue
        bare = _ANNOTATION_BARE.match(text, index)
        if bare is not None:
            index = bare.end()
            continue
        return index


def _as_type(member: Member, package: str, outer: tuple[str, ...]) -> JavaType:
    names = (*outer, member.name)
    return JavaType(
        name=member.name,
        kind=member.type_kind or "class",
        package=package,
        annotations=member.annotations,
        components=member.components,
        members=tuple(_scan(member.body)),
        body=member.body,
        fqn=".".join(part for part in (package, *names) if part),
        modifiers=member.modifiers,
    )


def _flatten(declared: JavaType, outer: tuple[str, ...]) -> tuple[JavaType, ...]:
    """The types declared inside `declared`, each with the names it sits under."""
    found: list[JavaType] = []
    names = (*outer, declared.name)
    for member in declared.nested():
        inner = _as_type(member, declared.package, names)
        found.append(inner)
        found.extend(_flatten(inner, names))
    return tuple(found)


def _package_of(text: str) -> str:
    found = re.search(rf"^\s*package\s+({_DOTTED})\s*;", text, re.M)
    return found.group(1) if found else ""


def _named_argument(arguments: str | None, key: str) -> str | None:
    if not arguments:
        return None
    for part in split_arguments(arguments):
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        if name.strip() == key:
            return value.strip()
    return None


def _first_string(text: str) -> str | None:
    found = _STRING.search(text)
    return None if found is None else unquote(found.group(0))


def unquote(literal: str) -> str:
    """The value of a Java string literal, escapes resolved."""
    inner = literal[1:-1]
    out: list[str] = []
    index = 0
    while index < len(inner):
        char = inner[index]
        if char == "\\" and index + 1 < len(inner):
            nxt = inner[index + 1]
            out.append(
                {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}.get(
                    nxt, nxt
                )
            )
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _skip_string(text: str, index: int) -> int:
    """Skip a string literal, a `\"\"\"` text block included."""
    if text.startswith('"""', index):
        end = text.find('"""', index + 3)
        return len(text) if end == -1 else end + 3
    position = index + 1
    while position < len(text):
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == '"':
            return position + 1
        if char == "\n":
            # An unterminated literal: stop at the line so the rest still reads.
            return position
        position += 1
    return position


def _skip_char_literal(text: str, index: int) -> int:
    position = index + 1
    while position < len(text):
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == "'":
            return position + 1
        if char == "\n":
            return position
        position += 1
    return position


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _identifier_ending_at(text: str, end: int) -> str | None:
    """The identifier whose last character is at `end`, or `None`."""
    if end < 0:
        return None
    found = re.search(rf"{_IDENT}$", text[: end + 1])
    return found.group(0) if found else None
