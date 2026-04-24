"""
Riverline Evals Take-Home Assignment
=====================================

Deterministic, rule-first AgentEvaluator for WhatsApp debt collection
conversations. Every rule is mapped directly to a section of the spec
(`spec.pdf`). Rules that require deep NLU are registered for taxonomy
completeness but their detectors are disabled and return no violations
(they rely on external model calls that are not allowed inside
``evaluate``).

Run locally from this directory:
    python eval_takehome.py
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------
# Each entry maps to a spec section. `severity` is the baseline severity
# emitted when the rule fires. `quality_weight` and `risk_weight` scale how
# much that severity contributes to the final quality and risk scores.
# `disabled` rules are registered for taxonomy completeness but their
# detectors always return an empty list (see Commit 6 in the plan).
#
# Weight philosophy:
#   - Compliance/invariant rules  -> high risk_weight (legal/regulatory)
#   - Amount/action rules         -> balanced (correctness + risk)
#   - Timing rules                -> medium (policy, not safety)
#   - Soft quality rules          -> high quality_weight, low risk_weight

RULES: dict[str, dict[str, Any]] = {
    # ---- Transitions (spec §4) -------------------------------------------
    "TR_INVALID_STATE_TRANSITION": {
        "spec_ref": "§4.8 Transition Matrix (Table 5)",
        "severity": 0.8,
        "quality_weight": 0.6,
        "risk_weight": 0.4,
    },
    "TR_BACKWARD_TRANSITION_NOT_ALLOWED": {
        "spec_ref": "§4.7, Invariant I1",
        "severity": 0.85,
        "quality_weight": 0.6,
        "risk_weight": 0.4,
    },
    "TR_BACKWARD_EXCEPTION_MISUSED": {
        "spec_ref": "§4.7 Allowed Backward Transition",
        "severity": 0.6,
        "quality_weight": 0.5,
        "risk_weight": 0.3,
    },

    # ---- Invariants (spec §7) --------------------------------------------
    "INV_EXIT_STATE_NOT_FINAL": {
        "spec_ref": "Invariant I2",
        "severity": 1.0,
        "quality_weight": 0.7,
        "risk_weight": 0.9,
    },
    "INV_CLASSIFICATION_MISSING": {
        "spec_ref": "Invariant I5",
        "severity": 0.7,
        "quality_weight": 0.55,
        "risk_weight": 0.3,
    },

    # ---- Actions (spec §5, I4) -------------------------------------------
    "ACT_REQUEST_SETTLEMENT_AMOUNT_INVALID_CONTEXT": {
        "spec_ref": "§5 request_settlement_amount",
        "severity": 0.7,
        "quality_weight": 0.5,
        "risk_weight": 0.35,
    },
    "ACT_SEND_SETTLEMENT_AMOUNT_INVALID_CONTEXT": {
        "spec_ref": "§5 send_settlement_amount",
        "severity": 0.75,
        "quality_weight": 0.55,
        "risk_weight": 0.4,
    },
    "ACT_CONFIRM_PAYMENT_INVALID_CONTEXT": {
        "spec_ref": "§5 confirm_payment",
        "severity": 0.8,
        "quality_weight": 0.55,
        "risk_weight": 0.45,
    },
    "ACT_ESCALATE_INVALID_CONTEXT": {
        "spec_ref": "§5 escalate, Invariant I4",
        "severity": 1.0,
        "quality_weight": 0.6,
        "risk_weight": 0.75,
    },
    "ACT_ZCM_TIMEOUT_INVALID_CONTEXT": {
        "spec_ref": "§5 zcm_timeout",
        "severity": 0.75,
        "quality_weight": 0.5,
        "risk_weight": 0.4,
    },

    # ---- Amounts (spec §9) -----------------------------------------------
    "AMT_POS_GREATER_THAN_TOS": {
        "spec_ref": "§9 A1",
        "severity": 0.9,
        "quality_weight": 0.5,
        "risk_weight": 0.5,
    },
    "AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS": {
        "spec_ref": "§9 A3",
        "severity": 0.8,
        "quality_weight": 0.5,
        "risk_weight": 0.5,
    },

    # ---- Timing (spec §6) ------------------------------------------------
    "TIM_QUIET_HOURS_VIOLATION": {
        "spec_ref": "§6.1 Quiet Hours (7pm-8am IST)",
        "severity": 0.8,
        "quality_weight": 0.4,
        "risk_weight": 0.6,
    },
    "TIM_FOLLOWUP_TOO_SOON": {
        "spec_ref": "§6.2 Follow-up Spacing (4h)",
        "severity": 0.65,
        "quality_weight": 0.45,
        "risk_weight": 0.35,
    },
    "TIM_DORMANCY_TIMEOUT_MISSED": {
        "spec_ref": "§6.3 Dormancy Timeout (7d)",
        "severity": 0.7,
        "quality_weight": 0.5,
        "risk_weight": 0.4,
    },

    # ---- Compliance (spec §8) -------------------------------------------
    "CMP_HARDSHIP_NO_ESCALATION": {
        "spec_ref": "§8.1, §8.2 Hardship Handling",
        "severity": 0.9,
        "quality_weight": 0.6,
        "risk_weight": 0.85,
    },
    "CMP_DNC_VIOLATION": {
        "spec_ref": "§8.3 Do Not Contact",
        "severity": 1.0,
        "quality_weight": 0.7,
        "risk_weight": 1.0,
    },
    "CMP_THREATENING_LANGUAGE": {
        "spec_ref": "§8.5 No Threats",
        "severity": 0.9,
        "quality_weight": 0.6,
        "risk_weight": 0.9,
    },

    # ---- Quality (spec §10) ----------------------------------------------
    "QLT_REPETITIVE_RESPONSE_LOOP": {
        "spec_ref": "§10 Q5 No Repetition",
        "severity": 0.35,
        "quality_weight": 0.5,
        "risk_weight": 0.1,
    },

    # ---- Qualitative stubs (registered, not evaluated) -------------------
    # These require dialog-state tracking or LLM-based semantic scoring,
    # which violates the self-contained requirement of evaluate(). They
    # stay in the registry so severity / weight can be calibrated against
    # annotations in future iterations.
    "QLT_CONTEXT_LOSS": {
        "spec_ref": "§10 Q4 Remembering Context",
        "severity": 0.7,
        "quality_weight": 0.6,
        "risk_weight": 0.3,
        "disabled": True,
    },
    "CMP_ESCALATION_TRIGGER_MISSED": {
        "spec_ref": "§8.1 Escalation Triggers",
        "severity": 0.85,
        "quality_weight": 0.55,
        "risk_weight": 0.8,
        "disabled": True,
    },
    "QLT_POOR_EMPATHY": {
        "spec_ref": "§10 Q3 Appropriate Tone",
        "severity": 0.6,
        "quality_weight": 0.6,
        "risk_weight": 0.3,
        "disabled": True,
    },
    "QLT_UNCLEAR_RESPONSE": {
        "spec_ref": "§10 Q4 Remembering Context (unclear responses)",
        "severity": 0.5,
        "quality_weight": 0.5,
        "risk_weight": 0.2,
        "disabled": True,
    },
}


# ---------------------------------------------------------------------------
# State machine constants (spec §2, §4)
# ---------------------------------------------------------------------------

PROGRESSION_STATES: set[str] = {
    "new",
    "message_received",
    "verification",
    "intent_asked",
    "settlement_explained",
    "amount_pending",
    "amount_sent",
    "date_amount_asked",
    "payment_confirmed",
}
EXIT_STATES: set[str] = {"escalated", "dormant"}

# Allowed non-self transitions from spec Table 5. Self-transitions for
# progression states are always allowed and are handled separately.
_ALLOWED_TRANSITIONS: set[tuple[str, str]] = {
    ("new", "message_received"),
    ("message_received", "verification"),
    ("verification", "intent_asked"),
    ("intent_asked", "settlement_explained"),
    ("settlement_explained", "amount_pending"),
    ("amount_pending", "amount_sent"),
    ("amount_sent", "date_amount_asked"),
    ("date_amount_asked", "payment_confirmed"),
}
# Any progression state may escalate or go dormant.
for _s in PROGRESSION_STATES:
    _ALLOWED_TRANSITIONS.add((_s, "escalated"))
    _ALLOWED_TRANSITIONS.add((_s, "dormant"))

# Conditional backward moves (spec §4.7). Only valid when the borrower
# classification at that turn is `unclear` with `low` confidence.
_BACKWARD_EXCEPTIONS: set[tuple[str, str]] = {
    ("settlement_explained", "intent_asked"),
    ("amount_pending", "intent_asked"),
}

# Spec §4: any progression state may jump to `payment_confirmed` when a
# `payment_received` system event is recorded in the transition `reason`.
_PAYMENT_EVENT_REASON_TOKEN = "payment_received"

# Spec §5 action-to-transition mapping used by _check_actions.
_ACTION_REQUIRED_TRANSITION: dict[str, tuple[str, str]] = {
    "request_settlement_amount": ("settlement_explained", "amount_pending"),
    "send_settlement_amount":    ("amount_pending", "amount_sent"),
    "confirm_payment":           ("date_amount_asked", "payment_confirmed"),
}
_ACTION_RULE_ID: dict[str, str] = {
    "request_settlement_amount": "ACT_REQUEST_SETTLEMENT_AMOUNT_INVALID_CONTEXT",
    "send_settlement_amount":    "ACT_SEND_SETTLEMENT_AMOUNT_INVALID_CONTEXT",
    "confirm_payment":           "ACT_CONFIRM_PAYMENT_INVALID_CONTEXT",
}


# ---------------------------------------------------------------------------
# Timing constants (spec §6)
# ---------------------------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))
_QUIET_HOUR_START = 19  # 7pm
_QUIET_HOUR_END   = 8   # 8am
_FOLLOWUP_MIN_GAP = timedelta(hours=4)
_DORMANCY_LIMIT   = timedelta(days=7)


# ---------------------------------------------------------------------------
# Keyword banks for hybrid compliance rules (spec §8)
# ---------------------------------------------------------------------------
# These lists are intentionally conservative. They favour precision over
# recall: a word only lands in a bank if it has a clear domain meaning. The
# writeup documents the known false-negative rate for each rule.

_HARDSHIP_KEYWORDS: tuple[str, ...] = (
    # English
    "lost my job", "lost job", "laid off", "unemployed", "no income",
    "job loss", "no salary", "salary stopped",
    "medical emergency", "hospital", "surgery", "health crisis",
    "family emergency", "family crisis", "death in family",
    "passed away", "financial crisis", "cannot afford", "can't afford",
    "bankrupt", "bankruptcy",
    # Hindi / Hinglish
    "नौकरी चली", "नौकरी छूट", "बीमार", "अस्पताल",
    "paisa nahi", "paise nahi", "kaam nahi", "naukri chali",
    "naukri gayi", "naukri chutt", "hospital mein", "ghar mein emergency",
)

_DNC_KEYWORDS: tuple[str, ...] = (
    # English
    "do not contact", "don't contact", "dont contact",
    "stop messaging", "stop calling", "stop contacting",
    "don't call", "dont call", "don't message", "dont message",
    "leave me alone", "stop bothering", "stop harassing",
    "never contact", "never call", "never message",
    "unsubscribe", "remove my number",
    # Hindi / Hinglish
    "mujhe mat call", "call mat karo", "message mat karo",
    "band karo", "pareshan mat karo",
)

_THREAT_KEYWORDS: tuple[str, ...] = (
    "legal action", "take legal", "file a case", "file case",
    "court", "lawsuit", "sue you", "we will sue",
    "police", "arrest", "jail", "criminal",
    "property seizure", "seize", "attach property",
    "credit score", "credit report", "blacklist",
    "public embarrassment", "publicly", "ruin your",
    "serious consequences", "face consequences",
)

# Tokens that indicate a threat keyword is being negated (benign context).
_NEGATION_TOKENS: tuple[str, ...] = (
    "not", "never", "won't", "wont", "don't", "dont", "no",
    "without", "avoid", "prevent",
)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class AgentEvaluator:
    """Evaluate a single conversation against the spec.

    The evaluator is fully self-contained: no external API calls, no
    network, no heavy dependencies. All rules documented in
    ``problem_statement/understanding.md`` are registered in ``RULES``.
    Quantitative rules are always active; hybrid rules use curated
    keyword banks; qualitative stubs are registered but disabled.
    """

    def __init__(self) -> None:
        self.rules = RULES

    # ----- public API -----------------------------------------------------
    def evaluate(self, conversation: dict) -> dict:
        messages = conversation.get("messages", []) or []
        transitions = sorted(
            conversation.get("state_transitions", []) or [],
            key=lambda t: (t.get("turn", -1), t.get("to_state", "")),
        )
        function_calls = conversation.get("function_calls", []) or []
        classifications = conversation.get("bot_classifications", []) or []
        metadata = conversation.get("metadata", {}) or {}

        class_by_turn: dict[int, dict] = {
            c.get("turn"): c for c in classifications if c.get("turn") is not None
        }

        violations: list[dict] = []
        violations += self._check_transitions(transitions, class_by_turn)
        violations += self._check_exit_state_invariant(transitions, messages)
        violations += self._check_classification_missing(messages, classifications)
        violations += self._check_actions(function_calls, transitions)
        violations += self._check_amounts(metadata)
        violations += self._check_timing(messages, transitions)
        violations += self._check_compliance(messages, transitions)
        violations += self._check_repetition(messages)

        # Qualitative stubs are intentionally skipped. They stay in RULES
        # for taxonomy completeness but produce no violations here.
        violations += self._check_context_loss(messages)
        violations += self._check_escalation_trigger(messages, transitions)
        violations += self._check_empathy(messages)
        violations += self._check_unclear_response(messages)

        quality_score, risk_score = self._score(violations)
        return {
            "quality_score": quality_score,
            "risk_score": risk_score,
            "violations": violations,
        }

    # ----- Commit 2: transitions + invariants -----------------------------
    def _check_transitions(
        self,
        transitions: list[dict],
        class_by_turn: dict[int, dict],
    ) -> list[dict]:
        out: list[dict] = []
        for t in transitions:
            frm = t.get("from_state")
            to  = t.get("to_state")
            turn = t.get("turn", -1)
            reason = (t.get("reason") or "").lower()

            if frm == to and frm in PROGRESSION_STATES:
                continue  # self-transitions always allowed for progression
            if (frm, to) in _ALLOWED_TRANSITIONS:
                continue

            # `payment_received` system event: jump to payment_confirmed
            if (
                to == "payment_confirmed"
                and frm in PROGRESSION_STATES
                and _PAYMENT_EVENT_REASON_TOKEN in reason
            ):
                continue

            # Conditional backward exception: valid only with unclear+low.
            if (frm, to) in _BACKWARD_EXCEPTIONS:
                cls = class_by_turn.get(turn) or {}
                if cls.get("classification") == "unclear" and cls.get("confidence") == "low":
                    continue
                out.append(self._mk_violation(
                    turn=turn,
                    rule_id="TR_BACKWARD_EXCEPTION_MISUSED",
                    explanation=(
                        f"Backward transition {frm} -> {to} used without the "
                        f"required unclear+low classification at turn {turn} "
                        f"(classification={cls.get('classification')}, "
                        f"confidence={cls.get('confidence')})."
                    ),
                ))
                continue

            # Any other move between progression states is illegal backward.
            if frm in PROGRESSION_STATES and to in PROGRESSION_STATES:
                out.append(self._mk_violation(
                    turn=turn,
                    rule_id="TR_BACKWARD_TRANSITION_NOT_ALLOWED",
                    explanation=(
                        f"Illegal backward transition {frm} -> {to}; only "
                        f"settlement_explained/amount_pending -> intent_asked "
                        f"is allowed under the backward exception."
                    ),
                ))
                continue

            out.append(self._mk_violation(
                turn=turn,
                rule_id="TR_INVALID_STATE_TRANSITION",
                explanation=(
                    f"Transition {frm} -> {to} is not permitted by the spec "
                    f"transition matrix (§4.8 Table 5)."
                ),
            ))
        return out

    def _check_exit_state_invariant(
        self,
        transitions: list[dict],
        messages: list[dict],
    ) -> list[dict]:
        out: list[dict] = []
        entered_turn: int | None = None

        for t in transitions:
            frm = t.get("from_state")
            to  = t.get("to_state")
            turn = t.get("turn", -1)

            if entered_turn is None and to in EXIT_STATES:
                entered_turn = turn
                continue
            if entered_turn is not None and frm in EXIT_STATES:
                out.append(self._mk_violation(
                    turn=turn,
                    rule_id="INV_EXIT_STATE_NOT_FINAL",
                    explanation=(
                        f"Transition leaves terminal state {frm} at turn "
                        f"{turn}; exit states are final per Invariant I2."
                    ),
                ))

        if entered_turn is not None:
            for m in messages:
                if m.get("role") != "bot":
                    continue
                mt = m.get("turn", -1)
                if mt > entered_turn:
                    out.append(self._mk_violation(
                        turn=mt,
                        rule_id="INV_EXIT_STATE_NOT_FINAL",
                        explanation=(
                            f"Bot sent message at turn {mt} after entering "
                            f"terminal state at turn {entered_turn}; no "
                            f"further automated messages are allowed after "
                            f"escalation/dormancy."
                        ),
                    ))
        return out

    def _check_classification_missing(
        self,
        messages: list[dict],
        classifications: list[dict],
    ) -> list[dict]:
        borrower_turns = {
            m["turn"] for m in messages
            if m.get("role") == "borrower" and m.get("turn") is not None
        }
        classified_turns = {c["turn"] for c in classifications if c.get("turn") is not None}
        missing = borrower_turns - classified_turns

        out: list[dict] = []
        for turn in sorted(missing):
            out.append(self._mk_violation(
                turn=turn,
                rule_id="INV_CLASSIFICATION_MISSING",
                explanation=(
                    f"Borrower message at turn {turn} has no bot_classifications "
                    f"entry; every borrower message must be classified "
                    f"(Invariant I5)."
                ),
            ))
        return out

    # ----- Commit 3: actions + amounts ------------------------------------
    def _check_actions(
        self,
        function_calls: list[dict],
        transitions: list[dict],
    ) -> list[dict]:
        trans_by_turn: dict[int, list[tuple[str, str]]] = {}
        for t in transitions:
            trans_by_turn.setdefault(t.get("turn", -1), []).append(
                (t.get("from_state"), t.get("to_state"))
            )

        out: list[dict] = []
        for fn in function_calls:
            name = fn.get("function")
            turn = fn.get("turn", -1)
            seen_pairs = trans_by_turn.get(turn, [])

            # Simple same-turn transition match for the three "happy path"
            # actions.
            if name in _ACTION_REQUIRED_TRANSITION:
                required = _ACTION_REQUIRED_TRANSITION[name]
                if required in seen_pairs:
                    continue
                out.append(self._mk_violation(
                    turn=turn,
                    rule_id=_ACTION_RULE_ID[name],
                    explanation=(
                        f"Action '{name}' must align with transition "
                        f"{required[0]} -> {required[1]} on the same turn. "
                        f"Observed transitions at turn {turn}: "
                        f"{seen_pairs or 'none'} (§5)."
                    ),
                ))
                continue

            # `escalate` must always lead into the `escalated` state.
            if name == "escalate":
                if any(pair[1] == "escalated" for pair in seen_pairs):
                    continue
                out.append(self._mk_violation(
                    turn=turn,
                    rule_id="ACT_ESCALATE_INVALID_CONTEXT",
                    explanation=(
                        f"Action 'escalate' at turn {turn} must transition "
                        f"into the escalated state. Observed transitions: "
                        f"{seen_pairs or 'none'} (§5, Invariant I4)."
                    ),
                ))
                continue

            # `zcm_timeout` may only fire while in `amount_pending`.
            if name == "zcm_timeout":
                if any(pair[0] == "amount_pending" for pair in seen_pairs):
                    continue
                out.append(self._mk_violation(
                    turn=turn,
                    rule_id="ACT_ZCM_TIMEOUT_INVALID_CONTEXT",
                    explanation=(
                        f"Action 'zcm_timeout' at turn {turn} can only fire "
                        f"from amount_pending. Observed transitions: "
                        f"{seen_pairs or 'none'} (§5)."
                    ),
                ))
                continue

            # Unknown actions are not flagged here to avoid noisy false
            # positives on extensions we don't yet model.
        return out

    def _check_amounts(self, metadata: dict) -> list[dict]:
        out: list[dict] = []
        pos = metadata.get("pos")
        tos = metadata.get("tos")
        offered = metadata.get("settlement_offered")

        if isinstance(pos, (int, float)) and isinstance(tos, (int, float)):
            if pos > tos:
                out.append(self._mk_violation(
                    turn=-1,
                    rule_id="AMT_POS_GREATER_THAN_TOS",
                    explanation=(
                        f"Metadata reports POS={pos} > TOS={tos}; POS must "
                        f"always be <= TOS (§9 A1)."
                    ),
                ))

        if (
            isinstance(offered, (int, float))
            and isinstance(pos, (int, float))
            and isinstance(tos, (int, float))
        ):
            # `settlement_floor` is not in metadata; we use POS as a
            # conservative upper-bound proxy for the floor since spec §9
            # says "settlement_floor <= POS <= TOS".
            if not (pos <= offered <= tos):
                out.append(self._mk_violation(
                    turn=-1,
                    rule_id="AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS",
                    explanation=(
                        f"settlement_offered={offered} is not within "
                        f"[POS={pos}, TOS={tos}]; settlement amount must lie "
                        f"within the allowed band (§9 A3). Note: settlement "
                        f"floor is not available in metadata, POS used as "
                        f"conservative lower bound."
                    ),
                ))
        return out

    # ----- Commit 4: timing -----------------------------------------------
    def _check_timing(
        self,
        messages: list[dict],
        transitions: list[dict],
    ) -> list[dict]:
        out: list[dict] = []
        if not messages:
            return out

        # Parse and sort once. Messages without parseable timestamps are
        # skipped silently so bad data doesn't poison the run.
        parsed: list[tuple[datetime, dict]] = []
        for m in messages:
            dt = _parse_iso(m.get("timestamp"))
            if dt is not None:
                parsed.append((dt, m))
        parsed.sort(key=lambda x: x[0])

        if not parsed:
            return out

        # ---- TIM_QUIET_HOURS_VIOLATION (§6.1) ----------------------------
        # Only bot-initiated messages count. "Bot-initiated" means the bot
        # message was not preceded by a borrower message within a few
        # minutes (i.e. not a reply to an in-window borrower message).
        for i, (dt, m) in enumerate(parsed):
            if m.get("role") != "bot":
                continue
            hour = dt.astimezone(IST).hour
            if not (hour >= _QUIET_HOUR_START or hour < _QUIET_HOUR_END):
                continue
            # Spec explicitly allows replying to a borrower message sent
            # during quiet hours. Treat a bot message as a reply when a
            # borrower message precedes it within 5 minutes.
            if _is_reply_to_recent_borrower(i, parsed, timedelta(minutes=5)):
                continue
            out.append(self._mk_violation(
                turn=m.get("turn", -1),
                rule_id="TIM_QUIET_HOURS_VIOLATION",
                explanation=(
                    f"Bot sent an initiating message at "
                    f"{dt.astimezone(IST).isoformat()} (IST hour={hour}); "
                    f"no outbound messages are allowed between 7pm-8am IST "
                    f"(§6.1)."
                ),
            ))

        # ---- TIM_FOLLOWUP_TOO_SOON (§6.2) --------------------------------
        # Look at consecutive bot-initiated messages; fire when the gap
        # between two such messages is < 4 hours.
        prev_initiated_dt: datetime | None = None
        for i, (dt, m) in enumerate(parsed):
            if m.get("role") != "bot":
                continue
            if _is_reply_to_recent_borrower(i, parsed, timedelta(minutes=5)):
                # This one is a reply, not an initiation: reset the clock so
                # we don't measure from a reply.
                prev_initiated_dt = dt
                continue
            if prev_initiated_dt is not None and (dt - prev_initiated_dt) < _FOLLOWUP_MIN_GAP:
                gap = dt - prev_initiated_dt
                out.append(self._mk_violation(
                    turn=m.get("turn", -1),
                    rule_id="TIM_FOLLOWUP_TOO_SOON",
                    explanation=(
                        f"Bot-initiated follow-up sent {gap} after the "
                        f"previous bot-initiated message; spec requires at "
                        f"least 4 hours between follow-ups (§6.2)."
                    ),
                ))
            prev_initiated_dt = dt

        # ---- TIM_DORMANCY_TIMEOUT_MISSED (§6.3) --------------------------
        # If >7 days pass with no borrower message and the bot keeps going
        # without marking the conversation dormant, flag once.
        last_borrower_dt: datetime | None = None
        reached_dormant = any(t.get("to_state") == "dormant" for t in transitions)

        for dt, m in parsed:
            role = m.get("role")
            if role == "borrower":
                last_borrower_dt = dt
                continue
            if role != "bot" or last_borrower_dt is None:
                continue
            if (dt - last_borrower_dt) > _DORMANCY_LIMIT and not reached_dormant:
                out.append(self._mk_violation(
                    turn=m.get("turn", -1),
                    rule_id="TIM_DORMANCY_TIMEOUT_MISSED",
                    explanation=(
                        f"Bot messaged {(dt - last_borrower_dt).days} days "
                        f"after the last borrower reply without marking the "
                        f"conversation dormant; spec requires dormancy after "
                        f"7 days of silence (§6.3)."
                    ),
                ))
                break  # one violation per conversation is enough

        return out

    # ----- Commit 5: compliance + repetition ------------------------------
    def _check_compliance(
        self,
        messages: list[dict],
        transitions: list[dict],
    ) -> list[dict]:
        out: list[dict] = []
        reached_exit = any(t.get("to_state") in EXIT_STATES for t in transitions)

        # ---- CMP_HARDSHIP_NO_ESCALATION (§8.1, §8.2) ---------------------
        hardship_turn: int | None = None
        for m in messages:
            if m.get("role") != "borrower":
                continue
            text = (m.get("text") or "").lower()
            if any(kw.lower() in text for kw in _HARDSHIP_KEYWORDS):
                hardship_turn = m.get("turn", -1)
                break
        if hardship_turn is not None and not reached_exit:
            out.append(self._mk_violation(
                turn=hardship_turn,
                rule_id="CMP_HARDSHIP_NO_ESCALATION",
                explanation=(
                    f"Borrower signalled hardship at turn {hardship_turn} "
                    f"but the conversation never escalated or went dormant. "
                    f"Spec §8.1/§8.2 require empathetic handling and "
                    f"escalation for severe hardship."
                ),
            ))

        # ---- CMP_DNC_VIOLATION (§8.3) ------------------------------------
        dnc_turn: int | None = None
        for m in messages:
            if m.get("role") != "borrower":
                continue
            text = (m.get("text") or "").lower()
            if any(kw.lower() in text for kw in _DNC_KEYWORDS):
                dnc_turn = m.get("turn", -1)
                break
        if dnc_turn is not None:
            for m in messages:
                if m.get("role") != "bot":
                    continue
                mt = m.get("turn", -1)
                if mt > dnc_turn:
                    out.append(self._mk_violation(
                        turn=mt,
                        rule_id="CMP_DNC_VIOLATION",
                        explanation=(
                            f"Borrower requested no further contact at turn "
                            f"{dnc_turn}; bot sent another message at turn "
                            f"{mt}. DNC is a hard regulatory stop (§8.3)."
                        ),
                    ))

        # ---- CMP_THREATENING_LANGUAGE (§8.5) -----------------------------
        for m in messages:
            if m.get("role") != "bot":
                continue
            raw = m.get("text") or ""
            lower = raw.lower()
            for kw in _THREAT_KEYWORDS:
                idx = lower.find(kw.lower())
                if idx < 0:
                    continue
                # Simple negation guard: look at a short window before the
                # keyword for "not"/"never"/"don't" etc.
                window = lower[max(0, idx - 24): idx]
                if any(tok in window for tok in _NEGATION_TOKENS):
                    continue
                out.append(self._mk_violation(
                    turn=m.get("turn", -1),
                    rule_id="CMP_THREATENING_LANGUAGE",
                    explanation=(
                        f"Bot message at turn {m.get('turn')} contains "
                        f"potentially threatening language near '{kw}'; "
                        f"spec §8.5 forbids threats of legal action, "
                        f"seizure, jail, credit damage, etc."
                    ),
                ))
                break  # one flag per message is enough

        return out

    def _check_repetition(self, messages: list[dict]) -> list[dict]:
        """Flag consecutive bot messages that are identical or near-duplicate.

        Near-duplicate uses a cheap token Jaccard ratio: if >= 0.85 of the
        normalized tokens overlap, treat as a near-duplicate. This keeps
        the check stdlib-only and avoids the complexity of full edit
        distance while still catching template-rewording loops.
        """
        out: list[dict] = []
        prev_text: str | None = None
        prev_tokens: set[str] | None = None
        prev_turn: int = -1

        sorted_msgs = sorted(
            messages, key=lambda x: (x.get("turn", -1), x.get("timestamp", ""))
        )
        for m in sorted_msgs:
            if m.get("role") != "bot":
                continue
            text_raw = (m.get("text") or "").strip()
            if not text_raw:
                continue
            norm = re.sub(r"\s+", " ", text_raw.lower())
            tokens = set(norm.split())

            if prev_text is not None:
                if norm == prev_text:
                    out.append(self._mk_violation(
                        turn=m.get("turn", -1),
                        rule_id="QLT_REPETITIVE_RESPONSE_LOOP",
                        explanation=(
                            f"Bot repeated an identical message at turn "
                            f"{m.get('turn')} (previous identical turn: "
                            f"{prev_turn})."
                        ),
                    ))
                elif prev_tokens and tokens:
                    inter = len(tokens & prev_tokens)
                    union = len(tokens | prev_tokens)
                    if union and (inter / union) >= 0.85:
                        v = self._mk_violation(
                            turn=m.get("turn", -1),
                            rule_id="QLT_REPETITIVE_RESPONSE_LOOP",
                            explanation=(
                                f"Bot sent a near-identical message at turn "
                                f"{m.get('turn')} (token overlap "
                                f"{inter}/{union} with turn {prev_turn})."
                            ),
                        )
                        # Near-duplicates are softer than exact duplicates.
                        v["severity"] = 0.25
                        out.append(v)

            prev_text, prev_tokens, prev_turn = norm, tokens, m.get("turn", -1)
        return out

    # ----- Commit 6: qualitative stubs (disabled) -------------------------
    def _check_context_loss(self, messages: list[dict]) -> list[dict]:
        # DISABLED: reliable detection needs dialog-state tracking so we
        # know which facts the bot should have retained. Keyword heuristics
        # produce >50% false negatives and we'd rather not poison the
        # scores. Stays in RULES so severity can be calibrated against
        # annotations (see understanding.md).
        return []

    def _check_escalation_trigger(
        self,
        messages: list[dict],
        transitions: list[dict],
    ) -> list[dict]:
        # DISABLED: escalation triggers (legal threats, abuse, crisis) are
        # context-dependent. Robust detection requires a classifier that
        # distinguishes "the borrower is asking about legal options" from
        # "the borrower is threatening legal action against us". Calling
        # such a model inside evaluate() is out of scope.
        return []

    def _check_empathy(self, messages: list[dict]) -> list[dict]:
        # DISABLED: "appropriate empathy" is explicitly left to human
        # judgment in spec §8.2. This rule is registered only so it can
        # be scored from annotations later.
        return []

    def _check_unclear_response(self, messages: list[dict]) -> list[dict]:
        # DISABLED: needs understanding of whether the bot's response
        # actually addressed the borrower's question. Keyword matches on
        # "I don't understand" are weak proxies with frequent false
        # positives.
        return []

    # ----- helpers --------------------------------------------------------
    def _mk_violation(self, *, turn: int, rule_id: str, explanation: str) -> dict:
        rule = self.rules[rule_id]
        return {
            "turn": turn,
            "rule": rule_id,
            "severity": float(rule["severity"]),
            "explanation": explanation,
        }

    def _score(self, violations: list[dict]) -> tuple[float, float]:
        """Combine violations into quality and risk scores.

        Quality starts at 1.0 and is reduced by weighted severities; the
        total penalty is capped so a single conversation cannot be pushed
        below 0.15 by this rule set alone (prevents runaway loops).

        Risk is additive on `risk_weight * severity`, but each rule's
        contribution per conversation is capped at the rule's base
        severity to stop repeated-keyword convictions (e.g. multiple
        bot messages after a DNC request) from double-counting beyond
        what the rule inherently warrants. Final risk is clipped to
        [0, 1].
        """
        quality_penalty = 0.0
        risk_by_rule: dict[str, float] = {}

        for v in violations:
            rule = self.rules.get(v["rule"])
            if rule is None or rule.get("disabled"):
                continue
            sev = float(v.get("severity", rule["severity"]))
            quality_penalty += sev * float(rule["quality_weight"])
            risk_by_rule[v["rule"]] = risk_by_rule.get(v["rule"], 0.0) + (
                sev * float(rule["risk_weight"])
            )

        # Cap each rule's risk contribution at its base severity so a
        # single conversation can't get 10x risk from 10 repetitions of
        # the same rule.
        risk_penalty = 0.0
        for rid, r in risk_by_rule.items():
            cap = float(self.rules[rid]["severity"])
            risk_penalty += min(r, cap)

        quality_penalty = min(quality_penalty, 0.85)
        quality_score = max(0.0, 1.0 - quality_penalty)
        risk_score = max(0.0, min(1.0, risk_penalty))
        return quality_score, risk_score


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, assuming IST when no timezone is given."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def _is_reply_to_recent_borrower(
    idx: int,
    parsed: list[tuple[datetime, dict]],
    window: timedelta,
) -> bool:
    """True if a recent borrower message precedes parsed[idx] within `window`."""
    dt, _ = parsed[idx]
    for j in range(idx - 1, -1, -1):
        pdt, pm = parsed[j]
        if (dt - pdt) > window:
            return False
        if pm.get("role") == "borrower":
            return True
    return False


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _iter_conversations(path: Path) -> Iterable[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def main():
    """Run evaluator on sample data for local testing."""
    evaluator = AgentEvaluator()

    data_path = Path("data/production_logs.jsonl")
    if not data_path.exists():
        print("No data found. Make sure data/production_logs.jsonl exists.")
        return

    conversations = []
    with open(data_path) as f:
        for line in f:
            conversations.append(json.loads(line))

    print(f"Evaluating {len(conversations)} conversations...")

    results = []
    for conv in conversations[:10]:
        result = evaluator.evaluate(conv)
        results.append(result)
        print(
            f"  {conv['conversation_id']}: quality={result['quality_score']:.2f}, "
            f"risk={result['risk_score']:.2f}, violations={len(result['violations'])}"
        )

    print(f"\nEvaluated {len(results)} conversations.")


# def main() -> None:
#     evaluator = AgentEvaluator()

#     data_path = Path("data/production_logs.jsonl")
#     if not data_path.exists():
#         print(f"No data found at {data_path.resolve()}. "
#               f"Run from the problem_statement/ directory.")
#         return

#     conversations = list(_iter_conversations(data_path))
#     print(f"Evaluating {len(conversations)} conversations...")

#     rule_counts: Counter[str] = Counter()
#     total_quality = 0.0
#     total_risk = 0.0
#     total_violations = 0

#     for i, conv in enumerate(conversations):
#         result = evaluator.evaluate(conv)
#         total_quality += result["quality_score"]
#         total_risk += result["risk_score"]
#         total_violations += len(result["violations"])
#         for v in result["violations"]:
#             rule_counts[v["rule"]] += 1
#         if i < 10:
#             print(
#                 f"  {conv['conversation_id']}: "
#                 f"quality={result['quality_score']:.2f}, "
#                 f"risk={result['risk_score']:.2f}, "
#                 f"violations={len(result['violations'])}"
#             )

#     n = max(1, len(conversations))
#     print()
#     print(f"Evaluated {len(conversations)} conversations.")
#     print(f"  mean quality_score = {total_quality / n:.3f}")
#     print(f"  mean risk_score    = {total_risk / n:.3f}")
#     print(f"  total violations   = {total_violations}")
#     print(f"  violations / conv  = {total_violations / n:.2f}")
#     print()
#     print("  Registered rules (with counts):")
#     for rid, meta in RULES.items():
#         cnt = rule_counts.get(rid, 0)
#         flag = " [disabled]" if meta.get("disabled") else ""
#         print(f"    {rid:<50} {cnt:>6}{flag}")


if __name__ == "__main__":
    main()
