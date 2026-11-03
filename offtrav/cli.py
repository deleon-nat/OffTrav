"""Command line interface.

  python -m offtrav check                               # is Ollama up, are the models pulled?
  python -m offtrav ingest --data data/sample_synthetic --index index/synthetic --org "Biringan Central Bank (synthetic)"
  python -m offtrav ask "How many airfare quotations do I need?" --index index/synthetic
  python -m offtrav ask "How many airfare quotations do I need?" --index index/synthetic --show-prompt
  python -m offtrav chat --index index/synthetic
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import load_config
from .ollama_client import OllamaClient, OllamaError


def _print_answer(r: dict, show_sources: bool) -> None:
    print("\n" + r["answer"].strip() + "\n")
    if r["citations"]:
        print("Sources:")
        for c in r["citations"]:
            print(f"  [{c['id']}] {c['source']} ({c['location']})")
    for w in r["warnings"]:
        print(f"  ! {w}")
    if show_sources and r["retrieved"]:
        print("Retrieved passages:")
        for i, h in enumerate(r["retrieved"], 1):
            print(f"  S{i}: {h['source']} ({h['location']}) " + (f"added: {h['expanded']}" if h.get('expanded') else f"score={h['score']}"))
    print(f"  ({r['latency_s']} s, {r['output_tokens']} output tokens, model {r['settings']['chat_model']})")


def print_prompt(state: dict) -> None:
    """Print exactly what is sent to the answer model, message by message."""
    if state["done"]:
        print("\n(No model call: nothing relevant was found, so OffTrav refuses without asking the model.)")
        return
    print("\n" + "=" * 30 + " MODEL INPUT " + "=" * 30)
    for m in state["messages"]:
        print(f"\n----- {m['role'].upper()} -----\n{m['content']}")
    print("\n" + "=" * 73)
    print("Where each source comes from:")
    for i, h in enumerate(state["hits"], 1):
        how = f"added: {h['expanded']}" if h.get("expanded") else "ranked"
        print(f"  [S{i}] {h['source']} ({h['location']})  {how}")


def cmd_check(cfg: dict) -> int:
    from .prompts import PROMPT_VERSION
    print(f"Chat model {cfg['ollama']['chat_model']}, prompt {PROMPT_VERSION}")
    client = OllamaClient(cfg)
    models = client.available_models()
    if not models:
        print(f"Ollama is not reachable at {client.host}. Start the Ollama app and try again.")
        return 1
    ok = True
    for m in (client.chat_model, client.embed_model):
        present = any(x == m or x.split(":")[0] == m or x == f"{m}:latest" for x in models)
        print(f"{'OK     ' if present else 'MISSING'} {m}" + ("" if present else f"   ->  ollama pull {m}"))
        ok &= present
    return 0 if ok else 1


def cmd_ingest(cfg: dict, args) -> int:
    from .retrieve import build_index
    client = None if args.no_embed else OllamaClient(cfg)
    m = build_index(args.data, args.index, cfg, client, org_name=args.org, embed=not args.no_embed)
    print(f"Indexed {m['n_chunks']} chunks from {len(m['files'])} files into {args.index}")
    for f in m["files"]:
        print(f"  {f['file']}: {f['chunks']} chunks")
    for w in m["warnings"]:
        print(f"  WARNING: {w}")
    return 0


def cmd_ask(cfg: dict, args) -> int:
    from .answer import Assistant
    a = Assistant(cfg, args.index)
    if args.show_prompt:
        print_prompt(a.prepare(args.question, mode=args.mode))
    r = a.ask(args.question, mode=args.mode)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        _print_answer(r, args.show_sources)
    return 0


def cmd_chat(cfg: dict, args) -> int:
    from .answer import Assistant
    a = Assistant(cfg, args.index)
    print(f"OffTrav ({a.org}) - type your question, or 'exit'.")
    history: list[dict] = []
    while True:
        try:
            q = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in {"exit", "quit"}:
            break
        if not q:
            continue
        r = a.ask(q, history=history)
        _print_answer(r, args.show_sources)
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": r["answer"]}]
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="offtrav")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("ingest")
    p.add_argument("--data", required=True)
    p.add_argument("--index", required=True)
    p.add_argument("--org", default="", help="organisation name used in prompts")
    p.add_argument("--no-embed", action="store_true", help="BM25 only (no embedding model needed)")
    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("--index", required=True)
    p.add_argument("--mode", default="rag", choices=["rag", "closed_book", "full_context"])
    p.add_argument("--show-sources", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--show-prompt", action="store_true", help="print the full model input (instructions, history, numbered sources, question)")
    p = sub.add_parser("chat")
    p.add_argument("--index", required=True)
    p.add_argument("--show-sources", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    try:
        if args.cmd == "check":
            return cmd_check(cfg)
        if args.cmd == "ingest":
            return cmd_ingest(cfg, args)
        if args.cmd == "ask":
            return cmd_ask(cfg, args)
        if args.cmd == "chat":
            return cmd_chat(cfg, args)
    except (OllamaError, FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0
