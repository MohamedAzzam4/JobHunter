"""
OpenRouter API client.

Provides an OpenAI-compatible wrapper for free models on OpenRouter.
Features:
- Automatic model fallback (primary → backup → fallback)
- Rate limit handling with exponential backoff
- Request/response logging
- Token usage tracking
"""

import logging
import os
import time
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = 90  # seconds — free models can be slow


@dataclass
class ChatResponse:
    """Response from an OpenRouter chat completion."""
    content: str
    model_used: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    success: bool = True
    error: str = ""
    latency_ms: int = 0


class OpenRouterClient:
    """Client for OpenRouter API with automatic model fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        models: list[str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY") or os.getenv("OpenRouter")
        if not self.api_key:
            raise ValueError(
                "OpenRouter API key not found. Set OPENROUTER_API_KEY in .env"
            )

        self.models = models or [
            "google/gemma-4-31b-it:free",
            "nvidia/nvidia-nemotron-3-super:free",
            "openai/gpt-oss-120b:free",
        ]
        self.timeout = timeout

        # Track usage
        self._total_requests = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> ChatResponse:
        """Send a chat completion request with automatic model fallback.
        
        Tries each model in order. If rate-limited (429), waits and tries
        the next model. If all models fail, returns error response.
        """
        last_error = ""

        for i, model in enumerate(self.models):
            try:
                response = self._request(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if response.success:
                    self._total_requests += 1
                    self._total_prompt_tokens += response.prompt_tokens
                    self._total_completion_tokens += response.completion_tokens
                    return response
                else:
                    last_error = response.error
                    logger.warning(
                        f"Model {model} failed: {response.error}. "
                        f"Trying next model..."
                    )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Model {model} exception: {e}. Trying next model..."
                )

            # Small delay before trying next model
            if i < len(self.models) - 1:
                time.sleep(1)

        # All models failed
        return ChatResponse(
            content="",
            model_used="none",
            success=False,
            error=f"All models failed. Last error: {last_error}",
        )

    def _request(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> ChatResponse:
        """Make a single request to OpenRouter."""
        start = time.time()

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/job-search-automation",
                    "X-Title": "Job Search Automation",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )

        latency = int((time.time() - start) * 1000)

        if resp.status_code == 429:
            # Rate limited — try to get retry-after
            retry_after = int(resp.headers.get("retry-after", "5"))
            logger.warning(
                f"Rate limited on {model}. Retry-After: {retry_after}s"
            )
            time.sleep(min(retry_after, 30))  # Cap wait at 30s
            return ChatResponse(
                content="",
                model_used=model,
                success=False,
                error=f"Rate limited (429). Retry-After: {retry_after}s",
                latency_ms=latency,
            )

        if resp.status_code != 200:
            return ChatResponse(
                content="",
                model_used=model,
                success=False,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                latency_ms=latency,
            )

        data = resp.json()

        # Check for error in response body
        if "error" in data:
            return ChatResponse(
                content="",
                model_used=model,
                success=False,
                error=f"API error: {data['error']}",
                latency_ms=latency,
            )

        # Extract content
        choices = data.get("choices", [])
        if not choices:
            return ChatResponse(
                content="",
                model_used=model,
                success=False,
                error="No choices in response",
                latency_ms=latency,
            )

        content = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})

        logger.info(
            "[%s] %s->%s tokens, %dms",
            model,
            usage.get('prompt_tokens', '?'),
            usage.get('completion_tokens', '?'),
            latency,
        )

        return ChatResponse(
            content=content,
            model_used=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            success=True,
            latency_ms=latency,
        )

    @property
    def usage_stats(self) -> dict:
        """Return cumulative usage stats."""
        return {
            "total_requests": self._total_requests,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
        }


def test_connection() -> bool:
    """Quick test that OpenRouter is reachable and the API key works."""
    try:
        client = OpenRouterClient()
        response = client.chat(
            system_prompt="You are a test assistant.",
            user_prompt="Reply with exactly: OK",
            max_tokens=10,
        )
        if response.success:
            logger.info(f"OpenRouter connection OK (model: {response.model_used})")
            return True
        else:
            logger.error(f"OpenRouter test failed: {response.error}")
            return False
    except Exception as e:
        logger.error(f"OpenRouter connection error: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing OpenRouter connection...")
    ok = test_connection()
    print(f"Result: {'✅ OK' if ok else '❌ FAILED'}")
