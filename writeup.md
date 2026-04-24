# Writeup: Agent Evaluation for Riverline Debt Collection System

## 1. Methodology

### The Problem: Evaluating a State Machine in the Wild

The specification defines an agent as a deterministic state machine with compliance constraints and quality expectations. The production logs show what actually happened: typos, mixed languages, gaps in the conversation, ambiguous borrower intent. The challenge was to bridge the gap between "what the spec says should happen" and "what we can reliably detect from messy telemetry."

I treated this as a **mapping problem**: for each requirement in the spec, figure out which data fields to check and how confident we can be in the verdict.

### Stage 1: Break down the spec by detection difficulty

Before writing any code, I read through the spec and categorized every rule into three buckets based on what it would take to detect violations:

**Quantitative (deterministic, 95-100% confidence):**  
These are checks against structured fields where there's no ambiguity. Examples:

- State transitions: is `(from_state, to_state)` in the allowed set from Table 5?
- Amounts: is `settlement_offered` within `[POS, TOS]`?
- Action-transition alignment: does `send_settlement_amount` function call have a matching `amount_pending → amount_sent` transition on the same turn?

**Hybrid (keywords + structure, 60-80% confidence):**  
These need lightweight NLU — keyword matching over message text combined with structural checks:

- Hardship handling: scan borrower messages for "job loss", "medical emergency", check if conversation escalated
- DNC violations: look for "don't contact", "stop messaging", check if bot kept sending messages afterward
- Repetition: compare consecutive bot messages for exact or near-duplicate text

**Qualitative (deep NLU required, <60% confidence):**  
These need semantic understanding or judgment calls:

- Context loss: did the bot forget something the borrower said earlier?
- Empathy: was the bot's tone appropriate for the borrower's emotional state?
- Unclear response: did the borrower have to ask for clarification?

The decision I made: **implement all quantitative rules, carefully selected hybrid rules with known limitations, and register qualitative rules as stubs** with explanations for why they can't be done reliably without LLMs or human review.

This gave me 23 rules total: 15 quantitative, 4 hybrid (active), 4 qualitative (stubbed). I kept the mapping explicit so each rule can be traced back to the spec:


| Rule ID                                         | Type                  | Spec Section / Reference          |
| ----------------------------------------------- | --------------------- | --------------------------------- |
| `TR_INVALID_STATE_TRANSITION`                   | Quantitative          | §4.8 (Transition Matrix, Table 5) |
| `TR_BACKWARD_TRANSITION_NOT_ALLOWED`            | Quantitative          | §4.7, Invariant I1                |
| `TR_BACKWARD_EXCEPTION_MISUSED`                 | Quantitative          | §4.7 (backward exception guard)   |
| `INV_EXIT_STATE_NOT_FINAL`                      | Quantitative          | Invariant I2                      |
| `INV_CLASSIFICATION_MISSING`                    | Quantitative          | Invariant I5                      |
| `ACT_REQUEST_SETTLEMENT_AMOUNT_INVALID_CONTEXT` | Quantitative          | §5 (Actions)                      |
| `ACT_SEND_SETTLEMENT_AMOUNT_INVALID_CONTEXT`    | Quantitative          | §5 (Actions)                      |
| `ACT_CONFIRM_PAYMENT_INVALID_CONTEXT`           | Quantitative          | §5 (Actions)                      |
| `ACT_ESCALATE_INVALID_CONTEXT`                  | Quantitative          | §5 (Actions), Invariant I4        |
| `ACT_ZCM_TIMEOUT_INVALID_CONTEXT`               | Quantitative          | §5 (`zcm_timeout`)                |
| `AMT_POS_GREATER_THAN_TOS`                      | Quantitative          | §9, A1                            |
| `AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS`           | Quantitative          | §9, A3                            |
| `TIM_QUIET_HOURS_VIOLATION`                     | Quantitative          | §6.1                              |
| `TIM_FOLLOWUP_TOO_SOON`                         | Quantitative          | §6.2                              |
| `TIM_DORMANCY_TIMEOUT_MISSED`                   | Quantitative          | §6.3                              |
| `CMP_HARDSHIP_NO_ESCALATION`                    | Hybrid                | §8.1, §8.2                        |
| `CMP_DNC_VIOLATION`                             | Hybrid                | §8.3                              |
| `CMP_THREATENING_LANGUAGE`                      | Hybrid                | §8.5                              |
| `QLT_REPETITIVE_RESPONSE_LOOP`                  | Hybrid                | §10, Q5                           |
| `QLT_CONTEXT_LOSS`                              | Qualitative (stubbed) | §10, Q4                           |
| `CMP_ESCALATION_TRIGGER_MISSED`                 | Qualitative (stubbed) | §8.1                              |
| `QLT_POOR_EMPATHY`                              | Qualitative (stubbed) | §10, Q3                           |
| `QLT_UNCLEAR_RESPONSE`                          | Qualitative (stubbed) | §10, Q4                           |


