// Builds Meridian_AI_Triage.pptx  —  node build_deck.js
const pptxgen = require("pptxgenjs");
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.title = "Meridian Care - AI Triage Agent";

const C = { dark: "1F2A36", dark2: "2C3A4A", light: "F4F6F8", white: "FFFFFF", orange: "F26B21",
            text: "1F2A36", muted: "5B6B7A", line: "D5DCE3", green: "2E8B57", red: "C0392B", tint: "FDEBDD" };
const H = "Cambria", B = "Calibri";
const W = 13.33;

function title(slide, t, sub) {
  slide.addText(t, { x: 0.6, y: 0.4, w: W - 1.2, h: 0.8, fontFace: H, fontSize: 34, bold: true, color: C.text, margin: 0, isTextBox: true });
  if (sub) slide.addText(sub, { x: 0.6, y: 1.15, w: W - 1.2, h: 0.45, fontFace: B, fontSize: 16, color: C.muted, margin: 0, isTextBox: true });
}
function badge(slide, n, x, y, d = 0.5, fill = C.orange) {
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill } });
  slide.addText(String(n), { x, y, w: d, h: d, fontFace: B, fontSize: d * 28, bold: true, color: C.white, align: "center", valign: "middle", margin: 0, isTextBox: true });
}
function card(slide, x, y, w, h, fill = C.white) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08, fill: { color: fill }, line: { color: C.line, width: 0.75 },
    shadow: { type: "outer", color: "000000", opacity: 0.08, blur: 6, offset: 2, angle: 90 } });
}
function txt(slide, t, o) { slide.addText(t, { fontFace: B, fontSize: 14, color: C.text, margin: 0, valign: "top", isTextBox: true, ...o }); }
function lightSlide() { const s = pres.addSlide(); s.background = { color: C.light }; return s; }
function tag(slide, label) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: W - 3.4, y: 0.45, w: 2.8, h: 0.4, rectRadius: 0.2, fill: { color: C.tint }, line: { color: C.tint } });
  txt(slide, label, { x: W - 3.4, y: 0.45, w: 2.8, h: 0.4, fontSize: 12, bold: true, color: C.orange, align: "center", valign: "middle" });
}

// 1. Title ---------------------------------------------------------------
{
  const s = pres.addSlide(); s.background = { color: C.dark };
  for (let i = 0; i < 6; i++) s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 8.3 + (i % 3) * 1.55, y: 1.6 + Math.floor(i / 3) * 2.0, w: 1.35, h: 1.8, rectRadius: 0.05,
    fill: { color: i === 1 ? C.orange : C.dark2 }, line: { color: "3D4E61", width: 1 } });
  txt(s, "AI TALENT QUEST · ROUND 3", { x: 0.7, y: 1.5, w: 7, h: 0.4, fontSize: 14, bold: true, color: C.orange, charSpacing: 3 });
  s.addText("Meridian Care", { x: 0.7, y: 2.0, w: 7.3, h: 1.1, fontFace: H, fontSize: 54, bold: true, color: C.white, margin: 0, isTextBox: true });
  txt(s, "An autonomous, guardrailed AI agent that triages tenant requests, fixes verified billing errors, and keeps humans in control.",
      { x: 0.7, y: 3.2, w: 7.0, h: 1.2, fontSize: 20, color: "CBD5DF" });
  txt(s, "Python · Claude · SQLAlchemy · Streamlit", { x: 0.7, y: 5.9, w: 7, h: 0.4, fontSize: 14, color: "8FA1B3" });
  s.addNotes("Open with the hook: some requests are emergencies, some are small billing errors we can fix instantly, and some are attempts to get money that isn't owed. The agent handles all three safely.");
}

