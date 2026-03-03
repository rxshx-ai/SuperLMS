"""
LLM client — supports both Google Gemini and Groq backends.

The active backend is controlled by the LLM_PROVIDER config value:
    "gemini"  →  google-generativeai SDK
    "groq"    →  groq SDK

Both expose the same  generate_response(prompt) -> str  interface.
"""

import logging
from typing import Literal

logger = logging.getLogger("llm")

# ── System prompt (shared) ────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. The user is asking you a question "
    "through a Moodle LMS blog relay system. Provide a clear, well-formatted "
    "response. Use plain text with simple formatting (no markdown headers with #). "
    "Keep the response concise but thorough."
)

Provider = Literal["gemini", "groq"]


# ── Gemini backend ────────────────────────────────────────────────────

class _GeminiBackend:
    """Wrapper around the Google Generative AI SDK."""

    DEFAULT_MODEL = "gemini-1.5-flash"

    def __init__(self, api_key: str, model_name: str | None = None):
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "google-generativeai is not installed. "
                "Run:  pip install google-generativeai"
            ) from exc

        genai.configure(api_key=api_key)
        model = model_name or self.DEFAULT_MODEL
        self._model = genai.GenerativeModel(
            model_name=model,
            system_instruction=_SYSTEM_PROMPT,
        )
        logger.info("Gemini client ready  |  model=%s", model)

    def generate(self, prompt: str) -> str:
        response = self._model.generate_content(
            prompt,
            generation_config={"temperature": 0.7, "max_output_tokens": 4096},
        )
        return response.text or ""


# ── Groq backend ──────────────────────────────────────────────────────

class _GroqBackend:
    """Wrapper around the Groq SDK."""

    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self, api_key: str, model_name: str | None = None):
        try:
            from groq import Groq
        except ImportError as exc:
            raise ImportError(
                "groq is not installed. Run:  pip install groq"
            ) from exc

        self._client = Groq(api_key=api_key)
        self._model = model_name or self.DEFAULT_MODEL
        logger.info("Groq client ready  |  model=%s", self._model)

    def generate(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""


# ── Public LLMClient ──────────────────────────────────────────────────

class LLMClient:
    """
    Unified LLM client.

    Usage:
        client = LLMClient(provider="gemini", api_key="AIza...")
        client = LLMClient(provider="groq",   api_key="gsk_...")
    """

    def __init__(
        self,
        provider: Provider,
        api_key: str,
        model_name: str | None = None,
    ):
        self._provider = provider
        if provider == "gemini":
            self._backend = _GeminiBackend(api_key, model_name)
        elif provider == "groq":
            self._backend = _GroqBackend(api_key, model_name)
        else:
            raise ValueError(f"Unknown LLM provider: {provider!r}. Choose 'gemini' or 'groq'.")

    def generate_response(self, prompt: str) -> str:
        """Send *prompt* to the active LLM backend and return the response text."""
        try:
            text = self._backend.generate(prompt)
            if text:
                logger.info(
                    "Got response (%d chars) [%s] for prompt: %.60s…",
                    len(text), self._provider, prompt,
                )
                return text
            else:
                logger.warning("Empty response for prompt: %.60s…", prompt)
                return "[Agent: LLM returned an empty response. Try rephrasing your prompt.]"
        except Exception as e:
            logger.error("%s API error: %s", self._provider.capitalize(), e)
            return f"[Agent Error: {type(e).__name__} — {e}]"
