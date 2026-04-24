"""
Segment-wise Violation Analysis
===============================

Breaks down violation rates and bad-outcome correlations across borrower
segments:

    language, dpd_bucket, pos_bucket, turn_length, behavioral

For each segment axis it produces a "TV block":
    - Table 1: segment summary (violation/outcome rates)
    - Table 2: per-rule rates by segment value + over-index vs global
    - Visuals: heatmap (segment x rule) and risk-lift bar chart

It also runs a statistical layer (odds ratio, risk difference, Fisher
p-value) for segment_value x rule x bad-outcome and picks 2 high-risk
+ 2 representative example conversations per segment/rule for the
evidence appendix.

All tables are written to CSV and plots to PNG under an output directory
so the violation report can lift them directly.

Usage from a notebook::

    from segment_violation_analysis import run_segment_analysis
    bundle = run_segment_analysis()
    bundle["segment_summary"]["language"].head()
"""

from __future__ import annotations

import ast
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact

try:
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except Exception:
    _HAS_MPL = False

try:
    import seaborn as sns
    _HAS_SNS = True
except Exception:
    _HAS_SNS = False


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_CSV = Path("/Users/surya/Desktop/riverline_takehome/dev/conversation_bundle_flat.csv")
DEFAULT_EVAL = Path("/Users/surya/Desktop/riverline_takehome/dev/eval_v2.jsonl")
DEFAULT_OUT = Path("/Users/surya/Desktop/riverline_takehome/dev/segment_analysis_output")

SEGMENTS: tuple[str, ...] = (
    "language",
    "dpd_bucket",
    "pos_bucket",
    "turn_length",
    "behavioral",
)

BAD_OUTCOMES: tuple[str, ...] = (
    "complaint_flag",
    "regulatory_flag",
    "required_intervention",
)

MIN_SUPPORT = 15        # min convs per cell for a stats row to be kept
MIN_RISK_DIFF = 0.05    # 5pp absolute risk difference to highlight


# ---------------------------------------------------------------------------
# Load + merge
# ---------------------------------------------------------------------------

def _safe_eval(x: Any) -> dict:
    if pd.isna(x) or x == "":
        return {}
    try:
        return ast.literal_eval(x)
    except Exception:
        return {}


def load_conversation_bundle(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)

    df["metadata_parsed"] = df["metadata"].apply(_safe_eval)
    df["prod_log_parsed"] = df["prod_log"].apply(_safe_eval)
    df["outcome_parsed"] = df["outcome"].apply(_safe_eval)

    df["language"] = df["metadata_parsed"].apply(lambda x: x.get("language", "unknown"))
    df["zone"]     = df["metadata_parsed"].apply(lambda x: x.get("zone", "unknown"))
    df["dpd"]      = df["metadata_parsed"].apply(lambda x: x.get("dpd", np.nan))
    df["pos"]      = df["metadata_parsed"].apply(lambda x: x.get("pos", np.nan))
    df["total_turns"] = df["metadata_parsed"].apply(lambda x: x.get("total_turns", np.nan))

    df["complaint_flag"] = df["outcome_parsed"].apply(lambda x: bool(x.get("borrower_complained", False)))
    df["regulatory_flag"] = df["outcome_parsed"].apply(lambda x: bool(x.get("regulatory_flag", False)))
    df["required_intervention"] = df["outcome_parsed"].apply(lambda x: bool(x.get("required_human_intervention", False)))
    df["payment_received"] = df["outcome_parsed"].apply(lambda x: bool(x.get("payment_received", False)))

    return df


