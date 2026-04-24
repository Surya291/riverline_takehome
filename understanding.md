## Quantitative vs Qualitative Rule Classification: Detection Difficulty Overview

Excellent question — this is exactly the kind of thinking that shows **eval design** maturity. Let me break down every rule into quantitative (deterministic) vs qualitative (requires NLU/judgment), with detection difficulty.

## Start Here: Annotation Categories and Rule Mapping

Use annotation categories as **calibration signals**, not strict ground truth. A single annotation category can map to multiple rule IDs, and one rule can align to multiple annotation categories.

### Annotator categories observed

- `misclassification`
- `repetition`
- `state_machine_error`
- `context_loss`
- `amount_error`
- `ignored_hardship`
- `wrong_information`
- `missed_escalation`
- `tone_mismatch`
- `inappropriate_pressure`
- `other`
- `stop_request_missed`
- `language_error`
- `compliance_concern`

### Practical mapping direction (category -> likely rules)

- `state_machine_error` -> `TR_INVALID_STATE_TRANSITION`, `TR_BACKWARD_TRANSITION_NOT_ALLOWED`, `TR_BACKWARD_EXCEPTION_MISUSED`, `INV_EXIT_STATE_NOT_FINAL`
- `repetition` -> `QLT_REPETITIVE_RESPONSE_LOOP`
- `context_loss` -> `QLT_CONTEXT_LOSS`
- `amount_error` -> `AMT_POS_GREATER_THAN_TOS`, `AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS`, `ACT_*` (context-linked amount/action mistakes)
- `ignored_hardship` -> `CMP_HARDSHIP_NO_ESCALATION`
- `missed_escalation` -> `CMP_ESCALATION_TRIGGER_MISSED`, `CMP_HARDSHIP_NO_ESCALATION`
- `stop_request_missed` -> `CMP_DNC_VIOLATION`
- `language_error` -> `CMP_LANGUAGE_MISMATCH`
- `misclassification` -> `INV_CLASSIFICATION_MISSING` (directly checkable), future classification-quality rules
- `tone_mismatch` / `inappropriate_pressure` -> `CMP_THREATENING_LANGUAGE`, `QLT_POOR_EMPATHY`
- `wrong_information` -> `QLT_CONTEXT_LOSS` (partial), future factual-consistency rules
- `compliance_concern` -> umbrella over `CMP_*` rules
- `other` -> manual audit bucket; convert recurring patterns into new rules

### How to use this mapping for severity

1. Keep a **base severity by spec risk tier** (Critical/High/Medium/Low).
2. Calibrate each rule with annotation overlap:
   - median annotated severity on matched turns
   - complaint/regulatory lift when the rule fires
3. Final rule severity should blend policy risk + observed annotation/outcome signal.

## Rule Classification Matrix

### Category 1: FULLY QUANTITATIVE (Deterministic, 95-100% confidence)

These can be checked with **zero ambiguity** using only structured fields.

---

#### **TR_INVALID_STATE_TRANSITION**

- **What it tracks:** State transition not in allowed set from spec Table 5
- **Data needed:** `state_transitions[].from_state`, `state_transitions[].to_state`
- **Logic:** 
  ```python
  if (from, to) not in ALLOWED_TRANSITIONS and not (from == to and from in PROGRESSION):
      violation
  ```
- **Difficulty:** **5/100** (trivial lookup)
- **Confidence:** 100% — either allowed or not, no interpretation needed
- **Current status:** ✅ Implemented in v1

---

#### **TR_BACKWARD_TRANSITION_NOT_ALLOWED**

- **What it tracks:** Going backward in state machine (except allowed exception)
- **Data needed:** `state_transitions[]` + state ordering
- **Logic:** Check if `to_state` comes before `from_state` in progression order
- **Difficulty:** **10/100** (need state ordering + exception handling)
- **Confidence:** 100% — purely structural
- **Current status:** ✅ Implemented in v1

---

#### **TR_BACKWARD_EXCEPTION_MISUSED**

- **What it tracks:** Using backward exception (settlement_explained/amount_pending → intent_asked) WITHOUT required `unclear+low` classification
- **Data needed:** `state_transitions[]`, `bot_classifications[turn]`
- **Logic:**
  ```python
  if (from, to) in BACKWARD_EXCEPTIONS:
      cls = bot_classifications[turn]
      if cls.classification != "unclear" or cls.confidence != "low":
          violation
  ```
