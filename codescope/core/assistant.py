"""AI assistant: answers questions about the project with a local LLM.

The model runs in a separate, OpenAI-compatible server (llama.cpp ``llama-server``, LM Studio,
Ollama, ...).  CodeScope sends the conversation plus a set of *tools* that look things up in the
project database (entities, source code, call/class/modification trees, text search), so the
model reads the real code instead of guessing.  Only the standard library is used.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import socket
import urllib.error
import urllib.request

from . import model as M

DEFAULT_URL = "http://127.0.0.1:8080/v1"
DEFAULT_SETTINGS = {
    "url": DEFAULT_URL,
    "model": "",              # empty: let the server use the model it has loaded
    "api_key": "",
    "temperature": 0.6,
    "max_rounds": 12,         # tool calling rounds per question
    "stream": True,
    "tools": True,
    "timeout": 900,
    "model_file": "",         # GGUF file chosen in the setup dialog
    "server_exe": "",         # llama-server used by the start script
}
TOOL_OUTPUT_LIMIT = 8000      # characters of one tool result sent back to the model
HISTORY_TOOL_LIMIT = 1500     # older tool results are shortened to this when the history grows
HISTORY_BUDGET = 90000        # characters of conversation kept before older tool results are shortened


# ---- settings -----------------------------------------------------------------------------------

def config_dir():
    d = os.environ.get("CODESCOPE_CONFIG_DIR")
    if d:
        return d
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "CodeScope")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "CodeScope")


def settings_path():
    return os.path.join(config_dir(), "ai.json")


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            s.update({k: v for k, v in json.load(fh).items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    if os.environ.get("CODESCOPE_AI_URL"):
        s["url"] = os.environ["CODESCOPE_AI_URL"]
    if os.environ.get("CODESCOPE_AI_MODEL"):
        s["model"] = os.environ["CODESCOPE_AI_MODEL"]
    return s


def save_settings(s):
    os.makedirs(config_dir(), exist_ok=True)
    with open(settings_path(), "w", encoding="utf-8") as fh:
        json.dump({k: s.get(k, v) for k, v in DEFAULT_SETTINGS.items()}, fh, indent=2)


# ---- HTTP client --------------------------------------------------------------------------------

class LLMError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

    @property
    def tools_unsupported(self):
        text = str(self).lower()
        return self.status in (400, 404, 422, 500, 501) and ("tool" in text or "jinja" in text)


class Cancelled(Exception):
    pass


def normalize_url(url):
    """'localhost:8080' -> 'http://localhost:8080/v1' (the OpenAI-compatible API root)."""
    url = (url or DEFAULT_URL).strip().rstrip("/")
    if "://" not in url:
        url = "http://" + url
    for suffix in ("/chat/completions", "/models"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    if not re.search(r"/v\d+$", url):
        url += "/v1"
    return url


class LLMClient:
    """Minimal OpenAI-compatible chat client (streaming, tool calls, reasoning)."""

    def __init__(self, url=DEFAULT_URL, model="", api_key="", timeout=900):
        self.url = normalize_url(url)
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._active = None          # the response being read, so abort() can close it

    def abort(self):
        """Cut the connection of a running request (from another thread).

        Shutting the socket down wakes up the thread blocked reading it and tells the server to stop
        generating; closing the response object instead would wait for that thread's read to finish.
        """
        r = self._active
        sock = getattr(getattr(getattr(r, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _request(self, path, body=None, timeout=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method="POST" if data else "GET")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        # local servers must not go through a system proxy
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            return opener.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                err = json.loads(detail).get("error", detail)
                detail = err.get("message", err) if isinstance(err, dict) else err
            except ValueError:
                pass
            raise LLMError(f"HTTP {exc.code}: {str(detail)[:600]}", exc.code) from None
        except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            raise LLMError(f"Cannot reach the AI server at {self.url} ({reason}). "
                           "Is the server running? See docs/AI_SETUP.md.") from None

    def models(self, timeout=10):
        with self._request("/models", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("id", "") for m in data.get("data", data.get("models", [])) if isinstance(m, dict)]

    def chat(self, messages, tools=None, temperature=None, stream=True, on_content=None, on_reasoning=None,
             cancelled=lambda: False):
        """One chat completion.  Returns {"content", "reasoning", "tool_calls"}."""
        body = {"messages": messages, "stream": bool(stream)}
        if self.model:
            body["model"] = self.model
        if tools:
            body["tools"] = tools
        if temperature is not None:
            body["temperature"] = temperature
        out = {"content": "", "reasoning": "", "tool_calls": []}
        try:
            return self._read_chat(self._request("/chat/completions", body), stream, out, on_content,
                                   on_reasoning, cancelled)
        except (OSError, ValueError, AttributeError) as exc:
            if cancelled():
                raise Cancelled() from None
            if isinstance(exc, ValueError):
                raise LLMError("The AI server sent a reply that is not valid JSON.") from None
            raise LLMError("The connection to the AI server was lost.") from None
        finally:
            self._active = None

    def _read_chat(self, response, stream, out, on_content, on_reasoning, cancelled):
        self._active = response
        if cancelled():
            raise Cancelled()
        with response as r:
            if not stream:
                data = json.loads(r.read().decode("utf-8"))
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                out["content"] = msg.get("content") or ""
                out["reasoning"] = msg.get("reasoning_content") or msg.get("reasoning") or ""
                out["tool_calls"] = msg.get("tool_calls") or []
                if on_reasoning and out["reasoning"]:
                    on_reasoning(out["reasoning"])
                if on_content and out["content"]:
                    on_content(out["content"])
                return out
            calls = {}
            for raw in r:
                if cancelled():
                    raise Cancelled()
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if "error" in chunk:
                    err = chunk["error"]
                    raise LLMError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = delta.get("reasoning_content") or delta.get("reasoning")
                    if text:
                        out["reasoning"] += text
                        if on_reasoning:
                            on_reasoning(text)
                    text = delta.get("content")
                    if text:
                        out["content"] += text
                        if on_content:
                            on_content(text)
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", len(calls)),
                                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
            out["tool_calls"] = [calls[k] for k in sorted(calls)]
        if cancelled():                 # the stream ended because Stop cut the connection
            raise Cancelled()
        return out


# ---- project tools ------------------------------------------------------------------------------

def _tool(name, description, props=None, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props or {}, "required": list(required)}}}


_NAME = {"type": "string", "description": "Entity name or qualified name, e.g. 'step' or 'ns::World::step'"}

TOOLS = [
    _tool("search", "Find classes, functions, fields, macros, files ... by name. Use '*' as a wildcard. "
          "Returns kind, qualified name and location of each match.",
          {"query": {"type": "string", "description": "Name or pattern, e.g. 'mass', '*Solver*'"},
           "kind": {"type": "string", "enum": ["any", "class", "function", "variable", "macro", "file", "typedef",
                                               "namespace"]}},
          ["query"]),
    _tool("describe", "Everything known about one entity: kind, signature, location, members, what it calls / "
          "is called by, uses / is used by, sets / is set by, base and derived classes, metrics.",
          {"name": _NAME}, ["name"]),
    _tool("read_code", "Source code with line numbers: of an entity (by name), or of a file line range.",
          {"name": _NAME, "file": {"type": "string", "description": "File path (or its end, e.g. 'world.hpp')"},
           "start": {"type": "integer"}, "end": {"type": "integer"}}),
    _tool("call_tree", "Call tree of a function: what it calls (down), what calls it (up) or both.",
          {"name": _NAME, "direction": {"type": "string", "enum": ["down", "up", "both"]},
           "depth": {"type": "integer", "description": "1-6, default 3"}}, ["name"]),
    _tool("class_tree", "Structure of a class/struct: inheritance, composition (member types) or every "
          "data and function member.",
          {"name": _NAME, "relation": {"type": "string", "enum": ["both", "inherit", "compose", "members"]},
           "depth": {"type": "integer"}}, ["name"]),
    _tool("modification_tree", "Where and in which order a class/struct, or one of its members, is modified: "
          "call paths from the entry points to every write, numbered in execution order.",
          {"name": {"type": "string", "description": "Class or struct name"},
           "member": {"type": "string", "description": "Optional data member name"}}, ["name"]),
    _tool("grep", "Search the text of all project files. Returns 'file:line: text' lines.",
          {"pattern": {"type": "string"}, "regex": {"type": "boolean", "description": "Pattern is a regex"},
           "files": {"type": "string", "description": "Optional file glob, e.g. '*.cpp'"}}, ["pattern"]),
    _tool("list_files", "Project files with their lines of code, optionally only those under a folder.",
          {"folder": {"type": "string"}}),
    _tool("top_metrics", "Entities ranked by a metric, e.g. the most complex functions "
          "(Cyclomatic, CountLineCode, MaxNesting, CountInput, CountOutput, ...).",
          {"metric": {"type": "string"}, "kind": {"type": "string", "enum": ["function", "class", "file"]},
           "count": {"type": "integer"}}, ["metric"]),
]

KINDS = {
    "class": sorted(M.CLASS_KINDS), "function": sorted(M.FUNCTION_KINDS),
    "variable": [M.VARIABLE, M.FIELD, M.PROPERTY], "macro": [M.MACRO], "file": [M.FILE],
    "typedef": [M.TYPEDEF], "namespace": [M.NAMESPACE, M.PACKAGE, M.MODULE],
}


def _clip(text, limit=TOOL_OUTPUT_LIMIT):
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} more characters cut; narrow the request)"


class ProjectTools:
    """The functions behind TOOLS, answering from the project database."""

    def __init__(self, db):
        self.db = db

    # -- helpers --
    def find(self, name, kinds=None):
        """Best matches for *name*: exact qualified name, then simple name, then name suffix."""
        name = (name or "").strip().rstrip("()")
        if not name:
            return []
        simple = re.split(r"::|\.", name)[-1]
        hits = [e for e in self.db.entities(name=simple, kinds=kinds, include_locals=False) if not e.is_unresolved]
        # C++ names use '::', Python names '.': accept either spelling
        spellings = {name, name.replace("::", "."), name.replace(".", "::")}
        exact = [e for e in hits if e.longname in spellings]
        tail = [e for e in hits if any(e.longname.endswith(sep + n) for n in spellings for sep in ("::", "."))]
        out = exact or tail or ([e for e in hits if e.name == name] if name == simple else [])
        out.sort(key=lambda e: (e.kind == M.FILE, e.file_id is None, len(e.longname)))
        return out

    def _one(self, name, kinds=None, what="entity"):
        hits = self.find(name, kinds)
        if not hits:
            similar = self.search(f"*{(name or '').split('::')[-1]}*", limit=8)
            raise ValueError(f"No {what} named '{name}'." + (f" Similar names:\n{similar}" if "(" in similar else ""))
        note = ""
        if len(hits) > 1:
            note = "Note: several matches, using the first. Others: " + \
                ", ".join(f"{e.longname} ({e.kind})" for e in hits[1:6]) + "\n"
        return hits[0], note

    def _loc(self, e):
        return self.db.location(e) or "(outside the project)"

    def _file(self, path):
        path = (path or "").replace("\\", "/").strip()
        files = self.db.files()
        for f in files:
            if f.relpath.replace("\\", "/") == path or f.path.replace("\\", "/") == path:
                return f
        hits = [f for f in files if f.relpath.replace("\\", "/").endswith("/" + path.lstrip("/"))
                or os.path.basename(f.relpath) == path]
        if not hits:
            raise ValueError(f"No project file matches '{path}'.")
        return hits[0]

    # -- tools --
    def overview(self):
        db = self.db
        cfg = db.config()
        pm = db.project_metrics()
        dirs = {}
        for f in db.files():
            d = os.path.dirname(f.relpath.replace("\\", "/")) or "."
            top = d.split("/")[0] if d != "." else "."
            dirs[top] = dirs.get(top, 0) + 1
        lines = [f"Project '{cfg.get('name', '')}'",
                 "Languages: " + ", ".join(f"{k} ({v} files)" for k, v in (pm.get("Languages") or {}).items()),
                 "Totals: " + ", ".join(f"{k}={v}" for k, v in pm.items() if not isinstance(v, dict)),
                 "Top-level folders: " + ", ".join(f"{d} ({n} files)" for d, n in sorted(dirs.items()))]
        return "\n".join(lines)

    def search(self, query, kind="any", limit=40):
        db = self.db
        q = (query or "").strip()
        if not q:
            return "Empty query."
        kinds = KINDS.get(kind or "any")
        pat = q.lower() if any(c in q for c in "*?[") else f"*{q.lower()}*"
        like = "%" + re.sub(r"[*?\[\]]+", "%", q).strip("%") + "%"
        out = []
        for e in db.entities(kinds=kinds, like=like, include_locals=False):
            if e.is_unresolved:
                continue
            if fnmatch.fnmatchcase(e.longname.lower(), pat) or fnmatch.fnmatchcase(e.name.lower(), pat):
                out.append(e)
        out.sort(key=lambda e: (e.name.lower() != q.lower(), len(e.longname)))
        if not out:
            return f"No entity matches '{q}'."
        lines = [f"{e.kind:<12} {e.longname}  ({self._loc(e)})" for e in out[:limit]]
        if len(out) > limit:
            lines.append(f"... {len(out) - limit} more matches; refine the query")
        return "\n".join(lines)

    def describe(self, name):
        db = self.db
        e, note = self._one(name)
        lines = [note + f"{e.longname}  [{e.kind}]"]
        if e.signature:
            lines.append(f"signature: {e.signature}")
        if e.file_id:
            end = f"-{e.end_line}" if e.end_line and e.end_line != e.line else ""
            lines.append(f"defined: {self._loc(e)}{end}")
        for d in db.declarations(e.id)[:5]:
            lines.append(f"declared: {db.location(d)}")
        p = db.parent(e)
        if p is not None and p.kind != M.FILE:
            lines.append(f"parent: {p.longname} [{p.kind}]")
        for label, val in (("modifiers", e.modifiers), ("type", e.type)):
            if val:
                lines.append(f"{label}: {val}")
        if e.kind in M.CLASS_KINDS or e.kind in (M.NAMESPACE, M.FILE):
            kids = db.children(e.id)
            kids = [k for k in kids if k.kind not in (M.PARAMETER, M.LOCAL)]
            if kids:
                lines.append(f"members ({len(kids)}):")
                lines += [f"  {k.kind:<11} {k.signature or k.name}  (line {k.line})" for k in kids[:80]]
                if len(kids) > 80:
                    lines.append(f"  ... {len(kids) - 80} more")
        elif e.kind in M.FUNCTION_KINDS:
            params = db.children(e.id, [M.PARAMETER])
            if params:
                lines.append("parameters: " + ", ".join(f"{x.name}: {x.type}" if x.type else x.name for x in params))
        for kind, (fwd, back) in M.REF_LABELS.items():
            for rel, reverse in ((fwd, False), (back, True)):
                ents = [x for x in db.related(e.id, (kind,), reverse=reverse) if x.kind not in M.LOCAL_KINDS]
                if ents:
                    shown = ", ".join(x.longname for x in ents[:25]) + (f" ... (+{len(ents) - 25})" if len(ents) > 25 else "")
                    lines.append(f"{rel}: {shown}")
        m = db.metrics(e.id)
        if m:
            lines.append("metrics: " + ", ".join(f"{k}={v:g}" for k, v in sorted(m.items())))
        return "\n".join(lines)

    def read_code(self, name=None, file=None, start=None, end=None, max_lines=220):
        db = self.db
        note = ""
        if name:
            e, note = self._one(name)
            f = db.entity_file(e)
            if f is None:
                return f"{e.longname} is not defined in the project (no source)."
            start = start or e.line
            end = end or max(e.end_line or e.line, e.line)
            header = f"{note}{e.longname} [{e.kind}] in {f.relpath}"
        elif file:
            f = self._file(file)
            start = start or 1
            end = end or start + max_lines - 1
            header = f"{f.relpath}"
        else:
            return "Give an entity name or a file."
        try:
            with open(f.path, encoding="utf-8", errors="replace") as fh:
                text = fh.read().split("\n")
        except OSError as exc:
            return f"Cannot read {f.relpath}: {exc}"
        start = max(1, int(start))
        end = min(len(text), int(end))
        cut = ""
        if end - start + 1 > max_lines:
            cut = f"\n... lines {start + max_lines}-{end} not shown; ask for them with file/start/end"
            end = start + max_lines - 1
        body = "\n".join(f"{i:5d}| {text[i - 1]}" for i in range(start, end + 1))
        return f"{header}, lines {start}-{end} of {len(text)}:\n{body}{cut}"

    def call_tree(self, name, direction="down", depth=3):
        from .calltree import build_call_tree, to_text
        e, note = self._one(name, sorted(M.FUNCTION_KINDS), "function")
        depth = max(1, min(int(depth or 3), 6))
        t = build_call_tree(self.db, e, direction in ("down", "both"), direction in ("up", "both"), depth,
                            hide_unresolved=True, max_nodes=400)
        return note + to_text(t)

    def class_tree(self, name, relation="both", depth=None):
        from .calltree import build_class_tree, build_member_tree, to_text
        e, note = self._one(name, sorted(M.CLASS_KINDS), "class")
        if relation == "members":
            t = build_member_tree(self.db, e, depth=max(1, min(int(depth or 2), 4)), max_nodes=400)
        else:
            t = build_class_tree(self.db, e, depth=max(1, min(int(depth or 4), 6)), hide_unresolved=True,
                                 max_nodes=400, relation=relation if relation in ("inherit", "compose") else "both")
        return note + to_text(t)

    def modification_tree(self, name, member=None):
        from .calltree import build_modify_tree, modifiable_fields, to_text
        e, note = self._one(name, sorted(M.CLASS_KINDS), "class")
        m = None
        if member:
            m = next((f for f in modifiable_fields(self.db, e) if f.name == member), None)
            if m is None:
                return f"{e.longname} has no data member '{member}'."
        return note + to_text(build_modify_tree(self.db, e, m, depth=8, max_nodes=500))

    def grep(self, pattern, regex=False, files=None, limit=60):
        try:
            rx = re.compile(pattern if regex else re.escape(pattern))
        except re.error as exc:
            return f"Bad regular expression: {exc}"
        out, total = [], 0
        for f in self.db.files():
            rel = f.relpath.replace("\\", "/")
            if files and not (fnmatch.fnmatch(rel, files) or fnmatch.fnmatch(os.path.basename(rel), files)):
                continue
            try:
                with open(f.path, encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            total += 1
                            if len(out) < limit:
                                out.append(f"{rel}:{i}: {line.strip()[:200]}")
            except OSError:
                continue
        if not out:
            return f"No matches for '{pattern}'."
        return "\n".join(out) + (f"\n... {total - limit} more matches" if total > limit else "")

    def list_files(self, folder=None):
        folder = (folder or "").replace("\\", "/").strip("/")
        rows = []
        for f in self.db.files():
            rel = f.relpath.replace("\\", "/")
            if folder and not (rel == folder or rel.startswith(folder + "/")):
                continue
            fe = self.db.file_entity(f.id)
            loc = int(self.db.metric(fe.id, "CountLineCode", 0) or 0) if fe else 0
            rows.append(f"{rel}  ({f.lang}, {loc} code lines)")
        return "\n".join(rows[:400]) + (f"\n... {len(rows) - 400} more files" if len(rows) > 400 else "") \
            if rows else "No files."

    def top_metrics(self, metric, kind="function", count=15):
        kinds = KINDS.get(kind or "function", KINDS["function"])
        names = self.db.metric_names(kinds)
        match = next((n for n in names if n.lower() == (metric or "").lower()), None)
        if match is None:
            return f"Unknown metric '{metric}'. Available: " + ", ".join(names)
        ents, vals = self.db.metric_table(kinds)
        ranked = sorted((e for e in ents if match in vals.get(e.id, {})), key=lambda e: -vals[e.id][match])
        count = max(1, min(int(count or 15), 50))
        return "\n".join(f"{vals[e.id][match]:>8g}  {e.longname}  ({self._loc(e)})" for e in ranked[:count]) \
            or "No values."

    def run(self, name, args):
        fn = {"search": self.search, "describe": self.describe, "read_code": self.read_code,
              "call_tree": self.call_tree, "class_tree": self.class_tree,
              "modification_tree": self.modification_tree, "grep": self.grep, "list_files": self.list_files,
              "top_metrics": self.top_metrics}.get(name)
        if fn is None:
            return f"Unknown tool '{name}'."
        try:
            return _clip(fn(**{k: v for k, v in (args or {}).items() if v not in (None, "")}))
        except (TypeError, ValueError) as exc:
            return f"Error: {exc}"


# ---- conversation -------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the code assistant built into CodeScope, a source code analysis tool. You answer \
questions about the user's project, a {langs} code base:

{overview}

Rules:
- Look things up with the tools before answering. Do not guess names, signatures or behaviour; read the code.
- Start broad (search, describe) and then read the relevant code (read_code). The trees (call_tree, \
class_tree, modification_tree) answer "who calls", "what does it contain" and "where is it changed".
- Cite code locations as path:line exactly as the tools print them (e.g. src/world.cpp:42); they become \
clickable links.
- Be concise and concrete. Use Markdown: short paragraphs, lists, and fenced code blocks for code.
- If the project does not contain the answer, say so."""

