#!/usr/bin/env python3
"""
Findings evidence pipeline
=========================

Builds evidence-backed artifacts for "Findings --- agent behavior and predictors
of bad outcomes" using outcomes + annotations + evaluator outputs.

Outputs are written to:
  dev/findings_output/

Run:
  python dev/findings_evidence_pipeline.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path("/Users/surya/Desktop/riverline_takehome")
BUNDLE_PATH = ROOT / "dev" / "conversation_bundle_flat.csv"
EVAL_PATH = ROOT / "dev" / "eval_v2.jsonl"
ANNOTATION_DIR = ROOT / "problem_statement" / "data" / "annotations"
EVAL_RULES_PATH = ROOT / "problem_statement" / "eval_takehome.py"
OUT_DIR = ROOT / "dev" / "findings_output"
FINDINGS_MD = ROOT / "FINDINGS_AGENT_BEHAVIOR.md"

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
    if b == 0:
        return np.nan
    return a / b


def load_bundle() -> pd.DataFrame:
    df = pd.read_csv(BUNDLE_PATH)
    df["outcome_parsed"] = df["outcome"].apply(_safe_eval)

    df["complaint_flag"] = df["outcome_parsed"].apply(lambda o: bool(o.get("borrower_complained", False)))
    df["regulatory_flag"] = df["outcome_parsed"].apply(lambda o: bool(o.get("regulatory_flag", False)))
    df["required_intervention"] = df["outcome_parsed"].apply(lambda o: bool(o.get("required_human_intervention", False)))
    df["bad_outcome"] = df[["complaint_flag", "regulatory_flag", "required_intervention"]].any(axis=1)
    return df


def load_rule_registry() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("eval_takehome_mod", str(EVAL_RULES_PATH))
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {rule: meta.get("spec_ref", "") for rule, meta in mod.RULES.items()}


def load_eval() -> pd.DataFrame:
    rows = []
    with open(EVAL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return pd.DataFrame(rows)


def rule_profiles(bundle_df: pd.DataFrame, eval_df: pd.DataFrame, rule_spec_ref: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # conversation-rule presence table
    conv_rows = []
    viol_rows = []
    for rec in eval_df.to_dict(orient="records"):
        cid = rec["conversation_id"]
        violations = rec.get("violations", []) or []
        seen = set()
        for v in violations:
            rule = v.get("rule")
            if not rule:
                continue
            seen.add(rule)
            viol_rows.append(
                {
                    "conversation_id": cid,
                    "rule": rule,
                    "turn": v.get("turn", -1),
                    "severity": float(v.get("severity", np.nan)),
                    "explanation": v.get("explanation", ""),
                }
            )
        for rule in seen:
            conv_rows.append({"conversation_id": cid, "rule": rule, "has_rule": 1})

    conv_rule = pd.DataFrame(conv_rows)
    viol_df = pd.DataFrame(viol_rows)

    merged = conv_rule.merge(
        bundle_df[["conversation_id", "bad_outcome", "complaint_flag", "regulatory_flag", "required_intervention"]],
        on="conversation_id",
        how="left",
    )

    stats_rows = []
    for rule, sub in merged.groupby("rule"):
        with_rule_bad = sub["bad_outcome"].mean()

        # among conversations without the rule
        cids_with_rule = set(sub["conversation_id"])
        without = bundle_df[~bundle_df["conversation_id"].isin(cids_with_rule)]
        without_bad = without["bad_outcome"].mean()

        support_bad = int(sub["bad_outcome"].sum())
        support_total = int(len(sub))

        turn_sub = viol_df[viol_df["rule"] == rule].copy()
        turn_valid = turn_sub[turn_sub["turn"] >= 0]
        avg_turn = float(turn_valid["turn"].mean()) if len(turn_valid) else np.nan
        early_share = float((turn_valid["turn"] <= 3).mean()) if len(turn_valid) else np.nan

        stats_rows.append(
            {
                "rule": rule,
                "spec_ref": rule_spec_ref.get(rule, ""),
                "n_with_rule": support_total,
                "n_bad_with_rule": support_bad,
                "bad_rate_with_rule": with_rule_bad,
                "bad_rate_without_rule": without_bad,
                "risk_difference_pp": (with_rule_bad - without_bad) * 100.0,
                "lift_vs_without": _lift(with_rule_bad, without_bad),
                "avg_violation_turn": avg_turn,
                "early_turn_share": early_share,
            }
        )

    bad_vs_nonbad_rule = pd.DataFrame(stats_rows).sort_values(
        ["risk_difference_pp", "n_with_rule"], ascending=[False, False]
    )

    # outcome-specific lifts
    out_rows = []
    for outcome in OUTCOME_FLAGS:
        for rule, sub in merged.groupby("rule"):
            with_rule_rate = sub[outcome].mean()
            cids_with_rule = set(sub["conversation_id"])
            without = bundle_df[~bundle_df["conversation_id"].isin(cids_with_rule)]
            without_rate = without[outcome].mean()
            out_rows.append(
                {
                    "outcome": outcome,
                    "rule": rule,
                    "spec_ref": rule_spec_ref.get(rule, ""),
                    "rate_with_rule": with_rule_rate,
                    "rate_without_rule": without_rate,
                    "risk_difference_pp": (with_rule_rate - without_rate) * 100.0,
                    "lift_vs_without": _lift(with_rule_rate, without_rate),
                    "n_with_rule": int(len(sub)),
                }
            )
    outcome_lift_rule = pd.DataFrame(out_rows).sort_values(
        ["outcome", "risk_difference_pp"], ascending=[True, False]
    )

    return bad_vs_nonbad_rule, outcome_lift_rule, viol_df


def load_annotations() -> pd.DataFrame:
    rows = []
    for ann in ANNOTATORS:
        p = ANNOTATION_DIR / f"{ann}.jsonl"
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                rows.append(
                    {
                        "annotator": ann,
                        "conversation_id": rec["conversation_id"],
                        "quality_score": rec.get("quality_score", np.nan),
                        "risk_flags": rec.get("risk_flags", []) or [],
                        "failure_points": rec.get("failure_points", []) or [],
                        "overall_assessment": (rec.get("overall_assessment") or "").strip(),
                    }
                )
    return pd.DataFrame(rows)


def annotation_profiles(bundle_df: pd.DataFrame, ann_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ann = ann_df.merge(
        bundle_df[["conversation_id", "bad_outcome", "complaint_flag", "regulatory_flag", "required_intervention"]],
        on="conversation_id",
        how="left",
    )

    ann["failure_count"] = ann["failure_points"].apply(len)
    ann["risk_flag_count"] = ann["risk_flags"].apply(len)

    base_rows = []
    for ann_name, sub in ann.groupby("annotator"):
        bad = sub[sub["bad_outcome"]]
        nonbad = sub[~sub["bad_outcome"]]
        base_rows.append(
            {
                "annotator": ann_name,
                "n_bad": len(bad),
                "n_nonbad": len(nonbad),
                "quality_mean_bad": bad["quality_score"].mean(),
                "quality_mean_nonbad": nonbad["quality_score"].mean(),
                "quality_delta_bad_minus_nonbad": bad["quality_score"].mean() - nonbad["quality_score"].mean(),
                "failure_count_mean_bad": bad["failure_count"].mean(),
                "failure_count_mean_nonbad": nonbad["failure_count"].mean(),
                "failure_count_delta_bad_minus_nonbad": bad["failure_count"].mean() - nonbad["failure_count"].mean(),
                "risk_flag_count_mean_bad": bad["risk_flag_count"].mean(),
                "risk_flag_count_mean_nonbad": nonbad["risk_flag_count"].mean(),
                "risk_flag_count_delta_bad_minus_nonbad": bad["risk_flag_count"].mean() - nonbad["risk_flag_count"].mean(),
            }
        )
    base_profile = pd.DataFrame(base_rows)

    # risk flag prevalence
    flag_rows = []
    exploded_flags = ann.explode("risk_flags").rename(columns={"risk_flags": "risk_flag"})
    exploded_flags = exploded_flags[exploded_flags["risk_flag"].notna() & (exploded_flags["risk_flag"] != "")]

    for (ann_name, flag), sub in exploded_flags.groupby(["annotator", "risk_flag"]):
        cids = set(sub["conversation_id"])
        ann_sub = ann[ann["annotator"] == ann_name]
        has_flag = ann_sub["conversation_id"].isin(cids)
        bad_rate_has = ann_sub.loc[has_flag, "bad_outcome"].mean() if has_flag.any() else np.nan
        bad_rate_no = ann_sub.loc[~has_flag, "bad_outcome"].mean() if (~has_flag).any() else np.nan
        flag_rows.append(
            {
                "annotator": ann_name,
                "risk_flag": flag,
                "n_conversations_with_flag": int(has_flag.sum()),
                "bad_rate_with_flag": bad_rate_has,
                "bad_rate_without_flag": bad_rate_no,
                "risk_difference_pp": (bad_rate_has - bad_rate_no) * 100.0,
                "lift_vs_without": _lift(bad_rate_has, bad_rate_no),
            }
        )
    risk_flag_profile = pd.DataFrame(flag_rows).sort_values(
        ["risk_difference_pp", "n_conversations_with_flag"], ascending=[False, False]
    )

    # category prevalence
    cat_rows = []
    ann_cat_rows = []
    for _, r in ann.iterrows():
        cats = [fp.get("category") for fp in r["failure_points"] if fp.get("category")]
        for c in set(cats):
            ann_cat_rows.append(
                {
                    "annotator": r["annotator"],
                    "conversation_id": r["conversation_id"],
                    "category": c,
                    "bad_outcome": r["bad_outcome"],
                    "complaint_flag": r["complaint_flag"],
                    "regulatory_flag": r["regulatory_flag"],
                    "required_intervention": r["required_intervention"],
                }
            )
    cat_df = pd.DataFrame(ann_cat_rows)
    if len(cat_df) == 0:
        cat_profile = pd.DataFrame(
            columns=[
                "annotator",
                "category",
                "n_conversations_with_category",
                "bad_rate_with_category",
                "bad_rate_without_category",
                "risk_difference_pp",
                "lift_vs_without",
            ]
        )
        outcome_lift_cat = pd.DataFrame()
    else:
        for (ann_name, cat), sub in cat_df.groupby(["annotator", "category"]):
            cids = set(sub["conversation_id"])
            ann_sub = ann[ann["annotator"] == ann_name]
            has_cat = ann_sub["conversation_id"].isin(cids)
            bad_rate_has = ann_sub.loc[has_cat, "bad_outcome"].mean() if has_cat.any() else np.nan
            bad_rate_no = ann_sub.loc[~has_cat, "bad_outcome"].mean() if (~has_cat).any() else np.nan
            cat_rows.append(
                {
                    "annotator": ann_name,
                    "category": cat,
                    "n_conversations_with_category": int(has_cat.sum()),
                    "bad_rate_with_category": bad_rate_has,
                    "bad_rate_without_category": bad_rate_no,
                    "risk_difference_pp": (bad_rate_has - bad_rate_no) * 100.0,
                    "lift_vs_without": _lift(bad_rate_has, bad_rate_no),
                }
            )
        cat_profile = pd.DataFrame(cat_rows).sort_values(
            ["risk_difference_pp", "n_conversations_with_category"], ascending=[False, False]
        )

        out_rows = []
        for outcome in OUTCOME_FLAGS:
            for (ann_name, cat), sub in cat_df.groupby(["annotator", "category"]):
                cids = set(sub["conversation_id"])
                ann_sub = ann[ann["annotator"] == ann_name]
                has_cat = ann_sub["conversation_id"].isin(cids)
                with_rate = ann_sub.loc[has_cat, outcome].mean() if has_cat.any() else np.nan
                without_rate = ann_sub.loc[~has_cat, outcome].mean() if (~has_cat).any() else np.nan
                out_rows.append(
                    {
                        "annotator": ann_name,
                        "category": cat,
                        "outcome": outcome,
                        "rate_with_category": with_rate,
                        "rate_without_category": without_rate,
                        "risk_difference_pp": (with_rate - without_rate) * 100.0,
                        "lift_vs_without": _lift(with_rate, without_rate),
                        "n_with_category": int(has_cat.sum()),
                    }
                )
        outcome_lift_cat = pd.DataFrame(out_rows).sort_values(
            ["outcome", "risk_difference_pp"], ascending=[True, False]
        )

    return base_profile, risk_flag_profile, cat_profile, outcome_lift_cat


def assessment_samples_bad_outcomes(ann_df: pd.DataFrame, bundle_df: pd.DataFrame, n_samples: int = 18) -> pd.DataFrame:
    ann = ann_df.merge(bundle_df[["conversation_id", "bad_outcome"]], on="conversation_id", how="left")
    bad = ann[(ann["bad_outcome"]) & ann["overall_assessment"].astype(bool)].copy()
    if bad.empty:
        return pd.DataFrame(columns=["annotator", "conversation_id", "theme", "overall_assessment"])

    theme_patterns = {
        "repetition_loop": r"repeat|loop|repetit",
        "hardship_missed": r"hardship|family|medical|job|cannot pay|can't pay",
        "state_machine_or_context": r"state|context|confus|unclear|flow",
        "escalation_missed": r"escalat|human|manager",
        "amount_inconsistency": r"amount|inconsisten|incorrect|wrong",
        "tone_or_pressure": r"tone|pressure|threat|aggressive|inappropriate",
        "verification_or_compliance": r"verification|identity|compliance|do-not-contact|dnc|regulatory",
    }

    rows = []
    for _, r in bad.iterrows():
        txt = str(r["overall_assessment"]).lower()
        matched = False
        for theme, pat in theme_patterns.items():
            if re.search(pat, txt):
                rows.append(
                    {
                        "annotator": r["annotator"],
                        "conversation_id": r["conversation_id"],
                        "theme": theme,
                        "overall_assessment": r["overall_assessment"],
                    }
                )
                matched = True
                break
        if not matched:
            rows.append(
                {
                    "annotator": r["annotator"],
                    "conversation_id": r["conversation_id"],
                    "theme": "other",
                    "overall_assessment": r["overall_assessment"],
                }
            )

    themed = pd.DataFrame(rows)
    picked = []
    for theme, sub in themed.groupby("theme"):
        picked.append(sub.head(3))
    out = pd.concat(picked, ignore_index=True) if picked else themed.head(0)
    return out.head(n_samples)


def evidence_examples_with_turns(
    bad_vs_nonbad_rule: pd.DataFrame,
    cat_profile: pd.DataFrame,
    viol_df: pd.DataFrame,
    bundle_df: pd.DataFrame,
    ann_df: pd.DataFrame,
) -> pd.DataFrame:
    # Top rules with strong signal and support
    top_rules = (
        bad_vs_nonbad_rule[(bad_vs_nonbad_rule["n_with_rule"] >= 25)]
        .sort_values("risk_difference_pp", ascending=False)
        .head(5)["rule"]
        .tolist()
    )

    out_rows = []

    # Rule evidence examples from evaluator output with turn numbers
    bflags = bundle_df.set_index("conversation_id")[["complaint_flag", "regulatory_flag", "required_intervention", "bad_outcome"]]
    for rule in top_rules:
        sub = viol_df[viol_df["rule"] == rule].copy()
        if sub.empty:
            continue
        sub = sub.merge(bflags, left_on="conversation_id", right_index=True, how="left")
        sub = sub[sub["bad_outcome"]].copy()
        if sub.empty:
            continue
        sub = sub.sort_values(["severity", "turn"], ascending=[False, True]).head(2)
        for _, r in sub.iterrows():
            out_rows.append(
                {
                    "evidence_type": "rule",
                    "topic": rule,
                    "conversation_id": r["conversation_id"],
                    "turn": int(r["turn"]) if pd.notna(r["turn"]) else -1,
                    "complaint_flag": bool(r["complaint_flag"]),
                    "regulatory_flag": bool(r["regulatory_flag"]),
                    "required_intervention": bool(r["required_intervention"]),
                    "evidence_note": str(r["explanation"])[:240],
                }
            )

    # Category evidence examples from annotations (failure_points turn)
    top_cats = (
        cat_profile[(cat_profile["n_conversations_with_category"] >= 20)]
        .sort_values("risk_difference_pp", ascending=False)
        .head(5)
    )

    if not top_cats.empty:
        ann = ann_df.merge(
            bundle_df[["conversation_id", "complaint_flag", "regulatory_flag", "required_intervention", "bad_outcome"]],
            on="conversation_id",
            how="left",
        )
        for _, row in top_cats.iterrows():
            cat = row["category"]
            ann_name = row["annotator"]
            sub = ann[(ann["annotator"] == ann_name) & (ann["bad_outcome"])].copy()
            cat_hits = []
            for _, r in sub.iterrows():
                fps = r["failure_points"]
                for fp in fps:
                    if fp.get("category") == cat:
                        cat_hits.append(
                            {
                                "conversation_id": r["conversation_id"],
                                "turn": fp.get("turn", -1),
                                "complaint_flag": bool(r["complaint_flag"]),
                                "regulatory_flag": bool(r["regulatory_flag"]),
                                "required_intervention": bool(r["required_intervention"]),
                                "note": fp.get("note", ""),
                            }
                        )
            if not cat_hits:
                continue
            hit_df = pd.DataFrame(cat_hits).sort_values(["required_intervention", "regulatory_flag", "complaint_flag"], ascending=False)
            for _, h in hit_df.head(2).iterrows():
                out_rows.append(
                    {
                        "evidence_type": "annotation_category",
                        "topic": f"{ann_name}:{cat}",
                        "conversation_id": h["conversation_id"],
                        "turn": int(h["turn"]) if pd.notna(h["turn"]) else -1,
                        "complaint_flag": bool(h["complaint_flag"]),
                        "regulatory_flag": bool(h["regulatory_flag"]),
                        "required_intervention": bool(h["required_intervention"]),
                        "evidence_note": str(h["note"])[:240],
                    }
                )

    out = pd.DataFrame(out_rows).drop_duplicates(subset=["evidence_type", "topic", "conversation_id", "turn"])
    return out.sort_values(["evidence_type", "topic"]).reset_index(drop=True)


def rank_findings(
    bad_vs_nonbad_rule: pd.DataFrame,
    cat_profile: pd.DataFrame,
    evidence_df: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    rank = 1

    # top rule-driven findings
    for _, r in (
        bad_vs_nonbad_rule[(bad_vs_nonbad_rule["n_with_rule"] >= 25)]
        .sort_values("risk_difference_pp", ascending=False)
        .head(4)
        .iterrows()
    ):
        topic = r["rule"]
        examples = evidence_df[(evidence_df["evidence_type"] == "rule") & (evidence_df["topic"] == topic)]["conversation_id"].drop_duplicates().tolist()[:2]
        rows.append(
            {
                "rank": rank,
                "finding_type": "rule_signal",
                "finding_title": f"{topic} over-indexes in bad outcomes",
                "metric": f"bad_rate_with_rule={r['bad_rate_with_rule']:.3f}; bad_rate_without_rule={r['bad_rate_without_rule']:.3f}; risk_diff_pp={r['risk_difference_pp']:.2f}",
                "support_n": int(r["n_with_rule"]),
                "reference": r["spec_ref"],
                "example_conversation_ids": ", ".join(examples),
                "caveat": "Associational signal, not causal proof.",
            }
        )
        rank += 1

    # top annotation category findings
    for _, r in (
        cat_profile[(cat_profile["n_conversations_with_category"] >= 20)]
        .sort_values("risk_difference_pp", ascending=False)
        .head(3)
        .iterrows()
    ):
        topic = f"{r['annotator']}:{r['category']}"
        examples = (
            evidence_df[(evidence_df["evidence_type"] == "annotation_category") & (evidence_df["topic"] == topic)]["conversation_id"]
            .drop_duplicates()
            .tolist()[:2]
        )
        rows.append(
            {
                "rank": rank,
                "finding_type": "annotation_signal",
                "finding_title": f"Category '{r['category']}' (by {r['annotator']}) tracks bad outcomes",
                "metric": f"bad_rate_with_cat={r['bad_rate_with_category']:.3f}; bad_rate_without_cat={r['bad_rate_without_category']:.3f}; risk_diff_pp={r['risk_difference_pp']:.2f}",
                "support_n": int(r["n_conversations_with_category"]),
                "reference": f"annotation_category:{r['category']}",
                "example_conversation_ids": ", ".join(examples),
                "caveat": "Depends on annotator style; compare across annotators.",
            }
        )
        rank += 1

    return pd.DataFrame(rows).sort_values("rank")


def write_findings_markdown(
    top_findings: pd.DataFrame,
    evidence_df: pd.DataFrame,
    base_profile: pd.DataFrame,
    bad_vs_nonbad_rule: pd.DataFrame,
) -> None:
    lines: list[str] = []
    lines.append("# FINDINGS_AGENT_BEHAVIOR")
    lines.append("")
    lines.append("This page summarizes what the analysis suggests about agent behavior and which patterns predict bad outcomes.")
    lines.append("All claims below are evidence-backed with support counts and example conversation IDs.")
    lines.append("")
    lines.append("## Cohort Snapshot")
    lines.append("")

    # Basic cohort reference from available tables
    if not bad_vs_nonbad_rule.empty:
        top = bad_vs_nonbad_rule.head(1).iloc[0]
        lines.append(f"- Strongest rule signal observed in this run: `{top['rule']}` ({top['spec_ref']}) with risk diff {top['risk_difference_pp']:.2f}pp over {int(top['n_with_rule'])} conversations.")
    lines.append("")

    lines.append("## Annotator-side Behavioral Signals")
    lines.append("")
    for _, r in base_profile.iterrows():
        lines.append(
            f"- `{r['annotator']}`: mean quality bad={r['quality_mean_bad']:.3f} vs non-bad={r['quality_mean_nonbad']:.3f}; "
            f"mean failure count bad={r['failure_count_mean_bad']:.2f} vs non-bad={r['failure_count_mean_nonbad']:.2f}."
        )
    lines.append("")

    lines.append("## Top Findings")
    lines.append("")
    for _, r in top_findings.iterrows():
        lines.append(f"### {int(r['rank'])}. {r['finding_title']}")
        lines.append(f"- Metric: {r['metric']}")
        lines.append(f"- Support: n={int(r['support_n'])}")
        lines.append(f"- Reference: {r['reference']}")
        lines.append(f"- Evidence conversations: {r['example_conversation_ids'] if r['example_conversation_ids'] else 'NA'}")
        lines.append(f"- Caveat: {r['caveat']}")
        lines.append("")

    lines.append("## Evidence with Turns")
    lines.append("")
    lines.append("Selected rows from `dev/findings_output/evidence_examples_with_turns.csv`:")
    lines.append("")
    for _, r in evidence_df.head(12).iterrows():
        lines.append(
            f"- `{r['evidence_type']}` | `{r['topic']}` | convo `{r['conversation_id']}` turn `{int(r['turn'])}` "
            f"| outcomes: complaint={bool(r['complaint_flag'])}, regulatory={bool(r['regulatory_flag'])}, intervention={bool(r['required_intervention'])}"
        )
    lines.append("")

    FINDINGS_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    bundle_df = load_bundle()
    eval_df = load_eval()
    ann_df = load_annotations()
    rule_spec_ref = load_rule_registry()

    # 1) cohorts (for verification output)
    cohort_counts = (
        bundle_df["bad_outcome"].value_counts().rename_axis("bad_outcome").reset_index(name="n_conversations")
    )
    cohort_counts.to_csv(OUT_DIR / "cohort_counts.csv", index=False)

    # 2) rule signal analysis
    bad_vs_nonbad_rule, outcome_lift_rule, viol_df = rule_profiles(bundle_df, eval_df, rule_spec_ref)
    bad_vs_nonbad_rule.to_csv(OUT_DIR / "bad_vs_nonbad_rule_profile.csv", index=False)
    outcome_lift_rule.to_csv(OUT_DIR / "outcome_lift_by_rule.csv", index=False)

    # 3) annotation signal analysis
    base_profile, risk_flag_profile, cat_profile, outcome_lift_cat = annotation_profiles(bundle_df, ann_df)
    annotation_profile = base_profile.copy()
    annotation_profile.to_csv(OUT_DIR / "bad_vs_nonbad_annotation_profile.csv", index=False)
    outcome_lift_cat.to_csv(OUT_DIR / "outcome_lift_by_annotation_category.csv", index=False)

    # 4) assessment quotes
    assess_samples = assessment_samples_bad_outcomes(ann_df, bundle_df, n_samples=18)
    assess_samples.to_csv(OUT_DIR / "annotator_assessment_samples_bad_outcomes.csv", index=False)

    # 5) evidence extraction with turns
    evidence_df = evidence_examples_with_turns(
        bad_vs_nonbad_rule=bad_vs_nonbad_rule,
        cat_profile=cat_profile,
        viol_df=viol_df,
        bundle_df=bundle_df,
        ann_df=ann_df,
    )
    evidence_df.to_csv(OUT_DIR / "evidence_examples_with_turns.csv", index=False)

    # 6) ranked findings for review
    top_findings = rank_findings(bad_vs_nonbad_rule, cat_profile, evidence_df)
    top_findings.to_csv(OUT_DIR / "top_findings_ranked.csv", index=False)

    # Keep useful intermediate exports
    risk_flag_profile.to_csv(OUT_DIR / "annotation_risk_flag_profile.csv", index=False)
    cat_profile.to_csv(OUT_DIR / "annotation_category_profile.csv", index=False)

    # 7) draft findings markdown (post-result review stage collapsed into pipeline run)
    write_findings_markdown(
        top_findings=top_findings,
        evidence_df=evidence_df,
        base_profile=base_profile,
        bad_vs_nonbad_rule=bad_vs_nonbad_rule,
    )

    print(f"Wrote findings artifacts to: {OUT_DIR}")
    print(f"Wrote findings markdown draft: {FINDINGS_MD}")
    print("Cohort counts:")
    print(cohort_counts.to_string(index=False))


if __name__ == "__main__":
    main()
