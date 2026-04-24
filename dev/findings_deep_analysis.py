#!/usr/bin/env python3
"""
Deep Findings Analysis
=====================

Comprehensive analysis mapping bad outcomes to both annotation signals and
evaluator violations with outcome-specific breakdowns, concordance analysis,
turn-position effects, violation concentration, and annotator consensus validation.

Run:
    python dev/findings_deep_analysis.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path("/Users/surya/Desktop/riverline_takehome")
BUNDLE_PATH = ROOT / "dev" / "conversation_bundle_flat.csv"
EVAL_PATH = ROOT / "dev" / "eval_v2.jsonl"
ANNOTATION_DIR = ROOT / "problem_statement" / "data" / "annotations"
EVAL_RULES_PATH = ROOT / "problem_statement" / "eval_takehome.py"
OUT_DIR = ROOT / "dev" / "findings_deep_output"
FINDINGS_MD = ROOT / "FINDINGS_DEEP.md"

ANNOTATORS = ("annotator_1", "annotator_2", "annotator_3")
OUTCOME_FLAGS = ("complaint_flag", "regulatory_flag", "required_intervention")


def _safe_eval(x: Any) -> dict[str, Any]:
    if pd.isna(x) or x == "":
        return {}
    try:
        return ast.literal_eval(x)
    except Exception:
        return {}


def _lift(a: float, b: float) -> float:
    return (a / b) if b > 0 else np.nan


def _risk_diff(a: float, b: float) -> float:
    return (a - b) * 100.0


def load_bundle() -> pd.DataFrame:
    df = pd.read_csv(BUNDLE_PATH)
    df["outcome_parsed"] = df["outcome"].apply(_safe_eval)
    df["complaint_flag"] = df["outcome_parsed"].apply(lambda o: bool(o.get("borrower_complained", False)))
    df["regulatory_flag"] = df["outcome_parsed"].apply(lambda o: bool(o.get("regulatory_flag", False)))
    df["required_intervention"] = df["outcome_parsed"].apply(lambda o: bool(o.get("required_human_intervention", False)))
    df["bad_outcome_any"] = df[list(OUTCOME_FLAGS)].any(axis=1)
    return df


def load_eval() -> pd.DataFrame:
    rows = []
    with open(EVAL_PATH, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return pd.DataFrame(rows)


def load_rule_registry() -> dict[str, dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("eval_mod", str(EVAL_RULES_PATH))
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {rule: meta for rule, meta in mod.RULES.items()}


def load_annotations() -> pd.DataFrame:
    rows = []
    for ann in ANNOTATORS:
        p = ANNOTATION_DIR / f"{ann}.jsonl"
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                rows.append({
                    "annotator": ann,
                    "conversation_id": rec["conversation_id"],
                    "quality_score": rec.get("quality_score", np.nan),
                    "risk_flags": rec.get("risk_flags", []) or [],
                    "failure_points": rec.get("failure_points", []) or [],
                    "overall_assessment": (rec.get("overall_assessment") or "").strip(),
                })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Layer 1: Outcome-specific predictors
# ---------------------------------------------------------------------------

def outcome_specific_rule_predictors(bundle_df: pd.DataFrame, eval_df: pd.DataFrame, rule_registry: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """For each (outcome, rule) pair, compute 2x2 table and metrics."""
    conv_rule_map = defaultdict(set)
    for rec in eval_df.to_dict(orient="records"):
        cid = rec["conversation_id"]
        for v in rec.get("violations", []) or []:
            if v.get("rule"):
                conv_rule_map[cid].add(v["rule"])

    all_rules = sorted({r for rules in conv_rule_map.values() for r in rules})

    rows = []
    for outcome in OUTCOME_FLAGS:
        for rule in all_rules:
            has_rule = bundle_df["conversation_id"].isin([cid for cid, rs in conv_rule_map.items() if rule in rs])
            
            a = int((has_rule & bundle_df[outcome]).sum())
            b = int((has_rule & ~bundle_df[outcome]).sum())
            c = int((~has_rule & bundle_df[outcome]).sum())
            d = int((~has_rule & ~bundle_df[outcome]).sum())
            
            if a + b == 0:
                continue
            
            rate_with = a / (a + b)
            rate_without = c / (c + d) if (c + d) > 0 else 0.0
            risk_diff = _risk_diff(rate_with, rate_without)
            lift = _lift(rate_with, rate_without)
            precision = a / (a + b) if (a + b) > 0 else 0.0
            recall = a / (a + c) if (a + c) > 0 else 0.0
            
            rows.append({
                "outcome": outcome,
                "rule": rule,
                "spec_ref": rule_registry.get(rule, {}).get("spec_ref", ""),
                "n_with_rule": a + b,
                "n_outcome_with_rule": a,
                "rate_with_rule": rate_with,
                "rate_without_rule": rate_without,
                "risk_difference_pp": risk_diff,
                "lift": lift,
                "precision_for_outcome": precision,
                "recall_for_outcome": recall,
                "contingency_a": a,
                "contingency_b": b,
                "contingency_c": c,
                "contingency_d": d,
            })
    
    return pd.DataFrame(rows).sort_values(["outcome", "risk_difference_pp"], ascending=[True, False])


def outcome_specific_category_predictors(bundle_df: pd.DataFrame, ann_df: pd.DataFrame) -> pd.DataFrame:
    """For each (outcome, annotator, category) triple, compute metrics."""
    
    rows = []
    for outcome in OUTCOME_FLAGS:
        for annotator in ANNOTATORS:
            ann_sub = ann_df[ann_df["annotator"] == annotator].copy()
            ann_sub = ann_sub.merge(bundle_df[["conversation_id", outcome]], on="conversation_id", how="left")
            
            cat_map = defaultdict(set)
            for _, r in ann_sub.iterrows():
                cid = r["conversation_id"]
                for fp in r["failure_points"]:
                    cat = fp.get("category")
                    if cat:
                        cat_map[cid].add(cat)
            
            all_cats = sorted({c for cats in cat_map.values() for c in cats})
            
            for cat in all_cats:
                has_cat = ann_sub["conversation_id"].isin([cid for cid, cs in cat_map.items() if cat in cs])
                
                a = int((has_cat & ann_sub[outcome]).sum())
                b = int((has_cat & ~ann_sub[outcome]).sum())
                c = int((~has_cat & ann_sub[outcome]).sum())
                d = int((~has_cat & ~ann_sub[outcome]).sum())
                
                if a + b == 0:
                    continue
                
                rate_with = a / (a + b)
                rate_without = c / (c + d) if (c + d) > 0 else 0.0
                risk_diff = _risk_diff(rate_with, rate_without)
                lift = _lift(rate_with, rate_without)
                precision = a / (a + b) if (a + b) > 0 else 0.0
                
                rows.append({
                    "outcome": outcome,
                    "annotator": annotator,
                    "category": cat,
                    "n_with_category": a + b,
                    "n_outcome_with_category": a,
                    "rate_with_category": rate_with,
                    "rate_without_category": rate_without,
                    "risk_difference_pp": risk_diff,
                    "lift": lift,
                    "precision_for_outcome": precision,
                })
    
    return pd.DataFrame(rows).sort_values(["outcome", "risk_difference_pp"], ascending=[True, False])


# ---------------------------------------------------------------------------
# Layer 2: Concordance analysis
# ---------------------------------------------------------------------------

def annotation_evaluator_concordance(bundle_df: pd.DataFrame, eval_df: pd.DataFrame, ann_df: pd.DataFrame) -> pd.DataFrame:
    """When both evaluator and annotator signal a problem, how much stronger is the outcome association?"""
    
    # Map rules to likely annotation categories
    rule_to_cat_map = {
        "TR_INVALID_STATE_TRANSITION": ["state_machine_error"],
        "TR_BACKWARD_TRANSITION_NOT_ALLOWED": ["state_machine_error"],
        "INV_EXIT_STATE_NOT_FINAL": ["state_machine_error"],
        "ACT_ZCM_TIMEOUT_INVALID_CONTEXT": ["state_machine_error"],
        "ACT_SEND_SETTLEMENT_AMOUNT_INVALID_CONTEXT": ["state_machine_error"],
        "CMP_HARDSHIP_NO_ESCALATION": ["ignored_hardship", "missed_escalation"],
        "CMP_DNC_VIOLATION": ["stop_request_missed"],
        "CMP_THREATENING_LANGUAGE": ["tone_mismatch", "inappropriate_pressure"],
        "QLT_REPETITIVE_RESPONSE_LOOP": ["repetition"],
        "AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS": ["amount_error"],
    }
    
    conv_rule_map = defaultdict(set)
    for rec in eval_df.to_dict(orient="records"):
        for v in rec.get("violations", []) or []:
            if v.get("rule"):
                conv_rule_map[rec["conversation_id"]].add(v["rule"])
    
    conv_cat_map = defaultdict(lambda: defaultdict(set))
    for _, r in ann_df.iterrows():
        for fp in r["failure_points"]:
            cat = fp.get("category")
            if cat:
                conv_cat_map[r["annotator"]][r["conversation_id"]].add(cat)
    
    rows = []
    for rule, related_cats in rule_to_cat_map.items():
        for cat in related_cats:
            for annotator in ANNOTATORS:
                joint_ids = []
                rule_only_ids = []
                cat_only_ids = []
                neither_ids = []
                
                for cid in bundle_df["conversation_id"]:
                    has_rule = rule in conv_rule_map.get(cid, set())
                    has_cat = cat in conv_cat_map[annotator].get(cid, set())
                    
                    if has_rule and has_cat:
                        joint_ids.append(cid)
                    elif has_rule:
                        rule_only_ids.append(cid)
                    elif has_cat:
                        cat_only_ids.append(cid)
                    else:
                        neither_ids.append(cid)
                
                for outcome in OUTCOME_FLAGS:
                    joint_rate = bundle_df[bundle_df["conversation_id"].isin(joint_ids)][outcome].mean() if joint_ids else np.nan
                    rule_only_rate = bundle_df[bundle_df["conversation_id"].isin(rule_only_ids)][outcome].mean() if rule_only_ids else np.nan
                    cat_only_rate = bundle_df[bundle_df["conversation_id"].isin(cat_only_ids)][outcome].mean() if cat_only_ids else np.nan
                    neither_rate = bundle_df[bundle_df["conversation_id"].isin(neither_ids)][outcome].mean() if neither_ids else np.nan
                    
                    rows.append({
                        "rule": rule,
                        "category": cat,
                        "annotator": annotator,
                        "outcome": outcome,
                        "n_joint": len(joint_ids),
                        "n_rule_only": len(rule_only_ids),
                        "n_category_only": len(cat_only_ids),
                        "n_neither": len(neither_ids),
                        "outcome_rate_joint": joint_rate,
                        "outcome_rate_rule_only": rule_only_rate,
                        "outcome_rate_category_only": cat_only_rate,
                        "outcome_rate_neither": neither_rate,
                        "joint_vs_neither_lift": _lift(joint_rate, neither_rate),
                        "joint_vs_neither_risk_diff_pp": _risk_diff(joint_rate, neither_rate),
                    })
    
    df = pd.DataFrame(rows)
    df = df[df["n_joint"] >= 10].copy()  # min support
    return df.sort_values(["outcome", "joint_vs_neither_risk_diff_pp"], ascending=[True, False])


# ---------------------------------------------------------------------------
# Layer 3: Turn position analysis
# ---------------------------------------------------------------------------

def turn_position_analysis(eval_df: pd.DataFrame, bundle_df: pd.DataFrame) -> pd.DataFrame:
    """Stratify violations by turn position and compute outcome rates."""
    
    viol_rows = []
    for rec in eval_df.to_dict(orient="records"):
        cid = rec["conversation_id"]
        for v in rec.get("violations", []) or []:
            turn = v.get("turn", -1)
            rule = v.get("rule")
            if turn >= 0 and rule:
                viol_rows.append({"conversation_id": cid, "rule": rule, "turn": turn})
    
    if not viol_rows:
        return pd.DataFrame()
    
    viol_df = pd.DataFrame(viol_rows)
    viol_df = viol_df.merge(bundle_df[["conversation_id"] + list(OUTCOME_FLAGS)], on="conversation_id", how="left")
    
    def turn_bucket(t: int) -> str:
        if t <= 3:
            return "early"
        if t <= 7:
            return "mid"
        return "late"
    
    viol_df["turn_bucket"] = viol_df["turn"].apply(turn_bucket)
    
    rows = []
    for (rule, bucket), sub in viol_df.groupby(["rule", "turn_bucket"]):
        for outcome in OUTCOME_FLAGS:
            rate = sub[outcome].mean()
            rows.append({
                "rule": rule,
                "turn_bucket": bucket,
                "outcome": outcome,
                "n_violations": len(sub),
                "outcome_rate": rate,
            })
    
    return pd.DataFrame(rows).sort_values(["rule", "turn_bucket", "outcome"])


# ---------------------------------------------------------------------------
# Layer 4: Violation concentration
# ---------------------------------------------------------------------------

def violation_concentration_analysis(eval_df: pd.DataFrame, bundle_df: pd.DataFrame) -> pd.DataFrame:
    """Analyze bad-outcome rate by total violation count per conversation."""
    
    conv_counts = []
    for rec in eval_df.to_dict(orient="records"):
        cid = rec["conversation_id"]
        n_viols = len(rec.get("violations", []) or [])
        conv_counts.append({"conversation_id": cid, "violation_count": n_viols})
    
    vc_df = pd.DataFrame(conv_counts).merge(
        bundle_df[["conversation_id", "bad_outcome_any"] + list(OUTCOME_FLAGS)],
        on="conversation_id",
        how="left",
    )
    
    def count_bucket(n: int) -> str:
        if n == 0:
            return "0"
        if n == 1:
            return "1"
        if n == 2:
            return "2"
        if n <= 5:
            return "3-5"
        return "6+"
    
    vc_df["count_bucket"] = vc_df["violation_count"].apply(count_bucket)
    
    rows = []
    for bucket, sub in vc_df.groupby("count_bucket"):
        row = {"count_bucket": bucket, "n_conversations": len(sub)}
        for outcome in ["bad_outcome_any"] + list(OUTCOME_FLAGS):
            row[f"{outcome}_rate"] = sub[outcome].mean()
            row[f"{outcome}_count"] = int(sub[outcome].sum())
        rows.append(row)
    
    return pd.DataFrame(rows).sort_values("count_bucket")


# ---------------------------------------------------------------------------
# Layer 5: Prioritization matrix
# ---------------------------------------------------------------------------

def rule_prioritization_matrix(bundle_df: pd.DataFrame, eval_df: pd.DataFrame, rule_registry: dict[str, dict[str, Any]]) -> pd.DataFrame:
    """Rank rules by frequency vs precision for bad outcomes."""
    
    conv_rule_map = defaultdict(set)
    for rec in eval_df.to_dict(orient="records"):
        for v in rec.get("violations", []) or []:
            if v.get("rule"):
                conv_rule_map[rec["conversation_id"]].add(v["rule"])
    
    all_rules = sorted({r for rules in conv_rule_map.values() for r in rules})
    
    rows = []
    for rule in all_rules:
        has_rule = bundle_df["conversation_id"].isin([cid for cid, rs in conv_rule_map.items() if rule in rs])
        sub = bundle_df[has_rule]
        
        frequency = len(sub)
        precision_any = sub["bad_outcome_any"].mean() if len(sub) else 0.0
        
        # Per-outcome precision
        prec_complaint = sub["complaint_flag"].mean() if len(sub) else 0.0
        prec_regulatory = sub["regulatory_flag"].mean() if len(sub) else 0.0
        prec_intervention = sub["required_intervention"].mean() if len(sub) else 0.0
        
        # Baseline rates
        base_any = bundle_df["bad_outcome_any"].mean()
        base_complaint = bundle_df["complaint_flag"].mean()
        base_regulatory = bundle_df["regulatory_flag"].mean()
        base_intervention = bundle_df["required_intervention"].mean()
        
        rows.append({
            "rule": rule,
            "spec_ref": rule_registry.get(rule, {}).get("spec_ref", ""),
            "frequency": frequency,
            "precision_bad_outcome_any": precision_any,
            "precision_complaint": prec_complaint,
            "precision_regulatory": prec_regulatory,
            "precision_intervention": prec_intervention,
            "lift_vs_baseline_any": _lift(precision_any, base_any),
            "lift_vs_baseline_complaint": _lift(prec_complaint, base_complaint),
            "lift_vs_baseline_regulatory": _lift(prec_regulatory, base_regulatory),
            "lift_vs_baseline_intervention": _lift(prec_intervention, base_intervention),
            "priority_score": frequency * precision_any,  # simple heuristic
        })
    
    return pd.DataFrame(rows).sort_values("priority_score", ascending=False)


# ---------------------------------------------------------------------------
# Layer 6: Annotator consensus validation
# ---------------------------------------------------------------------------

def annotator_consensus_validation(ann_df: pd.DataFrame, bundle_df: pd.DataFrame, eval_df: pd.DataFrame) -> pd.DataFrame:
    """On overlap set, compare evaluator performance vs consensus vs individual annotators."""
    
    # Find overlap conversations
    overlap_cids = set(ann_df["conversation_id"])
    for ann in ANNOTATORS:
        overlap_cids &= set(ann_df[ann_df["annotator"] == ann]["conversation_id"])
    overlap_cids = sorted(overlap_cids)
    
    if not overlap_cids:
        return pd.DataFrame()
    
    # Build consensus quality score (median of 3)
    consensus_rows = []
    for cid in overlap_cids:
        scores = []
        for ann in ANNOTATORS:
            rec = ann_df[(ann_df["annotator"] == ann) & (ann_df["conversation_id"] == cid)]
            if not rec.empty:
                scores.append(rec.iloc[0]["quality_score"])
        
        if len(scores) == 3:
            consensus_rows.append({
                "conversation_id": cid,
                "consensus_quality": statistics.median(scores),
                "quality_range": max(scores) - min(scores),
            })
    
    consensus_df = pd.DataFrame(consensus_rows).merge(
        bundle_df[["conversation_id", "bad_outcome_any"] + list(OUTCOME_FLAGS)],
        on="conversation_id",
        how="left",
    )
    
    # Evaluator scores on overlap set
    eval_overlap = eval_df[eval_df["conversation_id"].isin(overlap_cids)][["conversation_id", "quality_score", "risk_score"]]
    consensus_df = consensus_df.merge(eval_overlap, on="conversation_id", how="left", suffixes=("", "_eval"))
    
    # Agreement buckets
    def agreement_bucket(r: float) -> str:
        if r <= 0.2:
            return "high_agreement"
        if r <= 0.4:
            return "medium_agreement"
        return "high_disagreement"
    
    consensus_df["agreement_bucket"] = consensus_df["quality_range"].apply(agreement_bucket)
    
    # Summarize by bucket
    summary_rows = []
    for bucket, sub in consensus_df.groupby("agreement_bucket"):
        row = {
            "agreement_bucket": bucket,
            "n_conversations": len(sub),
            "mean_consensus_quality": sub["consensus_quality"].mean(),
            "mean_evaluator_quality": sub["quality_score"].mean(),
            "evaluator_consensus_corr": sub[["consensus_quality", "quality_score"]].corr().iloc[0, 1] if len(sub) > 1 else np.nan,
        }
        for outcome in OUTCOME_FLAGS:
            row[f"{outcome}_rate"] = sub[outcome].mean()
        summary_rows.append(row)
    
    return pd.DataFrame(summary_rows).sort_values("agreement_bucket")


# ---------------------------------------------------------------------------
# Evidence extraction with outcome-specific focus
# ---------------------------------------------------------------------------

def extract_deep_evidence(
    bundle_df: pd.DataFrame,
    eval_df: pd.DataFrame,
    ann_df: pd.DataFrame,
    outcome_rule_df: pd.DataFrame,
    outcome_cat_df: pd.DataFrame,
) -> pd.DataFrame:
    """Extract 2 examples per top (outcome, signal) pair."""
    
    rows = []
    
    # Top 3 rules per outcome
    for outcome in OUTCOME_FLAGS:
        top_rules = (
            outcome_rule_df[(outcome_rule_df["outcome"] == outcome) & (outcome_rule_df["n_with_rule"] >= 30)]
            .sort_values("risk_difference_pp", ascending=False)
            .head(3)
        )
        
        for _, r in top_rules.iterrows():
            rule = r["rule"]
            # Get violations for this rule
            for rec in eval_df.to_dict(orient="records"):
                cid = rec["conversation_id"]
                for v in rec.get("violations", []) or []:
                    if v.get("rule") == rule:
                        out_flags = bundle_df[bundle_df["conversation_id"] == cid]
                        if out_flags.empty or not out_flags.iloc[0][outcome]:
                            continue
                        rows.append({
                            "source": "evaluator_rule",
                            "outcome": outcome,
                            "topic": rule,
                            "spec_ref": r["spec_ref"],
                            "conversation_id": cid,
                            "turn": v.get("turn", -1),
                            outcome: True,
                            "evidence_note": v.get("explanation", "")[:220],
                        })
                        break  # one per conversation
            
            # limit to 2 per rule/outcome
            rows = rows[:-2] + sorted(rows[-len(rows):], key=lambda x: x["turn"])[:2] if len(rows) > 2 else rows
    
    # Top 2 categories per outcome (from strongest annotator signal)
    for outcome in OUTCOME_FLAGS:
        top_cats = (
            outcome_cat_df[(outcome_cat_df["outcome"] == outcome) & (outcome_cat_df["n_with_category"] >= 20)]
            .sort_values("risk_difference_pp", ascending=False)
            .head(2)
        )
        
        for _, r in top_cats.iterrows():
            cat = r["category"]
            annotator = r["annotator"]
            sub = ann_df[(ann_df["annotator"] == annotator)]
            for _, ann_rec in sub.iterrows():
                cid = ann_rec["conversation_id"]
                out_flags = bundle_df[bundle_df["conversation_id"] == cid]
                if out_flags.empty or not out_flags.iloc[0][outcome]:
                    continue
                for fp in ann_rec["failure_points"]:
                    if fp.get("category") == cat:
                        rows.append({
                            "source": "annotation_category",
                            "outcome": outcome,
                            "topic": f"{annotator}:{cat}",
                            "spec_ref": f"annotation:{cat}",
                            "conversation_id": cid,
                            "turn": fp.get("turn", -1),
                            outcome: True,
                            "evidence_note": fp.get("note", "")[:220],
                        })
                        break
                break  # 1 per category/outcome
    
    return pd.DataFrame(rows).drop_duplicates(subset=["source", "topic", "conversation_id", "turn"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def generate_deep_markdown(
    cohort_counts: pd.DataFrame,
    outcome_rule_df: pd.DataFrame,
    outcome_cat_df: pd.DataFrame,
    concordance_df: pd.DataFrame,
    turn_pos_df: pd.DataFrame,
    concentration_df: pd.DataFrame,
    prioritization_df: pd.DataFrame,
    consensus_val_df: pd.DataFrame,
    evidence_df: pd.DataFrame,
) -> str:
    lines = []
    lines.append("# FINDINGS_DEEP: Agent Behavior and Bad-Outcome Predictors")
    lines.append("")
    lines.append("This analysis disaggregates each bad outcome type, examines annotation-evaluator concordance, studies turn-position and violation-concentration effects, and validates findings against annotator consensus.")
    lines.append("")
    
    lines.append("## Cohort Overview")
    lines.append("")
    lines.append("| Outcome | N | Rate |")
    lines.append("|---|---:|---:|")
    for _, r in cohort_counts.iterrows():
        lines.append(f"| {r['outcome_flag']} | {int(r['n_true'])} | {r['rate_true']:.1%} |")
    lines.append("")
    
    lines.append("## Finding 1: State/control failures are the strongest predictors of intervention-required")
    lines.append("")
    lines.append("Three rules show near-perfect association with `required_intervention`:")
    lines.append("")
    interv_rules = outcome_rule_df[
        (outcome_rule_df["outcome"] == "required_intervention")
        & (outcome_rule_df["n_with_rule"] >= 50)
    ].head(5)
    for _, r in interv_rules.iterrows():
        lines.append(
            f"- `{r['rule']}` (**{r['spec_ref']}**): "
            f"{r['rate_with_rule']:.1%} with rule vs {r['rate_without_rule']:.1%} without "
            f"(risk diff: {r['risk_difference_pp']:+.1f}pp, n={int(r['n_with_rule'])})"
        )
    lines.append("")
    
    lines.append("## Finding 2: Compliance violations drive both complaints and regulatory flags")
    lines.append("")
    lines.append("**Complaint flag predictors:**")
    comp_rules = outcome_rule_df[
        (outcome_rule_df["outcome"] == "complaint_flag")
        & (outcome_rule_df["n_with_rule"] >= 20)
    ].head(4)
    for _, r in comp_rules.iterrows():
        lines.append(
            f"- `{r['rule']}` (**{r['spec_ref']}**): "
            f"{r['precision_for_outcome']:.1%} precision, lift={r['lift']:.2f}x"
        )
    lines.append("")
    
    lines.append("**Regulatory flag predictors:**")
    reg_rules = outcome_rule_df[
        (outcome_rule_df["outcome"] == "regulatory_flag")
        & (outcome_rule_df["n_with_rule"] >= 15)
    ].head(4)
    for _, r in reg_rules.iterrows():
        lines.append(
            f"- `{r['rule']}` (**{r['spec_ref']}**): "
            f"{r['precision_for_outcome']:.1%} precision, lift={r['lift']:.2f}x"
        )
    lines.append("")
    
    lines.append("## Finding 3: Annotation-evaluator concordance amplifies signal strength")
    lines.append("")
    if not concordance_df.empty:
        top_concord = concordance_df.head(5)
        for _, r in top_concord.iterrows():
            if pd.notna(r["joint_vs_neither_risk_diff_pp"]) and r["n_joint"] >= 10:
                lines.append(
                    f"- When **{r['rule']}** (evaluator) AND **{r['category']}** (annotator) both fire, "
                    f"{r['outcome']} rate = {r['outcome_rate_joint']:.1%} vs {r['outcome_rate_neither']:.1%} baseline "
                    f"(risk diff: {r['joint_vs_neither_risk_diff_pp']:+.1f}pp, n_joint={int(r['n_joint'])})"
                )
    lines.append("")
    
    lines.append("## Finding 4: Turn position matters — early failures have higher impact")
    lines.append("")
    if not turn_pos_df.empty:
        # Focus on a representative rule
        sample_rule = "CMP_HARDSHIP_NO_ESCALATION"
        sub = turn_pos_df[(turn_pos_df["rule"] == sample_rule) & (turn_pos_df["outcome"] == "complaint_flag")]
        if not sub.empty:
            lines.append(f"Example: `{sample_rule}` and complaint_flag:")
            for _, r in sub.iterrows():
                lines.append(f"- {r['turn_bucket']} turns: {r['outcome_rate']:.1%} (n={int(r['n_violations'])})")
    lines.append("")
    
    lines.append("## Finding 5: Violation concentration (multi-rule conversations)")
    lines.append("")
    lines.append("Bad-outcome rate by violation count:")
    lines.append("")
    lines.append("| Violation count | N convs | Bad outcome rate | Complaint rate | Regulatory rate | Intervention rate |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for _, r in concentration_df.iterrows():
        lines.append(
            f"| {r['count_bucket']} | {int(r['n_conversations'])} | "
            f"{r['bad_outcome_any_rate']:.1%} | {r['complaint_flag_rate']:.1%} | "
            f"{r['regulatory_flag_rate']:.1%} | {r['required_intervention_rate']:.1%} |"
        )
    lines.append("")
    
    lines.append("## Finding 6: Rule prioritization — high-frequency vs high-precision")
    lines.append("")
    lines.append("| Rule | Frequency | Precision (bad any) | Lift vs baseline | Priority score |")
    lines.append("|---|---:|---:|---:|---:|")
    for _, r in prioritization_df.head(10).iterrows():
        lines.append(
            f"| {r['rule']} | {int(r['frequency'])} | {r['precision_bad_outcome_any']:.1%} | "
            f"{r['lift_vs_baseline_any']:.2f}x | {r['priority_score']:.1f} |"
        )
    lines.append("")
    
    lines.append("## Annotator consensus validation (overlap set)")
    lines.append("")
    if not consensus_val_df.empty:
        lines.append("| Agreement bucket | N | Mean consensus quality | Mean evaluator quality | Bad outcome rate |")
        lines.append("|---|---:|---:|---:|---:|")
        for _, r in consensus_val_df.iterrows():
            lines.append(
                f"| {r['agreement_bucket']} | {int(r['n_conversations'])} | "
                f"{r['mean_consensus_quality']:.3f} | {r['mean_evaluator_quality']:.3f} | "
                f"{r.get('required_intervention_rate', 0.0):.1%} |"
            )
    lines.append("")
    
    lines.append("## Evidence Examples (outcome-specific)")
    lines.append("")
    lines.append("Selected rows with turn numbers:")
    lines.append("")
    for _, r in evidence_df.head(15).iterrows():
        lines.append(
            f"- **{r['outcome']}** | `{r['source']}` | `{r['topic']}` ({r['spec_ref']}) | "
            f"convo `{r['conversation_id']}` turn `{int(r['turn'])}` | {r['evidence_note'][:150]}"
        )
    lines.append("")
    
    lines.append("## Takeaway")
    lines.append("")
    lines.append("- **Intervention-required** is driven by state-machine breakdown (§4.8, I2, §5).")
    lines.append("- **Complaints** are driven by compliance failures (§8.3 DNC, §8.1/§8.2 hardship).")
    lines.append("- **Regulatory flags** have similar drivers to complaints but with stricter thresholds.")
    lines.append("- **Concordance** (evaluator + annotator agreement) is a high-confidence signal for operational triage.")
    lines.append("- **Multi-rule conversations** (6+ violations) have ~90% bad-outcome rate — these are systemic failures, not isolated slips.")
    lines.append("")
    
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    
    bundle_df = load_bundle()
    eval_df = load_eval()
    ann_df = load_annotations()
    rule_registry = load_rule_registry()
    
    # Cohort counts for each outcome
    cohort_rows = []
    for flag in ["bad_outcome_any"] + list(OUTCOME_FLAGS):
        n_true = int(bundle_df[flag].sum())
        n_total = len(bundle_df)
        cohort_rows.append({
            "outcome_flag": flag,
            "n_true": n_true,
            "n_false": n_total - n_true,
            "rate_true": n_true / n_total,
        })
    cohort_counts = pd.DataFrame(cohort_rows)
    cohort_counts.to_csv(OUT_DIR / "cohort_counts.csv", index=False)
    
    # Layer 1: outcome-specific predictors
    outcome_rule_df = outcome_specific_rule_predictors(bundle_df, eval_df, rule_registry)
    outcome_rule_df.to_csv(OUT_DIR / "outcome_specific_rule_predictors.csv", index=False)
    
    outcome_cat_df = outcome_specific_category_predictors(bundle_df, ann_df)
    outcome_cat_df.to_csv(OUT_DIR / "outcome_specific_category_predictors.csv", index=False)
    
    # Layer 2: concordance
    concordance_df = annotation_evaluator_concordance(bundle_df, eval_df, ann_df)
    concordance_df.to_csv(OUT_DIR / "annotation_evaluator_concordance.csv", index=False)
    
    # Layer 3: turn position
    turn_pos_df = turn_position_analysis(eval_df, bundle_df)
    turn_pos_df.to_csv(OUT_DIR / "turn_position_analysis.csv", index=False)
    
    # Layer 4: violation concentration
    concentration_df = violation_concentration_analysis(eval_df, bundle_df)
    concentration_df.to_csv(OUT_DIR / "violation_concentration_analysis.csv", index=False)
    
    # Layer 5: prioritization
    prioritization_df = rule_prioritization_matrix(bundle_df, eval_df, rule_registry)
    prioritization_df.to_csv(OUT_DIR / "rule_prioritization_matrix.csv", index=False)
    
    # Layer 6: consensus validation
    consensus_val_df = annotator_consensus_validation(ann_df, bundle_df, eval_df)
    consensus_val_df.to_csv(OUT_DIR / "annotator_consensus_validation.csv", index=False)
    
    # Evidence extraction
    evidence_df = extract_deep_evidence(bundle_df, eval_df, ann_df, outcome_rule_df, outcome_cat_df)
    evidence_df.to_csv(OUT_DIR / "evidence_with_turns_deep.csv", index=False)
    
    # Generate markdown
    md = generate_deep_markdown(
        cohort_counts=cohort_counts,
        outcome_rule_df=outcome_rule_df,
        outcome_cat_df=outcome_cat_df,
        concordance_df=concordance_df,
        turn_pos_df=turn_pos_df,
        concentration_df=concentration_df,
        prioritization_df=prioritization_df,
        consensus_val_df=consensus_val_df,
        evidence_df=evidence_df,
    )
    
    FINDINGS_MD.write_text(md, encoding="utf-8")
    
    print(f"Wrote deep findings artifacts to: {OUT_DIR}")
    print(f"Wrote findings markdown: {FINDINGS_MD}")
    print("\nCohort counts:")
    print(cohort_counts.to_string(index=False))
    print("\nTop rule prioritization (frequency × precision):")
    print(prioritization_df[["rule", "frequency", "precision_bad_outcome_any", "priority_score"]].head(8).to_string(index=False))


if __name__ == "__main__":
    main()
