# Riverline --- AI Engineer Take-Home

## About Riverline

Riverline builds AI agents that automate debt collection for lending companies in India. Our agents talk to borrowers over WhatsApp and voice calls --- negotiating payments, handling disputes, escalating sensitive cases, and managing multi-week follow-up workflows.

This is a high-stakes domain. A misclassified intent can send the wrong payment amount. A missed hardship signal can trigger a regulatory complaint. A stale context after a workflow handoff can confuse a borrower who already committed to pay.

## The Role

You'll build evals for our agent systems. You'll identify where agents fail, quantify how often, and build the measurement systems that tell us whether a change actually helped.

This assignment tests the core skills: understanding agent behaviour through formal specifications, discovering failure modes from data, handling ambiguous ground truth, and building evaluations that predict real-world outcomes.

## Requirements

- Python 3.10+
- Any operating system
- A PDF reader (for the specification document)

## Guidelines

- Use any tools you want --- LLMs, libraries, frameworks, whatever makes you effective.
- There are no right answers for many parts of this. Your reasoning matters more than your conclusions.
- Depth on fewer problems beats shallow coverage of everything.
- Questions? Reach out to jayanth@riverline.ai

## What's in the Repo

```
riverline-evals-takehome/
├── README.md                         # This document
├── spec.pdf                          # Agent specification (READ THIS FIRST)
├── eval_takehome.py                  # Your evaluator goes here
├── data/
│   ├── production_logs.jsonl         # 700 conversations
│   ├── outcomes.jsonl                # Outcomes (payment, complaints)
│   └── annotations/
│       ├── annotator_1.jsonl         # Quality labels from annotator 1
│       ├── annotator_2.jsonl         # Quality labels from annotator 2
│       └── annotator_3.jsonl         # Quality labels from annotator 3
└── docs/
    └── domain.md                     # Debt collection glossary & compliance rules
```

## The Specification

**Start here.** Read `spec.pdf` before looking at the data.

The specification defines the expected behaviour of our WhatsApp debt collection agent as a state machine. It includes:

- The set of valid states and transitions
- Actions and when they can be triggered
- Timing constraints
- Invariants that must hold
- Compliance requirements (some precise, some deliberately requiring judgment)
- Amount validation rules
- Quality expectations

Your job is to evaluate whether conversations conform to this specification, and to assess the quality and risk of each conversation.

## The Data

### Conversations

700 conversations from our agent system. Each conversation includes:

- **messages**: Full conversation with bot and borrower turns, timestamps
- **bot_classifications**: How the bot classified each borrower message (intent + confidence)
- **state_transitions**: What states the bot moved through (from_state, to_state, reason)
- **function_calls**: What actions the bot took (e.g., request_settlement_amount, escalate)
- **metadata**: Language, zone, DPD, POS, TOS, etc.

Messages have typos, code-switching between languages, voice-to-text artefacts, emoji-only responses, and long gaps between turns.

### Outcomes

Outcomes measured 30-60 days after each conversation:

```json
{
  "conversation_id": "conv_042",
  "payment_received": true,
  "days_to_payment": 45,
  "payment_amount": 45000,
  "expected_amount": 50000,
  "channel_attribution": "uncertain",
  "concurrent_channels": ["outbound_call", "field_visit"],
  "required_human_intervention": false,
  "borrower_complained": false,
  "regulatory_flag": false
}
```

Note: `channel_attribution` is often `"uncertain"`. Many borrowers interact through multiple channels simultaneously. Whether *this specific conversation* caused the payment is ambiguous. Your evaluation needs to account for this.

### Annotations

Three annotators independently labeled 200 conversations each, with 100 conversations labeled by all three. They were given the same rubric but interpreted it differently.

Each annotation includes:
```json
{
  "conversation_id": "conv_042",
  "quality_score": 0.7,
  "failure_points": [
    {"turn": 3, "category": "tone_mismatch", "severity": 0.8, "note": "..."},
    {"turn": 8, "category": "missed_escalation", "severity": 1.0, "note": "..."}
  ],
  "risk_flags": ["compliance_concern"],
  "overall_assessment": "Effective but risky"
}
```

They disagree on ~30% of overlapping labels. This reflects genuine ambiguity in what "good" means for a debt collection conversation. How you handle disagreement is important.

## What You Need to Build

### 1. An Evaluator

Build an `AgentEvaluator` class in `eval_takehome.py`. Given a conversation and the specification, it should produce quality scores, risk assessments, and specific spec violations:

```python
class AgentEvaluator:
    def evaluate(self, conversation: dict) -> dict:
        return {
            "quality_score": float,    # 0-1, higher = better
            "risk_score": float,       # 0-1, higher = more risky
            "violations": [
                {
                    "turn": int,
                    "rule": str,           # which spec rule was violated
                    "severity": float,     # 0-1
                    "explanation": str
                }
            ]
        }
```

Your evaluator should be self-contained --- it will be run on our machine against additional conversations not included in this repo. Do not make external API calls inside `evaluate()`.

### 2. A Violation Report

Analyse the 700 conversations against the specification. Create `violations.md` documenting:

- What types of spec violations are most common
- Which violations correlate with bad outcomes (complaints, regulatory flags)
- Statistical analysis: violation rates by borrower segment (language, DPD, temperament)
- Specific conversation examples with evidence

Be specific --- reference spec sections, turn numbers, and conversation IDs.

### 3. A Writeup

A **4-page maximum** document (`writeup.md`) covering:

- **Methodology** --- How did you approach this? How did you map spec rules to conversation data?
- **Annotator Disagreement** --- How do the three annotators differ? How did you handle disagreement?
- **Findings** --- What did you learn about the agent's behaviour? What patterns predict bad outcomes?
- **Limitations** --- Where does your evaluation fail? What would you need to do better?
- **If you had 3 months** --- How would you build a production eval system? How would you close the loop from eval to agent improvement?

The writeup is weighted heavily. Clear thinking with simple code beats complex code with no explanation.

## Submission

Send the following to jayanth@riverline.ai:

- A GitHub repo with your `eval_takehome.py`, `violations.md`, `writeup.md`, and any supporting analysis
- A 5-minute Loom recording walking through your approach and key findings

## What We're Looking For

- **Spec reasoning** --- Can you map a formal specification to messy conversation data?
- **Analytical rigour** --- Do you form hypotheses and test them, or pattern-match and guess?
- **Statistical literacy** --- Do you understand confounders, correlation vs. causation, and inter-annotator agreement?
- **Domain reasoning** --- Do you understand *why* certain failures matter in debt collection?
- **Eval design** --- Do your metrics measure something meaningful, or just something easy?
- **Communication** --- Can you explain complex findings clearly?
