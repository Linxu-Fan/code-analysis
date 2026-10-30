import pytest

from codescope.core import assistant as A
from tests.ai_mock import MockLLM


def test_normalize_url():
    assert A.normalize_url("localhost:8080") == "http://localhost:8080/v1"
    assert A.normalize_url("http://127.0.0.1:1234/v1/") == "http://127.0.0.1:1234/v1"
    assert A.normalize_url("http://h:11434/v1/chat/completions") == "http://h:11434/v1"


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CODESCOPE_CONFIG_DIR", str(tmp_path))
    s = A.load_settings()
    assert s["url"] == A.DEFAULT_URL
    s["model"] = "qwen"
    A.save_settings(s)
    assert A.load_settings()["model"] == "qwen"
    monkeypatch.setenv("CODESCOPE_AI_URL", "http://other:9/v1")
    assert A.load_settings()["url"] == "http://other:9/v1"


def test_project_tools(cpp_db):
    t = A.ProjectTools(cpp_db)
    assert "geo::Rect" in t.search("Rect", kind="class")
    assert "classify" in t.search("*lassif*")
    d = t.describe("geo::Rect::area")
    assert "Method" in d and "shape.cpp" in d
    code = t.read_code("geo::Rect::area")
    assert "return w_ * h_;" in code and "| " in code
    assert "#include" in t.read_code(file="shape.cpp", start=1, end=3)
    assert "← Shape" in t.class_tree("Rect", "inherit")
    assert "+ area()" in t.class_tree("geo::Rect", "members")
    assert "Callees" in t.call_tree("main")
    assert "sets w_, h_" in t.modification_tree("geo::Rect")
    assert "shape.h:" in t.grep("class Rect")
    assert "src/main.cpp" in t.list_files("src")
    assert "Cyclomatic" in t.top_metrics("nope") and t.top_metrics("Cyclomatic")
    assert t.run("describe", {"name": "NoSuchThing"}).startswith("Error: No entity")
    assert t.run("bogus", {}) == "Unknown tool 'bogus'."
    assert "Project" in t.overview()


def test_think_splitter():
    content, reasoning = [], []
    sp = A.ThinkSplitter(content.append, reasoning.append)
    for piece in ("Hi <thi", "nk>pondering", "</th", "ink>Answer"):
        sp.feed(piece)
    sp.flush()
    assert "".join(content) == "Hi Answer" and "".join(reasoning) == "pondering"


@pytest.mark.parametrize("stream", [True, False])
def test_assistant_tool_loop(cpp_db, stream):
    script = [
        {"reasoning": "I should look up Rect.", "tool_calls": [("search", {"query": "Rect", "kind": "class"})]},
        {"tool_calls": [("read_code", {"name": "geo::Rect::area"})]},
        {"content": "<think>done</think>`Rect::area` returns `w_ * h_` (src/shape.cpp:33)."},
    ]
    with MockLLM(script) as llm:
        asst = A.Assistant(cpp_db, {"url": llm.url, "stream": stream})
        events = []
        answer = asst.ask("What does Rect::area return?", lambda k, d: events.append((k, d)))
        assert answer == "`Rect::area` returns `w_ * h_` (src/shape.cpp:33)."
        tools = [d[0] for k, d in events if k == "tool"]
        assert tools == ["search", "read_code"]
        results = [d[1] for k, d in events if k == "tool_result"]
        assert "geo::Rect" in results[0] and "return w_ * h_;" in results[1]
        assert "".join(d for k, d in events if k == "reasoning").startswith("I should look up Rect.")
        # the model received the tool definitions, the system prompt and the tool results
        last = llm.requests[-1]
        assert last["tools"] and last["messages"][0]["role"] == "system"
        assert [m["role"] for m in last["messages"][1:]] == ["user", "assistant", "tool", "assistant", "tool"]
        assert last["messages"][2]["tool_calls"][0]["function"]["name"] == "search"
        # a follow-up question keeps the conversation
        llm.script.append({"content": "Yes."})
        assert asst.ask("Is it const?") == "Yes."
        assert len(llm.requests[-1]["messages"]) == 8
        assert A.LLMClient(llm.url).models() == ["qwen-test"]