// 2. Problem & traps -----------------------------------------------------
{
  const s = lightSlide();
  title(s, "The real test: can the agent be trusted?", "100 tenant requests and 29 billing records, with deliberate traps in the data");
  const traps = [
    ["$210 goodwill demand", "Billing record says $33.85. Never pay the claimed amount."],
    ["Record shows $0 or −$338", "No discrepancy, or the tenant actually owes. No credit."],
    ["~$310 verified overcharge", "Real, but above the auto-limit. A human approves."],
    ["Same name, different person", "Marguerite Effiong ≠ Marguerite Solheim. Exact match only."],
    ["Identical text, 4 tenants", "Not duplicates. Only same-tenant open tickets are."],
    ["\"URGENT\" vs real emergency", "Sprinkler dripping, tenant flying out in 1 hour: P1. Capital letters alone: not."],
  ];
  traps.forEach(([h, d], i) => {
    const x = 0.6 + (i % 3) * 4.1, y = 1.95 + Math.floor(i / 3) * 2.45;
    card(s, x, y, 3.85, 2.2);
    badge(s, i + 1, x + 0.3, y + 0.3, 0.5);
    txt(s, h, { x: x + 0.3, y: y + 0.95, w: 3.3, h: 0.45, fontSize: 17, bold: true });
    txt(s, d, { x: x + 0.3, y: y + 1.4, w: 3.3, h: 0.75, fontSize: 13, color: C.muted });
  });
  s.addNotes("Also: a Spanish-language request, vague claims ('not sure by how much'), off-taxonomy questions, and one true duplicate (Renwick TR-6039 vs open TR-6038).");
}

// 3. Solution flow -------------------------------------------------------
{
  const s = lightSlide();
  title(s, "What the agent does for every request", "It works as an agent, not a single call: it decides which tools to use, then takes exactly one final action");
  const steps = [["Classify", "5 categories, confidence, urgency signals, claim details"],
                 ["Check history", "Tool: same-tenant duplicates and repeats"],
                 ["Get context", "Tool: required when confidence < 0.75"],
                 ["Verify billing", "Tool: real record for specific claims"],
                 ["Decide", "Policy computes priority, team, credit"],
                 ["Act", "Acknowledge · close duplicate · route · credit"]];
  steps.forEach(([h, d], i) => {
    const x = 0.6 + i * 2.05;
    card(s, x, 2.2, 1.85, 2.7, i === 5 ? C.dark : C.white);
    badge(s, i + 1, x + 0.67, 2.45, 0.5);
    txt(s, h, { x: x + 0.12, y: 3.1, w: 1.6, h: 0.45, fontSize: 17, bold: true, align: "center", color: i === 5 ? C.white : C.text });
    txt(s, d, { x: x + 0.12, y: 3.6, w: 1.6, h: 1.2, fontSize: 12, align: "center", color: i === 5 ? "CBD5DF" : C.muted });
    if (i < 5) s.addShape(pres.shapes.RIGHT_TRIANGLE, { x: x + 1.88, y: 3.45, w: 0.15, h: 0.2, rotate: 0, fill: { color: C.orange }, line: { color: C.orange } });
  });
  card(s, 0.6, 5.3, 12.1, 1.4, C.tint);
  txt(s, [{ text: "Everything is persisted: ", options: { bold: true } },
          { text: "requests, classifications with reasoning, every tool call (including blocked ones), actions, courtesy credits and human overrides. Reviewers see all of it and can override anything." }],
      { x: 0.9, y: 5.5, w: 11.5, h: 1.0, fontSize: 15, valign: "middle" });
}

