"""
Google AI Studio client for Gemma models.

Uses the google-genai SDK for direct access to Google's free tier.
Free tier: 15 RPM, 1,500 RPD — 10x more than OpenRouter.

Used as the PRIMARY model for high-volume evaluation tasks.
"""

import logging
import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class GoogleAIResponse:
    """Response from Google AI Studio."""
    content: str
    model_used: str
    success: bool = True
    error: str = ""
    latency_ms: int = 0


class GoogleAIClient:
    """Client for Google AI Studio (Gemma models)."""

    def __init__(
        self,
        api_key: str | None = None,
        models: list[str] | None = None,
    ):
        self.api_key = api_key or os.getenv("GOOGLE_AI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Google AI API key not found. Set GOOGLE_AI_API_KEY in .env"
            )
        # 26b is more reliable; 31b has intermittent 500s
        self.models = models or ["gemma-4-26b-a4b-it", "gemma-4-31b-it"]

        # Lazy import to avoid crash if not installed
        try:
            from google import genai
            self.client = genai.Client(api_key=self.api_key)
        except ImportError:
            raise ImportError(
                "google-genai not installed. Run: pip install google-genai"
            )

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> GoogleAIResponse:
        """Send a chat request to Google AI Studio.
        
        Tries each model in order; falls back on 500/rate-limit errors.
        """
        last_error = ""

        for model in self.models:
            start = time.time()
            try:
                from google.genai import types

                response = self.client.models.generate_content(
                    model=model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                    ),
                )

                latency = int((time.time() - start) * 1000)

                if not response.text:
                    last_error = "Empty response"
                    logger.warning("Google/%s: empty response, trying next...", model)
                    continue

                logger.info("[Google/%s] %dms", model, latency)

                return GoogleAIResponse(
                    content=response.text,
                    model_used=f"google/{model}",
                    success=True,
                    latency_ms=latency,
                )

            except Exception as e:
                latency = int((time.time() - start) * 1000)
                last_error = str(e)[:200]

                if "429" in last_error or "RESOURCE_EXHAUSTED" in last_error:
                    logger.warning(
                        "Google/%s rate limited. Waiting 10s then trying next...", model
                    )
                    time.sleep(10)
                elif "500" in last_error or "INTERNAL" in last_error:
                    logger.warning("Google/%s server error, trying next model...", model)
                else:
                    logger.warning("Google/%s error: %s", model, last_error)

                continue

        # All models failed
        return GoogleAIResponse(
            content="",
            model_used="google/none",
            success=False,
            error=f"All Google models failed. Last: {last_error}",
        )


def test_google_connection() -> bool:
    """Quick test that Google AI Studio is reachable."""
    try:
        client = GoogleAIClient()
        response = client.chat(
            system_prompt="You are a test assistant.",
            user_prompt="Reply with exactly: OK",
            max_tokens=10,
        )
        if response.success:
            logger.info("Google AI connection OK (model: %s)", response.model_used)
            return True
        else:
            logger.error("Google AI test failed: %s", response.error)
            return False
    except Exception as e:
        logger.error("Google AI connection error: %s", e)
        return False
