"""
Moodle LLM Bridge — Main Agent

Continuously polls Moodle dashboard text blocks tagged as prompts
([LLMQ]), forwards them to the Groq LLM, and posts the responses
back as new dashboard text blocks tagged [LLMR#<id>].

Usage:
    python agent.py
"""

import json
import time
import signal
import logging
import sys
import html
import threading
from pathlib import Path

import config
from moodle_client import MoodleClient, TextBlock
from llm_client import LLMClient

# ── Logging Setup ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-8s | %(levelname)-5s | %(message)s",
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
    The main agent that bridges Moodle dashboard text blocks <-> Groq LLM.

    Workflow per poll cycle:
      1. Fetch all dashboard text blocks
      2. Identify NEW prompts ([LLMQ] prefix, not yet processed)
      3. For each new prompt:
         a. Extract the actual question
         b. Send to Groq
         c. Post the response as [LLMR#<block_id>] text block
         d. Mark the prompt as processed
    """

    def __init__(self):
        config.validate()

        self.moodle = MoodleClient(
            base_url=config.MOODLE_URL,
            username=config.MOODLE_USERNAME,
            password=config.MOODLE_PASSWORD,
        )
        self.llm = LLMClient(api_key=config.GROQ_API_KEY)
        self.processed_ids = load_processed_ids()
        self._running = True

    # ── Prompt / Response Identification ──────────────────────────────

    @staticmethod
    def _entry_id(entry: TextBlock) -> int:
        """Get the stable identifier for a dashboard text block."""
        return entry.block_id

    @staticmethod
    def _entry_title(entry: TextBlock) -> str:
        """Get title text from a dashboard text block."""
        return entry.title or ""

    @staticmethod
    def _entry_body(entry: TextBlock) -> str:
        """Get body text from a dashboard text block."""
        return entry.body or ""

    @staticmethod
    def _strip_marker(text: str, marker: str) -> str:
        """Remove a marker only if it appears as a leading prefix."""
        value = (text or "").strip()
        if value.startswith(marker):
            return value.replace(marker, "", 1).strip()
        return value

    @staticmethod
    def is_prompt(entry: TextBlock) -> bool:
        """Does this block look like a user prompt?"""
        title = LLMBridgeAgent._entry_title(entry).strip()
        body = LLMBridgeAgent._entry_body(entry).strip()
        return (
            title.startswith(config.PROMPT_MARKER)
            or body.startswith(config.PROMPT_MARKER)
        )

    @staticmethod
    def is_response(entry: TextBlock) -> bool:
        """Does this block look like an agent response?"""
        return LLMBridgeAgent._entry_title(entry).strip().startswith(config.RESPONSE_MARKER)

    @staticmethod
    def extract_prompt_text(entry: TextBlock) -> str:
        """
        Get the actual question text from a prompt block.

        The title/body can have the prefix: [LLMQ]
        The body may contain the full detailed prompt.
        """
        title_text = LLMBridgeAgent._strip_marker(
            LLMBridgeAgent._entry_title(entry),
            config.PROMPT_MARKER,
        )
        body_text = LLMBridgeAgent._strip_marker(
            LLMBridgeAgent._entry_body(entry),
            config.PROMPT_MARKER,
        )

        if body_text and len(body_text) > len(title_text):
            return body_text
        return title_text or body_text

    def get_response_entry_ids(self, entries: list[TextBlock]) -> set:
        """
        Collect the prompt IDs that already have responses.

        A response block has title like  [LLMR#142] Re: ...
        We extract 142 as the "already answered" prompt ID.
        """
        answered = set()
        import re
        for e in entries:
            m = re.match(r'\[LLMR#(\d+)\]', self._entry_title(e).strip())
            if m:
                answered.add(int(m.group(1)))
        return answered

    # ── Single Poll Cycle ─────────────────────────────────────────────

    def poll_once(self):
        """Run one scan → process → respond cycle."""
        try:
            entries = self.moodle.get_dashboard_text_blocks(
                edit_mode=True,
                block_region="content",
            )
        except Exception as e:
            logger.error("Failed to fetch dashboard text blocks: %s", e)
            return

        if not entries:
            logger.debug("No dashboard text blocks found in content region.")
            return

        # Find which prompt IDs already have a posted response
        already_answered = self.get_response_entry_ids(entries)

        # Filter to unprocessed prompts
        new_prompts = [
            e for e in entries
            if self.is_prompt(e)
            and self._entry_id(e) not in self.processed_ids
            and self._entry_id(e) not in already_answered
        ]

        if not new_prompts:
            logger.debug("No new prompts to process.")
            return

        logger.info("Found %d new prompt(s) to process.", len(new_prompts))

        for prompt_entry in new_prompts:
            self._process_prompt(prompt_entry)

    def _process_prompt(self, prompt_entry: TextBlock):
        """Process a single prompt: get LLM response and post it back."""
        prompt_id = self._entry_id(prompt_entry)
        prompt_text = self.extract_prompt_text(prompt_entry)
        logger.info(
            "Processing prompt block #%d: %.80s...",
            prompt_id,
            prompt_text,
        )

        # 1. Get LLM response
        try:
            response_text = self.llm.generate_response(prompt_text)
        except Exception as e:
            logger.error("LLM failed for block #%d: %s", prompt_id, e)
            response_text = f"[Agent Error: Could not get LLM response - {e}]"

        # 2. Build the response text block
        prompt_title = self._strip_marker(self._entry_title(prompt_entry), config.PROMPT_MARKER)
        if not prompt_title:
            prompt_title = prompt_text[:80]
        response_title = f"[LLMR#{prompt_id}] Re: {prompt_title}"

        # Wrap the response in basic HTML
        response_body = self._format_response_html(
            prompt_text,
            response_text,
            prompt_id,
        )

        # 3. Post the response
        try:
            response_block_id = self.moodle.create_dashboard_text_block(
                title=response_title,
                body=response_body,
                block_region="content",
            )
            if response_block_id is not None:
                self.processed_ids.add(prompt_id)
                save_processed_ids(self.processed_ids)
                logger.info(
                    "Posted response block #%s for prompt block #%d",
                    response_block_id,
                    prompt_id,
                )
            else:
                logger.error(
                    "Failed to post response for prompt block #%d",
                    prompt_id,
                )
        except Exception as e:
            logger.error(
                "Error posting response for block #%d: %s",
                prompt_id,
                e,
            )

    @staticmethod
    def _format_response_html(
        prompt_text: str, response_text: str, prompt_id: int
    ) -> str:
        """Format the LLM response as clean HTML for a dashboard text block."""
        safe_prompt = html.escape(prompt_text[:500])
        # Convert newlines in response to <br> for HTML display
        safe_response = html.escape(response_text).replace("\n", "<br>")

        return (
            f'<div style="font-family: sans-serif; line-height: 1.6;">'
            f'<p style="color: #666; font-size: 0.9em;">'
            f'<strong>Your prompt (#{prompt_id}):</strong><br>'
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
        logger.info("Moodle LLM Bridge Agent - Starting")
        logger.info("=" * 60)

        # Register graceful shutdown only when running in the main thread.
        # When embedded in a FastAPI app we run in a background thread,
        # where signal.signal(...) would raise ValueError.
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._shutdown)
            signal.signal(signal.SIGTERM, self._shutdown)

        # Login once (re-login handled automatically)
        try:
            self.moodle.login()
        except Exception as e:
            logger.critical("Could not log in to Moodle: %s", e)
            sys.exit(1)

        logger.info(
            "Polling every %ds for [LLMQ] dashboard text blocks",
            config.POLL_INTERVAL,
        )

        while self._running:
            self.poll_once()
            if self._running:
                time.sleep(config.POLL_INTERVAL)

        logger.info("Agent stopped.")

    def _shutdown(self, signum, frame):
        """Handle graceful shutdown."""
        logger.info("Received signal %s - shutting down", signum)
        self._running = False


# ── Entry Point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    agent = LLMBridgeAgent()
    agent.run()