// 4. Architecture --------------------------------------------------------
{
  const s = lightSlide();
  title(s, "Architecture"); tag(s, "ARCHITECTURE");
  const box = (x, y, w, h, t, d, fill = C.white, fc = C.text) => {
    card(s, x, y, w, h, fill);
    txt(s, t, { x: x + 0.15, y: y + 0.12, w: w - 0.3, h: 0.4, fontSize: 15, bold: true, color: fc, align: "center" });
    if (d) txt(s, d, { x: x + 0.15, y: y + 0.5, w: w - 0.3, h: h - 0.6, fontSize: 11.5, color: fc === C.text ? C.muted : "CBD5DF", align: "center" });
  };
  const arrow = (x1, y1, x2, y2) => s.addShape(pres.shapes.LINE, { x: x1, y: y1, w: x2 - x1, h: y2 - y1, line: { color: C.orange, width: 2, endArrowType: "triangle" } });
  box(0.6, 2.0, 1.9, 1.3, "Seed CSVs", "requests + billing");
  box(0.6, 3.9, 1.9, 1.3, "Ingest", "validated, idempotent");
  arrow(1.55, 3.3, 1.55, 3.9);
  box(3.1, 3.9, 2.0, 1.3, "SQL database", "SQLite WAL / Postgres");
  arrow(2.5, 4.55, 3.1, 4.55);
  box(3.1, 1.6, 2.0, 1.5, "Orchestrator", "bounded loop, 8 steps; parallel workers; safe fallback");
  arrow(4.1, 3.9, 4.1, 3.1);
  // agent group
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 5.7, y: 1.5, w: 4.3, h: 4.9, rectRadius: 0.1, fill: { color: C.dark }, line: { color: C.dark } });
  txt(s, "TRIAGE AGENT", { x: 5.7, y: 1.6, w: 4.3, h: 0.35, fontSize: 12, bold: true, color: C.orange, align: "center", charSpacing: 2 });
  box(6.0, 2.05, 3.7, 1.1, "Claude (LLM)", "versioned prompt · strict tool schemas", C.dark2, C.white);
  box(6.0, 3.45, 3.7, 1.1, "Tool environment", "protocol order + guardrails on every call", C.dark2, C.white);
  box(6.0, 4.85, 3.7, 1.25, "Policy engine", "priority · routing · 8-rule credit check", C.orange, C.white);
  arrow(7.85, 3.15, 7.85, 3.45); arrow(7.85, 4.55, 7.85, 4.85);
  arrow(5.1, 2.35, 5.7, 2.35);
  box(10.6, 1.6, 2.2, 1.5, "Review console", "Streamlit: reasoning, trace, overrides", C.white);
  box(10.6, 3.6, 2.2, 1.3, "Eval harness", "vs 100 gold labels");
  box(10.6, 5.3, 2.2, 1.2, "Audit log", "every action + override");
  arrow(10.0, 2.35, 10.6, 2.35); arrow(10.0, 4.2, 10.6, 4.2); arrow(10.0, 5.9, 10.6, 5.9);
  txt(s, "The LLM proposes, the policy decides.", { x: 0.6, y: 6.6, w: 9, h: 0.5, fontFace: H, fontSize: 20, italic: true, color: C.orange });
  s.addNotes("The data flow is left to right. Only the tool environment touches the database, and every write goes through policy.");
}

// 5. Single vs multi agent ------------------------------------------------
{
  const s = lightSlide();
  title(s, "Why one agent, not many", "A justified design choice, not a default"); tag(s, "AGENT DESIGN");
  card(s, 0.6, 1.95, 5.9, 3.9);
  txt(s, "Multi-agent (rejected)", { x: 0.9, y: 2.15, w: 5.3, h: 0.5, fontSize: 20, bold: true, color: C.red });
  txt(s, [
    { text: "Triage is one sequential decision; every step depends on the one before", options: { bullet: true, breakLine: true } },
    { text: "Hand-offs between classifier, billing and router agents add failure points", options: { bullet: true, breakLine: true } },
    { text: "3–4× the LLM calls, latency and cost, with no accuracy gain", options: { bullet: true, breakLine: true } },
    { text: "The specialist knowledge (billing rules, routing, priority) is deterministic, so it doesn't need an LLM", options: { bullet: true } },
  ], { x: 0.9, y: 2.8, w: 5.3, h: 3.5, fontSize: 15, paraSpaceAfter: 10, color: C.text });
  card(s, 6.85, 1.95, 5.9, 3.9, C.dark);
  txt(s, "Single agent + policy engine (chosen)", { x: 7.15, y: 2.15, w: 5.4, h: 0.5, fontSize: 20, bold: true, color: C.orange });
  txt(s, [
    { text: "The LLM handles language and judgement: classify, extract, reason, choose tools", options: { bullet: true, breakLine: true } },
    { text: "Code handles money and state: priority, routing, credit amount, duplicate validity", options: { bullet: true, breakLine: true } },
    { text: "Specialist logic is exposed as tools and policy, and is testable", options: { bullet: true, breakLine: true } },
    { text: "Scales out: many single-agent workers run in parallel, one per ticket", options: { bullet: true } },
  ], { x: 7.15, y: 2.8, w: 5.3, h: 3.5, fontSize: 15, paraSpaceAfter: 10, color: C.white });
}

