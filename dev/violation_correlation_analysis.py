#!/usr/bin/env python3
"""
Violation Correlation Analysis
==============================

Analyze which spec violations correlate with bad outcomes (complaints, regulatory flags).

This script:
1. Loads conversation bundle CSV (outcomes + metadata)  
2. Loads eval_v2.jsonl (violation data from AgentEvaluator)
3. Computes correlation between each violation type and bad outcomes
4. Provides statistical analysis and visualizations
"""

import pandas as pd
import numpy as np
import json
import ast
from pathlib import Path
import matplotlib.pyplot as plt
import seaborn as sns
from scipy.stats import chi2_contingency, fisher_exact
from collections import defaultdict, Counter
import warnings
warnings.filterwarnings('ignore')

def load_conversation_data(csv_path: str) -> pd.DataFrame:
    """Load and parse the conversation bundle CSV."""
    df = pd.read_csv(csv_path)
    
    # Parse the outcome JSON column
    def safe_eval(x):
        if pd.isna(x) or x == "":
            return {}
        try:
            return ast.literal_eval(x)
        except:
            return {}
    
    df['outcome_parsed'] = df['outcome'].apply(safe_eval)
    
    # Extract key outcome flags
    df['complaint_flag'] = df['outcome_parsed'].apply(lambda x: x.get('borrower_complained', False))
    df['regulatory_flag'] = df['outcome_parsed'].apply(lambda x: x.get('regulatory_flag', False))
    df['payment_received'] = df['outcome_parsed'].apply(lambda x: x.get('payment_received', False))
    df['required_intervention'] = df['outcome_parsed'].apply(lambda x: x.get('required_human_intervention', False))
    
    # Extract metadata
    df['metadata_parsed'] = df['metadata'].apply(safe_eval)
    df['language'] = df['metadata_parsed'].apply(lambda x: x.get('language', 'unknown'))
    df['zone'] = df['metadata_parsed'].apply(lambda x: x.get('zone', 'unknown'))
    df['dpd'] = df['metadata_parsed'].apply(lambda x: x.get('dpd', 0))
    
    return df

def load_eval_data(jsonl_path: str) -> pd.DataFrame:
    """Load evaluation results from JSONL."""
    records = []
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    
    eval_df = pd.DataFrame(records)
    
    # Extract violation counts per conversation
    violation_counts = defaultdict(lambda: defaultdict(int))
    
    for _, row in eval_df.iterrows():
        conv_id = row['conversation_id']
        for violation in row.get('violations', []):
            rule_id = violation['rule']
            violation_counts[conv_id][rule_id] += 1
    
    # Convert to DataFrame with binary flags (has violation yes/no)
    all_rules = set()
    for conv_violations in violation_counts.values():
        all_rules.update(conv_violations.keys())
    
    violation_df_data = []
    for conv_id in eval_df['conversation_id']:
        row = {'conversation_id': conv_id}
        row['quality_score'] = eval_df[eval_df['conversation_id'] == conv_id]['quality_score'].iloc[0]
        row['risk_score'] = eval_df[eval_df['conversation_id'] == conv_id]['risk_score'].iloc[0]
        row['total_violations'] = len(eval_df[eval_df['conversation_id'] == conv_id]['violations'].iloc[0])
        
        # Binary flags for each violation type
        for rule in sorted(all_rules):
            row[f'has_{rule}'] = int(violation_counts[conv_id][rule] > 0)
            row[f'count_{rule}'] = violation_counts[conv_id][rule]
        
        violation_df_data.append(row)
    
    return pd.DataFrame(violation_df_data)