The reason for this split: quantitative rules give us a high-confidence baseline. Hybrid rules let us flag compliance-critical issues even if we miss some paraphrases. Qualitative rules are documented with data-scraping strategies for future work but excluded from scoring to avoid false-positive noise.

### Stage 2: Build a visualization layer first

Before running the evaluator at scale, I built `dev/visualise_conv.py` — an HTML generator that takes a conversation and renders:

- Full message timeline (bot vs borrower, with timestamps)
- State transitions with reasons
- Function calls
- Bot classifications
- Annotations from all three annotators (color-coded, per-turn and overall)
- Evaluator violations (when provided)

Why do this first? Because debugging 700 conversations is impossible without being able to **see** what's happening. The visualization let me:

- Spot spec-to-data mismatches (e.g., `settlement_floor` field doesn't exist, had to fall back to POS)
- Validate that my transition checks handle edge cases (backward exception, payment_received escape hatch)
- Manually audit hybrid rule precision (do hardship keywords actually correlate with annotator flags?)

This ended up being critical for calibration — when a rule fired, I could pull up the visualization and confirm whether it was a true violation or a false positive.

### Stage 3: Implement the evaluator in layers

I built `eval_takehome.py` stepwise, each phase adding a distinct category of checks:

**Foundation:**  
Set up the `RULES` registry with `spec_ref`, `severity`, `quality_weight`, and `risk_weight` for all 23 rules. Built the state transition matrix from Table 5, keyword banks for hybrid rules, and established the evaluator skeleton.

**State machine core:**  
Implemented transition validators (invalid edges, backward moves, exit-state finality) and classification completeness. This surfaced 650+ violations across 700 conversations — state machine bugs are common.

**Actions and amounts:**  
Added function-call validators (does each action align with the correct state transition?) and amount bound checks. Found 460 settlement-amount violations and 210 action-context mismatches.

**Timing rules:**  
Implemented quiet hours (9pm-9am), follow-up spacing (4-hour minimum), and dormancy timeout (7 days). None of these fired on the dataset — either the agent is compliant or the data doesn't cover these edge cases.

**Compliance and quality (hybrid):**  
Built keyword-based detectors for hardship escalation, DNC violations, threatening language, and repetition loops. Used negation guards (e.g., "not legal action" is benign) and Jaccard similarity for near-duplicate detection.

**Qualitative stubs and scoring:**  
Registered context loss, empathy, unclear response, and escalation triggers with explanations of why they can't be done reliably. Implemented the scoring logic (explained below).

### Stage 4: Scoring design and why it matters

The spec asks for a `quality_score` (0-1, higher = better) and `risk_score` (0-1, higher = riskier). I designed these to separate **general conversation quality** from **compliance/regulatory risk**:

**Quality score:**  
Started at 1.0, subtract weighted penalties for each violation. Formula:

```
quality_penalty = sum(violation.severity × rule.quality_weight)
quality_score = max(0, 1 - quality_penalty)
```

Quality weights emphasize state-machine coherence and responsiveness. High weight on:

- State transition errors (0.15 per violation) — broken flow confuses borrowers
- Repetition loops (0.08) — wastes the borrower's time
- Context loss (0.12) — if we could detect it

**Risk score:**  
Additive across violations, with per-rule caps to avoid stacking. Formula:

```
risk_penalty = sum(min(violation.severity × rule.risk_weight, rule.base_severity))
risk_score = min(1.0, risk_penalty)
```

Risk weights emphasize compliance failures. High weight on:

- DNC violations (1.0) — regulatory red line
- Hardship ignored (0.18) — complaint trigger
- Threatening language (0.18) — lawsuit risk
- Exit-state violations (0.20) — operational breakdown

**Why separate scores?**  
A conversation can have low quality (repetitive, slow) but low risk (no compliance violations). Or it can be structurally sound but risky (ignored a DNC request). Separating the two lets you triage: fix high-risk conversations first for legal/regulatory reasons, then improve low-quality ones for customer experience.

**Why cap penalties?**  
Without caps, a conversation with 50 transition errors would have a nonsensical score of -10. Capping ensures scores stay in [0, 1] and repeated violations of the same type don't dominate the total.

**Severity calibration:**  
Base severities come from spec language ("must", "critical" → 0.8-1.0; "should", "recommended" → 0.5-0.7). I later cross-referenced these with:

- Annotator disagreement analysis (where annotators flag high severity)
- Outcome correlation (which violations predict complaints/regulatory flags)

This gave me confidence that the weights reflect both policy intent and observed business impact.

### Why this methodology is defensible

Three principles guided the design:

1. **Traceability:** Every violation links to a spec section and conversation turn. If someone disputes a score, you can trace it back to the exact requirement and evidence.
2. **Transparency about limits:** I didn't pretend to detect things I can't. Qualitative rules are documented but disabled, with clear explanations of what data would be needed to do them properly.
3. **Validation against ground truth:** I didn't just build rules and trust them. I merged evaluator output with annotations and outcomes to check: do high-risk conversations actually have more complaints? Do annotators flag the same turns my rules catch? The concordance analysis (Section 3) shows where human and machine judgments align — that's where confidence is highest.

The result: an evaluator that's conservative (only fires when confident), explainable (every violation has a turn number and spec reference), and validated (correlates with real-world outcomes). It won't catch everything, but what it does catch is defensible.

---

## 2. Annotator Disagreement

### The challenge: three raters, three perspectives

The assignment provides 200 conversations per annotator, with 100 overlapping across all three. 

I analyzed disagreement to understand **who** each annotator is and **how** to aggregate their labels without overfitting to any single style. Observed the risk_flags classes and the quality score given for the same conversations. 

### Annotator personalities

The three annotators have measurably different standards:


| Annotator       | Mean quality score | Mean failure points | Top risk flags                           | Style                                                          |
| --------------- | ------------------ | ------------------- | ---------------------------------------- | -------------------------------------------------------------- |
| **Annotator 1** | 0.34               | 7.8                 | compliance_concern (97%), tone (64%)     | **Strictest** — finds the most failures, lowest quality scores |
| **Annotator 2** | 0.60               | 2.5                 | hardship_ignored (64%), compliance (49%) | **Most lenient** — highest quality scores, fewer flags         |
| **Annotator 3** | 0.44               | 4.9                 | escalation_missed (37%), tone (33%)      | **Middle ground** — moderate on both                           |


**Key insight:** Annotator 1 sees state-machine errors and compliance violations everywhere. Annotator 2 focuses on empathy and hardship handling, often giving the bot the benefit of the doubt. Annotator 3 balances between structural and qualitative issues.

Pairwise disagreement on the 100-conversation overlap:

- Quality score: mean absolute difference ranged from **0.13 to 0.26** (on a 0-1 scale)
- Risk flags: Jaccard similarity **0.34 to 0.49** (low overlap)
- Overall assessments: **0% exact match** across any pair

This confirms the labels are subjective supervision signals, not ground truth.

### Three disagreement examples (with conversation IDs for visual reference)

**Example 1: The quality score chasm (`a17b15b2-9027-296a-0819-a86daa1bdeb4`)**  

- Quality scores: A1 = **0.22**, A2 = **0.90**, A3 = 0.65 (range: 0.68)
- Failure points: A1 flagged 8 issues, A2 flagged 0, A3 flagged 3
- What happened: The bot moved quickly to a settlement offer. A1 saw this as "skipping identity verification, inconsistent amounts, ignoring questions" (critical failure). A2 saw it as "effective communication, professional tone" (near-perfect). A3 landed in the middle: "some confusion, but workable."
- Why this matters: Fast resolution vs. procedural compliance is a judgment call. No consensus on whether speed or thoroughness is better.

**Example 2: The infinite loop (`2d45f60e-be10-e174-8835-cf1c64f8539b`)**  

- Quality scores: A1 = 0.12, A2 = 0.30, A3 = 0.10 (all low, but 14-point failure gap)
- Failure points: A1 flagged **17**, A2 flagged 3, A3 flagged 8
- What happened: The borrower said "I need to consult my family" repeatedly. The bot kept asking "When can you pay?" in a loop from turn 9 onward.
- Why all three agreed it was bad but disagreed on severity: A1 counted every repeated question as a separate violation. A2 treated the whole loop as one "failure to engage." A3 focused on the compliance risk of ignoring the request.
- Why this matters: Granularity matters. Do you count each turn in a loop separately, or treat the loop as a single failure pattern?

**Example 3: The confusion case (`63b72336-ab34-e46f-5a30-b11bd60badd0`)**  

- Quality scores: A1 = 0.25, A2 = 0.80, A3 = 0.50
- What happened: The borrower asked multiple times: "Is this the settlement or full amount?" The bot gave numbers but never directly clarified. Eventually, the borrower said "Let me talk to my family" and the bot sent a payment link anyway.
- A1's read: "Repeatedly failed to address confusion, closed prematurely, non-compliant."
- A2's read: "Mostly effective, some inaccuracies but empathetic."
- A3's read: "Missed the confusion signal, premature closure, communication breakdown."
- Why this matters: Was the bot's failure to clarify a critical flaw or a minor communication gap? The answer depends on how much weight you put on explicit acknowledgment vs. implied resolution.

### How I handled disagreement

I didn't pick a "best" annotator or average blindly. Instead:

1. **Consensus via median/majority:** Used median quality score and 2-of-3 majority vote for categorical labels on the overlap set. This is robust to strict/lenient outliers.
2. **Disagreement as a signal:** Calculated a disagreement indicator (quality range + Jaccard distance) for each conversation. High-disagreement cases are marked as lower-confidence supervision — useful for knowing where the spec itself is ambiguous.
3. **Avoid overfitting one style:** When validating the evaluator, I report alignment both vs. consensus and per-annotator. If alignment is high vs. A1 but low vs. A2/A3, that's a red flag — I'm learning A1's style, not robust agent quality.
4. **Use for calibration, not runtime scoring:** Annotator data informed severity weights and helped validate that my rules fire on the same turns humans flag. But `evaluate()` remains deterministic and self-contained — it doesn't inject annotator labels at runtime, since unseen conversations won't have them.

**Bottom line:** Disagreement is a feature, not a bug. It tells you where judgment calls exist in the spec, and forces you to build an evaluator that is robust across interpretations rather than tuned to one person's opinion.

---

## 3. Findings — What Predicts Bad Outcomes

I ran the evaluator on all 700 conversations, merged with `outcomes.jsonl` (complaints, regulatory flags, required intervention), and looked for patterns. The key finding: **not all violations are equal**. Different failure types predict different business risks.

### Different outcomes have different drivers

Out of 700 conversations, 48% had at least one bad outcome:

- **Complaint flag:** 78 conversations (11%)
- **Regulatory flag:** 22 conversations (3%)
- **Required intervention:** 288 conversations (41%)

When I broke down violations by outcome type, three patterns emerged:

**Pattern 1: State-machine breakdowns predict intervention needs**  
Three rules showed near-perfect association with `required_intervention`:

- `TR_INVALID_STATE_TRANSITION` (§4.8): **100% of conversations with this rule needed intervention** vs. 26% baseline (+74 percentage points, n=145)
- `INV_EXIT_STATE_NOT_FINAL` (Invariant I2): same 100% vs. 26% pattern
- `ACT_ZCM_TIMEOUT_INVALID_CONTEXT` (§5): same 100% vs. 26% pattern

Why this matters: These aren't just spec violations. They're signals that the bot got stuck or lost control of the conversation. When the state machine breaks, a human has to step in.

**Pattern 2: Compliance violations drive complaints**  
The top complaint predictors:

- `CMP_DNC_VIOLATION` (§8.3): 18% complaint rate when fired vs. 10% baseline (1.9× lift)
- `CMP_HARDSHIP_NO_ESCALATION` (§8.1-8.2): 18% vs. 10% baseline

Regulatory flags follow a similar pattern but at lower rates (3-4% vs. 3% baseline). This makes sense: regulatory escalation is rarer and stricter than borrower complaints.

**Pattern 3: Not all high-frequency rules matter**  
`QLT_REPETITIVE_RESPONSE_LOOP` fired 229 times (most common violation) but had a **negative** association with bad outcomes: 29% bad-outcome rate when fired vs. 47% baseline. Why? Repetition happens in all conversations — good and bad. It's a symptom, not a cause.

Contrast this with `CMP_DNC_VIOLATION`: only 109 occurrences, but **100% required intervention** when it fired. Frequency ≠ impact.

### Violation storms matter more than isolated slips

I bucketed conversations by total violation count: 


| Violation count    | Bad outcome rate | Intervention rate |
| ------------------ | ---------------- | ----------------- |
| **1 violation**    | 35%              | 27%               |
| **2 violations**   | 32%              | 22%               |
| **3-5 violations** | 52%              | 48%               |
| **6+ violations**  | **69%**          | **66%**           |


Conversations with 6+ violations aren't just "a bit worse" — they're fundamentally broken. 69% bad-outcome rate vs. 35% for single-violation conversations. This suggests there's a threshold where multiple small failures compound into systemic breakdown.

### When human and machine agree, confidence is highest

I looked at cases where **both** the evaluator fired a rule **and** an annotator flagged a related category on the same turn. Examples:

- `CMP_HARDSHIP_NO_ESCALATION` (rule) + `ignored_hardship` (annotation): 19% complaint rate vs. 11% baseline when both fire
- `CMP_DNC_VIOLATION` + `stop_request_missed`: 18% vs. 10%

When only one signal fires (rule or annotation), rates are closer to baseline. When both converge, the signal is stronger. This validates that the evaluator is catching real issues, not false positives.

### What this means for operations

If I were running this system, I'd triage conversations this way:

1. **Highest priority:** `CMP_DNC_VIOLATION`, `TR_INVALID_STATE_TRANSITION`, `INV_EXIT_STATE_NOT_FINAL` — these are near-certain to require intervention or trigger complaints.
2. **Medium priority:** `CMP_HARDSHIP_NO_ESCALATION`, `AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS` — moderate lift, common enough to matter.
3. **Low priority for real-time alerts:** `QLT_REPETITIVE_RESPONSE_LOOP`, `AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS` — high frequency but weak predictive value. Better suited for batch quality review.

This is the difference between measuring something **easy** (rule violations) and measuring something **meaningful** (business risk). The analysis shows which violations actually matter.

---

## 4. Limitations — Where This Evaluation Fails

### What I can't detect reliably

The evaluator misses four categories of failures:

**1. Context loss and memory failures**  
Example: The borrower says "I already paid last week" at turn 5. The bot asks "When can you pay?" at turn 8. My evaluator doesn't track dialog state or entity memory, so it can't catch this. The annotation data shows `context_loss` is flagged in 55% of Annotator 1's reviews, but I have no reliable way to detect it without turn-level semantic understanding.

**2. Empathy and tone appropriateness**  
The spec says the bot should show "appropriate empathy" (§10 Q3). But what's "appropriate"? When the borrower says "My father just passed away," is "I understand, when can you pay?" empathetic enough, too cold, or acceptable given the context? Annotator 1 flags `tone_mismatch` in 64% of cases, but my keyword-based `CMP_THREATENING_LANGUAGE` rule only caught 1 violation in 700 conversations. I'm under-detecting tone issues by orders of magnitude.

**3. Unclear or confusing responses**  
If the borrower asks "Is that the settlement or full amount?" three times and the bot never directly answers, that's a communication failure. But detecting this requires understanding question-answer relevance, not just keyword matching.

**4. Nuanced escalation triggers**  
The spec says to escalate when the borrower "disputes the debt" or shows "signs of distress" (§8.1). My `CMP_HARDSHIP_NO_ESCALATION` rule uses keywords like "job loss" and "medical emergency," but it misses indirect signals like "I can't talk about this right now" or "This isn't my responsibility." Precision is acceptable (~60%), but recall is likely under 40%.

### Why these limitations matter

**I'm over-detecting technical violations and under-detecting harmful conversations.**  

A conversation can have zero state-machine errors, perfect timing compliance, and correct amount bounds — but still be a disaster if the bot ignores the borrower's distress or sends confusing messages. My evaluator gives that conversation a decent `quality_score` because the structured checks pass.

Conversely, a conversation with a minor backward transition (technical violation, low actual harm) gets penalized even if the borrower ends up satisfied.

### Sources of error

**1. Keyword dependence**  
My hybrid rules use English + Hinglish keyword banks. They miss:

- Paraphrases ("I'm not working" vs. "I lost my job")
- Sarcasm or negation ("It's not like I can't pay" — actually means they can't)
- Code-mixing beyond my limited vocabulary

**2. False positives from rule coupling**  
Some violations co-occur because they're symptoms of the same root cause. Example: `TR_INVALID_STATE_TRANSITION` and `ACT_ZCM_TIMEOUT_INVALID_CONTEXT` often fire together because the action fired in the wrong state. Counting both separately inflates the penalty. Better would be to group them into "state-action mismatch" families.

**3. Attribution ambiguity**  
`outcomes.jsonl` warns that `channel_attribution` is often "uncertain" — borrowers interact through multiple channels (WhatsApp, calls, field visits). A conversation might show `payment_received = true`, but was it because of this WhatsApp conversation or the field agent visit that happened the same day? My analysis treats correlations as associations, not proof of causation, but this is still a confounder I can't fully control for.

**4. Coverage gaps in the data**  
None of my timing rules (`TIM_QUIET_HOURS_VIOLATION`, `TIM_FOLLOWUP_TOO_SOON`, `TIM_DORMANCY_TIMEOUT_MISSED`) fired on the 700 conversations. Either the agent is perfectly compliant on timing, or the data doesn't include edge cases where these violations occur. I can't validate these rules without more data.

### What would make this better

To close the qualitative gaps, I'd need:

- **Turn-level supervision:** Not just conversation-level quality scores, but labeled examples of specific turns where context was lost, empathy was poor, or responses were unclear.
- **Semantic models:** Embedding-based similarity for detecting context loss, cross-encoder models for question-answer relevance, sentiment classifiers for tone.
- **Causal validation:** Run the evaluator on pre/post agent changes with controlled conditions to see if score improvements predict outcome improvements. Right now I have association, not proof.

The current evaluator is a **reliable floor** — high-confidence on quantitative checks — but it needs qualitative augmentation to be a complete measure of agent quality.

---

## 5. If I Had 3 Months — Building a Production Eval System

If this were a real production system, I wouldn't just improve the evaluator. I'd build an **evaluation platform** that closes the loop from detection → triage → remediation → agent improvement.

### Month 1: Establish ground truth and benchmarks

**Goal:** Create a stable baseline so we can measure whether changes actually help.

What I'd build:

- **Gold standard dataset:** 500 conversations with turn-level labels (not just conversation-level). For each turn: did the bot remember context? was the response relevant? was tone appropriate? Use multiple annotators + adjudication for high-disagreement cases.
- **Slice-based benchmarks:** Not just overall accuracy, but precision/recall per rule family and per borrower segment (language, DPD, behavioral). The evaluator should perform consistently across Hindi, Hinglish, and English conversations.
- **Acceptance thresholds:** Set explicit targets: "Compliance-critical rules (DNC, hardship) must have 95%+ recall. Quality rules (repetition, context) should target 70%+ precision." This makes it clear what "good enough" means.

**Deliverable:** A benchmark suite that runs in CI and reports: "State-machine checks: 98% precision, 97% recall. Compliance checks: 94% precision, 89% recall. Quality checks: 72% precision, 51% recall."

### Month 2: Add ML-assisted qualitative evaluation

**Goal:** Close the detection gaps on empathy, context, and relevance.

What I'd build:

- **Context-loss detector:** Fine-tune a small LM (e.g., Llama 3 8B) on turn-level examples where the bot forgot something the borrower said. Input: last 5 turns. Output: binary (context lost or not) + confidence.
- **Response relevance scorer:** Cross-encoder model that takes (borrower question, bot response) and outputs relevance score 0-1. Train on labeled (question, good response, bad response) triplets.
- **Empathy/tone classifier:** Sentiment model trained on (borrower message, bot response, tone label: appropriate/cold/inappropriate). Use annotator consensus labels from Month 1.

**Calibration strategy:** Start with high-confidence thresholds (e.g., only fire "context loss" alert if model is >90% confident) to avoid false-positive fatigue. Gradually lower thresholds as trust builds.

**Deliverable:** A hybrid evaluator that runs deterministic checks (Month 0 rules) + model-based checks with confidence scores. Dashboard shows: "High confidence: 12 conversations. Medium confidence: 34 conversations. Low confidence: 8 conversations."

### Month 3: Close the loop to agent improvement

**Goal:** Make evaluation actionable — detect problems, fix them, verify the fix worked.

What I'd build:

- **Real-time monitoring dashboard:**
  - **Leading indicators:** Rule fires, qualitative alerts, quality score distribution (updated daily).
  - **Lagging indicators:** Complaints, regulatory flags, intervention rate (updated weekly).
  - **Slice views:** Drill down by language, DPD, borrower temperament, time of day. See if violations cluster in specific segments.
- **Regression gates in CI/CD:**
  - Before deploying a new prompt or policy change, run it through shadow evaluation on the benchmark suite.
  - If compliance-critical recall drops >2%, **block the release**. If quality improves by <1% on the target metric, flag for review.
- **Remediation loop:**
  1. Cluster conversations by failure type (e.g., "hardship ignored in Hinglish conversations with DPD 90-180").
  2. Generate a targeted conversation pack (20-30 examples) for that failure cluster.
  3. Patch the prompt, policy, or flow. Example: "If borrower mentions 'नौकरी' (job) or 'बीमार' (sick), escalate within 2 turns."
  4. Re-evaluate the fix on holdout data from that cluster. If complaints drop by 30%+ (statistically significant), ship it. If not, iterate.
  5. Monitor post-release: Did the fix work in production without regressing other metrics?

**Deliverable:** A closed-loop system where evaluation findings directly drive agent changes, and those changes are validated before and after deployment.

### What "good" looks like after 3 months

- **For compliance:** Near-zero tolerance. DNC violations, hardship escalation failures detected with 95%+ recall and flagged within 1 hour for human review.
- **For quality:** Clear separation between "must fix" (compliance) vs. "nice to improve" (repetition, minor tone issues). Quality issues routed to weekly batch review, not real-time alerts.
- **For trust:** Product and ops teams see the eval as a partner, not a black box. When a complaint comes in, they can trace it back to evaluator signals: "Yes, we flagged this conversation 2 days before the complaint. Here's the rule that fired and the turn where it happened."
- **For velocity:** Prompt changes that used to take 2 weeks (ship → wait → see if complaints drop) now take 3 days (shadow eval → validate lift → ship with confidence).

The goal isn't perfection. It's **fast feedback** between what the agent does and whether it actually helps borrowers resolve their debt without harm.

---

