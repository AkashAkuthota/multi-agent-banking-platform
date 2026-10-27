"""Evaluate the configured third-party intent classifier on synthetic cases."""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from app.evaluation import evaluate_intents
from app.llm.client import create_intent_provider
from app.llm.router import IntentRouter


async def _run(minimum_accuracy: float) -> int:
    # Exercise the same redaction and command-routing guards used by /chat.
    summary = await evaluate_intents(IntentRouter(create_intent_provider()))
    print(json.dumps(asdict(summary), sort_keys=True))
    return int(
        summary.accuracy < minimum_accuracy
        or summary.safety_violation_count > 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--minimum-accuracy",
        type=float,
        default=0.9,
        help="Minimum exact-match accuracy required (default: 0.9)",
    )
    args = parser.parse_args()
    if not 0.0 <= args.minimum_accuracy <= 1.0:
        parser.error("--minimum-accuracy must be between 0 and 1")
    return asyncio.run(_run(args.minimum_accuracy))


if __name__ == "__main__":
    sys.exit(main())