- **Difficulty:** **15/100** (requires joining two data sources by turn)
- **Confidence:** 100% — spec is explicit about the condition
- **Current status:** ✅ Implemented in v1

---

#### **INV_EXIT_STATE_NOT_FINAL**

- **What it tracks:** Bot activity (messages or transitions) after entering terminal state (escalated/dormant)
- **Data needed:** `state_transitions[]`, `messages[].role`
- **Logic:**
  1. Find first transition into `escalated` or `dormant` → mark `terminal_turn`
  2. Check if any `messages[turn > terminal_turn].role == "bot"` exist
  3. Check if any `state_transitions[turn > terminal_turn].from_state` is terminal
- **Difficulty:** **20/100** (needs timeline ordering)
- **Confidence:** 100% — unambiguous spec rule (Invariant I2)
- **Current status:** ✅ Implemented in v1

---

#### **AMT_POS_GREATER_THAN_TOS**

- **What it tracks:** Principal outstanding > total outstanding (data inconsistency)
- **Data needed:** `metadata.pos`, `metadata.tos`
- **Logic:** `if pos > tos: violation`
- **Difficulty:** **3/100** (single comparison)
- **Confidence:** 100% — pure math
- **Current status:** ✅ Implemented in v1

---

#### **AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS**

- **What it tracks:** Settlement offer not in [POS, TOS] range
- **Data needed:** `metadata.pos`, `metadata.tos`, `metadata.settlement_offered`
- **Logic:** `if not (pos <= offered <= tos): violation`
- **Difficulty:** **5/100** (range check)
- **Confidence:** 100% — spec A3 is explicit
- **Note:** Current v1 assumes `pos` as floor; if `settlement_floor` field exists, update to that
- **Current status:** ✅ Implemented in v1

---

#### **ACT_REQUEST_SETTLEMENT_AMOUNT_INVALID_CONTEXT**

- **What it tracks:** `request_settlement_amount` function called WITHOUT matching `settlement_explained → amount_pending` transition on same turn
- **Data needed:** `function_calls[]`, `state_transitions[]`, both keyed by `turn`
- **Logic:**
  ```python
  for fn in function_calls where fn.function == "request_settlement_amount":
      transitions_at_turn = state_transitions[fn.turn]
      if ("settlement_explained", "amount_pending") not in transitions_at_turn:
          violation
  ```
- **Difficulty:** **15/100** (join by turn)
- **Confidence:** 100% — spec Actions table is explicit
- **Current status:** ✅ Implemented in v1

---

#### **ACT_CONFIRM_PAYMENT_INVALID_CONTEXT**

- **What it tracks:** `confirm_payment` function called WITHOUT matching `date_amount_asked → payment_confirmed` transition
- **Data needed:** Same as above, different transition pair
- **Difficulty:** **15/100**
- **Confidence:** 100%
- **Current status:** ✅ Implemented in v1

---

#### **TIM_FOLLOWUP_TOO_SOON** (v2 proposed)

- **What it tracks:** Bot-initiated message < 4 hours after previous bot-initiated message (spec Section 6.2)
- **Data needed:** `messages[].role`, `messages[].timestamp`
- **Logic:**
  1. Filter to bot turns that are NOT responses to borrower (tricky — need to check if previous turn was borrower or system event)
  2. Compute time gap between consecutive bot-initiated turns
  3. If gap < 4 hours → violation
- **Difficulty:** **35/100** (timestamp parsing + determining "bot-initiated" vs "response")
- **Confidence:** 90% — "bot-initiated" requires heuristic (e.g., is it after a long gap?)
- **Ambiguity:** What if borrower sent a message but bot didn't see it yet?
- **Current status:** ❌ Not implemented

---

#### **TIM_QUIET_HOURS_VIOLATION** (v2 proposed)

- **What it tracks:** Bot message sent during 9pm-9am local time (spec Section 6.1)
- **Data needed:** `messages[].timestamp`, `metadata.zone` (for timezone)
- **Logic:**
  1. Parse timestamp, convert to borrower's local timezone using `zone`
  2. Extract hour, check if 21:00 ≤ hour < 09:00
- **Difficulty:** **40/100** (timezone handling, data may not have explicit timezone)
- **Confidence:** 85% — depends on accurate timezone mapping for `zone` field
- **Ambiguity:** If `zone` is missing or ambiguous (e.g., "north" = multiple timezones)
- **Current status:** ❌ Not implemented

