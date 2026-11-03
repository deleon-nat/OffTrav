// Builds docs/OffTrav_Architecture.pptx: one editable architecture slide in flowchart notation.
// Font: Montserrat. Palette: BSP midnight blue and yellow gold. Text in black. No em dashes.
// Run: node docs/build_architecture_pptx.js [out.pptx]   (set PPTX_SKILL to apply the theme colours)
const path = require("path");
const pptxgen = require("pptxgenjs");

const OUT = process.argv[2] || path.join(__dirname, "OffTrav_Architecture.pptx");
const SKILL = process.env.PPTX_SKILL;

const THEME = {
  name: "BSP Midnight and Gold",
  headFontFace: "Montserrat",
  bodyFontFace: "Montserrat",
  colors: {
    dk1: "000000",     // body text: black
    lt1: "FFFFFF",
    dk2: "0B2A5B",     // midnight blue
    lt2: "E4E9F2",     // pale blue for data shapes
    accent1: "0B2A5B", // midnight blue
    accent2: "C9A227", // yellow gold
    accent3: "F2E2A6", // pale gold for safeguards
    accent4: "071A3D",
    accent5: "2E5A9C",
    accent6: "7A5E08",
    hlink: "2E5A9C",
    folHlink: "7A5E08",
  },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5 in
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "OffTrav system architecture";
pres.author = "Nathaniel De Leon";
const C = pres.SchemeColor;
const BLACK = C.text1, NAVY = C.accent1, GOLD = C.accent2, PALEGOLD = C.accent3, PALEBLUE = C.background2, WHITE = C.background1;

pres.defineSlideMaster({
  title: "FIGURE",
  background: { color: WHITE },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.5, y: 0.3, w: 9.5, h: 0.5,
        fontSize: 22, bold: true, color: NAVY, align: "left", valign: "middle", margin: 0 }, text: "" } },
    { text: { text: "Stat 280 Capstone  |  N. De Leon  |  Checkpoint 1", options: {
        x: 8.33, y: 0.3, w: 4.5, h: 0.5, fontSize: 10, color: BLACK, align: "right", valign: "middle", margin: 0 } } },
  ],
});
pres.addSection({ title: "Architecture" });
const s = pres.addSlide({ masterName: "FIGURE", sectionTitle: "Architecture" });
s.addText("Figure 1. OffTrav system architecture", { placeholder: "title" });

let n = 0;
const id = (p) => `${p}_${++n}`;

// ------------------------------------------------------------------ helpers
const KIND = {
  process: { shape: pres.shapes.FLOWCHART_PROCESS, fill: WHITE, line: NAVY, text: BLACK },
  safe:    { shape: pres.shapes.FLOWCHART_PROCESS, fill: PALEGOLD, line: GOLD, text: BLACK },
  llm:     { shape: pres.shapes.FLOWCHART_PROCESS, fill: NAVY, line: NAVY, text: WHITE },
  docs:    { shape: pres.shapes.FLOWCHART_MULTIDOCUMENT, fill: PALEBLUE, line: NAVY, text: BLACK },
  doc:     { shape: pres.shapes.FLOWCHART_DOCUMENT, fill: PALEBLUE, line: NAVY, text: BLACK },
  store:   { shape: pres.shapes.CAN, fill: PALEBLUE, line: NAVY, text: BLACK },
  term:    { shape: pres.shapes.FLOWCHART_TERMINATOR, fill: WHITE, line: NAVY, text: BLACK },
};

function node(kind, x, y, w, h, text, fs = 10.5) {
  const k = KIND[kind];
  const runs = Array.isArray(text) ? text : [{ text, options: {} }];
  s.addText(runs.map((r, i) => ({ text: r.text, options: { color: k.text, breakLine: i < runs.length - 1, ...r.options } })), {
    shape: k.shape, x, y, w, h, fill: { color: k.fill }, line: { color: k.line, width: 1 },
    fontSize: fs, align: "center", valign: "middle", margin: [3, 7, 3, 7], // points isTextBox: true, objectName: id(kind),
  });
}

function seg(x1, y1, x2, y2, arrow = true, both = false, dash = "solid") {
  s.addShape(pres.shapes.LINE, {
    x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
    flipH: x2 < x1, flipV: y2 < y1,
    line: { color: BLACK, width: 1, dashType: dash, endArrowType: arrow ? "triangle" : undefined,
            beginArrowType: both ? "triangle" : undefined },
    objectName: id("connector"),
  });
}
const poly = (pts) => pts.slice(0, -1).forEach((p, i) => seg(p[0], p[1], pts[i + 1][0], pts[i + 1][1], i === pts.length - 2));