def test_assistant_text_tool_calls(cpp_db):
    # servers that do not parse Qwen's tool call syntax hand it over as text
    script = [{"content": 'Let me check.<tool_call>\n{"name": "describe", "arguments": {"name": "geo::Rect"}}\n'
                          '</tool_call>'},
              {"content": "Rect derives from Shape."}]
    with MockLLM(script) as llm:
        asst = A.Assistant(cpp_db, {"url": llm.url})
        events = []
        assert asst.ask("Base of Rect?", lambda k, d: events.append((k, d))) == "Rect derives from Shape."
        assert ("tool", ("describe", {"name": "geo::Rect"})) in events


def test_assistant_without_tool_support(cpp_db):
    with MockLLM([{"content": "Rect::area multiplies w_ and h_."}], reject_tools=True) as llm:
        asst = A.Assistant(cpp_db, {"url": llm.url})
        events = []
        assert asst.ask("What does Rect::area do?", lambda k, d: events.append((k, d))).startswith("Rect::area")
        assert any(k == "notice" and "--jinja" in d for k, d in events)
        sent = llm.requests[-1]
        assert "tools" not in sent and "return w_ * h_;" in sent["messages"][-1]["content"]


def test_assistant_errors(cpp_db):
    asst = A.Assistant(cpp_db, {"url": "http://127.0.0.1:9/v1", "timeout": 5})
    with pytest.raises(A.LLMError, match="Cannot reach"):
        asst.ask("hello")
    assert asst.history == []
    with MockLLM([]) as llm:
        asst = A.Assistant(cpp_db, {"url": llm.url, "max_rounds": 2})
        llm.script += [{"tool_calls": [("search", {"query": "x"})]}] * 2 + [{"content": "Giving up."}]
        assert asst.ask("loop forever") == "Giving up."
        assert "tools" not in llm.requests[-1]        # last round must answer without tools


def test_cli_ask(cpp_db, capsys, monkeypatch, tmp_path):
    from codescope.cli import main
    monkeypatch.setenv("CODESCOPE_CONFIG_DIR", str(tmp_path))
    path = cpp_db.path
    cpp_db.close()
    with MockLLM([{"tool_calls": [("describe", {"name": "geo::Rect"})]}, {"content": "Rect is a shape."}]) as llm:
        assert main(["ask", path, "--url", llm.url, "--save"]) in (0, None)
        assert "qwen-test" in capsys.readouterr().out
        assert main(["ask", path, "What is Rect?"]) in (0, None)      # uses the saved URL
        out, err = capsys.readouterr()
        assert "Rect is a shape." in out and "[describe] name='geo::Rect'" in err


def test_assistant_cancel(cpp_db):
    import threading
    import time
    from codescope.core.database import Database
    with MockLLM([{"content": "word " * 400}], delay=0.02) as llm:
        asst = A.Assistant(cpp_db, {"url": llm.url})
        asst.history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}]
        got, errors = [], []

        def run():
            # like the GUI worker: the thread uses its own SQLite connection
            asst.db = asst.tools.db = Database(cpp_db.path)
            try:
                asst.ask("long answer please", lambda k, d: got.append(d) if k == "content" else None)
            except A.Cancelled:
                errors.append("cancelled")
            finally:
                asst.db.close()
        t = threading.Thread(target=run)
        t.start()
        deadline = time.time() + 10
        while not got and t.is_alive() and time.time() < deadline:
            time.sleep(0.01)
        assert got, "no streamed text arrived"
        start = time.time()
        asst.cancel()
        t.join(5)
        assert not t.is_alive() and errors == ["cancelled"] and time.time() - start < 2
        assert len(asst.history) == 2                # the cancelled question left no trace