---

#### **TIM_DORMANCY_TIMEOUT_MISSED** (v2 proposed)

- **What it tracks:** Bot did not mark conversation dormant after 7 days of no borrower response (spec Section 6.3)
- **Data needed:** `messages[]`, `state_transitions[]`
- **Logic:**
  1. Find last borrower message timestamp
  2. Check if any bot activity occurred > 7 days later WITHOUT dormant transition
- **Difficulty:** **30/100** (timeline logic)
- **Confidence:** 95% — clear time threshold
- **Current status:** ❌ Not implemented

---

#### **INV_CLASSIFICATION_MISSING** (v2 proposed)

- **What it tracks:** Borrower message exists but no corresponding `bot_classifications` entry
- **Data needed:** `messages[]`, `bot_classifications[]`
- **Logic:**
  ```python
  borrower_turns = {m.turn for m in messages if m.role == "borrower"}
  classified_turns = {c.turn for c in bot_classifications}
  missing = borrower_turns - classified_turns
  ```
- **Difficulty:** **10/100** (set difference)
- **Confidence:** 100% — pure data completeness check
- **Current status:** ❌ Not implemented

---

#### **CMP_LANGUAGE_MISMATCH** (v2 proposed)

- **What it tracks:** Bot messages not in borrower's preferred language (spec Section 8.4)
- **Data needed:** `metadata.language`, `messages[].text` (requires language detection)
- **Logic:**
  1. For each bot message, detect language (e.g., using `langdetect` library or regex patterns)
  2. If detected language != `metadata.language` → violation
- **Difficulty:** **50/100** (language detection is probabilistic)
- **Confidence:** 75% — detection can fail on short messages, code-switching
- **Ambiguity:** Hinglish conversations mix Hindi/English — when is it a violation?
- **Current status:** ❌ Not implemented

---

### Category 2: HYBRID (Quantitative + Light NLU, 40-80% confidence)

These use **keywords + structural patterns** — deterministic matching but imperfect coverage.

---

#### **QLT_REPETITIVE_RESPONSE_LOOP** (v1)

- **What it tracks:** Bot sends identical or near-identical consecutive messages
- **Data needed:** `messages[].text` where `role == "bot"`
- **Logic:**
  ```python
  prev_text = None
  for msg in bot_messages:
      if msg.text == prev_text:  # exact match
          violation
      # OR: if levenshtein(msg.text, prev_text) < threshold
  ```
- **Difficulty:** **20/100** for exact match, **40/100** for fuzzy match
- **Confidence:** 
  - Exact match: 100% (but misses near-duplicates)
  - Fuzzy match: 70% (threshold tuning needed, may false-positive on template variations)
- **Current status:** ✅ Implemented in v1 (exact match only)
- **V2 improvement:** Add edit distance for "near-identical"

---

#### **CMP_HARDSHIP_NO_ESCALATION** (v2 proposed)

- **What it tracks:** Borrower signals hardship (job loss, medical emergency) but conversation does NOT escalate or go dormant
- **Data needed:** `messages[].text`, `state_transitions[]`
- **Logic:**
  1. Scan borrower messages for hardship keywords:
    - English: `job loss`, `unemployed`, `laid off`, `medical emergency`, `hospital`, `surgery`, `family emergency`, `death`
    - Hindi/Hinglish: `नौकरी`, `बीमार`, `अस्पताल`
  2. If keyword found in any borrower turn:
    - Check if conversation eventually transitions to `escalated` or `dormant`
    - If NO → violation
- **Difficulty:** **60/100** (keyword list incomplete, paraphrasing not caught)
- **Confidence:** 60% — catches explicit signals, misses:
  - Paraphrased hardship ("my situation is very bad")
  - Implicit signals ("I can't talk about this now")
  - Sarcasm/negation ("it's not like I lost my job" — actually means they did)
- **False positives:** Low (hardship keywords are unambiguous)
- **False negatives:** Moderate-High (40% of hardship cases may use different wording)
- **Current status:** ❌ Not implemented

---

#### **CMP_THREATENING_LANGUAGE** (v2 proposed)

