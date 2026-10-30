"""AI Assistant dock: chat with a local LLM about the project."""

from __future__ import annotations

import os
import re

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QTextBrowser,
                               QToolButton, QVBoxLayout, QWidget)

from ..core import assistant as A
from . import theme
from .flow import FlowLayout

SOURCE_EXT = r"(?:c|cc|cpp|cxx|c\+\+|h|hh|hpp|hxx|inl|ipp|tpp|py|pyi)"
PATH_RE = re.compile(r"`?((?:[\w.\-]+[/\\])*[\w.\-]+\." + SOURCE_EXT + r")(?![\w+])(?::(\d+)(?:-\d+)?)?`?")

SETUP_HELP = """### AI Assistant

Ask questions about this project in plain language, for example:

* *Where is `damping` modified, and in which order?*
* *Explain what `computeMassMatrixCRB` does.*
* *Which functions are the most complex, and why?*

The assistant reads your code with CodeScope's tools (search, source, call/class/modification trees) and
answers with clickable `file:line` references.

**It needs a local AI server.** None is reachable at `{url}` right now. To set one up:

1. Click **Set Up…** above (or **AI → Set Up Local Model…**) and choose your `.gguf` model file.
2. Press **Install & Write Start Script**: CodeScope downloads llama.cpp for your GPU and decides how much of
   the model goes into GPU memory.
3. Press **Start Server** and keep the server window open.

Already running LM Studio, Ollama or llama-server? Click **Settings** and enter its address.
Details: *docs/AI_SETUP.md*.
"""


class _ModelsWorker(QThread):
    done = Signal(object, str)          # models (list or None), error text

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self):
        s = self.settings
        try:
            models = A.LLMClient(s["url"], s["model"], s["api_key"], 10).models()
            self.done.emit(models, "")
        except A.LLMError as exc:
            self.done.emit(None, str(exc))


class _AskWorker(QThread):
    event = Signal(str, object)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, db_path, settings, history, question, context, parent=None):
        super().__init__(parent)
        self.db_path, self.settings, self.history = db_path, settings, history
        self.question, self.context = question, context
        self.assistant = None

    def run(self):
        from ..core.database import Database
        try:
            db = Database(self.db_path)          # SQLite connections stay in their own thread
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Cannot open the project: {exc}")
            return
        try:
            self.assistant = A.Assistant(db, self.settings)
            self.assistant.history = self.history
            answer = self.assistant.ask(self.question, lambda k, d: self.event.emit(k, d), self.context)
            self.finished_ok.emit(answer)
        except A.Cancelled:
            self.failed.emit("")
        except A.LLMError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            db.close()

    def cancel(self):
        if self.assistant is not None:
            self.assistant.cancel()


class AISettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Assistant Settings")
        self.setMinimumWidth(520)
        self.url = QLineEdit(settings["url"])
        self.url.setToolTip("OpenAI-compatible API address.\nllama-server: http://127.0.0.1:8080/v1\n"
                            "LM Studio: http://127.0.0.1:1234/v1\nOllama: http://127.0.0.1:11434/v1")
        self.model = QComboBox(editable=True)
        self.model.setEditText(settings["model"])
        self.model.lineEdit().setPlaceholderText("(the model the server has loaded)")
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.test)
        mrow = QHBoxLayout()
        mrow.addWidget(self.model, 1)
        mrow.addWidget(refresh)
        self.key = QLineEdit(settings["api_key"], echoMode=QLineEdit.Password, placeholderText="(not needed locally)")
        self.temp = QDoubleSpinBox(minimum=0.0, maximum=2.0, singleStep=0.1, value=float(settings["temperature"]))
        self.rounds = QSpinBox(minimum=1, maximum=40, value=int(settings["max_rounds"]))
        self.rounds.setToolTip("How many lookups the model may make for one question")
        self.stream = QCheckBox("Stream the answer while it is written", checked=bool(settings["stream"]))
        self.tools = QCheckBox("Let the model look things up in the project (tool calling)",
                               checked=bool(settings["tools"]))
        self.status = QLabel(wordWrap=True, objectName="Muted")
        test = QPushButton("Test Connection")
        test.clicked.connect(self.test)
        form = QFormLayout()
        form.addRow("Server address:", self.url)
        form.addRow("Model:", mrow)
        form.addRow("API key:", self.key)
        form.addRow("Temperature:", self.temp)
        form.addRow("Lookups per question:", self.rounds)
        form.addRow("", self.stream)
        form.addRow("", self.tools)
        trow = QHBoxLayout()
        trow.addWidget(test)
        trow.addWidget(self.status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addLayout(form)
        v.addLayout(trow)
        v.addWidget(QLabel("No server yet? Use AI → Set Up Local Model… (guide: docs/AI_SETUP.md)",
                           objectName="Muted"))
        v.addWidget(buttons)
        self._worker = None

    def values(self):
        return {"url": A.normalize_url(self.url.text()), "model": self.model.currentText().strip(),
                "api_key": self.key.text().strip(), "temperature": self.temp.value(),
                "max_rounds": self.rounds.value(), "stream": self.stream.isChecked(),
                "tools": self.tools.isChecked(), "timeout": A.DEFAULT_SETTINGS["timeout"]}

    def test(self):
        self.status.setText("Connecting…")
        self._worker = _ModelsWorker(self.values(), self)
        self._worker.done.connect(self._tested)
        self._worker.start()

    def _tested(self, models, error):
        if models is None:
            self.status.setText(f"<span style='color:#cf222e'>{error}</span>")
            return
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setEditText(current or (models[0] if len(models) == 1 else ""))
        self.status.setText("<span style='color:#1a7f37'>Connected.</span> Models: " + (", ".join(models) or "none"))


class _Input(QPlainTextEdit):
    submit = Signal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier:
            self.submit.emit()
            return
        super().keyPressEvent(e)


class AIPanel(QWidget):
    """Chat transcript, question box and server status."""
    locationActivated = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = None
        self.settings = A.load_settings()
        self.history = []
        self.turns = []
        self.worker = None
        self._orphans = []           # cancelled workers still waiting for the server
        self.online = None
        self.files = {}
        head = QWidget(objectName="PanelBar")
        hv = QVBoxLayout(head)
        hv.setContentsMargins(8, 4, 6, 4)
        hv.setSpacing(2)
        self.status = QLabel(objectName="Muted", wordWrap=True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hv.addWidget(self.status)
        h = FlowLayout(spacing=4, line_spacing=2)
        hv.addLayout(h)
        self.show_reasoning = QCheckBox("Reasoning")
        self.show_reasoning.setToolTip("Show the model's reasoning ('thinking') as well")
        self.show_reasoning.toggled.connect(self._render)
        h.addWidget(self.show_reasoning)
        for text, tip, slot in (("New Chat", "Forget the conversation", self.new_chat),
                                ("Set Up…", "Download llama.cpp and set up a local GGUF model", self.setup),
                                ("Settings", "AI server and model", self.edit_settings)):
            b = QToolButton(text=text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            h.addWidget(b)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        self.input = _Input(placeholderText="Ask about the code…  (Enter sends, Shift+Enter new line)")
        self.input.setFixedHeight(76)
        self.input.submit.connect(self.send)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.hide()
        brow = QVBoxLayout()
        brow.addWidget(self.send_btn)
        brow.addWidget(self.stop_btn)
        brow.addStretch(1)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(6, 6, 6, 6)
        bottom.addWidget(self.input, 1)
        bottom.addLayout(brow)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(head)
        v.addWidget(self.view, 1)
        v.addLayout(bottom)
        self._timer = QTimer(self, singleShot=True, interval=80)
        self._timer.timeout.connect(self._render)
        self._probe = None
        self._update_status()
        self._render()

    # ---- project / server -----------------------------------------------------------------------
    def set_db(self, db):
        self.stop()
        self.db = db
        self.refresh_files()
        self.new_chat()
        self.input.setEnabled(db is not None)
        self.send_btn.setEnabled(db is not None)

    def refresh_files(self):
        """Map every path suffix (a/b/c.cpp, b/c.cpp, c.cpp) to its file, for linking answers."""
        self.files = {}
        if self.db is not None:
            for f in self.db.files():
                parts = f.relpath.replace("\\", "/").split("/")
                for i in range(len(parts)):
                    self.files.setdefault("/".join(parts[i:]), f.id)

    def check_server(self):
        if self._probe is not None and self._probe.isRunning():
            return
        self._probe = _ModelsWorker(self.settings, self)
        self._probe.done.connect(self._probed)
        self._probe.start()

    def _probed(self, models, error):
        self.online = models is not None
        self._update_status(error)
        self._render()

    def _update_status(self, error=""):
        s = self.settings
        where = s["url"].replace("http://", "")
        model = s["model"] or "server's model"
        if self.online is None:
            self.status.setText(f"{model} @ {where}")
        elif self.online:
            self.status.setText(f"<span style='color:#1a7f37'>●</span> {model} @ {where}")
        else:
            self.status.setText(f"<span style='color:#cf222e'>●</span> offline: {where}")
            self.status.setToolTip(error)

    def setup(self):
        AISetupDialog(self).exec()
        self.check_server()

    def edit_settings(self):
        dlg = AISettingsDialog(self.settings, self)
        if dlg.exec():
            self.settings = dict(self.settings, **dlg.values())
            A.save_settings(self.settings)
            self.online = None
            self._update_status()
            self.check_server()

    # ---- conversation ---------------------------------------------------------------------------
    def new_chat(self):
        self.stop()
        self.history = []
        self.turns = []
        self._render()

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def send(self):
        text = self.input.toPlainText().strip()
        if text and self.ask(text):
            self.input.clear()

    def ask(self, question, context="", shown=None):
        """Ask *question*; *context* (e.g. selected code) goes to the model, *shown* to the transcript."""
        if self.db is None or self.busy():
            return False
        self.turns.append({"user": shown or question, "steps": [], "reasoning": "", "answer": "", "notices": [],
                           "error": "", "done": False})
        # the worker gets its own copy of the conversation; it is taken over when the answer is complete
        self.worker = _AskWorker(self.db.path, dict(self.settings), list(self.history), question, context, self)
        self.worker.event.connect(self._event)
        self.worker.finished_ok.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.start()
        self.send_btn.hide()
        self.stop_btn.show()
        self._render()
        return True

    def stop(self):
        """Cancel the running question. A worker still waiting for the server is detached and ignored."""
        w = self.worker
        if w is None or not w.isRunning():
            return
        w.cancel()
        if not w.wait(2000):
            for sig in (w.event, w.finished_ok, w.failed):
                sig.disconnect()
            self._orphans.append(w)
            w.finished.connect(lambda w=w: self._orphans.remove(w) if w in self._orphans else None)
            self.worker = None
            if self.turns and not self.turns[-1]["done"]:
                self._failed("")

    def _event(self, kind, data):
        t = self.turns[-1]
        if kind == "content":
            t["answer"] += data
        elif kind == "reasoning":
            t["reasoning"] += data
        elif kind == "tool":
            name, args = data
            t["steps"].append(f"{name}(" + ", ".join(f"{v!r}" if k in ("name", "query", "pattern") else f"{k}={v!r}"
                                                     for k, v in args.items()) + ")")
            t["answer"] = ""            # text written before a lookup was only an interim note
        elif kind == "notice":
            t["notices"].append(data)
        if not self._timer.isActive():
            self._timer.start()

    def _finished(self, answer):
        if self.worker is not None and self.worker.assistant is not None:
            self.history = self.worker.assistant.history
        t = self.turns[-1]
        t["answer"], t["done"] = answer, True
        self.online = True
        self._done()

    def _failed(self, error):
        t = self.turns[-1]
        t["done"] = True
        t["error"] = error or "Stopped."
        if error.startswith("Cannot reach"):
            self.online = False
        self._done()

    def _done(self):
        self.stop_btn.hide()
        self.send_btn.show()
        self._update_status()
        self._render()
        self.input.setFocus()

    # ---- rendering --------------------------------------------------------------------------------
    def linkify(self, text):
        """Turn 'path/file.cpp:42' (also in backticks) into links that open the code."""
        def repl(m):
            fid = self._file_id(m.group(1))
            if fid is None:
                return m.group(0)
            line = m.group(2) or "1"
            label = m.group(0).strip("`")
            return f"[`{label}`](cs:{fid}:{line})"
        parts = re.split(r"(```.*?```)", text, flags=re.S)
        return "".join(p if p.startswith("```") else PATH_RE.sub(repl, p) for p in parts)

    def _file_id(self, path):
        path = path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        parts = path.split("/")
        for i in range(len(parts)):
            fid = self.files.get("/".join(parts[i:]))
            if fid is not None:
                return fid
        return None

    def _link(self, url):
        m = re.match(r"cs:(\d+):(\d+)", url.toString())
        if m:
            self.locationActivated.emit(int(m.group(1)), int(m.group(2)))

    def markdown(self):
        if not self.turns:
            if self.db is None:
                return "### AI Assistant\n\nOpen a project to ask questions about its code."
            if self.online is False:
                return SETUP_HELP.format(url=self.settings["url"])
            return ("### AI Assistant\n\nAsk anything about this project, e.g. *Where is `mass` modified?* or "
                    "*Explain the class `World`*. Right-click code or an entity and choose **Ask AI** for a "
                    "quick start.")
        out = []
        for t in self.turns:
            out.append("> **You:** " + t["user"].replace("\n", "  \n> "))
            if t["steps"]:
                out.append("*Looked up:* " + " · ".join(f"`{s}`" for s in t["steps"]))
            for n in t["notices"]:
                out.append(f"*Note: {n}*")
            if t["reasoning"] and self.show_reasoning.isChecked():
                out.append("> *Reasoning:* " + t["reasoning"].strip().replace("\n", "  \n> "))
            if t["answer"]:
                out.append(self.linkify(A.strip_think(t["answer"])))
            elif not t["done"]:
                out.append("*Thinking…*" if not t["steps"] else "*Reading the code…*")
            if t["error"]:
                out.append(f"**{t['error']}**" if t["error"] == "Stopped." else f"**Error:** {t['error']}")
            out.append("---")
        return "\n\n".join(out)

    def _render(self):
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 30
        t = theme.tokens()
        self.view.document().setDefaultStyleSheet(
            f"a {{ color: {t['accent']}; }}")
        self.view.setMarkdown(self.markdown())
        self._shade_code(t["panel_alt"])
        if at_end or (self.turns and not self.turns[-1]["done"]):
            bar.setValue(bar.maximum())

    def _shade_code(self, color):
        """Background for code blocks (Markdown import ignores style sheets)."""
        doc = self.view.document()
        cur = QTextCursor(doc)
        fmt = QTextBlockFormat()
        fmt.setBackground(QColor(color))
        block = doc.begin()
        while block.isValid():
            if block.blockFormat().nonBreakableLines() or block.blockFormat().hasProperty(QTextFormat.BlockCodeFence):
                cur.setPosition(block.position())
                cur.mergeBlockFormat(fmt)
            block = block.next()

    def showEvent(self, e):
        super().showEvent(e)
        if self.online is None:
            self.check_server()


def selection_context(relpath, first_line, last_line, code, lang):
    fence = "python" if lang == "python" else "cpp"
    return f"Selected code from {relpath}:{first_line}-{last_line}:\n```{fence}\n{code}\n```"


class _SetupWorker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    done = Signal(object)            # dict with results, or None on failure

    def __init__(self, model, backend, server, port, ctx, install, parent=None):
        super().__init__(parent)
        self.model, self.backend, self.server = model, backend, server
        self.port, self.ctx, self.install = port, ctx, install

    def run(self):
        from ..core import ai_setup as S
        try:
            info = S.gguf_info(self.model)
            hw = S.detect_hardware()
            backend = S.choose_backend(hw) if self.backend == "auto" else self.backend
            self.log.emit(S.describe_hardware(hw))
            self.log.emit(f"llama.cpp build: {backend}")
            args, notes = S.plan(info, hw, backend, self.ctx)
            for n in notes:
                self.log.emit(n)
            if not self.install:
                self.done.emit({"planned": True})
                return
            server = self.server
            if not server:
                if not S.platform_supported():
                    raise RuntimeError("Automatic download is available on Windows only. Install llama.cpp "
                                       "and choose its llama-server program.")
                server = S.install_llama_cpp(backend, hw["cuda"], log=self.log.emit,
                                             progress=lambda d, t: self.progress.emit(d >> 20, t >> 20))
            script = S.write_start_script(server, self.model, args, self.port)
            self.log.emit(f"Start script written: {script}")
            self.done.emit({"script": script, "server": server})
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"<b style='color:#cf222e'>Error:</b> {exc}")
            self.done.emit(None)


class AISetupDialog(QDialog):
    """Pick a GGUF model; download llama.cpp, plan GPU use, write and run the start script."""

    def __init__(self, panel):
        super().__init__(panel)
        from PySide6.QtWidgets import QFileDialog, QProgressBar
        from ..core import ai_setup as S
        self._QFileDialog = QFileDialog
        self.panel = panel
        self.setWindowTitle("Set Up a Local AI Model")
        self.setMinimumSize(680, 520)
        s = panel.settings
        self.model = QLineEdit(s.get("model_file", ""), placeholderText=r"e.g. D:\AI\Models\model.gguf")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_model)
        mrow = QHBoxLayout()
        mrow.addWidget(self.model, 1)
        mrow.addWidget(browse)
        self.backend = QComboBox()
        for text, key in (("Automatic (CUDA for NVIDIA, Vulkan for AMD/Intel)", "auto"), ("NVIDIA CUDA", "cuda"),
                          ("Vulkan (any GPU)", "vulkan"), ("CPU only", "cpu")):
            self.backend.addItem(text, key)
        self.download = QCheckBox("Download the latest llama.cpp automatically", checked=S.platform_supported())
        self.download.setEnabled(S.platform_supported())
        self.server = QLineEdit(s.get("server_exe", ""), placeholderText="path to llama-server")
        sbrowse = QPushButton("Browse…")
        sbrowse.clicked.connect(self._browse_server)
        srow = QHBoxLayout()
        srow.addWidget(self.server, 1)
        srow.addWidget(sbrowse)
        self.download.toggled.connect(lambda on: (self.server.setEnabled(not on), sbrowse.setEnabled(not on)))
        self.server.setEnabled(not self.download.isChecked())
        sbrowse.setEnabled(not self.download.isChecked())
        self.port = QSpinBox(minimum=1024, maximum=65535, value=_port(s["url"]))
        self.ctx = QComboBox()
        for n in (8192, 16384, 32768, 65536, 131072):
            self.ctx.addItem(f"{n:,} tokens", n)
        self.ctx.setCurrentIndex(2)
        form = QFormLayout()
        form.addRow("Model file (.gguf):", mrow)
        form.addRow("llama.cpp build:", self.backend)
        form.addRow("", self.download)
        form.addRow("llama-server:", srow)
        form.addRow("Port:", self.port)
        form.addRow("Context window:", self.ctx)
        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(True)
        self.bar = QProgressBar()
        self.bar.hide()
        self.check_btn = QPushButton("Check Hardware")
        self.check_btn.clicked.connect(lambda: self._run(install=False))
        self.setup_btn = QPushButton("Install && Write Start Script")
        self.setup_btn.clicked.connect(lambda: self._run(install=True))
        self.start_btn = QPushButton("Start Server")
        self.start_btn.clicked.connect(self._start)
        self.start_btn.setEnabled(os.path.exists(_script_path()))
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        brow = QHBoxLayout()
        for b in (self.check_btn, self.setup_btn, self.start_btn):
            brow.addWidget(b)
        brow.addStretch(1)
        brow.addWidget(close)
        v = QVBoxLayout(self)
        v.addLayout(form)
        v.addWidget(self.log, 1)
        v.addWidget(self.bar)
        v.addLayout(brow)
        self.worker = None
        self._wait = QTimer(self, interval=3000)
        self._wait.timeout.connect(self._poll)
        self._waited = 0
        self.log.append("Choose your model file, then <b>Install &amp; Write Start Script</b>. CodeScope downloads "
                        "llama.cpp (about 0.2–0.6 GB), decides how much of the model goes to the GPU and writes "
                        "a start script. <b>Start Server</b> runs it in its own window.")

    def _browse_model(self):
        path, _ = self._QFileDialog.getOpenFileName(self, "Choose a GGUF model", os.path.dirname(self.model.text()),
                                                    "GGUF models (*.gguf);;All files (*)")
        if path:
            self.model.setText(os.path.normpath(path))

    def _browse_server(self):
        path, _ = self._QFileDialog.getOpenFileName(self, "Choose llama-server", "",
                                                    "llama-server (llama-server*);;All files (*)")
        if path:
            self.server.setText(os.path.normpath(path))

    def _run(self, install):
        model = self.model.text().strip().strip('"')
        if not os.path.isfile(model):
            self.log.append("<b>Choose an existing .gguf model file first.</b>")
            return
        server = "" if self.download.isChecked() else self.server.text().strip().strip('"')
        if install and not self.download.isChecked() and not os.path.isfile(server):
            self.log.append("<b>Choose the llama-server program, or tick the automatic download.</b>")
            return
        for b in (self.check_btn, self.setup_btn, self.start_btn):
            b.setEnabled(False)
        self.log.append("")
        self.worker = _SetupWorker(model, self.backend.currentData(), server, self.port.value(),
                                   self.ctx.currentData(), install, self)
        self.worker.log.connect(self.log.append)
        self.worker.progress.connect(self._progress)
        self.worker.done.connect(lambda res: self._finished(res, model))
        self.worker.start()

    def _progress(self, done, total):
        self.bar.show()
        self.bar.setMaximum(max(total, 1))
        self.bar.setValue(done)
        self.bar.setFormat(f"{done} / {total} MiB")

    def _finished(self, res, model):
        self.bar.hide()
        for b in (self.check_btn, self.setup_btn):
            b.setEnabled(True)
        self.start_btn.setEnabled(os.path.exists(_script_path()))
        if res and res.get("script"):
            s = self.panel.settings
            s.update(url=f"http://127.0.0.1:{self.port.value()}/v1", model_file=model, server_exe=res["server"])
            A.save_settings(s)
            self.panel.online = None
            self.panel._update_status()
            self.log.append("<b>Done.</b> Press <b>Start Server</b>. Loading the model takes a minute or two; "
                            "keep the server window open while you use the assistant.")

    def _start(self):
        from ..core import ai_setup as S
        S.start_server(_script_path())
        self.log.append("Starting the server in its own window; waiting for it to load the model …")
        self._waited = 0
        self._wait.start()

    def _poll(self):
        self._waited += 3
        if self._waited > 900:
            self._wait.stop()
            self.log.append("<b>The server did not answer.</b> Check its window for an error message.")
            return
        w = _ModelsWorker(self.panel.settings, self)
        w.done.connect(self._polled)
        w.start()

    def _polled(self, models, _error):
        if models is not None and self._wait.isActive():
            self._wait.stop()
            self.log.append("<b style='color:#1a7f37'>The AI server is ready.</b> Ask your questions in the AI "
                            "Assistant panel.")
            self.panel.online = None
            self.panel.check_server()


def _port(url):
    m = re.search(r":(\d+)", url.split("//", 1)[-1])
    return int(m.group(1)) if m else 8080


def _script_path():
    return os.path.join(A.config_dir(), "start-ai-server.bat" if os.name == "nt" else "start-ai-server.sh")


def show_markdown(parent, title, text):
    """A simple window showing a Markdown document (external links open in the browser)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(820, 760)
    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setMarkdown(text)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    v = QVBoxLayout(dlg)
    v.addWidget(view)
    v.addWidget(buttons)
    dlg.show()
    return dlg
