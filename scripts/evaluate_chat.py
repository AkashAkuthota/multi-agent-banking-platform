"""Evaluate the running synthetic chat demo without printing customer content."""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict

import httpx

from app.evaluation import evaluate_chat_responses
from app.schemas.chat import ChatResponse


async def _run(
    base_url: str,
    email: str,
    password: str,
    minimum_accuracy: float,
) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=90.0) as client:
        login_response = await client.post(
            "/auth/login",
            json={"email": email, "password": password},
        )
        login_response.raise_for_status()
        access_token = login_response.json().get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("Local login returned no access token")

        async def respond(message: str) -> ChatResponse:
            response = await client.post(
                "/chat",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"message": message},
            )
            response.raise_for_status()
            return ChatResponse.model_validate(response.json())

        summary = await evaluate_chat_responses(respond)

    print(json.dumps(asdict(summary), sort_keys=True))
    return int(
        summary.accuracy < minimum_accuracy
        or summary.relevance_violation_count > 0
        or summary.grounding_violation_count > 0
        or summary.safety_violation_count > 0
        or summary.availability_failure_count > 0
    )


def main() -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Local running demo base URL.",
    )
    parser.add_argument(
        "--email",
        default="demo.customer@banking.local",
        help="Synthetic demo account email.",
    )
    parser.add_argument(
        "--password",
        default="DemoBank@2026",
        help="Synthetic demo account password.",
    )
    parser.add_argument(
        "--minimum-accuracy",
        type=float,
        default=1.0,
        help="Minimum required evaluated-case accuracy (default: 1.0).",
    )
    args = parser.parse_args()
    if not 0.0 <= args.minimum_accuracy <= 1.0:
        parser.error("--minimum-accuracy must be between 0 and 1")
    return asyncio.run(
        _run(
            args.base_url.rstrip("/"),
            args.email,
            args.password,
            args.minimum_accuracy,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
