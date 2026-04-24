#!/usr/bin/env python3
"""
Annotator disagreement analysis
==============================

Builds an end-to-end profile of annotator behavior (strictness/leniency,
observability, risk-flag behavior), computes disagreement metrics on shared
conversations, exports report tables, and writes a concise markdown summary.

Run:
    python dev/annotator_disagreement_analysis.py
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path("/Users/surya/Desktop/riverline_takehome")
ANNOTATIONS_DIR = ROOT / "problem_statement" / "data" / "annotations"
OUT_DIR = ROOT / "dev" / "annotator_diff_output"
README_PATH = ROOT / "ANNOTATOR_DISAGREEMENT_README.md"

ANNOTATORS = ("annotator_1", "annotator_2", "annotator_3")


def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _safe_median(values: list[float]) -> float:
    return statistics.median(values) if values else float("nan")


def _safe_quantiles(values: list[float], n: int = 4) -> list[float]:
    if len(values) < 2:
        return [values[0], values[0], values[0]] if values else [float("nan")] * 3
    q = statistics.quantiles(values, n=n)
    # quartiles: q1, q2, q3
    if n == 4 and len(q) >= 3:
        return [q[0], q[1], q[2]]
    return q


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _pairwise(values: list[Any]) -> list[tuple[Any, Any]]:
    out = []
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            out.append((values[i], values[j]))
    return out


def load_annotations() -> dict[str, list[dict[str, Any]]]:
    data: dict[str, list[dict[str, Any]]] = {}
    for name in ANNOTATORS:
        path = ANNOTATIONS_DIR / f"{name}.jsonl"
        rows = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                rec["risk_flags"] = rec.get("risk_flags", []) or []
                rec["failure_points"] = rec.get("failure_points", []) or []
                rows.append(rec)
        data[name] = rows
    return data


def build_lookup(data: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, dict[str, Any]]]:
    lookup: dict[str, dict[str, dict[str, Any]]] = {}
    for ann, rows in data.items():
        lookup[ann] = {r["conversation_id"]: r for r in rows}
    return lookup


def coverage_and_overlap(lookup: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    sets = {ann: set(rows.keys()) for ann, rows in lookup.items()}
    all_three = set.intersection(*sets.values())
    pair = {}
    for a, b in _pairwise(list(ANNOTATORS)):
        pair[f"{a}__{b}"] = len(sets[a] & sets[b])

    return {
        "counts": {ann: len(sets[ann]) for ann in ANNOTATORS},
        "pairwise_overlap": pair,
        "all_three_overlap": len(all_three),
        "overlap_ids": sorted(all_three),
    }


def annotator_summary_rows(lookup: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for ann in ANNOTATORS:
        recs = list(lookup[ann].values())
        q = [float(r.get("quality_score", 0.0)) for r in recs]
        fp_counts = [len(r.get("failure_points", [])) for r in recs]
        risk_counts = [len(r.get("risk_flags", [])) for r in recs]
        cat_sets = [set(fp.get("category") for fp in r.get("failure_points", []) if fp.get("category")) for r in recs]

        q1, q2, q3 = _safe_quantiles(q, 4)
        rows.append(
            {
                "annotator": ann,
                "n_conversations": len(recs),
                "quality_mean": round(_safe_mean(q), 4),
                "quality_median": round(_safe_median(q), 4),
                "quality_q1": round(q1, 4),
                "quality_q3": round(q3, 4),
                "failure_points_mean": round(_safe_mean([float(x) for x in fp_counts]), 4),
                "failure_points_median": round(_safe_median([float(x) for x in fp_counts]), 4),
                "risk_flags_mean": round(_safe_mean([float(x) for x in risk_counts]), 4),
                "distinct_categories_total": len(set().union(*cat_sets)) if cat_sets else 0,
                "categories_per_conversation_mean": round(_safe_mean([float(len(s)) for s in cat_sets]), 4),
            }
        )
    return rows


def prevalence_tables(lookup: dict[str, dict[str, dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns:
      risk_flag_rows: annotator x flag with counts + rates
      category_rows: annotator x category with raw counts + conv prevalence
    """
    risk_rows = []
    cat_rows = []

    for ann in ANNOTATORS:
        recs = list(lookup[ann].values())
        n = len(recs)

        rf_counter: Counter[str] = Counter()
        rf_conv_counter: Counter[str] = Counter()

        cat_counter: Counter[str] = Counter()
        cat_conv_counter: Counter[str] = Counter()

        for r in recs:
            flags = [f for f in r.get("risk_flags", []) if f]
            rf_counter.update(flags)
            rf_conv_counter.update(set(flags))

            categories = [fp.get("category") for fp in r.get("failure_points", []) if fp.get("category")]
            cat_counter.update(categories)
            cat_conv_counter.update(set(categories))

        for flag, cnt in sorted(rf_counter.items(), key=lambda kv: (-kv[1], kv[0])):
            risk_rows.append(
                {
                    "annotator": ann,
                    "risk_flag": flag,
                    "raw_count": cnt,
                    "conv_count": rf_conv_counter[flag],
                    "conv_rate": round(rf_conv_counter[flag] / n if n else float("nan"), 4),
                }
            )

        for cat, cnt in sorted(cat_counter.items(), key=lambda kv: (-kv[1], kv[0])):
            cat_rows.append(
                {
                    "annotator": ann,
                    "category": cat,
                    "raw_count": cnt,
                    "conv_count": cat_conv_counter[cat],
                    "conv_rate": round(cat_conv_counter[cat] / n if n else float("nan"), 4),
                }
            )

    # add over-index columns vs pooled baseline
    pooled_rf = defaultdict(int)
    pooled_rf_n = defaultdict(int)
    for r in risk_rows:
        pooled_rf[r["risk_flag"]] += r["conv_count"]
        pooled_rf_n[r["risk_flag"]] += 1
    for r in risk_rows:
        baseline = pooled_rf[r["risk_flag"]] / (200 * len(ANNOTATORS))
        r["pooled_conv_rate"] = round(baseline, 4)
        r["lift_vs_pooled"] = round((r["conv_rate"] / baseline) if baseline > 0 else float("nan"), 4)

    pooled_cat = defaultdict(int)
    for r in cat_rows:
        pooled_cat[r["category"]] += r["conv_count"]
    for r in cat_rows:
        baseline = pooled_cat[r["category"]] / (200 * len(ANNOTATORS))
        r["pooled_conv_rate"] = round(baseline, 4)
        r["lift_vs_pooled"] = round((r["conv_rate"] / baseline) if baseline > 0 else float("nan"), 4)

    return risk_rows, cat_rows


