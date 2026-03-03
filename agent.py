"""
Moodle LLM Bridge — Main Agent

Continuously polls the Moodle LMS for blog entries tagged as prompts
([LLMQ]), forwards them to the Gemini LLM, and posts the responses
back as new blog entries tagged [LLMR#<id>].

Usage:
    python agent.py
"""

import json
import time
import signal
import logging
import sys
import os
import html
from pathlib import Path

import config
from moodle_client import MoodleClient, BlogEntry
from gemini_client import LLMClient

# ── Logging Setup ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-8s │ %(levelname)-5s │ %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("agent")

# ── State Persistence ─────────────────────────────────────────────────

STATE_FILE = Path(__file__).parent / "processed_ids.json"


def load_processed_ids() -> set:
    """Load the set of already-processed prompt entry IDs from disk."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return set(data)
        except (json.JSONDecodeError, TypeError):
            return set()
    return set()


def save_processed_ids(ids: set):
    """Persist the set of processed IDs."""
    STATE_FILE.write_text(
        json.dumps(sorted(ids), indent=2), encoding="utf-8"
    )


# ── Core Logic ────────────────────────────────────────────────────────

class LLMBridgeAgent:
    """
    The main agent that bridges Moodle blog ↔ Gemini LLM.

    Workflow per poll cycle:
      1. Fetch all blog entries for the logged-in user
      2. Identify NEW prompts ([LLMQ] prefix, not yet processed)
      3. For each new prompt:
         a. Extract the actual question
         b. Send to Gemini
         c. Post the response as [LLMR#<entry_id>] blog entry
         d. Mark the prompt as processed
    """

    def __init__(self):
        config.validate()

        self.moodle = MoodleClient(
            base_url=config.MOODLE_URL,
            username=config.MOODLE_USERNAME,
            password=config.MOODLE_PASSWORD,
        )
        api_key = (
            config.GEMINI_API_KEY
            if config.LLM_PROVIDER == "gemini"
            else config.GROQ_API_KEY
        )
        self.llm = LLMClient(provider=config.LLM_PROVIDER, api_key=api_key)
        self.processed_ids = load_processed_ids()
        self._running = True

    # ── Prompt / Response Identification ──────────────────────────────

    @staticmethod
    def is_prompt(entry: BlogEntry) -> bool:
        """Does this entry look like a user prompt?"""
        return entry.subject.strip().startswith(config.PROMPT_MARKER)

    @staticmethod
    def is_response(entry: BlogEntry) -> bool:
        """Does this entry look like an agent response?"""
        return entry.subject.strip().startswith(config.RESPONSE_MARKER)

    @staticmethod
    def extract_prompt_text(entry: BlogEntry) -> str:
        """
        Get the actual question text from a prompt entry.

        The subject has the form:  [LLMQ] What is quantum physics?
        The body may contain the full detailed prompt.
        """
        # Use body if it has meaningful content, otherwise use the subject
        subject_text = entry.subject.replace(config.PROMPT_MARKER, "", 1).strip()

        if entry.body and len(entry.body.strip()) > len(subject_text):
            return entry.body.strip()
        return subject_text

    def get_response_entry_ids(self, entries: list[BlogEntry]) -> set:
        """
        Collect the prompt IDs that already have responses.

        A response entry has subject like  [LLMR#142] Re: ...
        We extract 142 as the "already answered" prompt ID.
        """
        answered = set()
        import re
        for e in entries:
            m = re.match(r'\[LLMR#(\d+)\]', e.subject.strip())
            if m:
                answered.add(int(m.group(1)))
        return answered

    # ── Single Poll Cycle ─────────────────────────────────────────────

    def poll_once(self):
        """Run one scan → process → respond cycle."""
        try:
            entries = self.moodle.get_blog_entries()
        except Exception as e:
            logger.error("Failed to fetch blog entries: %s", e)
            return

        if not entries:
            logger.debug("No blog entries found.")
            return

        # Find which prompt IDs already have a posted response
        already_answered = self.get_response_entry_ids(entries)

        # Filter to unprocessed prompts
        new_prompts = [
            e for e in entries
            if self.is_prompt(e)
            and e.entry_id not in self.processed_ids
            and e.entry_id not in already_answered
        ]

        if not new_prompts:
            logger.debug("No new prompts to process.")
            return

        logger.info("🔍  Found %d new prompt(s) to process.", len(new_prompts))

        for prompt_entry in new_prompts:
            self._process_prompt(prompt_entry)

    def _process_prompt(self, prompt_entry: BlogEntry):
        """Process a single prompt: get LLM response and post it back."""
        prompt_text = self.extract_prompt_text(prompt_entry)
        logger.info(
            "⚡  Processing prompt #%d: %.80s…",
            prompt_entry.entry_id, prompt_text,
        )

        # 1. Get LLM response
        try:
            response_text = self.llm.generate_response(prompt_text)
        except Exception as e:
            logger.error("LLM failed for #%d: %s", prompt_entry.entry_id, e)
            response_text = f"[Agent Error: Could not get LLM response — {e}]"

        # 2. Build the response blog entry
        prompt_subject = prompt_entry.subject.replace(config.PROMPT_MARKER, "", 1).strip()
        response_subject = f"[LLMR#{prompt_entry.entry_id}] Re: {prompt_subject}"

        # Wrap the response in basic HTML
        response_body = self._format_response_html(
            prompt_text, response_text, prompt_entry.entry_id
        )

        # 3. Post the response
        try:
            success = self.moodle.create_blog_entry(
                subject=response_subject,
                body=response_body,
                publish_state=config.PUBLISH_STATE,
            )
            if success:
                self.processed_ids.add(prompt_entry.entry_id)
                save_processed_ids(self.processed_ids)
                logger.info(
                    "✅  Posted response for prompt #%d", prompt_entry.entry_id
                )
            else:
                logger.error(
                    "❌  Failed to post response for prompt #%d",
                    prompt_entry.entry_id,
                )
        except Exception as e:
            logger.error(
                "❌  Error posting response for #%d: %s",
                prompt_entry.entry_id, e,
            )

    @staticmethod
    def _format_response_html(
        prompt_text: str, response_text: str, prompt_id: int
    ) -> str:
        """Format the LLM response as clean HTML for the blog entry."""
        safe_prompt = html.escape(prompt_text[:500])
        # Convert newlines in response to <br> for HTML display
        safe_response = html.escape(response_text).replace("\n", "<br>")

        return (
            f'<div style="font-family: sans-serif; line-height: 1.6;">'
            f'<p style="color: #666; font-size: 0.9em;">'
            f'<strong>📩 Your prompt (#{prompt_id}):</strong><br>'
            f'<em>{safe_prompt}</em></p>'
            f'<hr style="border: 1px solid #ddd;">'
            f'<div style="margin-top: 10px;">'
            f'{safe_response}'
            f'</div>'
            f'</div>'
        )

    # ── Main Loop ─────────────────────────────────────────────────────

    def run(self):
        """Start the continuous polling loop."""
        logger.info("=" * 60)
        logger.info("  Moodle LLM Bridge Agent — Starting")
        logger.info("=" * 60)

        # Register graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        # Login once (re-login handled automatically)
        try:
            self.moodle.login()
        except Exception as e:
            logger.critical("Could not log in to Moodle: %s", e)
            sys.exit(1)

        logger.info(
            "🔄  Polling every %ds for [LLMQ] entries …",
            config.POLL_INTERVAL,
        )

        while self._running:
            self.poll_once()
            if self._running:
                time.sleep(config.POLL_INTERVAL)

        logger.info("Agent stopped.")

    def _shutdown(self, signum, frame):
        """Handle graceful shutdown."""
        logger.info("Received signal %s — shutting down …", signum)
        self._running = False


# ── Entry Point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    agent = LLMBridgeAgent()
    agent.run()