// 6. Tools ----------------------------------------------------------------
{
  const s = lightSlide();
  title(s, "Eight tools, each least-privilege"); tag(s, "AGENT DESIGN");
  const rows = [["Tool", "Role", "Guardrail"],
    ["record_classification", "Reasoning capture", "Strict enum schema; policy returns priority, team and next steps"],
    ["search_request_history", "Data tool #1", "Bound to this tenant (exact name); no ID arguments"],
    ["get_customer_context", "Data tool #2", "REQUIRED when confidence < 0.75"],
    ["lookup_billing_record", "Data", "Billing only; REQUIRED for a specific claim"],
    ["issue_courtesy_credit", "Money", "No amount argument; the amount comes from the record; 8-rule check"],
    ["acknowledge_request", "Final action", "Only after an issued credit"],
    ["route_request", "Final action", "The team is chosen by policy, not the LLM"],
    ["close_as_duplicate", "Final action", "Same tenant, earlier, still open; never P1 into a lower priority"]];
  s.addTable(rows.map((r, i) => r.map((c, j) => ({ text: c, options: {
      bold: i === 0 || j === 0, color: i === 0 ? C.white : C.text, fill: { color: i === 0 ? C.dark : (i % 2 ? C.white : "EEF1F4") },
      fontFace: j === 0 && i > 0 ? "Courier New" : B, fontSize: j === 0 && i > 0 ? 12 : 13 } }))),
    { x: 0.6, y: 1.6, w: 12.1, colW: [3.3, 2.0, 6.8], rowH: 0.52, border: { type: "solid", color: C.line, pt: 0.5 }, valign: "middle", margin: 0.08 });
  txt(s, "Only one final action is allowed per request. Any call after it is blocked.", { x: 0.6, y: 6.5, w: 12, h: 0.4, fontSize: 14, italic: true, color: C.muted });
}

// 7. Money guardrail -----------------------------------------------------
{
  const s = lightSlide();
  title(s, "The LLM never sets the amount"); tag(s, "GUARDRAILS");
  const rules = ["Category is Billing & Autopay Dispute", "Confidence ≥ 0.80", "Claim type is eligible (never goodwill)",
    "Tenant stated a specific amount", "Billing record matches the exact name", "Verified discrepancy > $0",
    "Claim within ±max($2, 10%) of the record", "No prior credit for this customer"];
  rules.forEach((r, i) => {
    const y = 1.65 + i * 0.6;
    badge(s, "✓", 0.7, y, 0.42, C.green);
    txt(s, r, { x: 1.3, y: y + 0.02, w: 6.2, h: 0.42, fontSize: 16, valign: "middle" });
  });
  card(s, 8.0, 1.6, 4.7, 2.3, C.dark);
  txt(s, "Credit = verified record amount", { x: 8.3, y: 1.8, w: 4.2, h: 0.5, fontSize: 18, bold: true, color: C.orange });
  txt(s, "Ravi claims \"$22\" and the record says 22.75, so the credit is $22.75. The tool has no amount parameter, so injection cannot change it.", { x: 8.3, y: 2.35, w: 4.2, h: 1.4, fontSize: 14, color: C.white });
  card(s, 8.0, 4.15, 4.7, 2.35, C.tint);
  txt(s, "> $50 auto-cap → human approval", { x: 8.3, y: 4.35, w: 4.2, h: 0.5, fontSize: 18, bold: true, color: C.orange });
  txt(s, "Percival's ~$310 is verified but becomes pending_approval. A reviewer approves or rejects it in the console. A lock and a unique constraint prevent double payouts.", { x: 8.3, y: 4.9, w: 4.2, h: 1.5, fontSize: 14 });
}

// 8. Defence in depth ----------------------------------------------------
{
  const s = lightSlide();
  title(s, "Defence in depth"); tag(s, "GUARDRAILS");
  const g = [["Protocol order", "The agent can't act before classify → history → context (if unsure) → billing lookup (if a claim)."],
             ["Output checks", "Tenant messages are ≤ 600 characters, mention no invented amounts, and make no refund promises unless a credit was issued."],
             ["Prompt injection", "Tenant text is fenced as untrusted data. Tools take no IDs or amounts, and schemas are strict."],
             ["Uncertainty", "Confidence < 0.55 goes to Human Triage, with no duplicate close and no credit."],
             ["Safety", "A P1 with active damage or a safety risk goes to Facilities Emergency Response and is never auto-closed."],
             ["Safe failure", "Errors, refusals, step budget or no final action: route to Human Triage. Never dropped."]];
  g.forEach(([h, d], i) => {
    const x = 0.6 + (i % 2) * 6.15, y = 1.6 + Math.floor(i / 2) * 1.75;
    card(s, x, y, 5.95, 1.55);
    badge(s, i + 1, x + 0.3, y + 0.5, 0.55);
    txt(s, h, { x: x + 1.1, y: y + 0.2, w: 4.6, h: 0.4, fontSize: 17, bold: true });
    txt(s, d, { x: x + 1.1, y: y + 0.62, w: 4.6, h: 0.85, fontSize: 13, color: C.muted });
  });
  s.addNotes("Blocked attempts are returned to the model as errors it can correct, and stored with allowed=false so reviewers see what was prevented.");
}

