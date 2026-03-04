"""FastAPI service wrapper for the Moodle LLM Bridge agent.

This application:
- Starts the existing LLMBridgeAgent in a background thread
- Exposes simple HTTP endpoints for health checks and basic control

Run locally:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from agent import LLMBridgeAgent
import config

logger = logging.getLogger("api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-8s │ %(levelname)-5s │ %(message)s",
)


_agent: Optional[LLMBridgeAgent] = None
_agent_thread: Optional[threading.Thread] = None


def start_agent_if_needed() -> None:
    """Ensure the background Moodle agent is running."""
    global _agent, _agent_thread

    if _agent is None:
        logger.info("Initialising LLMBridgeAgent for FastAPI service …")
        _agent = LLMBridgeAgent()

    if _agent_thread is None or not _agent_thread.is_alive():
        logger.info("Starting LLMBridgeAgent background thread …")
        _agent._running = True  # reuse the agent's run-loop flag
        _agent_thread = threading.Thread(
            target=_agent.run,
            name="llm-bridge-agent",
            daemon=True,
        )
        _agent_thread.start()


def stop_agent() -> None:
    """Request the background agent loop to stop."""
    global _agent
    if _agent is not None:
        logger.info("Stopping LLMBridgeAgent background thread …")
        _agent._running = False


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[override]
    """FastAPI lifespan context to manage the background agent."""
    # On startup
    start_agent_if_needed()
    try:
        yield
    finally:
        # On shutdown
        stop_agent()


app = FastAPI(
    title="Moodle LLM Bridge API",
    description=(
        "HTTP wrapper around the Moodle LLM Bridge agent. "
        "The agent polls Moodle for [LLMQ] blog entries and posts "
        "[LLMR#<id>] responses using an LLM backend."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """Basic health check endpoint."""
    return {
        "status": "ok",
        "moodle_url": config.MOODLE_URL,
        "llm_provider": "GROQ",
        "poll_interval": config.POLL_INTERVAL,
    }


@app.get("/status", tags=["agent"])
async def status() -> dict:
    global _agent, _agent_thread

    processed_count = len(_agent.processed_ids) if _agent else 0
    running = bool(_agent_thread and _agent_thread.is_alive())

    return {
        "agent_running": running,
        "processed_prompts": processed_count,
        "poll_interval": config.POLL_INTERVAL,
    }


@app.post("/control/poll-once", tags=["agent"])
async def poll_once() -> dict:

    global _agent
    if _agent is None:
        start_agent_if_needed()

    assert _agent is not None
    _agent.poll_once()

    return {"status": "ok"}


@app.post("/control/restart", tags=["agent"])
async def restart_agent() -> dict:
    """Restart the background agent thread."""
    stop_agent()
    start_agent_if_needed()
    return {"status": "restarted"}
