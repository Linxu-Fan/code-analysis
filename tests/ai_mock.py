"""A scripted OpenAI-compatible server (like llama-server) for the assistant tests."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockLLM:
    """Replies in order from *script*: {"content": str} or {"tool_calls": [(name, args)]}.

    With *reject_tools*, any request that carries tools fails like llama-server without --jinja.
    """

    def __init__(self, script, reject_tools=False, model="qwen-test", delay=0.0):
        self.script = list(script)
        self.delay = delay              # seconds between streamed chunks (to test Stop)
        self.reject_tools = reject_tools
        self.model = model
        self.requests = []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/v1/models":
                    self._json(200, {"object": "list", "data": [{"id": mock.model, "object": "model"}]})
                else:
                    self._json(404, {"error": {"message": "not found"}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.requests.append(body)
                if mock.reject_tools and body.get("tools"):
                    self._json(500, {"error": {"code": 500, "message": "tools param requires --jinja flag"}})
                    return
                reply = mock.script.pop(0) if mock.script else {"content": "(script exhausted)"}
                calls = [{"id": f"call_{i}", "type": "function",
                          "function": {"name": n, "arguments": json.dumps(a)}}
                         for i, (n, a) in enumerate(reply.get("tool_calls", []))]
                if not body.get("stream"):
                    msg = {"role": "assistant", "content": reply.get("content", "")}
                    if reply.get("reasoning"):
                        msg["reasoning_content"] = reply["reasoning"]
                    if calls:
                        msg["tool_calls"] = calls
                    self._json(200, {"choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                def send(delta):
                    chunk = {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    if mock.delay:
                        self.wfile.flush()
                        time.sleep(mock.delay)

                text = reply.get("reasoning", "")
                for i in range(0, len(text), 7):
                    send({"reasoning_content": text[i:i + 7]})
                text = reply.get("content", "")
                for i in range(0, len(text), 5):       # small pieces, splitting tags like <think>
                    send({"content": text[i:i + 5]})
                for i, c in enumerate(calls):
                    args = c["function"]["arguments"]
                    send({"tool_calls": [{"index": i, "id": c["id"], "type": "function",
                                          "function": {"name": c["function"]["name"], "arguments": ""}}]})
                    for j in range(0, len(args), 6):
                        send({"tool_calls": [{"index": i, "function": {"arguments": args[j:j + 6]}}]})
                self.wfile.write(b"data: [DONE]\n\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
