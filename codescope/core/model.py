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

REF_KINDS = [CALL, USE, SET, INHERIT, IMPLEMENT, TYPED, INCLUDE, IMPORT, CREATE]

# Which entity kinds a reference of a given kind may legally target.
TARGETS = {
    CALL: FUNCTION_KINDS | {MACRO, CLASS, STRUCT},
    CREATE: CLASS_KINDS | {CONSTRUCTOR},
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


@dataclass
class FileResult:
    path: str
    lang: str
    entities: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)   # lid -> {metric: value}
    error: str = ""
    parse_errors: int = 0
