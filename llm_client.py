"""Groq-only LLM client for the Moodle bridge agent."""

from __future__ import annotations

import logging
from typing import Optional

from groq import Groq

import config

logger = logging.getLogger("llm")


_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. The user is asking you a question "
    "through a Moodle LMS blog relay system. Provide a clear, well-formatted "
    "response. Use plain text with simple formatting (no markdown headers with #). "
    "Keep the response concise but thorough."
)


class LLMClient:
    """Thin wrapper around the Groq SDK.

    Usage:
        client = LLMClient()
        text = client.generate_response("Your prompt")
    """

    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self._api_key = api_key or config.GROQ_API_KEY
        self._model = model_name or getattr(config, "GROQ_MODEL", self.DEFAULT_MODEL)

        self._client = Groq(api_key=self._api_key)
        logger.info("Groq client ready  |  model=%s", self._model)

    def generate_response(self, prompt: str) -> str:
        """Send *prompt* to Groq and return the response text."""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=4096,
            )
            text = response.choices[0].message.content or ""
            if text:
                logger.info(
                    "Got response (%d chars) [groq] for prompt: %.60s…",
                    len(text), prompt,
                )
                return text
            logger.warning("Empty response for prompt: %.60s…", prompt)
            return "[Agent: LLM returned an empty response. Try rephrasing your prompt.]"
        except Exception as e:
            logger.error("Groq API error: %s", e)
            return f"[Agent Error: {type(e).__name__} — {e}]"