"""Core data model: entity kinds, reference kinds and extraction results."""

from __future__ import annotations

from dataclasses import dataclass, field

# ---- entity kinds -----------------------------------------------------------
FILE = "File"
PACKAGE = "Package"
NAMESPACE = "Namespace"
MODULE = "Module"
CLASS = "Class"
STRUCT = "Struct"
UNION = "Union"
INTERFACE = "Interface"
ENUM = "Enum"
ENUMERATOR = "Enumerator"
TYPEDEF = "Typedef"
MACRO = "Macro"
FUNCTION = "Function"
METHOD = "Method"
CONSTRUCTOR = "Constructor"
PROPERTY = "Property"
FIELD = "Field"
VARIABLE = "Variable"
PARAMETER = "Parameter"
LOCAL = "Local"
UNRESOLVED_FUNCTION = "Unresolved Function"
UNRESOLVED_TYPE = "Unresolved Type"
UNRESOLVED_MODULE = "Unresolved Module"
UNRESOLVED_INCLUDE = "Unresolved Include"

CLASS_KINDS = frozenset({CLASS, STRUCT, UNION, INTERFACE, ENUM})
TYPE_KINDS = CLASS_KINDS | {TYPEDEF}
FUNCTION_KINDS = frozenset({FUNCTION, METHOD, CONSTRUCTOR})
VARIABLE_KINDS = frozenset({VARIABLE, FIELD, PARAMETER, LOCAL, PROPERTY, ENUMERATOR})
SCOPE_KINDS = frozenset({PACKAGE, NAMESPACE, MODULE})
LOCAL_KINDS = frozenset({PARAMETER, LOCAL})
UNRESOLVED_KINDS = frozenset({UNRESOLVED_FUNCTION, UNRESOLVED_TYPE, UNRESOLVED_MODULE, UNRESOLVED_INCLUDE})

ALL_KINDS = [FILE, PACKAGE, NAMESPACE, MODULE, CLASS, STRUCT, UNION, INTERFACE, ENUM,
             ENUMERATOR, TYPEDEF, MACRO, FUNCTION, METHOD, CONSTRUCTOR, PROPERTY, FIELD,
             VARIABLE, PARAMETER, LOCAL, UNRESOLVED_FUNCTION, UNRESOLVED_TYPE,
             UNRESOLVED_MODULE, UNRESOLVED_INCLUDE]

# ---- reference kinds --------------------------------------------------------
# Wrapper templates whose instances hold objects of their template argument.
CONTAINER_TYPES = frozenset({
    "vector", "array", "list", "deque", "forward_list", "set", "multiset", "map", "multimap", "unordered_map",
    "unordered_set", "unordered_multimap", "span", "small_vector", "SmallVector", "QList", "QVector", "Array",
    "List", "Dict", "Set", "Sequence", "Iterable", "list_", "dict", "set_", "tuple", "Tuple"})
POINTER_TYPES = frozenset({"shared_ptr", "unique_ptr", "weak_ptr", "observer_ptr", "QPointer", "QSharedPointer",
                           "intrusive_ptr", "ref_ptr", "Ref"})

CALL = "Call"
USE = "Use"
SET = "Set"
INHERIT = "Inherit"
IMPLEMENT = "Implement"
TYPED = "Typed"
INCLUDE = "Include"
IMPORT = "Import"
CREATE = "Create"      # object instantiation (new Foo / Foo())
OBJECT = "Object"      # object defined by a declaration (Foo x; Foo x = y;), Foo{} or make_unique<Foo>()

REF_KINDS = [CALL, USE, SET, INHERIT, IMPLEMENT, TYPED, INCLUDE, IMPORT, CREATE, OBJECT]

