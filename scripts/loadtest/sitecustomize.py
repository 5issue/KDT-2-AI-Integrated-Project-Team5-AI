"""Redirect the serving LLM client to the private local mock for this test only."""

import os

import rag_lab.reason_service.client as reason_client

reason_client.BASE_URL = os.environ.get(
    "LOCAL_LLM_BASE_URL",
    "http://ai-loadtest-mock:8080/api/v1",
)
