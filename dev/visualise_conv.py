# 1) Setup and Data Loading

from pathlib import Path
import json
import re
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

pd.set_option("display.max_columns", 200)
pd.set_option("display.max_colwidth", 160)

ROOT = Path("../problem_statement")
DATA_DIR = ROOT / "data"
ANNOT_DIR = DATA_DIR / "annotations"


def read_jsonl(path: Path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

logs = read_jsonl(DATA_DIR / "production_logs.jsonl")
outcomes = read_jsonl(DATA_DIR / "outcomes.jsonl")
a1 = read_jsonl(ANNOT_DIR / "annotator_1.jsonl")
a2 = read_jsonl(ANNOT_DIR / "annotator_2.jsonl")
a3 = read_jsonl(ANNOT_DIR / "annotator_3.jsonl")

print(f"logs: {len(logs)}")
print(f"outcomes: {len(outcomes)}")
print(f"annotator_1: {len(a1)}")
print(f"annotator_2: {len(a2)}")
print(f"annotator_3: {len(a3)}")

### ------------- Build Unified Conversation Bundle ------------- ###

# 21) Fixed Visualizer: Card-Per-Turn Layout (v3)
# Replaces render_conversation_clean. Call: show_conversation("<id>")

import html as _html
from IPython.display import display, HTML


STATE_ID_MAP = {
    "new": 0, "message_received": 1, "verification": 2, "intent_asked": 3,
    "settlement_explained": 4, "amount_pending": 5, "amount_sent": 6,
    "date_amount_asked": 7, "payment_confirmed": 8, "escalated": 9, "dormant": 10,
}

CLS_COLORS = {
    "unclear":            ("#fde68a", "#92400e"),
    "wants_settlement":   ("#d1fae5", "#065f46"),
    "wants_closure":      ("#cffafe", "#164e63"),
    "refuses":            ("#fee2e2", "#991b1b"),
    "disputes":           ("#ffe4e6", "#9f1239"),
    "hardship":           ("#fce7f3", "#9d174d"),
    "asks_time":          ("#e0e7ff", "#3730a3"),
}

CONF_COLORS = {
    "high":   ("#bbf7d0", "#14532d"),
    "medium": ("#fef3c7", "#78350f"),
    "low":    ("#fee2e2", "#7f1d1d"),
}


def _h(v):
    return _html.escape(str(v)) if v is not None else ""

def _annotate(s):
    if not isinstance(s, str):
        return str(s)
    sid = STATE_ID_MAP.get(s.strip())
    return f"{s}[{sid}]" if sid is not None else s

def _tiny_badge(text, bg="#f3f4f6", fg="#374151", bold=False):
    bw = "font-weight:600;" if bold else ""
    return (
        f"<span style='display:inline-block;padding:1px 7px;border-radius:4px;"
        f"background:{bg};color:{fg};font-size:11px;{bw}'>{_h(text)}</span>"
    )

def _pill(text, bg="#f3f4f6", fg="#374151"):
    return (
        f"<span style='display:inline-block;padding:2px 9px;border-radius:999px;"
        f"background:{bg};color:{fg};font-size:11px;margin:1px 2px 1px 0;line-height:1.4'>{_h(text)}</span>"
    )

def _kv_row(k, v, striped=False):
    bg = "background:#fafafa;" if striped else ""
    return (
        f"<tr style='{bg}'>"
        f"<td style='padding:4px 10px;border-bottom:1px solid #f0f0f0;color:#6b7280;font-size:12px;"
        f"white-space:nowrap;font-weight:600;vertical-align:top'>{_h(k)}</td>"
        f"<td style='padding:4px 10px;border-bottom:1px solid #f0f0f0;font-size:12px;word-break:break-word'>{_h(v)}</td>"
        f"</tr>"
    )


def _ensure_bundle():
    global conv_bundle
    if "conv_bundle" in globals() and isinstance(conv_bundle, dict) and conv_bundle:
        return conv_bundle
    log_map     = {x["conversation_id"]: x for x in logs}
    outcome_map = {x["conversation_id"]: x for x in outcomes}
    a1_map      = {x["conversation_id"]: x for x in a1}
    a2_map      = {x["conversation_id"]: x for x in a2}
    a3_map      = {x["conversation_id"]: x for x in a3}
    all_ids = sorted(set(log_map)|set(outcome_map)|set(a1_map)|set(a2_map)|set(a3_map))
    conv_bundle = {}
    for cid in all_ids:
        conv_bundle[cid] = dict(
            conversation_id=cid,
            prod_log=log_map.get(cid),
            outcome=outcome_map.get(cid),
            an1=a1_map.get(cid), an2=a2_map.get(cid), an3=a3_map.get(cid),
        )
    return conv_bundle


def _build_turns(prod_log):
    messages      = sorted(prod_log.get("messages", []) or [], key=lambda x: (x.get("turn",-1), x.get("timestamp","")))
    cls_map       = {c["turn"]: c for c in (prod_log.get("bot_classifications", []) or [])}
    trans_map     = {}
    for t in (prod_log.get("state_transitions", []) or []):
        trans_map.setdefault(t["turn"], []).append(t)
    fn_map        = {}
    for f in (prod_log.get("function_calls", []) or []):
        fn_map.setdefault(f["turn"], []).append(f)

    out = []
    for m in messages:
        t = m.get("turn")
        c = cls_map.get(t, {}) if m.get("role") == "borrower" else {}
        out.append(dict(
            turn=t,
            timestamp=m.get("timestamp",""),
            role=m.get("role",""),
            text=m.get("text",""),
            cls=c.get("classification"),
            conf=c.get("confidence"),
            transitions=trans_map.get(t, []),
            functions=fn_map.get(t, []),
        ))
    return out


def _build_ann_turn_map(ann_data):
    """Build {turn: [failure_point_dict, ...]} from one annotator's data."""
    if not ann_data:
        return {}
    fps = ann_data.get("failure_points") or []
    m = {}
    for fp in fps:
        t = fp.get("turn")
        if t is not None:
            m.setdefault(t, []).append(fp)
    return m


# Annotator color scheme: a1=red, a2=blue, a3=green
ANN_COLORS = {
    "AN1": {"strong": "#dc2626", "light": "#fca5a5", "bg": "#fef2f2", "border": "#fecaca"},
    "AN2": {"strong": "#2563eb", "light": "#93c5fd", "bg": "#eff6ff", "border": "#bfdbfe"},
    "AN3": {"strong": "#16a34a", "light": "#86efac", "bg": "#f0fdf4", "border": "#bbf7d0"},
}


def _render_turn_annotations(turn, ann_turn_maps):
    """Render annotation failure points for a specific turn across all annotators."""
    parts = []
    for label, tmap in ann_turn_maps:
        fps = tmap.get(turn, [])
        if not fps:
            continue
        colors = ANN_COLORS[label]
        for fp in fps:
            cat = fp.get("category", "")
            sev = fp.get("severity", "")
            note = fp.get("note", "")
            parts.append(
                f"<div style='margin:3px 0;padding:4px 8px;border-left:3px solid {colors['strong']};"
                f"background:{colors['bg']};border-radius:0 4px 4px 0'>"
                f"<span style='font-size:11px;font-weight:700;color:{colors['strong']}'>{label}</span>"
                f"<span style='font-size:11px;color:#374151;margin-left:6px'>{_h(cat)}</span>"
                f"<span style='font-size:10px;color:{colors['strong']};margin-left:6px;font-weight:600'>sev: {_h(sev)}</span>"
                f"<div style='font-size:10px;color:#6b7280;margin-top:2px;line-height:1.3'>{_h(note)}</div>"
                f"</div>"
            )
    return "".join(parts)


def _render_annotation_summary(label, ann, colors):
    """Render compact annotator summary line for header."""
    if ann is None:
        return (
            f"<div style='font-size:11px;color:#9ca3af;margin:2px 0'>"
            f"<b style='color:{colors['strong']}'>{label}</b>: not annotated"
            f"</div>"
        )

    qs = ann.get("quality_score")
    flags = ann.get("risk_flags") or []
    fps_count = len(ann.get("failure_points") or [])
    assessment = ann.get("overall_assessment") or "none"
    flags_str = ", ".join(flags) if flags else "none"
    return (
        f"<div style='font-size:11px;margin:3px 0;line-height:1.4'>"
        f"<b style='color:{colors['strong']}'>{label}</b> "
        f"score=<b>{qs}</b> flags=[{_h(flags_str)}] failures={fps_count}<br>"
        f"<span style='color:#6b7280'><b>overall_assessment:</b> {_h(assessment)}</span>"
        f"</div>"
    )


def show_conversation(conversation_id: str):
    bundle = _ensure_bundle()
    row = bundle.get(conversation_id)
    if not row:
        print(f"Not found: {conversation_id}")
        return

    prod_log = row.get("prod_log")
    outcome  = row.get("outcome") or {}
    an1_r, an2_r, an3_r = row.get("an1"), row.get("an2"), row.get("an3")
    if not prod_log:
        print(f"No prod_log for: {conversation_id}")
        return

    meta  = prod_log.get("metadata", {}) or {}
    turns = _build_turns(prod_log)

    # Pre-build per-turn annotation maps
    ann_turn_maps = [
        ("AN1", _build_ann_turn_map(an1_r)),
        ("AN2", _build_ann_turn_map(an2_r)),
        ("AN3", _build_ann_turn_map(an3_r)),
    ]

    # ── Header ─────────────────────────────────────────────────────────────
    pr  = _pill(f"payment_received={outcome.get('payment_received')}", "#dcfce7", "#166534")
    cmp = _pill(f"complained={outcome.get('borrower_complained')}", "#fee2e2", "#991b1b")
    reg = _pill(f"regulatory_flag={outcome.get('regulatory_flag')}", "#fef3c7", "#92400e")
    att = _pill(f"channel_attribution={outcome.get('channel_attribution')}", "#e0f2fe", "#075985")

    # Annotation summary for header
    ann_summary_parts = []
    for label, ann, colors_key in [("AN1", an1_r, "AN1"), ("AN2", an2_r, "AN2"), ("AN3", an3_r, "AN3")]:
        colors = ANN_COLORS[colors_key]
        ann_summary_parts.append(_render_annotation_summary(label, ann, colors))
    ann_summary_html = "".join(ann_summary_parts)

    header = f"""
    <div style='border:2px solid #d1d5db;border-radius:10px;padding:12px 16px;margin:0 0 12px 0;'>
      <div style='font-size:16px;font-weight:700;margin-bottom:6px;'>
        Conversation: <code style='font-size:14px'>{_h(conversation_id)}</code>
      </div>
      <div style='font-size:13px;line-height:1.8'>
        <b>Language:</b> {_h(meta.get('language'))} &nbsp;|&nbsp;
        <b>Zone:</b> {_h(meta.get('zone'))} &nbsp;|&nbsp;
        <b>DPD:</b> {_h(meta.get('dpd'))} &nbsp;|&nbsp;
        <b>POS / TOS:</b> ₹{_h(meta.get('pos'))} / ₹{_h(meta.get('tos'))} &nbsp;|&nbsp;
        <b>Total Turns:</b> {_h(meta.get('total_turns'))}
      </div>
      <div style='margin-top:6px'><b style='font-size:13px'>Outcome: </b>{pr}{cmp}{reg}{att}</div>
      <div style='margin-top:6px;padding-top:6px;border-top:1px solid #e5e7eb'>
        <b style='font-size:13px'>Annotations: </b>{ann_summary_html}
      </div>
    </div>"""
    display(HTML(header))

    # ── State legend ───────────────────────────────────────────────────────
    legend = "  ".join(f"<b style='font-size:11px'>[{v}]</b> {_h(k)}" for k, v in STATE_ID_MAP.items())
    display(HTML(f"<div style='font-size:11px;color:#6b7280;margin:0 0 10px 0'><b>States: </b>{legend}</div>"))

    # ── Turn cards ─────────────────────────────────────────────────────────
    cards = []
    for r in turns:
        role = r["role"]

        if role == "bot":
            role_badge = "<span style='padding:1px 10px;border-radius:999px;background:#dbeafe;color:#1d4ed8;font-weight:700;font-size:12px'>bot</span>"
            row_bg     = "#f8faff"
        else:
            role_badge = "<span style='padding:1px 10px;border-radius:999px;background:#dcfce7;color:#166534;font-weight:700;font-size:12px'>borrower</span>"
            row_bg     = "#f8fff9"

        ts = str(r["timestamp"]).replace("T", " ")
        left = (
            f"<div style='min-width:80px;width:80px;padding:8px 10px;border-right:2px solid #e5e7eb;"
            f"background:{row_bg};display:flex;flex-direction:column;align-items:center;gap:4px;"
            f"justify-content:flex-start'>"
            f"<span style='font-size:16px;font-weight:700;color:#374151'>{_h(r['turn'])}</span>"
            f"{role_badge}"
            f"<span style='font-size:10px;color:#9ca3af;margin-top:2px;text-align:center'>{_h(ts)}</span>"
            f"</div>"
        )

        text_block = f"<div style='font-size:13px;line-height:1.6;color:#111827;padding:10px 14px'>{_h(r['text'])}</div>"

        # --- right info panel (classification + transitions + functions)
        right_parts = []

        if r.get("cls"):
            cls_bg, cls_fg = CLS_COLORS.get(r["cls"], ("#f3f4f6", "#374151"))
            conf_bg, conf_fg = CONF_COLORS.get(r.get("conf",""), ("#f3f4f6", "#374151"))
            right_parts.append(
                f"<div style='margin-bottom:5px'>"
                f"<span style='padding:2px 9px;border-radius:999px;background:{cls_bg};color:{cls_fg};"
                f"font-size:12px;font-weight:600'>{_h(r['cls'])}</span>"
                f"<span style='padding:2px 8px;border-radius:999px;background:{conf_bg};color:{conf_fg};"
                f"font-size:11px;margin-left:4px'>{_h(r.get('conf'))}</span>"
                f"</div>"
            )

        for t in r.get("transitions", []):
            src = _annotate(t.get("from_state",""))
            tgt = _annotate(t.get("to_state",""))
            reason = t.get("reason", "")
            right_parts.append(
                f"<div style='font-size:11px;font-family:monospace;color:#374151;"
                f"background:#f0fdf4;border:1px solid #bbf7d0;border-radius:4px;"
                f"padding:3px 8px;margin:2px 0;white-space:normal'>"
                f"{_h(src)} <b style='color:#059669'>→</b> {_h(tgt)}"
                f"<div style='font-size:10px;color:#6b7280;margin-top:2px;font-family:inherit'>"
                f"reason: {_h(reason)}</div>"
                f"</div>"
            )

        for fn in r.get("functions", []):
            fname  = fn.get("function","")
            params = fn.get("params", {})
            param_str = "  ".join(f"{_h(k)}: {_h(v)}" for k, v in params.items())
            right_parts.append(
                f"<div style='font-size:11px;font-family:monospace;color:#1e1b4b;"
                f"background:#eef2ff;border:1px solid #c7d2fe;border-radius:4px;"
                f"padding:3px 8px;margin:2px 0'>"
                f"<b style='color:#4338ca'>{_h(fname)}</b><br>"
                f"<span style='color:#6366f1;font-size:10px'>{param_str}</span>"
                f"</div>"
            )

        right_panel = (
            f"<div style='min-width:260px;width:260px;padding:8px 10px;"
            f"border-left:2px solid #e5e7eb;background:{row_bg}'>"
            + ("".join(right_parts) if right_parts else "<span style='color:#d1d5db;font-size:11px'>—</span>")
            + "</div>"
        )

        # --- annotation column (per-turn failure points from a1/a2/a3)
        ann_html = _render_turn_annotations(r["turn"], ann_turn_maps)
        ann_panel = (
            f"<div style='min-width:280px;width:280px;padding:6px 8px;"
            f"border-left:2px solid #e5e7eb;background:{row_bg};overflow-y:auto'>"
            + (ann_html if ann_html else "<span style='color:#d1d5db;font-size:11px'>—</span>")
            + "</div>"
        )

        card = (
            f"<div style='display:flex;border-bottom:1px solid #e5e7eb;background:{row_bg}'>"
            f"{left}"
            f"<div style='flex:1;min-width:0'>{text_block}</div>"
            f"{right_panel}"
            f"{ann_panel}"
            f"</div>"
        )
        cards.append(card)

    timeline = (
        "<div style='border:2px solid #d1d5db;border-radius:10px;overflow:hidden;margin-bottom:14px;'>"
        + "".join(cards) + "</div>"
    )
    display(HTML(timeline))

    # ── Metadata + Outcome ─────────────────────────────────────────────────
    meta_rows = "".join(_kv_row(k, v, i%2==1) for i,(k,v) in enumerate(meta.items()))
    out_items = {k:v for k,v in outcome.items() if k != "conversation_id"}
    out_rows  = "".join(_kv_row(k, v, i%2==1) for i,(k,v) in enumerate(out_items.items()))

    two_col = f"""
    <div style='display:flex;gap:14px;margin-bottom:14px;align-items:flex-start;'>
      <div style='flex:1;border:1px solid #d1d5db;border-radius:8px;overflow:hidden;'>
        <div style='padding:6px 10px;background:#f9fafb;font-weight:700;font-size:13px;border-bottom:1px solid #d1d5db'>Metadata</div>
        <table style='width:100%;border-collapse:collapse'>{meta_rows}</table>
      </div>
      <div style='flex:1;border:1px solid #d1d5db;border-radius:8px;overflow:hidden;'>
        <div style='padding:6px 10px;background:#f9fafb;font-weight:700;font-size:13px;border-bottom:1px solid #d1d5db'>Outcome</div>
        <table style='width:100%;border-collapse:collapse'>{out_rows}</table>
      </div>
    </div>"""
    display(HTML(two_col))


print("show_conversation('<conversation_id>') is ready")