- **What it tracks:** Bot uses threats (legal action, credit score damage, police, jail) — prohibited by spec Section 8.5
- **Data needed:** `messages[].text` where `role == "bot"`
- **Logic:** Scan bot messages for threat keywords:
  - `legal action`, `court`, `lawsuit`, `police`, `jail`, `arrest`, `credit score`, `credit report`, `ruin`, `blacklist`, `consequences`
- **Difficulty:** **55/100** (context matters — "we won't take legal action" is benign)
- **Confidence:** 65% — high false positive risk
  - "We don't want legal action" (benign)
  - "Avoiding legal consequences" (borderline)
  - Needs negation detection or phrase-level analysis
- **False positives:** Moderate (20-30% of keyword matches may be benign in context)
- **False negatives:** Low (explicit threats usually use these keywords)
- **Current status:** ❌ Not implemented

---

#### **CMP_DNC_VIOLATION** (v2 proposed)

- **What it tracks:** Borrower says "do not contact" but bot continues messaging (spec Section 8.3)
- **Data needed:** `messages[].text`, turn sequence
- **Logic:**
  1. Scan for DNC keywords in borrower messages:
    - `do not contact`, `don't call`, `stop messaging`, `unsubscribe`, `leave me alone`, `stop bothering`, `don't message`
  2. If found at turn N, check if any bot messages exist at turn > N
  3. If YES → violation
- **Difficulty:** **50/100** (keyword matching + timeline check)
- **Confidence:** 70% — catches explicit DNC, misses:
  - Indirect refusals ("I'm busy, not interested")
  - Ambiguous statements ("maybe later" — is this DNC?)
- **False positives:** Low if keywords are specific
- **False negatives:** Moderate (soft refusals not caught)
- **Current status:** ❌ Not implemented

---

### Category 3: QUALITATIVE (Requires Deep NLU, <60% confidence without ML)

These are **hard** — need semantic understanding, context, or judgment. Rule-based approaches will have high false positive/negative rates.

---

#### **QLT_CONTEXT_LOSS** (v2 proposed)

- **What it tracks:** Bot response ignores prior commitments or borrower-stated facts (spec Quality Q3)
- **Data needed:** `messages[]` full conversation history
- **Examples:**
  - Borrower T5: "I already paid last week"
  - Bot T6: "When can you pay?" ← context loss
  ---
  - Borrower T3: "I'll pay on the 15th"
  - Bot T7: "Can you commit to a date?" ← context loss
- **Logic (keyword-based, imperfect):**
  1. Scan borrower messages for commitment keywords:
    - `already paid`, `I paid`, `committed`, `I said`, `I told you`, `you said`, `last time`, `previous`
  2. If found, check if next bot message:
    - Repeats the same question (e.g., asks for date again)
    - Ignores the stated fact
  3. Detection heuristic: if bot message similarity to previous bot message > threshold → likely loop
- **Difficulty:** **75/100** (very hard without dialog state tracking)
- **Confidence:** 40% with keywords alone
  - **False negatives:** Most cases (60%+) — requires understanding:
    - Pronoun resolution ("it" refers to what?)
    - Temporal reasoning ("last week" vs "next week")
    - Implicit commitments ("maybe on Friday" is vague)
  - **False positives:** Moderate — bot may intentionally re-ask for confirmation
- **Better approach:** Would need:
  - Dialog state tracker (entities: payment_date, payment_amount, etc.)
  - Coreference resolution
  - OR: LLM-based classifier (but violates self-contained requirement for `evaluate()`)
- **Current status:** ❌ Not implemented

---

#### **CMP_ESCALATION_TRIGGER_MISSED** (general, v2)

- **What it tracks:** Borrower shows signs requiring escalation (disputes debt, mentions lawyer, requests supervisor) but bot does not escalate (spec Section 8.1)
- **Data needed:** `messages[].text`, `state_transitions[]`
- **Examples:**
  - "I already paid, this is wrong" → should escalate
  - "I want to talk to your manager" → should escalate
  - "My lawyer will handle this" → should escalate
- **Logic (keyword-based):**
  1. Scan for escalation trigger keywords:
    - `dispute`, `wrong amount`, `already paid`, `paid in full`, `not my debt`, `fraud`, `lawyer`, `attorney`, `supervisor`, `manager`, `speak to someone else`
  2. If found, check if conversation transitions to `escalated` within next 2-3 turns
- **Difficulty:** **70/100** (many triggers are context-dependent)
- **Confidence:** 50% — high false positive risk
  - "I'm not disputing, just asking" (benign)
  - "Can I talk to someone else about payment options?" (maybe not escalation-worthy)