def conversation_disagreement_rows(lookup: dict[str, dict[str, dict[str, Any]]], overlap_ids: list[str]) -> list[dict[str, Any]]:
    rows = []

    for cid in overlap_ids:
        q = {}
        fp_cnt = {}
        rf_sets = {}
        cat_sets = {}
        assessments = {}

        for ann in ANNOTATORS:
            rec = lookup[ann][cid]
            q[ann] = float(rec.get("quality_score", 0.0))
            fp_cnt[ann] = len(rec.get("failure_points", []))
            rf_sets[ann] = set(rec.get("risk_flags", []) or [])
            cat_sets[ann] = set(fp.get("category") for fp in rec.get("failure_points", []) if fp.get("category"))
            assessments[ann] = str(rec.get("overall_assessment", "")).strip()

        quality_vals = [q[a] for a in ANNOTATORS]
        quality_range = max(quality_vals) - min(quality_vals)

        fp_vals = [fp_cnt[a] for a in ANNOTATORS]
        fp_range = max(fp_vals) - min(fp_vals)

        rf_jaccs = [_jaccard(rf_sets[a], rf_sets[b]) for a, b in _pairwise(list(ANNOTATORS))]
        cat_jaccs = [_jaccard(cat_sets[a], cat_sets[b]) for a, b in _pairwise(list(ANNOTATORS))]

        rf_jacc_mean = _safe_mean(rf_jaccs)
        cat_jacc_mean = _safe_mean(cat_jaccs)

        assessment_unique = len(set(assessments.values()))
        assessment_match_rate = 1.0 if assessment_unique == 1 else 0.0

        # Composite disagreement score on 0-1 scale
        score = (
            0.35 * min(quality_range, 1.0)
            + 0.20 * min(fp_range / 10.0, 1.0)
            + 0.20 * (1.0 - rf_jacc_mean)
            + 0.20 * (1.0 - cat_jacc_mean)
            + 0.05 * (1.0 - assessment_match_rate)
        )

        if score >= 0.70:
            bucket = "high"
        elif score >= 0.40:
            bucket = "medium"
        else:
            bucket = "low"

        rows.append(
            {
                "conversation_id": cid,
                "quality_annotator_1": q["annotator_1"],
                "quality_annotator_2": q["annotator_2"],
                "quality_annotator_3": q["annotator_3"],
                "quality_range": round(quality_range, 4),
                "failure_count_annotator_1": fp_cnt["annotator_1"],
                "failure_count_annotator_2": fp_cnt["annotator_2"],
                "failure_count_annotator_3": fp_cnt["annotator_3"],
                "failure_count_range": fp_range,
                "risk_flags_annotator_1": ", ".join(sorted(rf_sets["annotator_1"])),
                "risk_flags_annotator_2": ", ".join(sorted(rf_sets["annotator_2"])),
                "risk_flags_annotator_3": ", ".join(sorted(rf_sets["annotator_3"])),
                "risk_jaccard_mean": round(rf_jacc_mean, 4),
                "categories_annotator_1": ", ".join(sorted(cat_sets["annotator_1"])),
                "categories_annotator_2": ", ".join(sorted(cat_sets["annotator_2"])),
                "categories_annotator_3": ", ".join(sorted(cat_sets["annotator_3"])),
                "category_jaccard_mean": round(cat_jacc_mean, 4),
                "overall_assessment_annotator_1": assessments["annotator_1"],
                "overall_assessment_annotator_2": assessments["annotator_2"],
                "overall_assessment_annotator_3": assessments["annotator_3"],
                "overall_assessment_unique_count": assessment_unique,
                "disagreement_score": round(score, 4),
                "disagreement_bucket": bucket,
            }
        )

    return rows