// 9. Priority & routing --------------------------------------------------
{
  const s = lightSlide();
  title(s, "Explainable priority and routing", "The LLM extracts signals; deterministic weights turn them into P1–P4"); tag(s, "AI OUTPUT");
  const w = [["active_property_damage", 60], ["safety_risk", 60], ["locked_out", 35], ["time_critical_deadline", 30], ["lien_or_auction_risk", 30],
             ["repeat_unresolved_issue", 25], ["financial_impact_imminent", 20], ["billing_discrepancy", 10], ["informational_only", -15], ["explicitly_not_urgent", -20]];
  s.addChart(pres.charts.BAR, [{ name: "weight", labels: w.map(x => x[0]), values: w.map(x => x[1]) }], {
    x: 0.5, y: 1.75, w: 6.6, h: 4.9, barDir: "bar", chartColors: [C.orange], showValue: true, dataLabelPosition: "outEnd",
    dataLabelFontSize: 10, catAxisLabelFontSize: 11, valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    catAxisOrientation: "maxMin", catAxisLabelPos: "low", showLegend: false, showTitle: true, title: "Signal weights (base 15; P1 ≥ 70, P2 ≥ 40, P3 ≥ 15)", titleFontSize: 12 });
  card(s, 7.5, 1.75, 5.2, 2.2, C.dark);
  txt(s, "TR-6096 · sprinkler dripping, flying out in 1 hour", { x: 7.8, y: 1.95, w: 4.7, h: 0.4, fontSize: 14, bold: true, color: C.orange });
  txt(s, "base 15 | active_property_damage +60 | time_critical_deadline +30 = 105 → P1 → Facilities Emergency Response",
      { x: 7.8, y: 2.45, w: 4.7, h: 1.3, fontFace: "Courier New", fontSize: 13, color: C.white });
  card(s, 7.5, 4.2, 5.2, 2.45);
  txt(s, "TR-6076 · Business Elite, \"no rush at all\"", { x: 7.8, y: 4.4, w: 4.7, h: 0.4, fontSize: 14, bold: true, color: C.orange });
  txt(s, "base 15 | explicitly_not_urgent −20 | tier +5 = 0 → P4. Account tier and capital letters can't jump the queue. Every reviewer sees this arithmetic.",
      { x: 7.8, y: 4.9, w: 4.7, h: 1.6, fontSize: 14 });
}

// 10. Eval ----------------------------------------------------------------
{
  const s = lightSlide();
  title(s, "Measured, not assumed", "Hand-labelled gold outcomes for all 100 requests; the eval gates every prompt or model change"); tag(s, "AI OUTPUT");
  const stats = [["100%", "credit precision target", "zero wrong payouts"], ["100%", "P1 recall target", "no missed emergency"],
                 ["≥ 90%", "category accuracy target", "ambiguous items accept alternatives"], ["38", "automated tests", "every trap + failure mode"]];
  stats.forEach(([n, l, d], i) => {
    const x = 0.6 + i * 3.075;
    card(s, x, 1.9, 2.85, 2.6);
    txt(s, n, { x, y: 2.1, w: 2.85, h: 1.0, fontFace: H, fontSize: 48, bold: true, color: C.orange, align: "center" });
    txt(s, l, { x: x + 0.15, y: 3.15, w: 2.55, h: 0.5, fontSize: 15, bold: true, align: "center" });
    txt(s, d, { x: x + 0.15, y: 3.65, w: 2.55, h: 0.7, fontSize: 12, color: C.muted, align: "center" });
  });
  card(s, 0.6, 4.8, 12.1, 1.8, C.tint);
  txt(s, [{ text: "The eval also reports ", options: {} },
          { text: "priority and action accuracy, strict credit recall, fallbacks, guardrail blocks, average steps, latency and cost per run", options: { bold: true } },
          { text: ". Every classification stores the model's reasoning. When a tool shows it was wrong, the model revises the classification, and every revision is kept." }],
      { x: 0.9, y: 5.0, w: 11.5, h: 1.4, fontSize: 15, valign: "middle" });
  s.addNotes("Replace the targets with the measured Claude numbers from eval/latest_eval.json before presenting.");
}

