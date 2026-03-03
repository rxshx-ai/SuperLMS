"""
Groq LLM client.

Wraps the Groq SDK to provide a simple
`generate_response(prompt)` interface for the agent.
"""

import logging
from groq import Groq

logger = logging.getLogger("llm")


class LLMClient:
    """Thin wrapper around the Groq API."""

    def __init__(self, api_key: str, model_name: str = "llama-3.3-70b-versatile"):
        self.client = Groq(api_key=api_key)
        self._model_name = model_name
        logger.info("Groq client ready  |  model=%s", model_name)

    def generate_response(self, prompt: str) -> str:
        """
        Send a prompt to Groq and return the text response.
        """
        system_context = (
            "You are a helpful AI assistant. The user is asking you a question "
            "through a Moodle LMS blog relay system. Provide a clear, well-formatted "
            "response. Use plain text with simple formatting (no markdown headers with #). "
            "Keep the response concise but thorough."
        )

        try:
            response = self.client.chat.completions.create(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_context},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=4096,
            )

            text = response.choices[0].message.content
            if text:
                logger.info(
                    "Got response (%d chars) for prompt: %.60s…",
                    len(text), prompt,
                )
                return text
            else:
                logger.warning("Empty response for prompt: %.60s…", prompt)
                return "[Agent: LLM returned an empty response. Try rephrasing your prompt.]"

        except Exception as e:
            logger.error("Groq API error: %s", e)
            return f"[Agent Error: {type(e).__name__} — {e}]"