def pairwise_disagreement_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []

    # Need original set columns for pairwise; reconstruct from row text columns safely
    for a, b in _pairwise(list(ANNOTATORS)):
        qdiff = []
        rj = []
        cj = []
        assess_match = 0

        for r in rows:
            qa = r[f"quality_{a}"]
            qb = r[f"quality_{b}"]
            qdiff.append(abs(qa - qb))

            ra = set(x.strip() for x in r[f"risk_flags_{a}"].split(",") if x.strip())
            rb = set(x.strip() for x in r[f"risk_flags_{b}"].split(",") if x.strip())
            ca = set(x.strip() for x in r[f"categories_{a}"].split(",") if x.strip())
            cb = set(x.strip() for x in r[f"categories_{b}"].split(",") if x.strip())

            rj.append(_jaccard(ra, rb))
            cj.append(_jaccard(ca, cb))

            oa = r[f"overall_assessment_{a}"]
            ob = r[f"overall_assessment_{b}"]
            assess_match += int(oa == ob)

        out.append(
            {
                "pair": f"{a}__{b}",
                "n_overlap": len(rows),
                "mean_abs_quality_diff": round(_safe_mean(qdiff), 4),
                "median_abs_quality_diff": round(_safe_median(qdiff), 4),
                "mean_risk_flag_jaccard": round(_safe_mean(rj), 4),
                "mean_category_jaccard": round(_safe_mean(cj), 4),
                "overall_assessment_exact_match_rate": round(assess_match / len(rows) if rows else float("nan"), 4),
            }
        )

    return out


