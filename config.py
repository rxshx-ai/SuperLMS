"""
Configuration loader for Moodle LLM Bridge.
Reads settings from .env file or environment variables.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# ── Moodle LMS Settings ──────────────────────────────────────────────
MOODLE_URL = os.getenv("MOODLE_URL", "https://lms.vit.ac.in").rstrip("/")
MOODLE_USERNAME = os.getenv("MOODLE_USERNAME")
MOODLE_PASSWORD = os.getenv("MOODLE_PASSWORD")

# ── Groq API Settings ────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ── Agent Behaviour ──────────────────────────────────────────────────
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))        # seconds
PUBLISH_STATE = os.getenv("PUBLISH_STATE", "site")           # site | public | draft

# ── Markers ──────────────────────────────────────────────────────────
PROMPT_MARKER  = "[LLMQ]"      # prefix for user prompts
RESPONSE_MARKER = "[LLMR#"     # prefix for agent responses  (e.g. [LLMR#142])

# ── Validation ───────────────────────────────────────────────────────
_REQUIRED = {
    "MOODLE_USERNAME": MOODLE_USERNAME,
    "MOODLE_PASSWORD": MOODLE_PASSWORD,
    "GROQ_API_KEY":    GROQ_API_KEY,
}

def validate():
    """Check that all required configuration values are set."""
    missing = [k for k, v in _REQUIRED.items() if not v]
    if missing:
        print(f"❌  Missing required config: {', '.join(missing)}")
        print("   Copy .env.example → .env and fill in the values.")
        sys.exit(1)
    print(f"✅  Config loaded  |  LMS: {MOODLE_URL}  |  Poll: {POLL_INTERVAL}s")
