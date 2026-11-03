"""Minimal HTTP client for the local Ollama server (no SDK dependency).

Only two endpoints are used:
  POST /api/embed  -> embeddings for retrieval
  POST /api/chat   -> answer generation
"""
from __future__ import annotations

import json
import re
import time

import requests

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, cfg: dict):
        o = cfg["ollama"]
        self.host = o["host"].rstrip("/")
        self.chat_model = o["chat_model"]
        self.embed_model = o["embed_model"]
        self.timeout = o.get("timeout_s", 900)
        self.options = {
            "temperature": o.get("temperature", 0.0),
            "seed": o.get("seed", 42),
            "top_p": o.get("top_p", 1.0),
            "num_ctx": o.get("num_ctx", 8192),
            "num_predict": o.get("num_predict", 700),
        }
        self.think = o.get("think", False)
        self._think_supported = True

    # ------------------------------------------------------------------ helpers
    def _post(self, path: str, payload: dict) -> dict:
        try:
            r = requests.post(f"{self.host}{path}", json=payload, timeout=self.timeout)
        except requests.ConnectionError as e:
            raise OllamaError(
                f"Cannot reach Ollama at {self.host}. Is the Ollama app running? ({e})") from e
        if r.status_code != 200:
            raise OllamaError(f"Ollama {path} returned {r.status_code}: {r.text[:300]}")
        return r.json()

    def available_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=10)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    # ------------------------------------------------------------------ API
    def chat_stream(self, messages: list[dict], stats: dict, model: str | None = None,
                    num_ctx: int | None = None):
        """Yield answer text as it is generated. When finished, `stats` holds the same fields as chat()."""
        options = dict(self.options)
        if num_ctx:
            options["num_ctx"] = num_ctx
        payload = {"model": model or self.chat_model, "messages": messages, "stream": True, "options": options}
        if self._think_supported and self.think is not None:
            payload["think"] = bool(self.think)
        t0 = time.perf_counter()
        raw, last, in_think, started = "", {}, False, False
        try:
            lines = _stream_lines(self, payload)
            first = next(lines, None)
        except OllamaError as e:
            if "think" in str(e).lower() and "think" in payload:
                self._think_supported = False
                payload.pop("think")
                lines = _stream_lines(self, payload)
                first = next(lines, None)
            else:
                raise
        def all_lines():
            if first is not None:
                yield first
            yield from lines
        for data in all_lines():
            last = data
            piece = data.get("message", {}).get("content", "")
            if not piece:
                continue
            raw += piece
            if not started and not raw.strip():
                continue
            if not started:
                started = True
                if raw.lstrip().lower().startswith("<think>"):
                    in_think = True
            if in_think:                        # hide reasoning until it closes
                if "</think>" in raw.lower():
                    in_think = False
                    tail = re.split(r"</think>", raw, flags=re.IGNORECASE)[-1].lstrip()
                    if tail:
                        yield tail
                continue
            yield piece
        content, notes = clean_output(raw, last.get("done_reason"))
        stats.update(content=content, notes=notes, prompt_tokens=last.get("prompt_eval_count"),
                     output_tokens=last.get("eval_count"), latency_s=round(time.perf_counter() - t0, 3),
                     model=last.get("model", payload["model"]))

    def embed(self, texts: list[str]) -> list[list[float]]:
        data = self._post("/api/embed", {"model": self.embed_model, "input": texts})
        embs = data.get("embeddings")
        if not embs or len(embs) != len(texts):
            raise OllamaError("Unexpected /api/embed response (is the embedding model pulled?)")
        return embs

    def chat(self, messages: list[dict], model: str | None = None, num_ctx: int | None = None) -> dict:
        """Return {'content', 'prompt_tokens', 'output_tokens', 'latency_s', 'model'}."""
        options = dict(self.options)
        if num_ctx:
            options["num_ctx"] = num_ctx
        payload = {"model": model or self.chat_model, "messages": messages,
                   "stream": False, "options": options}
        if self._think_supported and self.think is not None:
            payload["think"] = bool(self.think)
        t0 = time.perf_counter()
        try:
            data = self._post("/api/chat", payload)
        except OllamaError as e:
            # Models without a thinking mode (e.g. llama3.1) may reject the 'think' flag.
            if "think" in str(e).lower() and "think" in payload:
                self._think_supported = False
                payload.pop("think")
                data = self._post("/api/chat", payload)
            else:
                raise
        latency = time.perf_counter() - t0
        content, notes = clean_output(data.get("message", {}).get("content", ""),
                                      data.get("done_reason"))
        return {
            "content": content,
            "notes": notes,
            "prompt_tokens": data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
            "latency_s": round(latency, 3),
            "model": data.get("model", payload["model"]),
        }


def _stream_lines(client: "OllamaClient", payload: dict):
    try:
        r = requests.post(f"{client.host}/api/chat", json=payload, timeout=client.timeout, stream=True)
    except requests.ConnectionError as e:
        raise OllamaError(f"Cannot reach Ollama at {client.host}. Is the Ollama app running? ({e})") from e
    if r.status_code != 200:
        raise OllamaError(f"Ollama /api/chat returned {r.status_code}: {r.text[:300]}")
    for line in r.iter_lines():
        if line:
            yield json.loads(line)


def clean_output(raw: str, done_reason: str | None = None) -> tuple[str, list[str]]:
    """Remove model reasoning from the answer and report truncation.

    Reasoning ("thinking") models may print their reasoning before the answer, sometimes with
    only a closing </think> tag. Everything up to the last </think> is reasoning, not answer.
    """
    notes = []
    text = THINK_RE.sub("", raw)
    if "</think>" in text.lower():
        text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
        notes.append("model_reasoning_removed")
    elif "<think>" in text.lower():
        # reasoning started but never finished: there is no answer to show
        text = ""
        notes.append("cut_off_while_reasoning")
    if done_reason == "length":
        notes.append("cut_off_at_output_limit")
    return text.strip(), notes
