"""Locust workload for the local-only AI Serving capacity check.

The user id is injected at runtime and should refer to a synthetic user in the
throwaway local database. This file intentionally contains no credentials.
"""

import json
import os

from locust import HttpUser, constant, events, task

USER_ID = os.environ.get("LOADTEST_USER_ID", "")
MOCK_REASON_ACCEPTED = 0
MOCK_REASON_FALLBACK = 0


@events.test_stop.add_listener
def report_reason_source(_environment: object | None = None, **_kwargs: object) -> None:
    checked = MOCK_REASON_ACCEPTED + MOCK_REASON_FALLBACK
    ratio = MOCK_REASON_ACCEPTED / checked if checked else 0.0
    print(
        "LLM mock result check: "
        f"accepted={MOCK_REASON_ACCEPTED}, fallback_or_other={MOCK_REASON_FALLBACK}, "
        f"accepted_ratio={ratio:.3f}"
    )


class AIServiceUser(HttpUser):
    """Approximate a fridge recommendation screen with a 60/20/20 GET mix."""

    wait_time = constant(0.25)

    def on_start(self) -> None:
        if not USER_ID:
            raise RuntimeError("Set LOADTEST_USER_ID to a synthetic local user id")
        self.headers = {"X-User-Id": USER_ID}

    @task(6)
    def my_recipes(self) -> None:
        global MOCK_REASON_ACCEPTED, MOCK_REASON_FALLBACK
        with self.client.get(
            "/api/v1/recommendations/my-recipes",
            headers=self.headers,
            name="GET my-recipes",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            try:
                items = json.loads(response.text).get("data", {}).get("items", [])
                for item in items[:3]:
                    if "풍미가" in item.get("recommendation_reason", ""):
                        MOCK_REASON_ACCEPTED += 1
                    else:
                        MOCK_REASON_FALLBACK += 1
            except (AttributeError, json.JSONDecodeError, TypeError):
                MOCK_REASON_FALLBACK += 3
                response.failure("Could not inspect recommendation reasons")

    @task(2)
    def home_bubbles(self) -> None:
        self._get("/api/v1/home/bubbles", "GET home bubbles")

    @task(2)
    def fridge_items(self) -> None:
        self._get("/api/v1/users/me/fridge", "GET fridge items")

    def _get(self, path: str, name: str) -> None:
        with self.client.get(path, headers=self.headers, name=name, catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