def compute_correlations(merged_df: pd.DataFrame, outcome_cols: list, violation_cols: list) -> pd.DataFrame:
    """Compute correlation matrix between violations and bad outcomes."""
    
    results = []
    
    for outcome in outcome_cols:
        for violation in violation_cols:
            if violation not in merged_df.columns:
                continue
                
            # Create contingency table
            contingency = pd.crosstab(merged_df[violation], merged_df[outcome])
            
            if contingency.shape != (2, 2):
                # Skip if not a proper 2x2 table
                continue
            
            # Chi-square test
            try:
                chi2, p_chi2, _, _ = chi2_contingency(contingency)
            except:
                chi2, p_chi2 = np.nan, np.nan
            
            # Fisher's exact test (better for small samples)
            try:
                _, p_fisher = fisher_exact(contingency)
            except:
                p_fisher = np.nan
            
            # Effect size metrics
            n = contingency.sum().sum()
            if n > 0:
                # Odds ratio
                try:
                    odds_ratio = (contingency.iloc[1,1] * contingency.iloc[0,0]) / (contingency.iloc[1,0] * contingency.iloc[0,1])
                except:
                    odds_ratio = np.nan
                
                # Risk difference
                risk_with_violation = contingency.iloc[1,1] / (contingency.iloc[1,1] + contingency.iloc[1,0]) if (contingency.iloc[1,1] + contingency.iloc[1,0]) > 0 else 0
                risk_without_violation = contingency.iloc[0,1] / (contingency.iloc[0,1] + contingency.iloc[0,0]) if (contingency.iloc[0,1] + contingency.iloc[0,0]) > 0 else 0
                risk_difference = risk_with_violation - risk_without_violation
                
                # Prevalence
                violation_prevalence = merged_df[violation].mean()
                outcome_prevalence = merged_df[outcome].mean()
            else:
                odds_ratio, risk_difference, violation_prevalence, outcome_prevalence = np.nan, np.nan, np.nan, np.nan
            
            results.append({
                'violation': violation.replace('has_', ''),
                'outcome': outcome,
                'chi2_stat': chi2,
                'p_chi2': p_chi2,
                'p_fisher': p_fisher,
                'odds_ratio': odds_ratio,
                'risk_difference': risk_difference,
                'violation_prevalence': violation_prevalence,
                'outcome_prevalence': outcome_prevalence,
                'n_conversations': n,
                'contingency_table': str(contingency.values.tolist())
            })
    
    return pd.DataFrame(results)

def analyze_quality_risk_scores(merged_df: pd.DataFrame, outcome_cols: list) -> pd.DataFrame:
    """Analyze how quality/risk scores correlate with outcomes."""
    
    score_analysis = []
    
    for outcome in outcome_cols:
        outcome_true = merged_df[merged_df[outcome] == True]
        outcome_false = merged_df[merged_df[outcome] == False]
        
        if len(outcome_true) == 0 or len(outcome_false) == 0:
            continue
        
        # Quality score analysis
        quality_mean_true = outcome_true['quality_score'].mean()
        quality_mean_false = outcome_false['quality_score'].mean()
        quality_diff = quality_mean_true - quality_mean_false
        
        # Risk score analysis  
        risk_mean_true = outcome_true['risk_score'].mean()
        risk_mean_false = outcome_false['risk_score'].mean()
        risk_diff = risk_mean_true - risk_mean_false
        
        # Statistical tests
        from scipy.stats import ttest_ind
        quality_ttest = ttest_ind(outcome_true['quality_score'], outcome_false['quality_score'])
        risk_ttest = ttest_ind(outcome_true['risk_score'], outcome_false['risk_score'])
        
        score_analysis.append({
            'outcome': outcome,
            'quality_mean_with_outcome': quality_mean_true,
            'quality_mean_without_outcome': quality_mean_false,
            'quality_difference': quality_diff,
            'quality_pvalue': quality_ttest.pvalue,
            'risk_mean_with_outcome': risk_mean_true,
            'risk_mean_without_outcome': risk_mean_false,
            'risk_difference': risk_diff,
            'risk_pvalue': risk_ttest.pvalue,
            'n_with_outcome': len(outcome_true),
            'n_without_outcome': len(outcome_false)
        })
    
    return pd.DataFrame(score_analysis)