function note(x, y, w, text, align = "center") {
  s.addText(text, { x, y, w, h: 0.22, fontSize: 9, color: BLACK, align, valign: "middle", margin: 0,
    isTextBox: true, objectName: id("note") });
}

// ------------------------------------------------------------------ frame and swimlanes
const F = { x: 0.5, y: 1.0, w: 12.33, h: 5.78 };
const HEAD = 0.36, LABEL_W = 0.42;
const LANES = [
  { key: "index", title: "INDEXING", h: 1.45 },
  { key: "answer", title: "ANSWERING", h: 2.62 },
  { key: "eval", title: "EVALUATION", h: F.h - HEAD - 1.45 - 2.62 },
];
// frame = platform boundary
s.addShape(pres.shapes.RECTANGLE, { x: F.x, y: F.y, w: F.w, h: F.h, fill: { color: WHITE },
  line: { color: NAVY, width: 1.5, dashType: "dash" }, objectName: "platform_boundary" });
s.addText([{ text: "Platform boundary: ", options: { bold: true } },
           { text: "Ollama local on the user's laptop (ThinkPad T14, 32 GB RAM, CPU only). No external services; documents, prompts and answers stay on the machine.", options: {} }],
  { x: F.x + 0.12, y: F.y, w: F.w - 0.24, h: HEAD, fontSize: 10, color: BLACK, valign: "middle", margin: 0,
    isTextBox: true, objectName: "boundary_label" });
let ly = F.y + HEAD;
for (const L of LANES) {
  L.y = ly;
  s.addShape(pres.shapes.LINE, { x: F.x, y: ly, w: F.w, h: 0, line: { color: NAVY, width: 0.75 }, objectName: id("lane_line") });
  s.addText(L.title, { shape: pres.shapes.RECTANGLE, x: F.x, y: ly, w: LABEL_W, h: L.h, fill: { color: NAVY },
    line: { color: NAVY, width: 0.75 }, color: WHITE, bold: true, fontSize: 10, charSpacing: 1.5, vert: "vert270",
    align: "center", valign: "middle", margin: 0, isTextBox: true, objectName: id("lane_label") });
  ly += L.h;
}
const [LI, LA, LE] = LANES;

// six columns for the process steps, Ollama column on the right
const X0 = 1.12, SW = 1.34, GAP = 0.25;
const col = (i) => X0 + i * (SW + GAP);
const mid = (i) => col(i) + SW / 2;
const OX = 10.8, OW = 1.7;

// ------------------------------------------------------------------ Ollama server container (lanes 1-2)
const OY = LI.y + 0.06, OH = LI.h + LA.h - 0.18;
s.addShape(pres.shapes.RECTANGLE, { x: OX - 0.12, y: OY, w: OW + 0.24, h: OH, fill: { color: WHITE },
  line: { color: GOLD, width: 1.25 }, objectName: "ollama_container" });
s.addText("Ollama server (localhost)", { shape: pres.shapes.RECTANGLE, x: OX - 0.12, y: OY, w: OW + 0.24, h: 0.3,
  fill: { color: GOLD }, line: { color: GOLD, width: 1.25 }, fontSize: 9.5, bold: true, color: BLACK,
  align: "center", valign: "middle", margin: 0, isTextBox: true, objectName: "ollama_header" });

// ------------------------------------------------------------------ lane 1: indexing
const iy = LI.y + 0.42, ih = 0.78, icy = iy + ih / 2;
node("docs", col(0), iy, SW, ih, "Travel documents");
note(col(0) - 0.2, LI.y + 0.12, SW + 0.4, "PDF, DOCX, XLSX, CSV");
node("safe", col(1), iy, SW, ih, "Scanned PDF check");
node("process", col(2), iy, SW, ih, "Split into passages");
node("store", col(3), iy - 0.04, SW, ih + 0.08, "Search index");
node("llm", OX, iy, OW, ih, [{ text: "Embedding model", options: { bold: true } }, { text: "nomic-embed-text", options: { fontSize: 9 } }]);
for (let i = 0; i < 3; i++) seg(col(i) + SW, icy, col(i + 1), icy);
seg(col(3) + SW, icy, OX, icy, true, true);
note(col(4), icy - 0.25, 2 * SW + GAP, "passage and query vectors");

