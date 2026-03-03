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

# ── LLM Provider Selection ───────────────────────────────────────────
# Set LLM_PROVIDER to "gemini" or "groq" in your .env
LLM_PROVIDER   = os.getenv("LLM_PROVIDER", "gemini").lower().strip()

# ── API Keys ─────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY")

# ── Agent Behaviour ──────────────────────────────────────────────────
POLL_INTERVAL  = int(os.getenv("POLL_INTERVAL", "30"))   # seconds
PUBLISH_STATE  = os.getenv("PUBLISH_STATE", "site")      # site | public | draft

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

    if LLM_PROVIDER not in ("gemini", "groq"):
        print(f"❌  Invalid LLM_PROVIDER={LLM_PROVIDER!r}. Must be 'gemini' or 'groq'.")
        print("   Set LLM_PROVIDER=gemini or LLM_PROVIDER=groq in your .env file.")
        sys.exit(1)

    if LLM_PROVIDER == "gemini" and not GEMINI_API_KEY:
        errors.append("GEMINI_API_KEY")
    if LLM_PROVIDER == "groq" and not GROQ_API_KEY:
        errors.append("GROQ_API_KEY")

    if errors:
        print(f"❌  Missing required config: {', '.join(errors)}")
        print("   Copy .env.example → .env and fill in the values.")
        sys.exit(1)

    active_key = "GEMINI_API_KEY" if LLM_PROVIDER == "gemini" else "GROQ_API_KEY"
    print(
        f"✅  Config loaded  |  LMS: {MOODLE_URL}  "
        f"|  Provider: {LLM_PROVIDER.upper()}  "
        f"|  Poll: {POLL_INTERVAL}s"
    )
