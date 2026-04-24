#!/usr/bin/env python3
"""
Simple Violation-Outcome Correlation Analysis
==============================================

Minimal analysis using only standard library + basic stats.
This version works without pandas/scipy for environments where those aren't available.
"""

import json
import ast
import csv
from collections import defaultdict, Counter
import math

def load_csv_data(csv_path):
    """Load conversation data from CSV."""
    conversations = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Parse outcome JSON
            try:
                outcome = ast.literal_eval(row['outcome']) if row['outcome'] else {}
            except:
                outcome = {}

            # Handle n_annotations field to set annotated flag

            n_annotations = int(row['n_annotations'])
            # col value should be 0, 1, 2, or 3; annotated True if > 0, else False
            if n_annotations in [1, 2, 3]:
                annotated = True
            elif n_annotations == 0:
                annotated = False


            conversations.append({
                'conversation_id': row['conversation_id'],
                'complaint_flag': outcome.get('borrower_complained', False),
                'regulatory_flag': outcome.get('regulatory_flag', False),
                'payment_received': outcome.get('payment_received', False),
                'required_intervention': outcome.get('required_human_intervention', False),
                'annotated': annotated
            })
    
    return conversations

def load_eval_data(jsonl_path):
    """Load evaluation results from JSONL."""
    evaluations = []
    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                evaluations.append(json.loads(line))
    
    return evaluations

def compute_simple_correlations(conversations, evaluations):
    """Compute basic correlation metrics."""
    
    # Create lookup for evaluations
    eval_lookup = {ev['conversation_id']: ev for ev in evaluations}
    
    # Build violation flags per conversation
    conversation_violations = {}
    all_rules = set()
    
    for conv in conversations:
        conv_id = conv['conversation_id']
        if conv_id not in eval_lookup:
            continue
            
        violations = eval_lookup[conv_id].get('violations', [])
        violation_rules = [v['rule'] for v in violations]
        all_rules.update(violation_rules)
        
        # Binary flags for each violation type
        conversation_violations[conv_id] = {
            'violations': set(violation_rules),
            'outcomes': conv
        }
    
    print(f"Found {len(all_rules)} unique violation types:")
    rule_counts = Counter()
    for conv_data in conversation_violations.values():
        rule_counts.update(conv_data['violations'])
    
    for rule, count in rule_counts.most_common():
        print(f"  • {rule}: {count} conversations ({count/len(conversation_violations):.1%})")
    
    print(f"\nOutcome prevalence:")
    outcome_counts = {
        'complaint_flag': 0,
        'regulatory_flag': 0, 
        'required_intervention': 0,
        'payment_received': 0
    }
    
    for conv_data in conversation_violations.values():
        for outcome in outcome_counts:
            if conv_data['outcomes'][outcome]:
                outcome_counts[outcome] += 1
    
    total_convs = len(conversation_violations)
    for outcome, count in outcome_counts.items():
        print(f"  • {outcome}: {count} ({count/total_convs:.1%})")
    
    # Compute correlations for bad outcomes
    bad_outcomes = ['complaint_flag', 'regulatory_flag', 'required_intervention']
    
    print(f"\n" + "="*60)
    print("VIOLATION-OUTCOME CORRELATIONS")
    print("="*60)
    
    results = []
    
    for outcome in bad_outcomes:
        print(f"\n📊 {outcome.replace('_', ' ').title()}:")
        print("-" * 30)
        
        # Count conversations with/without outcome
        outcome_true = [conv_data for conv_data in conversation_violations.values() 
                       if conv_data['outcomes'][outcome]]
        outcome_false = [conv_data for conv_data in conversation_violations.values() 
                        if not conv_data['outcomes'][outcome]]
        
        if len(outcome_true) == 0:
            print(f"  ⚠️ No conversations with {outcome} - cannot analyze")
            continue
            
        print(f"  • With {outcome}: {len(outcome_true)} conversations")
        print(f"  • Without {outcome}: {len(outcome_false)} conversations")
        
        # For each violation type, compute correlation
        rule_results = []
        
        for rule in sorted(all_rules):
            # 2x2 contingency table
            # violation_yes_outcome_yes, violation_yes_outcome_no
            # violation_no_outcome_yes, violation_no_outcome_no
            
            viol_yes_out_yes = len([c for c in outcome_true if rule in c['violations']])
            viol_yes_out_no = len([c for c in outcome_false if rule in c['violations']])
            viol_no_out_yes = len(outcome_true) - viol_yes_out_yes
            viol_no_out_no = len(outcome_false) - viol_yes_out_no
            
            # Skip if no instances of violation
            if viol_yes_out_yes + viol_yes_out_no == 0:
                continue
                
            # Compute odds ratio
            if viol_no_out_yes == 0 or viol_yes_out_no == 0:
                odds_ratio = float('inf') if viol_no_out_yes == 0 else 0
            else:
                odds_ratio = (viol_yes_out_yes * viol_no_out_no) / (viol_yes_out_no * viol_no_out_yes)
            
            # Risk difference
            risk_with_violation = viol_yes_out_yes / (viol_yes_out_yes + viol_yes_out_no) if (viol_yes_out_yes + viol_yes_out_no) > 0 else 0
            risk_without_violation = viol_no_out_yes / (viol_no_out_yes + viol_no_out_no) if (viol_no_out_yes + viol_no_out_no) > 0 else 0
            risk_difference = risk_with_violation - risk_without_violation
            
            # Chi-square test (simplified)
            total = viol_yes_out_yes + viol_yes_out_no + viol_no_out_yes + viol_no_out_no
            expected_vyoy = (viol_yes_out_yes + viol_yes_out_no) * (viol_yes_out_yes + viol_no_out_yes) / total
            expected_vyon = (viol_yes_out_yes + viol_yes_out_no) * (viol_yes_out_no + viol_no_out_no) / total
            expected_vnoy = (viol_no_out_yes + viol_no_out_no) * (viol_yes_out_yes + viol_no_out_yes) / total
            expected_vnon = (viol_no_out_yes + viol_no_out_no) * (viol_yes_out_no + viol_no_out_no) / total
            
            chi_square = 0
            for observed, expected in [(viol_yes_out_yes, expected_vyoy), (viol_yes_out_no, expected_vyon), 
                                     (viol_no_out_yes, expected_vnoy), (viol_no_out_no, expected_vnon)]:
                if expected > 0:
                    chi_square += (observed - expected) ** 2 / expected
            
            rule_results.append({
                'rule': rule,
                'odds_ratio': odds_ratio,
                'risk_difference': risk_difference,
                'chi_square': chi_square,
                'contingency': [viol_yes_out_yes, viol_yes_out_no, viol_no_out_yes, viol_no_out_no],
                'risk_with_violation': risk_with_violation,
                'risk_without_violation': risk_without_violation
            })
        
        # Sort by effect size and show top results
        rule_results.sort(key=lambda x: x['contingency'][0], reverse=True)
   
        
        print(f"\n  🔴 Top correlations for {outcome}:")
        for i, result in enumerate(rule_results[:5]):
            if result['risk_difference'] == 0:
                continue
            direction = "increases" if result['odds_ratio'] > 1 else "decreases"

            matrix = [
                ["",                "Outcome: Yes",  "Outcome: No"],
                ["Violation: Yes",  f"{result['contingency'][0]}", f"{result['contingency'][1]}"],
                ["Violation: No",   f"{result['contingency'][2]}", f"{result['contingency'][3]}"]
            ]

            print(f"    {i+1}. {result['rule']}")
            print(f"       OR: {result['odds_ratio']:.2f}, Risk Δ: {result['risk_difference']*100:+.1f}%")
            print(f"       {direction} risk of {outcome.replace('_', ' ')}")

            print("       2x2 Contingency Table:")
            for row in matrix:
                print("          " + " | ".join(f"{cell:^14}" for cell in row))

            print(f"       Risk w/ violation:    {result['risk_with_violation']*100:.1f}%")
            print(f"       Risk w/o violation:   {result['risk_without_violation']*100:.1f}%")
            print(f"       Chi-square (approx):  {result['chi_square']:.2f}")
            print()
   
        results.extend([(outcome, r) for r in rule_results])
    
    return results