def create_visualizations(correlation_df: pd.DataFrame, merged_df: pd.DataFrame, outcome_cols: list):
    """Create correlation heatmaps and other visualizations."""
    
    # Filter for significant results
    sig_results = correlation_df[correlation_df['p_fisher'] < 0.05].copy()
    
    if len(sig_results) == 0:
        print("No statistically significant correlations found (p < 0.05)")
        return
    
    # Create heatmap of odds ratios for significant results
    plt.figure(figsize=(12, 8))
    
    # Pivot for heatmap
    heatmap_data = sig_results.pivot(index='violation', columns='outcome', values='odds_ratio')
    
    # Create heatmap
    sns.heatmap(heatmap_data, annot=True, fmt='.2f', cmap='RdYlBu_r', center=1.0,
                cbar_kws={'label': 'Odds Ratio'})
    plt.title('Odds Ratios for Significant Violation-Outcome Correlations\n(p < 0.05, OR > 1 means violation increases outcome risk)')
    plt.xlabel('Outcome Type')
    plt.ylabel('Violation Type')
    plt.xticks(rotation=45)
    plt.yticks(rotation=0)
    plt.tight_layout()
    plt.savefig('/Users/surya/Desktop/riverline_takehome/dev/violation_outcome_heatmap.png', dpi=150, bbox_inches='tight')
    plt.show()
    
    # Risk difference plot
    plt.figure(figsize=(12, 6))
    sig_results_sorted = sig_results.sort_values('risk_difference', ascending=True)
    
    colors = ['red' if x > 0 else 'blue' for x in sig_results_sorted['risk_difference']]
    
    plt.barh(range(len(sig_results_sorted)), sig_results_sorted['risk_difference'], color=colors, alpha=0.7)
    plt.yticks(range(len(sig_results_sorted)), [f"{row['violation']}\n→ {row['outcome']}" for _, row in sig_results_sorted.iterrows()])
    plt.xlabel('Risk Difference (% increase in bad outcome when violation present)')
    plt.title('Risk Difference for Significant Violation-Outcome Pairs')
    plt.axvline(x=0, color='black', linestyle='-', alpha=0.3)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig('/Users/surya/Desktop/riverline_takehome/dev/risk_difference_plot.png', dpi=150, bbox_inches='tight')
    plt.show()