# Facts recorded for the linker only.  These references never get a target: the linker turns
# them into Set references once it knows the called function (see Linker._resolve_writes).
OUT_ARG = "OutArg"         # a name passed as a call argument; detail: index, count, r/p, callee, qualifier
EXPOSE = "Expose"          # a name returned by a function that returns a non-const reference
WRITE_CALL = "WriteCall"   # f(x) = value: writes through the reference f returns; detail: argument count
LINKER_REF_KINDS = (OUT_ARG, EXPOSE, WRITE_CALL)
VIA_PREFIX = "via\t"       # detail of a Set made by the linker: followed by the name of the called function
# Detail part of a write made through a local alias (auto& d = j.damping; d = 1;): the alias name, then
# how it was written: "" the alias itself, "part" a member or element of it, or the name of a mutating call.
# Such a reference is stored at the position of the alias, so its name does not match the source text there.
ALIAS_PREFIX = "alias\t"
ALIAS_LIKE = "%" + ALIAS_PREFIX + "%"

OUT_TOKEN = "out="         # modifier of a function: its writable parameters, e.g. out=r3,p5


def out_token(params) -> str:
    """Modifier text for writable parameters such as ['r3', 'p5'] ('' when there are none)."""
    return OUT_TOKEN + ",".join(params) if params else ""


def out_params(modifiers) -> frozenset:
    """Writable parameters stored in a modifiers string: {'r3', 'p5'}."""
    for m in (modifiers or "").split():
        if m.startswith(OUT_TOKEN):
            return frozenset(m[len(OUT_TOKEN):].split(","))
    return frozenset()


def shown_modifiers(modifiers) -> str:
    """Modifiers for display: without the internal out= token."""
    return " ".join(m for m in (modifiers or "").split() if not m.startswith(OUT_TOKEN))

# Which entity kinds a reference of a given kind may legally target.
TARGETS = {
    CALL: FUNCTION_KINDS | {MACRO, CLASS, STRUCT},
    CREATE: CLASS_KINDS | {CONSTRUCTOR},
    OBJECT: CLASS_KINDS | {TYPEDEF},
    USE: VARIABLE_KINDS | FUNCTION_KINDS | {MACRO} | CLASS_KINDS | {TYPEDEF} | SCOPE_KINDS,
    SET: VARIABLE_KINDS,
    INHERIT: CLASS_KINDS,
    IMPLEMENT: CLASS_KINDS,
    TYPED: TYPE_KINDS | {MACRO},
    INCLUDE: frozenset({FILE}),
    IMPORT: frozenset({FILE, MODULE, PACKAGE, NAMESPACE}) | CLASS_KINDS | FUNCTION_KINDS | {VARIABLE, TYPEDEF},
}

# Unresolved placeholder kind created for an unresolved reference (None = drop).
UNRESOLVED_FOR = {
    CALL: UNRESOLVED_FUNCTION,
    CREATE: UNRESOLVED_TYPE,
    INHERIT: UNRESOLVED_TYPE,
    IMPLEMENT: UNRESOLVED_TYPE,
    TYPED: UNRESOLVED_TYPE,
    INCLUDE: UNRESOLVED_INCLUDE,
    IMPORT: UNRESOLVED_MODULE,
}

# Human readable inverse names used by the information browser.
REF_LABELS = {
    CALL: ("Calls", "Called By"),
    CREATE: ("Creates", "Created By"),
    OBJECT: ("Defines Objects Of", "Objects Defined By"),
    USE: ("Uses", "Used By"),
    SET: ("Sets", "Set By"),
    INHERIT: ("Base Classes", "Derived Classes"),
    IMPLEMENT: ("Implements", "Implemented By"),
    TYPED: ("Types Used", "Typed By"),
    INCLUDE: ("Includes", "Included By"),
    IMPORT: ("Imports", "Imported By"),
}


@dataclass
class XEntity:
    """An entity found while extracting a single file (ids are file-local)."""
    lid: int
    kind: str
    name: str
    longname: str
    parent: int | None
    line: int
    col: int
    end_line: int
    end_col: int
    signature: str = ""
    modifiers: str = ""
    type: str = ""
    owner: str = ""        # qualified owner for out-of-line definitions (C++ Foo::bar)


@dataclass
class XRef:
    kind: str
    src: int               # file-local entity id of the referencing scope
    name: str
    qualifier: str
    line: int
    col: int
    detail: str = ""       # extra data; its meaning depends on the kind (see the kinds above)


@dataclass
class FileResult:
    path: str
    lang: str
    entities: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)   # lid -> {metric: value}
    error: str = ""
    parse_errors: int = 0