- **False negatives:** High — implicit disputes ("this doesn't seem right") not caught
- **Current status:** ❌ Not implemented (overlap with hardship rule)

---

#### **QLT_POOR_EMPATHY** (spec Quality Q2)

- **What it tracks:** Bot response tone-deaf to borrower distress (spec says "appropriate empathy")
- **Data needed:** `messages[]`
- **Example:**
  - Borrower: "My father just passed away, I can't deal with this"
  - Bot: "Okay, so when can you pay?" ← poor empathy
- **Logic:** **Cannot be done with rules** — requires:
  - Sentiment analysis (detect borrower distress)
  - Response appropriateness evaluation (does bot acknowledgment match?)
  - Cultural norms (what counts as empathetic in Hindi vs English?)
- **Difficulty:** **95/100** (near-impossible without LLM)
- **Confidence:** <20% with keyword approach (too many edge cases)
- **Recommendation:** Skip for rule-based eval OR use human annotation as ground truth only
- **Current status:** ❌ Not feasible for v1/v2

---

#### **QLT_UNCLEAR_RESPONSE** (spec Quality Q4)

- **What it tracks:** Bot sends confusing/ambiguous messages that borrower asks to repeat or clarify
- **Data needed:** `messages[]`
- **Example:**
  - Bot T5: "You can settle for ₹1,71,500"
  - Borrower T6: "Is that the settlement or full amount?"
  - Bot T7: (same info again) ← unclear response
- **Logic (detection heuristic):**
  1. Scan borrower messages for clarification keywords:
    - `what do you mean`, `I don't understand`, `please explain`, `can you repeat`, `confused`, `not clear`
  2. If found, mark previous bot message as "unclear"
- **Difficulty:** **65/100** (detection okay, causality is hard)
- **Confidence:** 55% — catches explicit confusion, misses:
  - Borrower confusion that doesn't use these keywords
  - False positives: borrower may say "not clear" for reasons unrelated to bot clarity
- **Current status:** ❌ Not implemented

---

## Summary Table


| Rule ID                                         | Category     | Mapped annotation categories                                  | Confidence | Difficulty | V1/V2 Status                 |
| ----------------------------------------------- | ------------ | ------------------------------------------------------------- | ---------- | ---------- | ---------------------------- |
| `TR_INVALID_STATE_TRANSITION`                   | Quantitative | `state_machine_error`                                         | 100%       | 5/100      | ✅ V1                         |
| `TR_BACKWARD_TRANSITION_NOT_ALLOWED`            | Quantitative | `state_machine_error`                                         | 100%       | 10/100     | ✅ V1                         |
| `TR_BACKWARD_EXCEPTION_MISUSED`                 | Quantitative | `state_machine_error`, `misclassification`                    | 100%       | 15/100     | ✅ V1                         |
| `INV_EXIT_STATE_NOT_FINAL`                      | Quantitative | `state_machine_error`, `context_loss`, `missed_escalation`    | 100%       | 20/100     | ✅ V1                         |
| `AMT_POS_GREATER_THAN_TOS`                      | Quantitative | `amount_error`                                                | 100%       | 3/100      | ✅ V1                         |
| `AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS`           | Quantitative | `amount_error`, `wrong_information`                           | 100%       | 5/100      | ✅ V1                         |
| `ACT_REQUEST_SETTLEMENT_AMOUNT_INVALID_CONTEXT` | Quantitative | `state_machine_error`, `amount_error`, `wrong_information`    | 100%       | 15/100     | ✅ V1                         |
| `ACT_CONFIRM_PAYMENT_INVALID_CONTEXT`           | Quantitative | `state_machine_error`, `wrong_information`                    | 100%       | 15/100     | ✅ V1                         |
| `TIM_FOLLOWUP_TOO_SOON`                         | Quantitative | `inappropriate_pressure`, `compliance_concern`                | 90%        | 35/100     | ❌ V2                         |
| `TIM_QUIET_HOURS_VIOLATION`                     | Quantitative | `inappropriate_pressure`, `compliance_concern`                | 85%        | 40/100     | ❌ V2                         |
| `TIM_DORMANCY_TIMEOUT_MISSED`                   | Quantitative | `missed_escalation`, `state_machine_error`                    | 95%        | 30/100     | ❌ V2                         |
| `INV_CLASSIFICATION_MISSING`                    | Quantitative | `misclassification`, `other`                                  | 100%       | 10/100     | ❌ V2                         |
| `CMP_LANGUAGE_MISMATCH`                         | Quantitative | `language_error`, `tone_mismatch`                             | 75%        | 50/100     | ❌ V2                         |
| `QLT_REPETITIVE_RESPONSE_LOOP`                  | Hybrid       | `repetition`, `context_loss`                                  | 70-100%    | 20-40/100  | ✅ V1 (exact), improve in V2  |
| `CMP_HARDSHIP_NO_ESCALATION`                    | Hybrid       | `ignored_hardship`, `missed_escalation`, `compliance_concern` | 60%        | 60/100     | ❌ V2 (high priority)         |
| `CMP_THREATENING_LANGUAGE`                      | Hybrid       | `inappropriate_pressure`, `tone_mismatch`, `compliance_concern` | 65%      | 55/100     | ❌ V2                         |
| `CMP_DNC_VIOLATION`                             | Hybrid       | `stop_request_missed`, `compliance_concern`                   | 70%        | 50/100     | ❌ V2                         |
| `QLT_CONTEXT_LOSS`                              | Qualitative  | `context_loss`, `wrong_information`, `other`                  | 40%        | 75/100     | ❌ V2 (best-effort)           |
| `CMP_ESCALATION_TRIGGER_MISSED`                 | Qualitative  | `missed_escalation`, `ignored_hardship`, `compliance_concern` | 50%        | 70/100     | ❌ V2 (overlap with hardship) |
| `QLT_POOR_EMPATHY`                              | Qualitative  | `tone_mismatch`, `ignored_hardship`, `other`                  | <20%       | 95/100     | ❌ Skip (needs LLM)           |
| `QLT_UNCLEAR_RESPONSE`                          | Qualitative  | `misclassification`, `context_loss`, `other`                  | 55%        | 65/100     | ❌ Skip or V2 low-priority    |


