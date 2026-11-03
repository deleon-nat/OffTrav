"""A tiny stand-in for the Ollama HTTP API, used ONLY for automated tests of the plumbing.

It is NOT a language model: /api/embed returns hashed bag-of-words vectors and /api/chat
copies the first retrieved source back with a citation. Scores obtained with this server
say nothing about answer quality and must never be reported as results.

  python tests/mock_ollama.py --port 11435
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 256
TOK = re.compile(r"[a-z0-9]+")


def fake_embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for t in TOK.findall(text.lower()):
        h = int(hashlib.md5(t.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0 if (h >> 8) % 2 else -1.0
    return v


def fake_chat(messages: list[dict]) -> str:
    user = messages[-1]["content"]
    m = re.search(r"\[S1\] \(.*?\)\n<<<\n(.*?)\n>>>", user, re.DOTALL)
    if not m:
        return "I don't know the specific policy for your organisation."
    first = m.group(1).strip().replace("\n", " ")
    return f"{first[:300]} [S1]"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": "mock-chat:latest"}, {"name": "mock-embed:latest"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/embed":
            inp = req["input"] if isinstance(req["input"], list) else [req["input"]]
            self._send({"model": req["model"], "embeddings": [fake_embed(t) for t in inp]})
        elif self.path == "/api/chat" and req.get("stream"):
            content = fake_chat(req["messages"])
            words = content.split(" ")
            lines = [json.dumps({"model": req["model"], "message": {"role": "assistant",
                     "content": w + (" " if i < len(words) - 1 else "")}, "done": False})
                     for i, w in enumerate(words)]
            lines.append(json.dumps({"model": req["model"], "message": {"role": "assistant", "content": ""},
                                     "done": True, "done_reason": "stop", "eval_count": len(content) // 4,
                                     "prompt_eval_count": sum(len(m["content"]) // 4 for m in req["messages"])}))
            body = ("\n".join(lines) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/chat":
            content = fake_chat(req["messages"])
            self._send({"model": req["model"], "message": {"role": "assistant", "content": content},
                        "prompt_eval_count": sum(len(m["content"]) // 4 for m in req["messages"]),
                        "eval_count": len(content) // 4, "done": True})
        else:
            self._send({"error": "not found"}, 404)


def start(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435)
    srv, port = start(ap.parse_args().port)
    print(f"mock Ollama on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