def main():
    """Main analysis workflow."""
    
    print("=== Violation-Outcome Correlation Analysis ===\n")
    
    # Load data
    print("1. Loading data...")
    conv_df = load_conversation_data('/Users/surya/Desktop/riverline_takehome/dev/conversation_bundle_flat.csv')
    eval_df = load_eval_data('/Users/surya/Desktop/riverline_takehome/dev/eval_v2.jsonl')
    
    # Merge datasets
    merged_df = pd.merge(conv_df[['conversation_id', 'complaint_flag', 'regulatory_flag', 'payment_received', 'required_intervention', 'language', 'zone', 'dpd']], 
                        eval_df, on='conversation_id', how='inner')
    
    print(f"   - Loaded {len(conv_df)} conversations from CSV")
    print(f"   - Loaded {len(eval_df)} evaluations from JSONL")
    print(f"   - Merged dataset: {len(merged_df)} conversations")
    print(f"   - Outcome prevalence:")
    print(f"     • Complaints: {merged_df['complaint_flag'].mean():.1%}")
    print(f"     • Regulatory flags: {merged_df['regulatory_flag'].mean():.1%}")
    print(f"     • Payment received: {merged_df['payment_received'].mean():.1%}")
    print(f"     • Required intervention: {merged_df['required_intervention'].mean():.1%}")
    
    # Define outcome columns and violation columns
    outcome_cols = ['complaint_flag', 'regulatory_flag', 'required_intervention']
    violation_cols = [col for col in merged_df.columns if col.startswith('has_')]
    
    print(f"   - Found {len(violation_cols)} violation types")
    
    # 2. Compute correlations
    print("\n2. Computing violation-outcome correlations...")
    correlation_df = compute_correlations(merged_df, outcome_cols, violation_cols)
    
    # 3. Analyze quality/risk scores
    print("\n3. Analyzing quality/risk scores vs outcomes...")
    score_analysis_df = analyze_quality_risk_scores(merged_df, outcome_cols)
    
    # 4. Display results
    print("\n" + "="*70)
    print("KEY FINDINGS")
    print("="*70)
    
    # Top correlations by p-value
    significant = correlation_df[correlation_df['p_fisher'] < 0.05].sort_values('p_fisher')
    
    if len(significant) > 0:
        print(f"\n🔴 SIGNIFICANT CORRELATIONS (p < 0.05, n={len(significant)}):")
        print("-" * 60)
        
        for _, row in significant.head(10).iterrows():
            risk_pct = row['risk_difference'] * 100
            or_interpretation = "increases risk" if row['odds_ratio'] > 1 else "decreases risk"
            
            print(f"• {row['violation']}")
            print(f"  → {row['outcome']}: OR={row['odds_ratio']:.2f}, Risk Δ={risk_pct:+.1f}%, p={row['p_fisher']:.3f}")
            print(f"    ({or_interpretation} of {row['outcome'].replace('_', ' ')})")
            print()
        
        # Summary stats
        print(f"📊 SUMMARY STATISTICS:")
        print("-" * 30)
        print(f"Most predictive violations (by odds ratio):")
        top_or = significant.nlargest(5, 'odds_ratio')[['violation', 'outcome', 'odds_ratio', 'risk_difference']]
        for _, row in top_or.iterrows():
            print(f"  • {row['violation']} → {row['outcome']}: OR={row['odds_ratio']:.2f} (+{row['risk_difference']*100:.1f}%)")
        
    else:
        print("\n⚠️  No statistically significant correlations found (p < 0.05)")
        print("This could indicate:")
        print("  • Small sample sizes for bad outcomes")
        print("  • Violations are not strong predictors of these specific outcomes")
        print("  • Need different outcome metrics")
    
    # Quality/Risk score analysis
    print(f"\n📈 QUALITY/RISK SCORE ANALYSIS:")
    print("-" * 40)
    
    for _, row in score_analysis_df.iterrows():
        outcome = row['outcome'].replace('_', ' ')
        quality_sig = "***" if row['quality_pvalue'] < 0.001 else "**" if row['quality_pvalue'] < 0.01 else "*" if row['quality_pvalue'] < 0.05 else ""
        risk_sig = "***" if row['risk_pvalue'] < 0.001 else "**" if row['risk_pvalue'] < 0.01 else "*" if row['risk_pvalue'] < 0.05 else ""
        
        print(f"\n• {outcome.title()}:")
        print(f"  Quality: {row['quality_mean_with_outcome']:.3f} vs {row['quality_mean_without_outcome']:.3f} (Δ={row['quality_difference']:+.3f}) {quality_sig}")
        print(f"  Risk:    {row['risk_mean_with_outcome']:.3f} vs {row['risk_mean_without_outcome']:.3f} (Δ={row['risk_difference']:+.3f}) {risk_sig}")
    
    # Violation frequency analysis
    print(f"\n📊 VIOLATION FREQUENCY ANALYSIS:")
    print("-" * 40)
    
    violation_freq = {}
    for col in violation_cols:
        rule_name = col.replace('has_', '')
        freq = merged_df[col].mean()
        violation_freq[rule_name] = freq
    
    # Sort by frequency
    top_violations = sorted(violation_freq.items(), key=lambda x: x[1], reverse=True)[:10]
    
    print("Most common violations:")
    for violation, freq in top_violations:
        print(f"  • {violation}: {freq:.1%}")
    
    # 5. Create visualizations
    print(f"\n4. Creating visualizations...")
    try:
        create_visualizations(correlation_df, merged_df, outcome_cols)
        print("   ✓ Saved heatmap: dev/violation_outcome_heatmap.png")
        print("   ✓ Saved risk difference plot: dev/risk_difference_plot.png")
    except Exception as e:
        print(f"   ⚠️ Visualization error: {e}")
    
    # 6. Save detailed results
    correlation_df.to_csv('/Users/surya/Desktop/riverline_takehome/dev/violation_correlation_results.csv', index=False)
    score_analysis_df.to_csv('/Users/surya/Desktop/riverline_takehome/dev/score_analysis_results.csv', index=False)
    
    print(f"\n✅ Analysis complete!")
    print(f"   • Detailed results saved to:")
    print(f"     - dev/violation_correlation_results.csv")
    print(f"     - dev/score_analysis_results.csv")
    
    return correlation_df, score_analysis_df, merged_df

if __name__ == "__main__":
    correlation_df, score_analysis_df, merged_df = main()