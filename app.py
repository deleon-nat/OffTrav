"""OffTrav chat app (Streamlit).  Run:  streamlit run app.py

Tabs: Ask (grounded chat), My trip (deadline planner), Contacts, Documents.
Everything runs locally: the browser talks to this Streamlit process on localhost, which talks to the
local Ollama server. Fonts are bundled in static/fonts; usage statistics are off in .streamlit/config.toml.
Feedback (thumbs up/down) is appended to runs/feedback.jsonl on this laptop.
"""
from __future__ import annotations

import csv
import html
import io
import json
import os
from datetime import date, datetime
from itertools import chain
from pathlib import Path

import streamlit as st

from offtrav.answer import Assistant
from offtrav.config import load_config
from offtrav.contacts import fill_subject, load_contacts, mailto
from offtrav.ollama_client import OllamaClient, OllamaError
from offtrav.planner import find_rules_file, load_rules, plan, to_ics

ROOT = Path(__file__).resolve().parent
FEEDBACK_FILE = ROOT / "runs" / "feedback.jsonl"
st.set_page_config(page_title="OffTrav", page_icon=":material/flight_takeoff:", layout="centered")
st.markdown(f"<style>{(ROOT / 'static' / 'offtrav.css').read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True)

STARTERS = [
    "It's my first official trip abroad. What do I need to prepare?",
    "What allowances do I get per day abroad?",
    "Am I entitled to the clothing allowance for my trip?",
    "Who do I email for airfare quotations?",
]
LABELS = {"synthetic": "Biringan Central Bank (synthetic)", "private": "BSP documents (private)"}
SHORT = {"synthetic": "Biringan (synthetic)", "private": "BSP (private)"}
AVATAR = {"user": ":material/person:", "assistant": ":material/flight_takeoff:"}
e = html.escape
FIELD_FOR = {"registration_deadline": "Organiser's registration deadline", "fta_approval": "FTA approved on",
             "event_start": "Event starts", "event_end": "Event ends", "departure": "Departure date",
             "return_date": "Return date"}


def shorten(text: str, limit: int = 160) -> str:
    """Cut long text at a word boundary instead of mid-word."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."

cfg = load_config()
index_root = ROOT / "index"
indexes = sorted(p.name for p in index_root.glob("*") if (p / "chunks.jsonl").exists()) if index_root.exists() else []
# Demo or recording: set OFFTRAV_DEMO=1 so only the synthetic documents can be selected or searched
DEMO = os.environ.get("OFFTRAV_DEMO", "").strip() not in ("", "0", "false")
if DEMO:
    indexes = [n for n in indexes if n == "synthetic"]
st.session_state.setdefault("messages", [])
st.session_state.setdefault("fb_logged", {})

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### Settings")
    if not indexes:
        st.error("No documents are indexed yet. In the terminal, run:\n\n"
                 "`python -m offtrav ingest --data data/sample_synthetic --index index/synthetic`")
        st.stop()
    index_name = st.selectbox("Documents", indexes, format_func=lambda n: SHORT.get(n, n))
    models = OllamaClient(cfg).available_models()
    chat_models = [m for m in models if "embed" not in m] or [cfg["ollama"]["chat_model"]]
    default = next((m for m in chat_models if m.split(":latest")[0] == cfg["ollama"]["chat_model"]), chat_models[0])
    cfg["ollama"]["chat_model"] = st.selectbox("Answer model", chat_models, index=chat_models.index(default))
    mode = st.radio("Answer from", ["rag", "closed_book"], format_func=lambda m: {
        "rag": "The travel documents", "closed_book": "The model alone (for comparison)"}[m])
    cfg["retrieval"]["top_k"] = st.slider("Passages to read per question", 2, 10, cfg["retrieval"]["top_k"])
    show_passages = st.toggle("Show what the search found", value=False)
    if st.button("New chat", icon=":material/refresh:"):
        st.session_state.messages, st.session_state.fb_logged = [], {}
        st.rerun()
    convo_slot = st.empty()
    st.markdown('<p class="ot-small">Runs entirely on this laptop. Answers can be wrong; confirm with the '
                "office named in the source before you act.</p>", unsafe_allow_html=True)


@st.cache_resource(show_spinner="Opening the documents")
def get_assistant(index_name: str, chat_model: str, top_k: int) -> Assistant:
    return Assistant(cfg, str(index_root / index_name))


try:
    assistant = get_assistant(index_name, cfg["ollama"]["chat_model"], cfg["retrieval"]["top_k"])
except (OllamaError, FileNotFoundError, RuntimeError) as err:
    st.error(f"{err}\n\nStart the Ollama app, then reload this page.")
    st.stop()

manifest = assistant.retriever.manifest
data_dir = Path(manifest.get("data_dir", ""))
data_dir = data_dir if data_dir.is_absolute() else ROOT / data_dir
doc_label = LABELS.get(index_name, assistant.org)


def conversation_markdown() -> str:
    lines = ["# OffTrav conversation", "", f"Documents: {doc_label}",
             f"Saved: {datetime.now():%d %B %Y, %H:%M}", ""]
    for m in st.session_state.messages:
        if m["role"] == "user":
            lines += [f"**You:** {m['content']}", ""]
        else:
            lines += [f"**OffTrav:** {m['content']}", ""]
            cites = (m.get("result") or {}).get("citations") or []
            if cites:
                lines += ["Sources: " + "; ".join(f"{c['source']} ({c['location']})" for c in cites), ""]
    lines += ["Answers can be wrong. Confirm with the office named in the source."]
    return "\n".join(lines)


convo_slot.download_button("Save this chat", conversation_markdown(), file_name="offtrav_conversation.md",
                           mime="text/markdown", icon=":material/download:",
                           disabled=not st.session_state.messages)

# ------------------------------------------------------------------ header
st.markdown(f"""
<div class="ot-pass" role="banner">
  <div class="ot-main">
    <div class="ot-word">Off<span>Trav</span></div>
    <div class="ot-tag">Your official travel buddy for training, meetings and presentations abroad</div>
  </div>
  <div class="ot-stub">
    <div class="ot-field"><small>Documents</small><b>{e(doc_label)}</b></div>
    <div class="ot-field"><small>Answers from</small><b>{"Travel documents" if mode == "rag" else "Model alone"}</b></div>
  </div>
</div>
""", unsafe_allow_html=True)

tab_ask, tab_trip, tab_contacts, tab_docs = st.tabs(["Ask", "My trip", "Contacts", "Documents"])


# ================================================================== Ask
def log_feedback(i: int, value: int) -> None:
    msgs = st.session_state.messages
    r = msgs[i].get("result") or {}
    rec = {"time": datetime.now().isoformat(timespec="seconds"), "documents": index_name, "mode": r.get("mode"),
           "rating": "helpful" if value == 1 else "not helpful", "question": msgs[i - 1]["content"],
           "answer": msgs[i]["content"], "sources": [c["source"] for c in r.get("citations", [])],
           "model": (r.get("settings") or {}).get("chat_model"),
           "prompt_version": (r.get("settings") or {}).get("prompt_version")}
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def render_meta(r: dict) -> None:
    cites = r.get("citations") or []
    if cites:
        items = "".join(f"<li>[{e(c['id'])}] {e(c['source'])}, {e(c['location'])}</li>" for c in cites)
        st.markdown(f'<div class="ot-sources"><b>Sources</b><ol>{items}</ol></div>', unsafe_allow_html=True)
        if any(c.get("text") for c in cites):
            with st.expander("Read the cited passages"):
                for c in cites:
                    st.markdown(f"**[{c['id']}] {c['source']}, {c['location']}**")
                    st.text(c.get("text", ""))
    claims = [x for x in r.get("claim_checks") or [] if x["checks"]]
    if claims:
        label = {"supported": "found in the cited source", "from_question": "from your question",
                 "calculated": "calculated", "unit_mismatch": "unit differs from the source",
                 "not_in_cited": "not in the cited source", "uncited": "no citation", "not_found": "not in any source"}
        with st.expander("How the numbers were checked"):
            for x in claims:
                marks = "; ".join(f"{c['number']}: {label.get(c['status'], c['status'])}"
                                  + (f" ({c['detail']})" if c["detail"] else "") for c in x["checks"])
                st.markdown(f"- {e(shorten(x['claim']))}  \n  {e(marks)}")
            st.caption("Code checks that each number comes from the cited source, your question, or a simple "
                       "calculation. It does not check that the rule fits your situation; confirm that with "
                       "the DES Administrative Unit.")
    for w in r.get("warnings", []):
        st.warning(w)
    if show_passages and r.get("retrieved"):
        with st.expander("What the search found"):
            for k, h in enumerate(r["retrieved"], 1):
                st.markdown(f"S{k}. {h['source']}, {h['location']}")
    if r.get("latency_s") is not None:
        st.markdown(f'<div class="ot-meta">Answered in {r["latency_s"]:.0f} seconds by '
                    f'{e(r["settings"]["chat_model"])}</div>', unsafe_allow_html=True)


with tab_ask:
    for i, m in enumerate(st.session_state.messages):
        with st.container(key=f"msg-{m['role']}-{i}"):
            with st.chat_message(m["role"], avatar=AVATAR[m["role"]]):
                st.markdown(m["content"])
                if m["role"] == "assistant" and "result" in m:
                    render_meta(m["result"])
                    if hasattr(st, "feedback"):
                        val = st.feedback("thumbs", key=f"fb-{i}")
                        if val is not None and st.session_state.fb_logged.get(i) != val:
                            log_feedback(i, val)
                            st.session_state.fb_logged[i] = val
                            st.toast("Feedback saved on this laptop")

    question = None
    if not st.session_state.messages:
        st.markdown('<div class="ot-h">Planning an official trip abroad?</div>'
                    '<p class="ot-p">Ask about requirements, allowances, forms, contacts and deadlines. '
                    "Every answer shows the document it came from.</p>", unsafe_allow_html=True)
        cols = st.columns(2)
        for i, s in enumerate(STARTERS):
            try:
                clicked = cols[i % 2].button(s, key=f"starter_{i}", width="stretch")
            except TypeError:  # older Streamlit without the width argument
                clicked = cols[i % 2].button(s, key=f"starter_{i}", use_container_width=True)
            if clicked:
                question = s

    typed = st.chat_input("Ask about your official foreign travel")
    question = typed or question

    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        n = len(st.session_state.messages)
        with st.container(key=f"msg-user-{n - 1}"):
            with st.chat_message("user", avatar=AVATAR["user"]):
                st.markdown(question)
        history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
        with st.container(key=f"msg-assistant-{n}"):
            with st.chat_message("assistant", avatar=AVATAR["assistant"]):
                try:
                    with st.spinner("Searching the travel documents" if mode == "rag" else "Thinking"):
                        state = assistant.prepare(question, mode=mode, history=history)
                        stream = assistant.stream_text(state)
                        first = next(stream, "")          # wait for the first words inside the spinner
                    st.write_stream(chain([first], stream))
                    r = assistant.finish_stream(state)
                except OllamaError as err:
                    st.error(f"{err}\n\nStart the Ollama app, then ask again.")
                    st.stop()
        st.session_state.messages.append({"role": "assistant", "content": r["answer"], "result": r})
        st.rerun()


# ================================================================== My trip
def date_box(item: dict) -> str:
    d = item["due"]
    if d is None:
        return '<div class="ot-date none"><div class="d">Needs a date</div></div>'
    cls = {"overdue": " overdue", "due this week": " soon"}.get(item["status"], "")
    return f'<div class="ot-date{cls}"><div class="d">{d.day}</div><div class="m">{d:%b %Y}, {d:%a}</div></div>'


with tab_trip:
    st.markdown('<div class="ot-h">Your deadlines, worked out for you</div>'
                '<p class="ot-p">Enter your dates. OffTrav lists every deadline in order, from the deadline '
                "table kept with the documents.</p>", unsafe_allow_html=True)
    rules_file = find_rules_file(data_dir)
    if rules_file is None:
        st.info(f"No deadline table found for these documents. Add a CSV or Excel file with \"Deadlines\" in its "
                f"name to {data_dir / '_planner'} (columns: Step, Applies to, From, Days, Day type, Direction, "
                "Office, Condition, Source). The Biringan example is in data/sample_synthetic/_planner.")
    else:
        rules, problems = load_rules(rules_file)
        for p in problems:
            st.warning(f"{rules_file.name}, {p}")
        c1, c2 = st.columns([1.5, 1])
        EVENT_TYPES = {"Training": "training", "Meeting or conference": "non-training",
                       "Presentation": "non-training"}   # presentations are non-training events
        event_label = c1.radio("Type of event", list(EVENT_TYPES), horizontal=True)
        event_type = EVENT_TYPES[event_label]
        trip_name = c2.text_input("Trip name (for your calendar)", placeholder="e.g. IMF course, Washington")
        dates: dict = {}
        r1 = st.columns(3)
        if event_type == "training":
            dates["registration_deadline"] = r1[0].date_input("Organiser's registration deadline", value=None)
            dates["fta_approval"] = r1[1].date_input("FTA approved on (if known)", value=None)
        else:
            r1[0].markdown('<p class="ot-small">Meetings, conferences and presentations have no registration '
                           "or Deed of Undertaking steps.</p>", unsafe_allow_html=True)
        r2 = st.columns(4)
        dates["event_start"] = r2[0].date_input("Event starts", value=None)
        dates["event_end"] = r2[1].date_input("Event ends", value=None)
        dates["departure"] = r2[2].date_input("Departure date", value=None)
        dates["return_date"] = r2[3].date_input("Return date", value=None)

        if dates["departure"] and dates["return_date"] and dates["return_date"] < dates["departure"]:
            st.error("The return date is before the departure date. Check the dates.")
        else:
            items = plan(rules, event_type, dates, today=date.today())
            dated = [i for i in items if i["due"]]
            keyp = f"done-{index_name}-{event_type}-"
            done = sum(1 for i in items if st.session_state.get(keyp + i["step"][:60]))
            overdue = sum(1 for i in dated if i["status"] == "overdue")
            summary = f"{done} of {len(items)} steps done"
            if overdue:
                summary += f". {overdue} overdue"
            st.markdown(f'<div class="ot-progress">{summary}</div>', unsafe_allow_html=True)
            st.progress(done / len(items) if items else 0.0)
            for item in items:
                key = keyp + item["step"][:60]
                left, right = st.columns([6, 1], vertical_alignment="center")
                is_done = st.session_state.get(key, False)
                badges = ""
                if item["status"] == "overdue" and not is_done:
                    badges += '<span class="ot-badge overdue">Overdue</span>'
                elif item["status"] == "due this week" and not is_done:
                    badges += '<span class="ot-badge soon">Due within 7 days</span>'
                if item["weekend"]:
                    badges += '<span class="ot-badge weekend">Falls on a weekend</span>'
                when = ""
                if item["window_start"] and item["due"]:
                    when = f"Do this between {item['window_start']:%a %d %b} and {item['due']:%a %d %b %Y}. "
                elif item["due"] is None:
                    when = f"Fill in \u201c{FIELD_FOR.get(item.get('anchor'), 'the date it counts from')}\u201d above to see this deadline. "
                cond = f" Note: {e(item['condition'])}." if item["condition"] else ""
                left.markdown(
                    f'<div class="ot-item">{date_box(item)}<div><div class="ot-step{" done" if is_done else ""}">'
                    f'{e(item["step"])}{badges}</div><div class="ot-sub">{e(when)}{e(item["office"])}. '
                    f'Rule: {e(item["rule"])}.{cond}<br>Source: {e(item["source"])}</div></div></div>',
                    unsafe_allow_html=True)
                right.checkbox("Done", key=key)

            if dated:
                d1, d2, _ = st.columns([1.3, 1.3, 2])
                d1.download_button("Add to calendar", to_ics(items, trip_name or "Official trip"),
                                   file_name="offtrav_deadlines.ics", mime="text/calendar", icon=":material/event:")
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["Due date", "Step", "Office", "Rule", "Source", "Note"])
                for i in items:
                    w.writerow([i["due"] or "", i["step"], i["office"], i["rule"], i["source"], i["condition"]])
                d2.download_button("Download list", buf.getvalue(), file_name="offtrav_deadlines.csv",
                                   mime="text/csv", icon=":material/download:")
            st.markdown(f'<p class="ot-small">Working-day rules skip Saturdays and Sundays but not holidays; check '
                        f"the holiday calendar for these periods. Rules come from {e(rules_file.name)}. "
                        "Confirm each date with the office named.</p>", unsafe_allow_html=True)


# ================================================================== Contacts
with tab_contacts:
    st.markdown('<div class="ot-h">Who to contact</div><p class="ot-p">From the contact list in the documents. '
                "Fill in your details and OffTrav prepares the subject line.</p>", unsafe_allow_html=True)
    contacts = load_contacts(data_dir)
    if not contacts:
        st.info("No contact list found. Add a CSV or Excel file with the columns Topic, Office, Email, "
                "When to contact and Suggested subject line to the documents folder.")
    else:
        f1, f2, f3 = st.columns(3)
        who = f1.text_input("Your name", placeholder="e.g. N. De Leon")
        event = f2.text_input("Event or course", placeholder="e.g. IMF course")
        when = f3.text_input("Travel dates", placeholder="e.g. 18 to 24 Jan 2027")
        cols = st.columns(2)
        for k, c in enumerate(contacts):
            subject = fill_subject(c["subject"], name=who, event=event, dates=when)
            link = mailto(c["email"], subject)
            with cols[k % 2]:
                st.markdown(
                    f'<div class="ot-contact"><div class="t">{e(c["topic"])}</div><div class="o">{e(c["office"])}</div>'
                    f'<div class="w">{e(c["when"])}</div><div class="e">{e(c["email"])}</div></div>',
                    unsafe_allow_html=True)
                if link:
                    st.link_button("Write email", link, icon=":material/mail:")
                st.markdown("")


# ================================================================== Documents
with tab_docs:
    created = manifest.get("created", "")
    try:
        created = datetime.fromisoformat(created).astimezone().strftime("%d %B %Y, %H:%M")
    except ValueError:
        pass
    st.markdown(f'<div class="ot-h">What OffTrav is reading</div><p class="ot-p">{e(doc_label)}: '
                f'{len(manifest.get("files", []))} documents split into {manifest.get("n_chunks", 0)} passages. '
                f"Indexed {e(created)}.</p>", unsafe_allow_html=True)
    rows = "".join(f"<tr><td>{e(f['file'])}</td><td>{f['chunks']}</td></tr>" for f in manifest.get("files", []))
    st.markdown(f'<table class="ot-docs"><tr><th>Document</th><th>Passages</th></tr>{rows}</table>',
                unsafe_allow_html=True)
    if manifest.get("warnings"):
        st.markdown('<div class="ot-h">Check these</div>', unsafe_allow_html=True)
        for w in manifest["warnings"]:
            st.warning(w)
    rf = find_rules_file(data_dir)
    st.markdown(f'<p class="ot-small" style="margin-top:1rem">Deadline table for My trip: '
                f'{e(rf.name) if rf else "none"} (used only by the planner, not by answers). After you add or '
                "change a document, rebuild the index:<br><code>python -m offtrav ingest --data "
                f'{e(str(manifest.get("data_dir", "")))} --index index/{e(index_name)}</code></p>',
                unsafe_allow_html=True)
