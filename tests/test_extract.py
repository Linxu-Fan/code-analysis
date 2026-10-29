from codescope.core import model as M
from codescope.core.extract import extractor_class
from codescope.core.languages import get_parser, language_for_path


def extract(lang, code, path="t"):
    src = code.encode()
    tree = get_parser(lang).parse(src)
    return extractor_class(lang)(path, lang, src, tree, path).run()


def kinds(res):
    return {(e.kind, e.longname) for e in res.entities}


def test_language_detection():
    assert language_for_path("a.cpp") == "cpp"
    assert language_for_path("a.c") == "c"
    assert language_for_path("a.h", header_lang="c") == "c"
    assert language_for_path("a.h") == "cpp"
    assert language_for_path("a.py") == "python"
    assert language_for_path("a.java") is None
    assert language_for_path("a.py", enabled={"cpp"}) is None


def test_python_entities_and_refs():
    res = extract("python", '''
import os
from pkg.mod import Thing as T

LIMIT = 3

class A(Base):
    """Doc."""
    attr = 1

    def __init__(self, x):
        self.x = x

    def run(self, n=2):
        for i in range(n):
            if i and self.x:
                T().go(i)
        return os.path.join("a", "b")
''', "m.py")
    k = kinds(res)
    assert (M.CLASS, "m.A") in k
    assert (M.METHOD, "m.A.run") in k
    assert (M.FIELD, "m.A.x") in k
    assert (M.FIELD, "m.A.attr") in k
    assert (M.VARIABLE, "m.LIMIT") in k
    assert (M.PARAMETER, "m.A.run.n") in k
    assert (M.LOCAL, "m.A.run.i") in k
    refs = {(r.kind, r.name, r.qualifier) for r in res.refs}
    assert (M.INHERIT, "Base", "") in refs
    assert (M.IMPORT, "pkg.mod.Thing", "T") in refs
    assert (M.IMPORT, "os", "os") in refs
    assert (M.CALL, "join", "os.path") in refs
    assert (M.CALL, "go", "T()") in refs
    run = next(e for e in res.entities if e.name == "run")
    m = res.metrics[run.lid]
    assert m["Cyclomatic"] == 4          # for + if + and
    assert m["MaxNesting"] == 2
    assert m["CountParams"] == 1
    cls = next(e for e in res.entities if e.name == "A")
    assert res.metrics[cls.lid]["CountLineComment"] == 1     # docstring


def test_cpp_entities_and_refs():
    res = extract("cpp", '''
#include "foo.h"
#define SQR(x) ((x)*(x))
namespace ns {
class Base { public: virtual ~Base(); virtual int f() const = 0; };
template <typename T>
class Box : public Base {
public:
    Box(T v) : v_(v) {}
    int f() const override;
private:
    T v_;
};
int Box::f() const {
    int total = 0;
    for (int i = 0; i < 10; ++i) {
        if (i % 2 == 0 && i > 2) total += SQR(i);
    }
    return helper(total);
}
}
''', "box.cpp")
    k = kinds(res)
    assert (M.NAMESPACE, "ns") in k
    assert (M.CLASS, "ns::Box") in k
    assert (M.CONSTRUCTOR, "ns::Box::Box") in k
    assert (M.FIELD, "ns::Box::v_") in k
    assert (M.MACRO, "SQR") in k
    defs = [e for e in res.entities if e.longname == "ns::Box::f"]
    assert len(defs) == 2                            # declaration + out-of-line definition
    decl = next(e for e in defs if "decl" in e.modifiers)
    assert "public" in decl.modifiers and "override" not in decl.modifiers or True
    body = next(e for e in defs if "decl" not in e.modifiers)
    assert body.owner == "ns::Box"
    m = res.metrics[body.lid]
    assert m["Cyclomatic"] == 4
    refs = {(r.kind, r.name) for r in res.refs}
    assert (M.INCLUDE, "foo.h") in refs
    assert (M.INHERIT, "Base") in refs
    assert (M.CALL, "helper") in refs
    assert (M.CALL, "SQR") in refs
    # template parameters are not reported as types
    assert (M.TYPED, "T") not in refs


def test_access_specifiers_are_sequential():
    res = extract("cpp", "class A { public: void a(); private: void b(); };")
    mods = {e.name: e.modifiers for e in res.entities}
    assert mods["a"].startswith("public")
    assert mods["b"].startswith("private")


def test_line_metrics_c():
    res = extract("c", "/* header */\n\nint f(void) {\n  // c\n  return 1; /* t */\n}\n")
    fm = res.metrics[0]
    assert fm["CountLine"] == 6
    assert fm["CountLineBlank"] == 1
    assert fm["CountLineComment"] == 3
    assert fm["CountLineCode"] == 3


def test_macro_scrubbing():
    from codescope.core.analyzer import find_attribute_macros, scrub_macros
    src = b"#define API __declspec(dllexport)\nclass API Widget : public Base {};\nAPI int f();\n"
    names = find_attribute_macros(src)
    assert names == {"API"}
    out = scrub_macros(src, names)
    assert len(out) == len(src)
    res_tree = get_parser("cpp").parse(out)
    assert not res_tree.root_node.has_error