// ------------------------------------------------------------------ lane 2: answering
const ay = LA.y + 0.35, ah = 0.82, acy = ay + ah / 2;
node("term", col(0), ay, SW, ah, "Employee asks a question");
node("process", col(1), ay, SW, ah, "Chat app (Streamlit)");
node("safe", col(2), ay, SW, ah, "Input check");
node("process", col(3), ay, SW, ah, "Hybrid search (keyword and embeddings)", 10);
node("safe", col(4), ay, SW, ah, "Remove suspicious passages", 10);
node("process", col(5), ay, SW, ah, "Build prompt with top 6 sources", 10);
node("llm", OX, ay, OW, ah, [{ text: "Answer model", options: { bold: true } }, { text: "qwen3:30b", options: { fontSize: 9 } }]);
for (let i = 0; i < 5; i++) seg(col(i) + SW, acy, col(i + 1), acy);
seg(col(5) + SW, acy, OX, acy);
seg(mid(3), ay, mid(3), iy + ih + 0.04);                // search reads the index
note(mid(3) + 0.08, LA.y + 0.06, 1.3, "reads index", "left");

const by = ay + ah + 0.52, bh = 0.72, bcy = by + bh / 2;
node("safe", col(5), by, SW, bh, "Check citations and numbers", 10);
node("term", col(0), by, SW, bh, "Answer with sources and warnings", 10);
poly([[OX + OW / 2, ay + ah], [OX + OW / 2, bcy], [col(5) + SW, bcy]]);
note(OX - 0.05, ay + ah + 0.12, OW / 2 - 0.05, "draft answer", "right");
seg(col(5), bcy, col(0) + SW, bcy);
note(col(1), bcy - 0.25, 4 * SW + 3 * GAP, "shown in the chat app");

// ------------------------------------------------------------------ lane 3: evaluation
const ey = LE.y + 0.22, eh = LE.h - 0.44, ecy = ey + eh / 2;
node("doc", col(0), ey, SW, eh, "26 test cases with expected answers", 10);
const half = (eh - 0.1) / 2;
node("process", col(1), ey, 2 * SW + GAP, half, "Baseline: same model, no documents", 10);
node("process", col(1), ey + half + 0.1, 2 * SW + GAP, half, "OffTrav: full pipeline", 10);
node("process", col(3), ey, SW, eh, "Automatic scoring", 10);
node("process", col(4), ey, SW, eh, "Author review of every score", 10);
node("doc", col(5), ey, SW, eh, "Results and case evidence", 10);
seg(col(0) + SW, ey + half / 2, col(1), ey + half / 2);
seg(col(0) + SW, ey + half * 1.5 + 0.1, col(1), ey + half * 1.5 + 0.1);
seg(col(2) + SW, ey + half / 2, col(3), ey + half / 2);
seg(col(2) + SW, ey + half * 1.5 + 0.1, col(3), ey + half * 1.5 + 0.1);
seg(col(3) + SW, ecy, col(4), ecy);
seg(col(4) + SW, ecy, col(5), ecy);
s.addText("Metrics: answer correctness (primary), citation accuracy, retrieval recall at 6, latency, output tokens.",
  { x: OX - 0.05, y: ey, w: OW + 0.17, h: eh, fontSize: 9, color: BLACK, valign: "middle", margin: 0,
    isTextBox: true, objectName: "metrics_note" });

// ------------------------------------------------------------------ notation (black text)
s.addText([
  { text: "Notation: ", options: { bold: true } },
  { text: "rectangle = processing step; cylinder = stored index; wavy edge = documents; rounded ends = start or end. " +
          "Navy = language model call on Ollama; gold = safeguard; all other steps are deterministic Python code.", options: {} },
], { x: 0.5, y: 6.88, w: 12.33, h: 0.3, fontSize: 9.5, color: BLACK, margin: 0, valign: "middle", isTextBox: true,
     objectName: "notation" });

s.addNotes("Indexing runs once per document update: documents are checked (scanned PDFs flagged for proofreading), split into " +
  "about 900-character passages, and stored with their embeddings. For each question: input check (length cap, injection flag), " +
  "hybrid search (BM25 plus embeddings, fused by reciprocal rank, at most 3 passages per file), removal of passages with " +
  "instruction-like text, prompt with rules and the top 6 numbered sources, answer from qwen3:30b at temperature 0, then " +
  "citation validation and a check that every number appears in the sources. Evaluation runs the baseline and the full " +
  "pipeline on the same 26 cases.");

(async () => {
  await pres.writeFile({ fileName: OUT });
  if (SKILL) {
    const { applyTheme } = require(path.join(SKILL, "scripts/apply_theme.js"));
    await applyTheme(OUT, THEME);
  }
  console.log("wrote", OUT);
})();