def select_variety_examples(conv_rows: list[dict[str, Any]], k: int = 5) -> list[dict[str, Any]]:
    if not conv_rows:
        return []

    # Helper sorted pick with uniqueness
    selected: list[dict[str, Any]] = []
    used = set()

    def pick_best(key_fn, label: str, rationale_fn):
        for r in sorted(conv_rows, key=key_fn, reverse=True):
            cid = r["conversation_id"]
            if cid in used:
                continue
            r2 = dict(r)
            r2["example_type"] = label
            r2["example_rationale"] = rationale_fn(r)
            selected.append(r2)
            used.add(cid)
            return

    pick_best(
        key_fn=lambda r: (r["quality_range"], r["disagreement_score"]),
        label="quality_score_gap",
        rationale_fn=lambda r: f"Largest quality-score spread ({r['quality_range']:.2f}) across annotators.",
    )
    pick_best(
        key_fn=lambda r: ((1 - r["risk_jaccard_mean"]), r["disagreement_score"]),
        label="risk_flag_mismatch",
        rationale_fn=lambda r: f"Strong risk-flag disagreement (mean Jaccard={r['risk_jaccard_mean']:.2f}).",
    )
    pick_best(
        key_fn=lambda r: (r["failure_count_range"], r["disagreement_score"]),
        label="failure_point_count_gap",
        rationale_fn=lambda r: f"Large failure-point count gap (range={r['failure_count_range']}).",
    )
    pick_best(
        key_fn=lambda r: ((1 - r["category_jaccard_mean"]), r["disagreement_score"]),
        label="failure_category_mismatch",
        rationale_fn=lambda r: f"Different failure categories selected (mean Jaccard={r['category_jaccard_mean']:.2f}).",
    )

    # Explicit overall assessment mismatch example (if available)
    assessment_mismatch = [
        r
        for r in conv_rows
        if r["overall_assessment_unique_count"] > 1 and r["conversation_id"] not in used
    ]
    if assessment_mismatch:
        r = sorted(assessment_mismatch, key=lambda x: x["disagreement_score"], reverse=True)[0]
        r2 = dict(r)
        r2["example_type"] = "overall_assessment_mismatch"
        r2["example_rationale"] = "Annotators gave different overall assessments."
        selected.append(r2)
        used.add(r2["conversation_id"])

    # Fill remaining slots with highest overall disagreement if needed
    for r in sorted(conv_rows, key=lambda x: x["disagreement_score"], reverse=True):
        if len(selected) >= k:
            break
        if r["conversation_id"] in used:
            continue
        r2 = dict(r)
        r2["example_type"] = "high_overall_disagreement"
        r2["example_rationale"] = f"High composite disagreement score ({r['disagreement_score']:.2f})."
        selected.append(r2)
        used.add(r2["conversation_id"])

    return selected[:k]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("")
        return
    fields = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _fmt_pct(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "NA"
    return f"{100*x:.1f}%"


def _top_items(rows: list[dict[str, Any]], annotator: str, key: str, n: int = 3) -> str:
    sub = [r for r in rows if r["annotator"] == annotator]
    sub = sorted(sub, key=lambda r: r[key], reverse=True)[:n]
    if not sub:
        return "None"
    label_key = "risk_flag" if "risk_flag" in sub[0] else "category"
    return "; ".join(f"{r[label_key]} ({_fmt_pct(r['conv_rate'])})" for r in sub)


def generate_markdown(
    coverage: dict[str, Any],
    summary_rows: list[dict[str, Any]],
    pairwise_rows: list[dict[str, Any]],
    risk_rows: list[dict[str, Any]],
    cat_rows: list[dict[str, Any]],
    variety_examples: list[dict[str, Any]],
) -> str:
    smap = {r["annotator"]: r for r in summary_rows}

    lines = []
    lines.append("# ANNOTATOR_DISAGREEMENT_README")
    lines.append("")
    lines.append("## What was analyzed")
    lines.append("")
    lines.append("- Inputs: `problem_statement/data/annotations/annotator_1.jsonl`, `annotator_2.jsonl`, `annotator_3.jsonl`")
    lines.append("- Per annotator: 200 conversations")
    lines.append(f"- 3-way overlap: {coverage['all_three_overlap']} conversations")
    lines.append("")

    lines.append("## How disagreement was measured")
    lines.append("")
    lines.append("- `quality_score` disagreement: pairwise absolute score difference")
    lines.append("- `risk_flags` disagreement: pairwise Jaccard overlap of risk-flag sets")
    lines.append("- failure-point disagreement: count gap + category-set Jaccard")
    lines.append("- overall disagreement bucket: low / medium / high via a composite score")
    lines.append("")

    lines.append("## Annotator personalities (leniency, strictness, observability)")
    lines.append("")
    lines.append("| Annotator | Mean quality | Median quality | Mean failure points | Mean risk flags | Category breadth |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for ann in ANNOTATORS:
        r = smap[ann]
        lines.append(
            f"| {ann} | {r['quality_mean']:.3f} | {r['quality_median']:.3f} | {r['failure_points_mean']:.2f} | {r['risk_flags_mean']:.2f} | {r['distinct_categories_total']} |"
        )
    lines.append("")

    # Personality interpretation
    strict_sorted = sorted(summary_rows, key=lambda r: r["quality_mean"])
    lines.append(f"- **Strictest on quality**: `{strict_sorted[0]['annotator']}` (lowest mean quality).")
    lines.append(f"- **Most lenient**: `{strict_sorted[-1]['annotator']}` (highest mean quality).")

    observant_sorted = sorted(summary_rows, key=lambda r: r["failure_points_mean"], reverse=True)
    lines.append(f"- **Most observant (finds more failures)**: `{observant_sorted[0]['annotator']}`.")
    lines.append("")

    lines.append("### Category and risk-flag tendencies")
    lines.append("")
    for ann in ANNOTATORS:
        lines.append(f"- `{ann}` top failure categories: {_top_items(cat_rows, ann, 'conv_rate', 4)}")
        lines.append(f"- `{ann}` top risk flags: {_top_items(risk_rows, ann, 'conv_rate', 4)}")
    lines.append("")

    lines.append("## Pairwise disagreement summary (on shared conversations)")
    lines.append("")
    lines.append("| Pair | Mean abs quality diff | Mean risk-flag Jaccard | Mean category Jaccard | Overall-assessment exact match |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in pairwise_rows:
        lines.append(
            f"| {r['pair']} | {r['mean_abs_quality_diff']:.3f} | {r['mean_risk_flag_jaccard']:.3f} | {r['mean_category_jaccard']:.3f} | {_fmt_pct(r['overall_assessment_exact_match_rate'])} |"
        )
    lines.append("")

    lines.append("## Five high-variety disagreement examples")
    lines.append("")
    lines.append("These examples were selected to cover different disagreement archetypes, not just the highest score gaps.")
    lines.append("")

    for i, r in enumerate(variety_examples, start=1):
        lines.append(f"### Example {i}: `{r['example_type']}`")
        lines.append(f"- Conversation: `{r['conversation_id']}`")
        lines.append(f"- Why selected: {r['example_rationale']}")
        lines.append(
            f"- Quality scores: A1={r['quality_annotator_1']:.2f}, A2={r['quality_annotator_2']:.2f}, A3={r['quality_annotator_3']:.2f} (range={r['quality_range']:.2f})"
        )
        lines.append(
            f"- Failure points: A1={r['failure_count_annotator_1']}, A2={r['failure_count_annotator_2']}, A3={r['failure_count_annotator_3']} (range={r['failure_count_range']})"
        )
        lines.append(
            f"- Risk-flag overlap (mean Jaccard): {r['risk_jaccard_mean']:.2f}; Category overlap (mean Jaccard): {r['category_jaccard_mean']:.2f}"
        )
        lines.append(
            f"- Overall assessments: A1='{r['overall_assessment_annotator_1']}', A2='{r['overall_assessment_annotator_2']}', A3='{r['overall_assessment_annotator_3']}'"
        )
        lines.append("")

    lines.append("## How disagreement was handled")
    lines.append("")
    lines.append("- Use **median quality score** across annotators for consensus on overlap set.")
    lines.append("- Use **majority vote** for binary/category-level labels when at least 2/3 agree.")
    lines.append("- Keep a **disagreement indicator** (quality range + Jaccard gaps) and treat high-disagreement cases as lower-confidence supervision.")
    lines.append("- Report evaluator alignment both vs consensus and per-annotator slices to avoid overfitting one rater style.")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    data = load_annotations()
    lookup = build_lookup(data)

    coverage = coverage_and_overlap(lookup)
    summary_rows = annotator_summary_rows(lookup)
    risk_rows, cat_rows = prevalence_tables(lookup)

    conv_rows = conversation_disagreement_rows(lookup, coverage["overlap_ids"])
    pairwise_rows = pairwise_disagreement_summary(conv_rows)
    variety_examples = select_variety_examples(conv_rows, k=5)

    # exports
    write_csv(OUT_DIR / "annotator_summary.csv", summary_rows)
    write_csv(OUT_DIR / "risk_flag_prevalence.csv", risk_rows)
    write_csv(OUT_DIR / "category_prevalence.csv", cat_rows)
    write_csv(OUT_DIR / "pairwise_disagreement_metrics.csv", pairwise_rows)
    write_csv(OUT_DIR / "conversation_level_disagreement.csv", sorted(conv_rows, key=lambda r: r["disagreement_score"], reverse=True))
    write_csv(OUT_DIR / "variety_disagreement_examples.csv", variety_examples)

    md = generate_markdown(
        coverage=coverage,
        summary_rows=summary_rows,
        pairwise_rows=pairwise_rows,
        risk_rows=risk_rows,
        cat_rows=cat_rows,
        variety_examples=variety_examples,
    )
    README_PATH.write_text(md, encoding="utf-8")

    print("Wrote outputs to:", OUT_DIR)
    print("Wrote markdown:", README_PATH)


if __name__ == "__main__":
    main()
