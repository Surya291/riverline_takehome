"""
Riverline Evals Take-Home Assignment
=====================================

Rule-first AgentEvaluator (v1).

Run locally (from this directory):
    python eval_takehome.py

See README.md and spec.pdf for the formal specification.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------
# Each rule maps to a spec section. Severity is the baseline severity emitted
# when the rule fires; quality_weight and risk_weight scale how much that
# severity contributes to the final quality and risk scores respectively.
# Weights are kept simple and explicit so they can be discussed in the
# writeup and tuned against annotations later.

RULES: dict[str, dict[str, Any]] = {
    # Transitions (spec Section: Transitions / Table tab:matrix)
    "TR_INVALID_STATE_TRANSITION": {
        "spec_ref": "Transitions / Transition Matrix",
        "severity": 0.8,
        "quality_weight": 0.6,
        "risk_weight": 0.4,
    },
    "TR_BACKWARD_TRANSITION_NOT_ALLOWED": {
        "spec_ref": "Invariants I1 / Allowed Backward Transition",
        "severity": 0.85,
        "quality_weight": 0.6,
        "risk_weight": 0.4,
    },
    "TR_BACKWARD_EXCEPTION_MISUSED": {
        "spec_ref": "Transitions / Allowed Backward Transition",
        "severity": 0.6,
        "quality_weight": 0.5,
        "risk_weight": 0.3,
    },

    # Invariants (spec Section: Invariants)
    "INV_EXIT_STATE_NOT_FINAL": {
        "spec_ref": "Invariants I2",
        "severity": 1.0,
        "quality_weight": 0.7,
        "risk_weight": 0.9,
    },

    # Actions (spec Section: Actions)
    "ACT_REQUEST_SETTLEMENT_AMOUNT_INVALID_CONTEXT": {
        "spec_ref": "Actions / request_settlement_amount",
        "severity": 0.7,
        "quality_weight": 0.5,
        "risk_weight": 0.35,
    },
    "ACT_CONFIRM_PAYMENT_INVALID_CONTEXT": {
        "spec_ref": "Actions / confirm_payment",
        "severity": 0.8,
        "quality_weight": 0.55,
        "risk_weight": 0.45,
    },

    # Amounts (spec Section: Amount Validation)
    "AMT_POS_GREATER_THAN_TOS": {
        "spec_ref": "Amount Validation A1",
        "severity": 0.9,
        "quality_weight": 0.5,
        "risk_weight": 0.5,
    },
    "AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS": {
        "spec_ref": "Amount Validation A3",
        "severity": 0.8,
        "quality_weight": 0.5,
        "risk_weight": 0.5,
    },

    # Quality (soft)
    "QLT_REPETITIVE_RESPONSE_LOOP": {
        "spec_ref": "Quality Q5",
        "severity": 0.35,
        "quality_weight": 0.4,
        "risk_weight": 0.1,
    },
}


# Canonical state sets from spec Section "States"
PROGRESSION_STATES = {
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
EXIT_STATES = {"escalated", "dormant"}

# Allowed non-self transitions encoded from spec Table tab:matrix.
# Self-transitions (from == to) for progression states are always allowed and
# are handled separately, not listed here.
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

# Conditional backward moves (only valid when borrower said unclear+low).
_BACKWARD_EXCEPTIONS: set[tuple[str, str]] = {
    ("settlement_explained", "intent_asked"),
    ("amount_pending", "intent_asked"),
}

# Any progression state may also jump to payment_confirmed if the system
# records a payment_received event; we accept the transition when the
# transition "reason" mentions payment_received.
_PAYMENT_EVENT_REASON_TOKEN = "payment_received"

# Action -> required (from_state, to_state) pair on the same turn.
_ACTION_REQUIRED_TRANSITION: dict[str, tuple[str, str]] = {
    "request_settlement_amount": ("settlement_explained", "amount_pending"),
    "confirm_payment": ("date_amount_asked", "payment_confirmed"),
}
_ACTION_RULE_ID: dict[str, str] = {
    "request_settlement_amount": "ACT_REQUEST_SETTLEMENT_AMOUNT_INVALID_CONTEXT",
    "confirm_payment": "ACT_CONFIRM_PAYMENT_INVALID_CONTEXT",
}


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class AgentEvaluator:
    """
    Evaluate WhatsApp debt collection conversations against the spec.

    v1 scope (deterministic, self-contained, no external calls):
      - transition matrix + backward-exception check
      - terminal/exit-state invariant
      - action-to-transition alignment (request_settlement_amount, confirm_payment)
      - basic amount sanity from metadata (pos/tos/settlement_offered)
      - one soft quality rule: consecutive duplicate bot messages
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

        class_by_turn: dict[int, dict] = {c.get("turn"): c for c in classifications}

        violations: list[dict] = []
        violations += self._check_transitions(transitions, class_by_turn)
        violations += self._check_exit_state_invariant(transitions, messages)
        violations += self._check_actions(function_calls, transitions)
        violations += self._check_amounts(metadata)
        violations += self._check_repetition(messages)

        quality_score, risk_score = self._score(violations)
        return {
            "quality_score": quality_score,
            "risk_score": risk_score,
            "violations": violations,
        }

    # ----- detectors ------------------------------------------------------
    def _check_transitions(
        self,
        transitions: list[dict],
        class_by_turn: dict[int, dict],
    ) -> list[dict]:
        out: list[dict] = []
        for t in transitions:
            frm = t.get("from_state")
            to = t.get("to_state")
            turn = t.get("turn", -1)
            reason = (t.get("reason") or "").lower()

            # self-transitions always allowed for progression states
            if frm == to and frm in PROGRESSION_STATES:
                continue

            # allowed non-self transitions
            if (frm, to) in _ALLOWED_TRANSITIONS:
                continue

            # payment_received system event jump to payment_confirmed
            if (
                to == "payment_confirmed"
                and frm in PROGRESSION_STATES
                and _PAYMENT_EVENT_REASON_TOKEN in reason
            ):
                continue

            # conditional backward moves (only if borrower unclear+low)
            if (frm, to) in _BACKWARD_EXCEPTIONS:
                cls = class_by_turn.get(turn) or {}
                classification = cls.get("classification")
                confidence = cls.get("confidence")
                if classification == "unclear" and confidence == "low":
                    continue
                out.append(self._mk_violation(
                    turn=turn,
                    rule_id="TR_BACKWARD_EXCEPTION_MISUSED",
                    explanation=(
                        f"Backward transition {frm} -> {to} used without the required "
                        f"unclear+low borrower classification at this turn "
                        f"(classification={classification}, confidence={confidence})."
                    ),
                ))
                continue

            # any other backward move between progression states is illegal
            if frm in PROGRESSION_STATES and to in PROGRESSION_STATES:
                out.append(self._mk_violation(
                    turn=turn,
                    rule_id="TR_BACKWARD_TRANSITION_NOT_ALLOWED",
                    explanation=(
                        f"Illegal backward transition {frm} -> {to}; only "
                        f"settlement_explained/amount_pending -> intent_asked is "
                        f"allowed under the backward exception."
                    ),
                ))
                continue

            # everything else is an invalid transition per the matrix
            out.append(self._mk_violation(
                turn=turn,
                rule_id="TR_INVALID_STATE_TRANSITION",
                explanation=(
                    f"Transition {frm} -> {to} is not permitted by the spec "
                    f"transition matrix."
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
            to = t.get("to_state")
            frm = t.get("from_state")
            turn = t.get("turn", -1)
            if entered_turn is None and to in EXIT_STATES:
                entered_turn = turn
                continue
            if entered_turn is not None and frm in EXIT_STATES:
                out.append(self._mk_violation(
                    turn=turn,
                    rule_id="INV_EXIT_STATE_NOT_FINAL",
                    explanation=(
                        f"Transition leaves terminal state {frm} at turn {turn}; "
                        f"exit states are final per Invariant I2."
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
                            f"Bot sent message at turn {mt} after entering terminal "
                            f"state at turn {entered_turn}; no further automated "
                            f"messages are allowed after escalation/dormancy."
                        ),
                    ))
        return out

    def _check_actions(
        self,
        function_calls: list[dict],
        transitions: list[dict],
    ) -> list[dict]:
        out: list[dict] = []
        # build turn -> set of (from, to) for quick lookup
        trans_by_turn: dict[int, list[tuple[str, str]]] = {}
        for t in transitions:
            trans_by_turn.setdefault(t.get("turn", -1), []).append(
                (t.get("from_state"), t.get("to_state"))
            )

        for fn in function_calls:
            name = fn.get("function")
            turn = fn.get("turn", -1)
            required = _ACTION_REQUIRED_TRANSITION.get(name)
            if required is None:
                continue  # v1 only checks a subset
            seen_pairs = trans_by_turn.get(turn, [])
            if required in seen_pairs:
                continue
            rule_id = _ACTION_RULE_ID[name]
            out.append(self._mk_violation(
                turn=turn,
                rule_id=rule_id,
                explanation=(
                    f"Action '{name}' must align with transition "
                    f"{required[0]} -> {required[1]} on the same turn; "
                    f"observed transitions at turn {turn}: {seen_pairs or 'none'}."
                ),
            ))
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
                        f"Metadata reports POS={pos} greater than TOS={tos}; "
                        f"POS must always be <= TOS (A1)."
                    ),
                ))

        if (
            isinstance(offered, (int, float))
            and isinstance(pos, (int, float))
            and isinstance(tos, (int, float))
        ):
            # Without a settlement floor in the data, use POS as the lower bound
            # (settlement floor <= POS per spec A2). This is a conservative
            # approximation noted in the writeup.
            if not (pos <= offered <= tos):
                out.append(self._mk_violation(
                    turn=-1,
                    rule_id="AMT_SETTLEMENT_AMOUNT_OUT_OF_BOUNDS",
                    explanation=(
                        f"settlement_offered={offered} not within [POS={pos}, "
                        f"TOS={tos}]; settlement amount must lie within the "
                        f"allowed band (A3)."
                    ),
                ))
        return out

    def _check_repetition(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        prev_text: str | None = None
        prev_turn: int = -1
        for m in sorted(messages, key=lambda x: (x.get("turn", -1), x.get("timestamp", ""))):
            if m.get("role") != "bot":
                continue
            text = (m.get("text") or "").strip()
            if not text:
                prev_text, prev_turn = text, m.get("turn", -1)
                continue
            if prev_text is not None and text == prev_text:
                out.append(self._mk_violation(
                    turn=m.get("turn", -1),
                    rule_id="QLT_REPETITIVE_RESPONSE_LOOP",
                    explanation=(
                        f"Bot repeated identical message at turn {m.get('turn')} "
                        f"(previous identical turn: {prev_turn})."
                    ),
                ))
            prev_text, prev_turn = text, m.get("turn", -1)
        return out

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
        # Quality starts at 1.0 and is reduced by weighted severities, capped
        # so a single conversation cannot be pushed below 0.15 by this rule
        # set alone. Risk is additive with its own weights, clipped to [0, 1].
        quality_penalty = 0.0
        risk_penalty = 0.0
        for v in violations:
            rule = self.rules.get(v["rule"])
            if rule is None:
                continue
            sev = float(v.get("severity", rule["severity"]))
            quality_penalty += sev * float(rule["quality_weight"])
            risk_penalty += sev * float(rule["risk_weight"])
        quality_penalty = min(quality_penalty, 0.85)
        quality_score = max(0.0, 1.0 - quality_penalty)
        risk_score = max(0.0, min(1.0, risk_penalty))
        return quality_score, risk_score


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _iter_conversations(path: Path) -> Iterable[dict]:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def main():
    evaluator = AgentEvaluator()

    data_path = Path("data/production_logs.jsonl")
    if not data_path.exists():
        print("No data found. Make sure data/production_logs.jsonl exists.")
        return

    conversations = list(_iter_conversations(data_path))
    print(f"Evaluating {len(conversations)} conversations...")

    rule_counts: Counter[str] = Counter()
    total_quality = 0.0
    total_risk = 0.0
    total_violations = 0

    for i, conv in enumerate(conversations):
        result = evaluator.evaluate(conv)
        total_quality += result["quality_score"]
        total_risk += result["risk_score"]
        total_violations += len(result["violations"])
        for v in result["violations"]:
            rule_counts[v["rule"]] += 1
        if i < 10:
            print(
                f"  {conv['conversation_id']}: "
                f"quality={result['quality_score']:.2f}, "
                f"risk={result['risk_score']:.2f}, "
                f"violations={len(result['violations'])}"
            )

    n = max(1, len(conversations))
    print()
    print(f"Evaluated {len(conversations)} conversations.")
    print(f"  mean quality_score = {total_quality / n:.3f}")
    print(f"  mean risk_score    = {total_risk / n:.3f}")
    print(f"  total violations   = {total_violations}")
    print(f"  violations / conv  = {total_violations / n:.2f}")
    if rule_counts:
        print("  violations by rule:")
        for rule_id, count in rule_counts.most_common():
            print(f"    {rule_id:<48} {count}")


if __name__ == "__main__":
    main()
