"""
Configuration loader for Moodle LLM Bridge.
Reads settings from .env file or environment variables.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# ── Moodle LMS Settings ──────────────────────────────────────────────
MOODLE_URL      = os.getenv("MOODLE_URL", "https://lms.vit.ac.in").rstrip("/")
MOODLE_USERNAME = os.getenv("MOODLE_USERNAME")
MOODLE_PASSWORD = os.getenv("MOODLE_PASSWORD")

# ── LLM (Groq) Settings ─────────────────────────────────────────────
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")

# ── Agent Behaviour ──────────────────────────────────────────────────
POLL_INTERVAL  = int(os.getenv("POLL_INTERVAL", "30"))   # seconds
# Default to 'draft' so responses are visible only to yourself
PUBLISH_STATE  = os.getenv("PUBLISH_STATE", "draft")     # site | public | draft

# ── Markers ──────────────────────────────────────────────────────────
PROMPT_MARKER   = "[LLMQ]"   # prefix for user prompts
RESPONSE_MARKER = "[LLMR#"   # prefix for agent responses (e.g. [LLMR#142])


# ── Validation ───────────────────────────────────────────────────────

def validate():
    """Check that all required configuration values are set."""
    errors = []

    if not MOODLE_USERNAME:
        errors.append("MOODLE_USERNAME")
    if not MOODLE_PASSWORD:
        errors.append("MOODLE_PASSWORD")

    if not GROQ_API_KEY:
        errors.append("GROQ_API_KEY")

    if errors:
        print(f"Missing required config: {', '.join(errors)}")
        print("Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    print(
        f"Config loaded | LMS: {MOODLE_URL} | Provider: GROQ | Poll: {POLL_INTERVAL}s"
    )
