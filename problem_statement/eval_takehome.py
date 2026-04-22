"""
Riverline Evals Take-Home Assignment
=====================================

Implement the AgentEvaluator class below.

Run your evaluator locally:
    python eval_takehome.py

See README.md for full instructions and spec.pdf for the formal specification.
"""

import json
from pathlib import Path


class AgentEvaluator:
    """
    Evaluate WhatsApp debt collection conversations against the agent specification.

    Your evaluator should assess:
    - Spec compliance (does the conversation follow the state machine?)
    - Risk (how likely is this conversation to cause a complaint?)
    - Specific violations (which spec rules were broken, and where?)

    You may use any tools, models, or libraries you want.
    """

    def __init__(self):
        """
        Initialize your evaluator.
        Load any models, rules, resources, or cached data you need.
        Must work without arguments.
        """
        pass

    def evaluate(self, conversation: dict) -> dict:
        """
        Evaluate a single conversation.

        Args:
            conversation: dict with keys:
                - conversation_id: str
                - messages: list of {role, text, timestamp, turn}
                - bot_classifications: list of {turn, input_text, classification, confidence}
                - state_transitions: list of {turn, from_state, to_state, reason}
                - function_calls: list of {turn, function, params}
                - metadata: {language, zone, dpd, pos, tos, total_turns, ...}

        Returns:
            dict with keys:
                - quality_score: float 0-1 (higher = better conversation)
                - risk_score: float 0-1 (higher = more likely to cause complaint)
                - violations: list of {
                    turn: int,
                    rule: str,          # reference to spec rule violated
                    severity: float,    # 0-1
                    explanation: str
                  }
        """
        # TODO: Implement your evaluation logic here
        return {
            "quality_score": 0.5,
            "risk_score": 0.5,
            "violations": [],
        }


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


if __name__ == "__main__":
    main()