// 11. Human in the loop --------------------------------------------------
{
  const s = lightSlide();
  title(s, "Humans stay in control", "Streamlit review console");
  const f = [["Priority queue", "Sorted P1 first, with filters by team, category and status"],
             ["See the reasoning", "LLM reasoning, priority arithmetic, routing rule"],
             ["Full tool trace", "Every step, with blocked attempts highlighted"],
             ["Override anything", "Category, priority, team, status; a reviewer and reason are required"],
             ["Credit control", "Reverse issued credits; approve or reject pending ones"],
             ["Audit log", "Every agent action and human change, with who, when and why"]];
  f.forEach(([h, d], i) => {
    const y = 1.75 + i * 0.83;
    badge(s, i + 1, 0.6, y, 0.5);
    txt(s, h, { x: 1.3, y: y - 0.02, w: 3.2, h: 0.5, fontSize: 17, bold: true, valign: "middle" });
    txt(s, d, { x: 4.4, y: y - 0.02, w: 8.3, h: 0.5, fontSize: 15, color: C.muted, valign: "middle" });
  });
}

// 12. Production readiness -----------------------------------------------
{
  const s = lightSlide();
  title(s, "Production readiness"); tag(s, "PRODUCTION");
  const p = [["Reliability", "Retries with backoff, timeouts, refusal fallback, safe fallback to Human Triage"],
             ["Model-agnostic", "LLMClient interface; Claude or offline agent chosen by config"],
             ["Idempotent", "Re-runnable ingest; untriaged requests only; unique credits"],
             ["Observability", "JSON logs with PII redacted; tokens, latency, cost and prompt version per run"],
             ["Cost", "Prompt caching on the system prompt and tools; effort=medium; per-run cost"],
             ["Deployable", "Docker (non-root, healthcheck); SQLite → Postgres by URL; config via env"],
             ["Tested", "38 pytest tests: traps, protocol, failures, overrides, end to end"],
             ["Scalable", "Worker pool today; queue + Batches API + Postgres next"]];
  p.forEach(([h, d], i) => {
    const x = 0.6 + (i % 4) * 3.075, y = 1.6 + Math.floor(i / 4) * 2.55;
    card(s, x, y, 2.85, 2.3);
    txt(s, h, { x: x + 0.25, y: y + 0.25, w: 2.35, h: 0.45, fontSize: 17, bold: true, color: C.orange });
    txt(s, d, { x: x + 0.25, y: y + 0.8, w: 2.35, h: 1.4, fontSize: 13, color: C.text });
  });
}

// 13. Close ----------------------------------------------------------------
{
  const s = pres.addSlide(); s.background = { color: C.dark };
  s.addText("Autonomous where it's safe.\nHuman where it's not.", { x: 0.7, y: 1.5, w: 11.5, h: 2.0, fontFace: H, fontSize: 44, bold: true, color: C.white, margin: 0, isTextBox: true });
  txt(s, "Every decision is explained, measured, and reversible.", { x: 0.7, y: 3.7, w: 11, h: 0.6, fontSize: 22, color: C.orange });
  const d = [["Live demo", "TR-6096 emergency · TR-6013 verified credit · TR-6073 $210 blocked · TR-6070 approval · TR-6039 duplicate"]];
  txt(s, d[0][0], { x: 0.7, y: 4.9, w: 11, h: 0.4, fontSize: 16, bold: true, color: "CBD5DF" });
  txt(s, d[0][1], { x: 0.7, y: 5.35, w: 11.8, h: 0.8, fontSize: 15, color: "8FA1B3" });
}

pres.writeFile({ fileName: "Meridian_AI_Triage.pptx" }).then(f => console.log("wrote", f));