_TOOL_CALL_TEXT = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def _text_tool_calls(content):
    """Tool calls a model wrote as text (<tool_call>{json}</tool_call>) when the server did not parse them."""
    calls = []
    for i, m in enumerate(_TOOL_CALL_TEXT.finditer(content or "")):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        args = obj.get("arguments", obj.get("parameters", {}))
        calls.append({"id": f"text_call_{i}", "type": "function",
                      "function": {"name": obj.get("name", ""),
                                   "arguments": args if isinstance(args, str) else json.dumps(args)}})
    return calls


class ThinkSplitter:
    """Routes '<think>...</think>' parts of streamed content to the reasoning channel."""

    def __init__(self, on_content, on_reasoning):
        self.on_content, self.on_reasoning = on_content, on_reasoning
        self.inside = False
        self.buf = ""

    def feed(self, text):
        self.buf += text
        while self.buf:
            tag = "</think>" if self.inside else "<think>"
            i = self.buf.find(tag)
            if i < 0:
                # keep a possible partial tag at the end
                keep = next((k for k in range(min(len(tag) - 1, len(self.buf)), 0, -1)
                             if tag.startswith(self.buf[-k:])), 0)
                out, self.buf = self.buf[:len(self.buf) - keep], self.buf[len(self.buf) - keep:]
                if out:
                    (self.on_reasoning if self.inside else self.on_content)(out)
                return
            if i:
                (self.on_reasoning if self.inside else self.on_content)(self.buf[:i])
            self.buf = self.buf[i + len(tag):]
            self.inside = not self.inside

    def flush(self):
        if self.buf:
            (self.on_reasoning if self.inside else self.on_content)(self.buf)
        self.buf = ""