def load_eval(eval_path: Path) -> pd.DataFrame:
    rows = []
    with open(eval_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rows.append(rec)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Segment derivation
# ---------------------------------------------------------------------------

_BEHAVIORAL_MAP = {
    "unclear": "confused",
    "asks_time": "delay_seeking",
    "wants_settlement": "cooperative",
    "wants_closure": "cooperative",
    "refuses": "resistant",
    "disputes": "resistant",
    "hardship": "distressed",
}


def _dpd_bucket(dpd: float) -> str:
    if pd.isna(dpd):
        return "unknown"
    if dpd <= 30:
        return "0-30"
    if dpd <= 90:
        return "31-90"
    if dpd <= 180:
        return "91-180"
    return "181+"


def _pos_bucket(pos: float) -> str:
    if pd.isna(pos):
        return "unknown"
    if pos < 100_000:
        return "<100k"
    if pos < 200_000:
        return "100k-200k"
    return "200k+"


def _turn_length_bucket(series: pd.Series) -> pd.Series:
    """Balanced short/medium/long bins via quantiles."""
    try:
        bins = pd.qcut(series, q=3, labels=["short", "medium", "long"], duplicates="drop")
        return bins.astype(object).fillna("unknown")
    except Exception:
        return pd.Series(["unknown"] * len(series), index=series.index)


def _behavioral_bucket(prod_log: dict) -> str:
    classifs = prod_log.get("bot_classifications", []) or []
    if not classifs:
        return "no_classifications"
    counts: Counter[str] = Counter()
    for c in classifs:
        raw = c.get("classification")
        bucket = _BEHAVIORAL_MAP.get(raw, "other")
        counts[bucket] += 1
    dominant, dom_n = counts.most_common(1)[0]
    total = sum(counts.values())
    if total and dom_n / total < 0.4:
        return "mixed"
    return dominant


def derive_segments(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["dpd_bucket"] = out["dpd"].apply(_dpd_bucket)
    out["pos_bucket"] = out["pos"].apply(_pos_bucket)
    out["turn_length"] = _turn_length_bucket(out["total_turns"])
    out["behavioral"] = out["prod_log_parsed"].apply(_behavioral_bucket)
    return out


# ---------------------------------------------------------------------------
# Violation flags
# ---------------------------------------------------------------------------

def attach_violations(df: pd.DataFrame, eval_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Attach boolean has_<RULE> and count_<RULE> columns to df."""
    by_id: dict[str, list[dict]] = {
        rec["conversation_id"]: rec.get("violations", [])
        for rec in eval_df.to_dict(orient="records")
    }

    all_rules: set[str] = set()
    counts_by_conv: dict[str, Counter[str]] = defaultdict(Counter)
    for cid, violations in by_id.items():
        for v in violations:
            rid = v.get("rule")
            if not rid:
                continue
            all_rules.add(rid)
            counts_by_conv[cid][rid] += 1

    ordered_rules = sorted(all_rules)
    for rule in ordered_rules:
        df[f"has_{rule}"] = df["conversation_id"].map(
            lambda cid, r=rule: int(counts_by_conv.get(cid, {}).get(r, 0) > 0)
        )
        df[f"count_{rule}"] = df["conversation_id"].map(
            lambda cid, r=rule: counts_by_conv.get(cid, {}).get(r, 0)
        )

    df["any_violation"] = df[[f"has_{r}" for r in ordered_rules]].max(axis=1)
    df["num_violations"] = df[[f"count_{r}" for r in ordered_rules]].sum(axis=1)

    eval_scores = eval_df.set_index("conversation_id")[["quality_score", "risk_score"]]
    df = df.merge(eval_scores, left_on="conversation_id", right_index=True, how="left")

    return df, ordered_rules


# ---------------------------------------------------------------------------
# TV blocks
# ---------------------------------------------------------------------------

def segment_summary_table(df: pd.DataFrame, segment: str) -> pd.DataFrame:
    g = df.groupby(segment, dropna=False)
    rows = []
    for name, sub in g:
        rows.append({
            segment: name,
            "n_conversations": len(sub),
            "pct_any_violation": sub["any_violation"].mean(),
            "avg_violations_per_conv": sub["num_violations"].mean(),
            "avg_quality_score": sub["quality_score"].mean(),
            "avg_risk_score": sub["risk_score"].mean(),
            "pct_complaint_flag": sub["complaint_flag"].mean(),
            "pct_regulatory_flag": sub["regulatory_flag"].mean(),
            "pct_required_intervention": sub["required_intervention"].mean(),
        })
    out = pd.DataFrame(rows).sort_values("n_conversations", ascending=False)
    return out.reset_index(drop=True)


def rule_rate_table(df: pd.DataFrame, segment: str, rules: list[str]) -> pd.DataFrame:
    """Rate of each rule per segment value + over-index vs global rate."""
    global_rate = {r: df[f"has_{r}"].mean() for r in rules}

    pivot = df.groupby(segment)[[f"has_{r}" for r in rules]].mean()
    pivot.columns = [c.replace("has_", "") for c in pivot.columns]

    over_index = pivot.copy()
    for r in rules:
        gr = global_rate[r]
        over_index[r] = pivot[r] / gr if gr > 0 else np.nan

    rate_long = pivot.reset_index().melt(id_vars=segment, var_name="rule", value_name="rate")
    lift_long = over_index.reset_index().melt(id_vars=segment, var_name="rule", value_name="lift")
    out = rate_long.merge(lift_long, on=[segment, "rule"])
    out["global_rate"] = out["rule"].map(global_rate)
    out = out.sort_values(["rule", "lift"], ascending=[True, False]).reset_index(drop=True)
    return out


def build_rule_profiles(
    df: pd.DataFrame,
    rules: list[str],
) -> pd.DataFrame:
    """
    Build a rule-first long table:
    rule x segment_axis x segment_value with rate/lift and outcomes when rule fires.
    """
    rows = []
    for rule in rules:
        rule_col = f"has_{rule}"
        global_rate = df[rule_col].mean()
        for segment in SEGMENTS:
            for seg_val, sub in df.groupby(segment, dropna=False):
                fired = sub[sub[rule_col] == 1]
                rate = sub[rule_col].mean()
                lift = (rate / global_rate) if global_rate > 0 else np.nan
                rows.append(
                    {
                        "rule": rule,
                        "segment_axis": segment,
                        "segment_value": str(seg_val),
                        "n_conversations": len(sub),
                        "global_rate": global_rate,
                        "rate_in_segment": rate,
                        "lift_vs_global": lift,
                        "pct_complaint_flag": fired["complaint_flag"].mean() if len(fired) else np.nan,
                        "pct_regulatory_flag": fired["regulatory_flag"].mean() if len(fired) else np.nan,
                        "pct_required_intervention": fired["required_intervention"].mean() if len(fired) else np.nan,
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["rule", "segment_axis", "lift_vs_global"],
        ascending=[True, True, False],
    ).reset_index(drop=True)


def _save_heatmap(rate_df: pd.DataFrame, segment: str, out_path: Path) -> None:
    if not _HAS_MPL or rate_df.empty:
        return
    wide = rate_df.pivot(index=segment, columns="rule", values="rate")
    if wide.empty:
        return
    fig_h = max(3.5, 0.45 * len(wide.index) + 2)
    fig_w = max(8, 0.55 * len(wide.columns) + 3)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    if _HAS_SNS:
        sns.heatmap(wide, annot=True, fmt=".1%", cmap="Reds",
                    cbar_kws={"label": "Violation rate"}, ax=ax)
    else:
        values = wide.to_numpy(dtype=float)
        im = ax.imshow(values, cmap="Reds", aspect="auto")
        fig.colorbar(im, ax=ax, label="Violation rate")
        ax.set_xticks(range(wide.shape[1]))
        ax.set_xticklabels(wide.columns)
        ax.set_yticks(range(wide.shape[0]))
        ax.set_yticklabels(wide.index)
        for i in range(wide.shape[0]):
            for j in range(wide.shape[1]):
                v = values[i, j]
                if np.isfinite(v):
                    ax.text(j, i, f"{v:.0%}", ha="center", va="center",
                            fontsize=8,
                            color="white" if v > 0.5 else "black")

    ax.set_title(f"Violation rate: {segment} x rule")
    plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
    plt.setp(ax.get_yticklabels(), rotation=0)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def _save_outcome_lift_chart(
    df: pd.DataFrame,
    segment: str,
    out_path: Path,
) -> None:
    if not _HAS_MPL:
        return
    rows = []
    for outcome in BAD_OUTCOMES:
        global_rate = df[outcome].mean()
        for name, sub in df.groupby(segment):
            rate = sub[outcome].mean()
            lift = (rate / global_rate) if global_rate > 0 else np.nan
            rows.append({segment: name, "outcome": outcome, "rate": rate, "lift": lift})
    lift_df = pd.DataFrame(rows)
    if lift_df.empty:
        return

    n_seg = lift_df[segment].nunique()
    fig, ax = plt.subplots(figsize=(max(8, 0.8 * n_seg + 4), 5))

    if _HAS_SNS:
        sns.barplot(data=lift_df, x=segment, y="lift", hue="outcome", ax=ax)
    else:
        pivot = lift_df.pivot(index=segment, columns="outcome", values="lift").fillna(0)
        x = np.arange(len(pivot.index))
        width = 0.8 / max(1, len(pivot.columns))
        for i, col in enumerate(pivot.columns):
            ax.bar(x + i * width - 0.4 + width / 2, pivot[col].values, width, label=col)
        ax.set_xticks(x)
        ax.set_xticklabels(pivot.index)
        ax.legend(title="Outcome", loc="upper right")

    ax.axhline(1.0, color="black", linestyle="--", alpha=0.5)
    ax.set_title(f"Outcome lift vs global mean: {segment}")
    ax.set_ylabel("Lift (segment_rate / global_rate)")
    plt.setp(ax.get_xticklabels(), rotation=25, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def _save_rule_profile_chart(
    rule_profile_df: pd.DataFrame,
    rule: str,
    out_path: Path,
) -> None:
    if not _HAS_MPL:
        return
    sub = rule_profile_df[rule_profile_df["rule"] == rule]
    if sub.empty:
        return

    n_axes = len(SEGMENTS)
    fig, axes = plt.subplots(n_axes, 1, figsize=(10, 3.2 * n_axes), sharey=True)
    if n_axes == 1:
        axes = [axes]

    global_rate = sub["global_rate"].iloc[0]

    for ax, segment in zip(axes, SEGMENTS):
        part = sub[sub["segment_axis"] == segment].copy()
        if part.empty:
            ax.axis("off")
            continue

        part = part.sort_values("rate_in_segment", ascending=False)
        x = np.arange(len(part))
        y = part["rate_in_segment"].to_numpy(dtype=float)

        if _HAS_SNS:
            sns.barplot(
                data=part,
                x="segment_value",
                y="rate_in_segment",
                ax=ax,
                color="#4e79a7",
            )
        else:
            ax.bar(x, y, color="#4e79a7")
            ax.set_xticks(x)
            ax.set_xticklabels(part["segment_value"].tolist())

        ax.axhline(global_rate, color="black", linestyle="--", alpha=0.6)
        ax.set_title(f"{segment}: rate by segment value")
        ax.set_xlabel("")
        ax.set_ylabel("Violation rate")
        plt.setp(ax.get_xticklabels(), rotation=25, ha="right")

    fig.suptitle(f"{rule} across borrower segments (global rate = {global_rate:.1%})", y=1.0)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Statistical layer
# ---------------------------------------------------------------------------

def stats_segment_rule_outcome(
    df: pd.DataFrame,
    segment: str,
    rules: list[str],
) -> pd.DataFrame:
    rows = []
    for seg_value, sub in df.groupby(segment, dropna=False):
        if len(sub) < MIN_SUPPORT:
            continue
        for rule in rules:
            rule_col = f"has_{rule}"
            for outcome in BAD_OUTCOMES:
                a = int(((sub[rule_col] == 1) & (sub[outcome])).sum())   # rule+ outcome+
                b = int(((sub[rule_col] == 1) & (~sub[outcome])).sum())  # rule+ outcome-
                c = int(((sub[rule_col] == 0) & (sub[outcome])).sum())   # rule- outcome+
                d = int(((sub[rule_col] == 0) & (~sub[outcome])).sum())  # rule- outcome-

                if (a + b) == 0 or (c + d) == 0:
                    continue

                risk_rule = a / (a + b) if (a + b) else np.nan
                risk_noru = c / (c + d) if (c + d) else np.nan
                risk_diff = risk_rule - risk_noru

                if b == 0 or c == 0:
                    odds_ratio = np.inf if b == 0 else 0.0
                else:
                    odds_ratio = (a * d) / (b * c)

                try:
                    _, p_fisher = fisher_exact([[a, b], [c, d]])
                except Exception:
                    p_fisher = np.nan

                rows.append({
                    "segment": segment,
                    "segment_value": seg_value,
                    "rule": rule,
                    "outcome": outcome,
                    "n_segment": len(sub),
                    "a_ruleY_outY": a,
                    "b_ruleY_outN": b,
                    "c_ruleN_outY": c,
                    "d_ruleN_outN": d,
                    "risk_with_rule": risk_rule,
                    "risk_without_rule": risk_noru,
                    "risk_difference": risk_diff,
                    "odds_ratio": odds_ratio,
                    "p_fisher": p_fisher,
                })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["highlight"] = (
        (out["risk_difference"].abs() >= MIN_RISK_DIFF)
        & ((out["a_ruleY_outY"] + out["b_ruleY_outN"]) >= MIN_SUPPORT)
    )
    return out.sort_values(
        ["segment_value", "rule", "outcome"]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Example selection
# ---------------------------------------------------------------------------

def _evidence_note(row: pd.Series, rule: str) -> str:
    outs = []
    if row["complaint_flag"]:
        outs.append("complaint")
    if row["regulatory_flag"]:
        outs.append("regulatory")
    if row["required_intervention"]:
        outs.append("intervention")
    out_str = ",".join(outs) if outs else "no bad outcome flags"
    return (
        f"Rule {rule} fired x{int(row.get(f'count_{rule}', 0))}; "
        f"quality={row['quality_score']:.2f} risk={row['risk_score']:.2f}; "
        f"outcomes: {out_str}"
    )


def extract_examples(
    df: pd.DataFrame,
    segment: str,
    rules: list[str],
    top_k_rules: int = 3,
    n_high_risk: int = 2,
    n_representative: int = 2,
) -> pd.DataFrame:
    """
    For each segment value, for each of its top-k over-indexed rules, pick
    n_high_risk + n_representative example conversations where that rule
    fired.
    """
    global_rate = {r: df[f"has_{r}"].mean() for r in rules}
    rows = []

    for seg_value, sub in df.groupby(segment, dropna=False):
        if len(sub) < MIN_SUPPORT:
            continue

        seg_rates = {r: sub[f"has_{r}"].mean() for r in rules}
        lift = {
            r: (seg_rates[r] / global_rate[r]) if global_rate[r] > 0 else 0
            for r in rules
            if seg_rates[r] > 0
        }
        top_rules = sorted(lift.items(), key=lambda kv: kv[1], reverse=True)[:top_k_rules]

        for rule, rule_lift in top_rules:
            firing = sub[sub[f"has_{rule}"] == 1]
            if firing.empty:
                continue

            high_risk = firing.sort_values(
                ["risk_score", "complaint_flag", "regulatory_flag"],
                ascending=[False, False, False],
            ).head(n_high_risk)

            remainder = firing.drop(high_risk.index, errors="ignore")
            if not remainder.empty:
                median_risk = remainder["risk_score"].median()
                remainder = remainder.assign(
                    _distance=(remainder["risk_score"] - median_risk).abs()
                )
                representative = remainder.sort_values("_distance").head(n_representative)
                representative = representative.drop(columns=["_distance"])
            else:
                representative = firing.iloc[0:0]

            for kind, block in (("high_risk", high_risk), ("representative", representative)):
                for _, r in block.iterrows():
                    rows.append({
                        "segment": segment,
                        "segment_value": seg_value,
                        "rule": rule,
                        "segment_lift": rule_lift,
                        "example_kind": kind,
                        "conversation_id": r["conversation_id"],
                        "language": r["language"],
                        "dpd_bucket": r["dpd_bucket"],
                        "pos_bucket": r["pos_bucket"],
                        "turn_length": r["turn_length"],
                        "behavioral": r["behavioral"],
                        "quality_score": r["quality_score"],
                        "risk_score": r["risk_score"],
                        "complaint_flag": r["complaint_flag"],
                        "regulatory_flag": r["regulatory_flag"],
                        "required_intervention": r["required_intervention"],
                        "evidence_note": _evidence_note(r, rule),
                    })

    return pd.DataFrame(rows).sort_values(
        ["segment_value", "rule", "example_kind"]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_segment_analysis(
    csv_path: Path | str = DEFAULT_CSV,
    eval_path: Path | str = DEFAULT_EVAL,
    out_dir: Path | str = DEFAULT_OUT,
    verbose: bool = True,
) -> dict[str, Any]:
    csv_path = Path(csv_path)
    eval_path = Path(eval_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"Loading {csv_path.name} + {eval_path.name}...")
    conv_df = load_conversation_bundle(csv_path)
    eval_df = load_eval(eval_path)

    conv_df = derive_segments(conv_df)
    df, rules = attach_violations(conv_df, eval_df)

    if verbose:
        print(f"  rows: {len(df)}  rules: {len(rules)}  segments: {SEGMENTS}")

    results: dict[str, Any] = {
        "df": df,
        "rules": rules,
        "segment_summary": {},
        "rule_rates": {},
        "stats": {},
        "examples": {},
        "rule_segment_matrix": pd.DataFrame(),
    }

    charts_dir = out_dir / "charts"
    charts_dir.mkdir(exist_ok=True)

    for segment in SEGMENTS:
        if verbose:
            print(f"\n=== {segment} ===")

        summary = segment_summary_table(df, segment)
        rates = rule_rate_table(df, segment, rules)
        stats = stats_segment_rule_outcome(df, segment, rules)
        examples = extract_examples(df, segment, rules)

        results["segment_summary"][segment] = summary
        results["rule_rates"][segment] = rates
        results["stats"][segment] = stats
        results["examples"][segment] = examples

        summary.to_csv(out_dir / f"summary_{segment}.csv", index=False)
        rates.to_csv(out_dir / f"rule_rates_{segment}.csv", index=False)
        stats.to_csv(out_dir / f"stats_{segment}.csv", index=False)
        examples.to_csv(out_dir / f"examples_{segment}.csv", index=False)

        _save_heatmap(rates, segment, charts_dir / f"heatmap_{segment}.png")
        _save_outcome_lift_chart(df, segment, charts_dir / f"outcome_lift_{segment}.png")

        if verbose:
            print(summary.to_string(index=False))
            sig = stats[stats["highlight"]] if not stats.empty else stats
            if not sig.empty:
                print(f"  highlighted stats rows: {len(sig)}  (|risk diff| >= {MIN_RISK_DIFF:.0%}, support >= {MIN_SUPPORT})")

    # Rule-first matrix: one table containing every rule across every segment axis
    rule_segment_matrix = build_rule_profiles(df, rules)
    rule_segment_matrix.to_csv(out_dir / "rule_segment_matrix.csv", index=False)
    results["rule_segment_matrix"] = rule_segment_matrix

    # One chart per rule: five sub-panels (one per segment axis)
    by_rule_dir = charts_dir / "by_rule"
    by_rule_dir.mkdir(exist_ok=True)
    for rule in rules:
        _save_rule_profile_chart(
            rule_segment_matrix,
            rule,
            by_rule_dir / f"{rule}.png",
        )

    # Cross-segment top findings: strongest risk differences across all axes
    all_stats = pd.concat([s for s in results["stats"].values() if not s.empty], ignore_index=True) \
        if any(not s.empty for s in results["stats"].values()) else pd.DataFrame()

    if not all_stats.empty:
        top = all_stats[all_stats["highlight"]].copy()
        top["abs_risk_diff"] = top["risk_difference"].abs()
        top = top.sort_values("abs_risk_diff", ascending=False).head(15)
        top.to_csv(out_dir / "top_cross_segment_findings.csv", index=False)
        results["top_findings"] = top
    else:
        results["top_findings"] = pd.DataFrame()

    # Evidence appendix: concatenate every segment's examples
    examples_all = pd.concat(
        [e for e in results["examples"].values() if not e.empty],
        ignore_index=True,
    ) if any(not e.empty for e in results["examples"].values()) else pd.DataFrame()
    if not examples_all.empty:
        examples_all.to_csv(out_dir / "examples_all.csv", index=False)
    results["examples_all"] = examples_all

    if verbose:
        print(f"\nWrote CSVs + charts to {out_dir}")

    return results


if __name__ == "__main__":
    run_segment_analysis()