---

## Recommendation for Your 1-Day Plan

**Implement these 3-4 v2 rules (prioritized by confidence × business impact):**

1. `**CMP_HARDSHIP_NO_ESCALATION`** (Hybrid, 60% conf) — High regulatory risk, good for writeup story
2. `**TIM_FOLLOWUP_TOO_SOON**` (Quant, 90% conf) — Shows spec precision, easy to validate
3. `**QLT_CONTEXT_LOSS**` (Qual, 40% conf) — Shows eval design thinking (meaningful vs easy), acknowledge limitations in writeup
4. **Optional: `CMP_DNC_VIOLATION`** (Hybrid, 70% conf) — Another compliance critical rule

**In your writeup, explicitly discuss this quantitative/qualitative split:**

- "I prioritized high-confidence quantitative rules for v1 to establish baseline accuracy"
- "Hybrid rules use keyword matching — I validated these with manual audit and found ~60% precision, which I considered acceptable for flagging review candidates"
- "Fully qualitative rules like context loss and empathy require LLM-based scoring, which violates the self-contained requirement; I defer these to human annotation or future ML models"

This shows **eval design maturity** — you understand the tradeoffs and made principled choices.

---

## Quick reference — how to use each data source in your submission

Keep this succinct in your writeup: one line per source describing its role and what to use it for.

- production_logs.jsonl (Logs)
  - Use for: primary detection. Build rule detectors, extract transitions/messages/functions/timestamps and compute per-conversation features (n_violations, top_rules, time-gaps).
  - Why: factual, machine-readable evidence of spec compliance or violations.

- annotations (annotator_*.jsonl)
  - Use for: calibration and qualitative validation. Compute annotator agreement, median severity per failure point, and sample disagreements for manual audit.
  - Why: human judgment to set severity priors, expose ambiguous edge cases, and justify trade-offs in the writeup.

- outcomes.jsonl
  - Use for: external validation and prioritization. Measure complaint/regulatory/payout signals against rule firings and risk_score; run confounder-aware regressions (channel_attribution, DPD, language).
  - Why: proves business relevance — rules that predict complaints/regulatory flags should be prioritized even if rare.

Recommendation: in the writeup include a short pipeline diagram (logs → evaluator → merge annotations → merge outcomes → analysis) and one compact table showing, per rule: n_fired, median_annotator_severity, complaint_rate_when_fired. This demonstrates you used all three sources meaningfully.