def main():
    print("=== Simple Violation-Outcome Correlation Analysis ===\n")
    
    # Load data
    conversations = load_csv_data('/Users/surya/Desktop/riverline_takehome/dev/conversation_bundle_flat.csv')
    evaluations = load_eval_data('/Users/surya/Desktop/riverline_takehome/dev/eval_v2.jsonl')
    
    print(f"Loaded {len(conversations)} conversations and {len(evaluations)} evaluations")
    
    # Compute correlations
    results = compute_simple_correlations(conversations, evaluations)
    
    print("\n" + "="*60)
    print("SUMMARY")  
    print("="*60)
    
    # Overall insights
    significant_correlations = [r for outcome, r in results if abs(r['risk_difference']) > 0.05]  # >5% risk difference
    
    if significant_correlations:
        print(f"\n🎯 Found {len(significant_correlations)} potentially meaningful correlations (>5% risk difference):")
        
        # Sort by risk difference
        significant_correlations.sort(key=lambda x: abs(x['risk_difference']), reverse=True)
        
        for result in significant_correlations[:10]:
            direction = "↑" if result['risk_difference'] > 0 else "↓"
            print(f"  • {result['rule']}: {direction} {abs(result['risk_difference'])*100:.1f}% risk")
    else:
        print("\n⚠️ No strong correlations found (>5% risk difference)")
        print("This suggests:")
        print("  • Low base rates of bad outcomes make detection difficult")
        print("  • Violations may not strongly predict these specific outcomes")
        print("  • Larger sample sizes may be needed")
    
    print(f"\n✅ Analysis complete!")

if __name__ == "__main__":
    main()