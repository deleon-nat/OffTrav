"""Question answering pipeline.

Modes
  rag           hybrid retrieval -> safeguards -> LLM with numbered sources -> post-checks   (the system)
  closed_book   the same LLM prompted directly, no retrieval                                    (baseline)
  full_context  every chunk pasted into the prompt up to the context budget, no retrieval        (optional)

What is deterministic code vs. LLM:
  code: loading, chunking, retrieval, ranking, injection quarantine, no-evidence gate,
        citation validation, numeric-grounding check, logging
  LLM:  composing the answer from the supplied sources (and embeddings for dense retrieval)
"""
from __future__ import annotations

import time
from datetime import date

from . import prompts
from .ollama_client import OllamaClient
from .retrieve import Retriever
from . import safeguards as sg


class Assistant:
    def __init__(self, cfg: dict, index_dir: str, client: OllamaClient | None = None):
        self.cfg = cfg
        self.client = client or OllamaClient(cfg)
        self.retriever = Retriever(index_dir, cfg, self.client)
        self.org = self.retriever.org_name

    # ----------------------------------------------------------------- helpers
    def _settings(self) -> dict:
        return {"chat_model": self.client.chat_model, "embed_model": self.client.embed_model,
                **self.client.options, "think": self.client.think,
                "retrieval_mode": self.retriever.mode, "top_k": self.cfg["retrieval"]["top_k"],
                "prompt_version": prompts.PROMPT_VERSION, "today": self.today}

    @property
    def today(self) -> str:
        """Date given to the model; fix it in config (conversation.today) for reproducible evaluation runs."""
        return str(self.cfg.get("conversation", {}).get("today") or date.today().isoformat())

    @staticmethod
    def _history_messages(history: list[dict] | None, turns: int) -> list[dict]:
        if not history or turns <= 0:
            return []
        return [{"role": m["role"], "content": m["content"]} for m in history[-2 * turns:]]

    @staticmethod
    def _model_warnings(r: dict, out: dict) -> None:
        notes = r.get("notes") or []
        out["flags"]["model_notes"] = notes
        if "cut_off_while_reasoning" in notes or "cut_off_at_output_limit" in notes:
            out["warnings"].append(
                "The answer was cut off at the output limit. If the model printed its reasoning, use a "
                "non-thinking (instruct) model; otherwise raise num_predict in config.yaml.")
        if "model_reasoning_removed" in notes:
            out["warnings"].append("The model printed its reasoning; it was removed. A non-thinking (instruct) "
                                   "model is faster for this task.")

    def _no_evidence(self, res: dict) -> bool:
        s = self.cfg["safeguards"]
        if not res["hits"]:
            return True
        dense_low = res["max_dense"] is None or res["max_dense"] <= s["min_dense_similarity"]
        return res["max_bm25"] <= s["min_bm25_score"] and dense_low

    # ----------------------------------------------------------------- public
    def ask(self, question: str, mode: str = "rag", history: list[dict] | None = None) -> dict:
        """Answer in one call (used by the CLI and the evaluation)."""
        state = self.prepare(question, mode, history)
        if state["done"]:
            return state["out"]
        r = self.client.chat(state["messages"], num_ctx=state["num_ctx"])
        return self.finalize(state, r)

    # Streaming (used by the chat app; same pipeline as ask):
    #   state = a.prepare(question, mode, history)
    #   for piece in a.stream_text(state): show(piece)
    #   result = a.finish_stream(state)
    def stream_text(self, state: dict):
        if state["done"]:
            yield state["out"]["answer"]
            return
        stats: dict = {}
        state["_stats"] = stats
        yield from self.client.chat_stream(state["messages"], stats, num_ctx=state["num_ctx"])

    def finish_stream(self, state: dict) -> dict:
        if state["done"]:
            return state["out"]
        return self.finalize(state, state["_stats"])

    def prepare(self, question: str, mode: str = "rag", history: list[dict] | None = None) -> dict:
        """Everything before the model call: input checks, retrieval, safeguards, prompt."""
        s = self.cfg["safeguards"]
        question = question.strip()
        out = {"question": question, "mode": mode, "answer": "", "warnings": [], "flags": {},
               "retrieved": [], "quarantined": [], "citations": [], "settings": self._settings(),
               "latency_s": None, "retrieval_s": None, "prompt_tokens": None, "output_tokens": None}
        if len(question) > s["max_question_chars"]:
            question = question[: s["max_question_chars"]]
            out["warnings"].append("Question was truncated.")
        out["flags"]["question_injection"] = sg.detect_question_injection(question)
        state = {"out": out, "mode": mode, "question": question, "hits": [], "num_ctx": None, "done": False}

        if mode == "closed_book":
            state["messages"] = [
                {"role": "system", "content": prompts.BASELINE_SYSTEM.format(org=self.org, today=self.today)},
                {"role": "user", "content": prompts.BASELINE_USER.format(question=question)}]
            return state

        t0 = time.perf_counter()
        if mode == "full_context":
            hits = [{**c.to_dict(), "score": None} for c in self.retriever.chunks]
            res = {"hits": hits, "max_bm25": 1.0, "max_dense": 1.0}
        else:
            # follow-up questions ("what about Europe?") reuse the previous user question for retrieval
            q_for_search = question
            if history and len(question.split()) < 8:
                prev = [m["content"] for m in history if m["role"] == "user"]
                if prev:
                    q_for_search = f"{prev[-1]} {question}"
            res = self.retriever.search(q_for_search)
            hits = res["hits"]
        out["retrieval_s"] = round(time.perf_counter() - t0, 3)

        if s["quarantine_suspicious_chunks"]:
            hits, dropped = sg.quarantine_chunks(hits)
            out["quarantined"] = [{"source": d["source"], "location": d["location"]} for d in dropped]
            if dropped:
                out["warnings"].append(
                    f"{len(dropped)} retrieved passage(s) contained instruction-like text and were excluded: "
                    + ", ".join(f"{d['source']} ({d['location']})" for d in dropped))
        out["retrieved"] = [{**{k: h[k] for k in ("id", "source", "location", "section", "score")},
                             "expanded": h.get("expanded", "")} for h in hits]
        state["hits"] = hits

        if mode == "rag" and (not hits or self._no_evidence({**res, "hits": hits})):
            out["answer"] = (prompts.REFUSAL_SENTENCE +
                             " Please ask your Administrative Officer or the office in the contact list.")
            out["flags"].update(refusal=True, no_evidence_gate=True)
            out["latency_s"] = out["retrieval_s"]
            state["done"] = True
            return state

        sources = prompts.format_sources(hits)
        msgs = [{"role": "system", "content": prompts.RAG_SYSTEM.format(
                    org=self.org, refusal=prompts.REFUSAL_SENTENCE, today=self.today)}]
        msgs += self._history_messages(history, self.cfg.get("conversation", {}).get("history_turns", 0))
        msgs.append({"role": "user", "content": prompts.RAG_USER.format(sources=sources, question=question)})
        if mode == "full_context":
            approx_tokens = sum(len(m["content"]) for m in msgs) // 3   # conservative chars->tokens
            state["num_ctx"] = max(self.client.options["num_ctx"],
                                   self.cfg["ollama"].get("full_context_num_ctx", 16384))
            if approx_tokens + self.client.options["num_predict"] > state["num_ctx"]:
                out["warnings"].append(f"Full-context prompt (~{approx_tokens} tokens) may exceed "
                                       f"num_ctx={state['num_ctx']}; Ollama will truncate it.")
        state["messages"] = msgs
        return state

    def finalize(self, state: dict, r: dict) -> dict:
        """Everything after the model call: output cleaning results, citation and number checks."""
        s = self.cfg["safeguards"]
        out, hits, question = state["out"], state["hits"], state["question"]
        answer = r["content"]
        base = out["retrieval_s"] or 0.0
        out.update(answer=answer, latency_s=round(base + (r.get("latency_s") or 0.0), 3),
                   prompt_tokens=r.get("prompt_tokens"), output_tokens=r.get("output_tokens"))
        self._model_warnings(r, out)
        refusal = sg.is_refusal(answer)
        out["flags"]["refusal"] = refusal
        if state["mode"] == "closed_book":
            return out
        if s["citation_check"]:
            c = sg.check_citations(answer, len(hits))
            out["citations"] = [{"id": f"S{i}", "source": hits[i - 1]["source"],
                                 "location": hits[i - 1]["location"], "text": hits[i - 1]["text"]}
                                for i in c["cited"]]
            if c["invalid"]:
                out["warnings"].append(f"Answer cited non-existent sources: {c['invalid']}")
            if not c["has_citation"] and not refusal:
                out["warnings"].append("Answer has no source citation; verify before relying on it.")
            out["flags"]["invalid_citations"] = c["invalid"]
        if s["numeric_grounding_check"]:
            # numbers may also come from file names and locations shown with each source (e.g. "OO 2024-101")
            ctx = "\n".join(" ".join((h["text"], h.get("section", ""), h["source"], h["location"])) for h in hits)
            bad = sg.ungrounded_numbers(answer, ctx, question)
            out["flags"]["ungrounded_numbers"] = bad          # kept for the evaluation metric
            # claim-level check: each number against the passage its sentence cites (see safeguards.py)
            claims = sg.check_claims(answer, hits, question, extra=self.today)
            out["claim_checks"] = claims
            problems = sg.claim_warnings(claims)
            out["flags"]["claim_problems"] = problems
            out["warnings"] += problems
            if problems:
                out["warnings"].append("Confirm these figures with the DES Administrative Unit before relying on them.")
        return out