def strip_think(text):
    return re.sub(r"<think>.*?(</think>|$)", "", text or "", flags=re.S).strip()


class Assistant:
    """A conversation about one project.

    *on_event(kind, data)* receives: "reasoning" / "content" (streamed text), "tool" ((name, args)),
    "tool_result" ((name, text)), "notice" (text), "round" (number).
    """

    def __init__(self, db, settings=None, client=None):
        self.db = db
        self.settings = dict(DEFAULT_SETTINGS, **(settings or {}))
        s = self.settings
        self.client = client or LLMClient(s["url"], s["model"], s["api_key"], s["timeout"])
        self.tools = ProjectTools(db)
        self.use_tools = bool(s.get("tools", True))
        self.history = []          # conversation without the system prompt
        self._cancel = False

    def reset(self):
        self.history = []

    def cancel(self):
        self._cancel = True
        self.client.abort()

    def system_prompt(self):
        langs = ", ".join(sorted((self.db.project_metrics().get("Languages") or {}).keys())) or "C/C++/Python"
        return SYSTEM_PROMPT.format(langs=langs, overview=self.tools.overview())

    def _trim_history(self):
        total = sum(len(m.get("content") or "") for m in self.history)
        for m in self.history:
            if total <= HISTORY_BUDGET:
                break
            if m.get("role") == "tool" and len(m.get("content") or "") > HISTORY_TOOL_LIMIT:
                total -= len(m["content"]) - HISTORY_TOOL_LIMIT
                m["content"] = m["content"][:HISTORY_TOOL_LIMIT] + "\n... (shortened)"

    def _auto_context(self, question):
        """Without tool calling: look up the names mentioned in the question."""
        parts, seen = [], set()
        for word in dict.fromkeys(re.findall(r"[A-Za-z_][\w:]*[\w]", question)):
            if len(word) < 3 or word.lower() in seen:
                continue
            hits = self.tools.find(word)
            if not hits:
                continue
            seen.add(word.lower())
            e = hits[0]
            parts.append(self.tools.describe(e.longname))
            if e.kind in M.FUNCTION_KINDS | M.CLASS_KINDS:
                parts.append(self.tools.read_code(e.longname, max_lines=120))
            if len(parts) >= 8:
                break
        return _clip("\n\n".join(parts), 14000)

    def ask(self, question, on_event=lambda kind, data: None, context=""):
        """Answer *question* (with optional extra *context*, e.g. selected code). Returns the answer text."""
        self._cancel = False
        user = question if not context else f"{context}\n\n{question}"
        start = len(self.history)
        self.history.append({"role": "user", "content": user})
        s = self.settings
        rounds = max(1, int(s.get("max_rounds") or 12))
        answer = ""
        try:
            for rnd in range(rounds + 1):
                last = rnd == rounds
                if last:
                    self.history.append({"role": "user", "content": "Answer now with the information you have."})
                on_event("round", rnd + 1)
                msg = self._complete(on_event, tools=self.use_tools and not last)
                calls = msg["tool_calls"] or (_text_tool_calls(msg["content"]) if self.use_tools else [])
                content = strip_think(_TOOL_CALL_TEXT.sub("", msg["content"]))
                if not calls:
                    answer = content
                    self.history.append({"role": "assistant", "content": content})
                    break
                self.history.append({"role": "assistant", "content": content or None, "tool_calls": calls})
                for c in calls:
                    if self._cancel:
                        raise Cancelled()
                    name = c["function"]["name"]
                    try:
                        args = json.loads(c["function"]["arguments"] or "{}")
                    except ValueError:
                        args = {}
                    on_event("tool", (name, args))
                    result = self.tools.run(name, args if isinstance(args, dict) else {})
                    on_event("tool_result", (name, result))
                    self.history.append({"role": "tool", "tool_call_id": c.get("id") or name, "content": result})
                self._trim_history()
        except Cancelled:
            del self.history[start:]
            raise
        except LLMError:
            del self.history[start:]
            raise
        return answer

    def _complete(self, on_event, tools):
        s = self.settings
        messages = [{"role": "system", "content": self.system_prompt()}] + self.history
        splitter = ThinkSplitter(lambda t: on_event("content", t), lambda t: on_event("reasoning", t))
        try:
            msg = self.client.chat(messages, TOOLS if tools else None, s.get("temperature"), s.get("stream", True),
                                   on_content=splitter.feed, on_reasoning=lambda t: on_event("reasoning", t),
                                   cancelled=lambda: self._cancel)
        except LLMError as exc:
            if not (tools and exc.tools_unsupported):
                raise
            # the server cannot do tool calling: gather the context ourselves and retry once
            self.use_tools = False
            on_event("notice", "The AI server does not support tool calling (for llama-server add --jinja). "
                               "Answering from automatically collected context instead.")
            q = self.history[-1]["content"] if self.history and self.history[-1]["role"] == "user" else ""
            ctx = self._auto_context(q)
            if ctx and self.history and self.history[-1]["role"] == "user":
                self.history[-1] = {"role": "user", "content": f"Project information:\n{ctx}\n\nQuestion: {q}"}
            messages = [{"role": "system", "content": self.system_prompt()}] + self.history
            msg = self.client.chat(messages, None, s.get("temperature"), s.get("stream", True),
                                   on_content=splitter.feed, on_reasoning=lambda t: on_event("reasoning", t),
                                   cancelled=lambda: self._cancel)
        splitter.flush()
        return msg
