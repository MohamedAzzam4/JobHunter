"""
Smart AI Router — orchestrates across multiple providers and API keys.

Strategy:
- Google AI Studio (Gemma 4): PRIMARY for evaluation (1,500 RPD)
- OpenRouter keys (4x rotation): CV + cover letter generation
- Automatic fallback: if Google fails -> OpenRouter, if one key fails -> next key

Key rotation: Distributes requests across multiple OpenRouter accounts to
multiply the effective daily quota from ~200/key to ~800 total.
"""

import logging
import os
import time
from dataclasses import dataclass
from itertools import cycle

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class SmartResponse:
    """Unified response from any provider."""
    content: str
    model_used: str
    provider: str  # "google" or "openrouter"
    success: bool = True
    error: str = ""
    latency_ms: int = 0


class SmartRouter:
    """Routes AI requests across Google AI Studio + multiple OpenRouter keys.
    
    Usage:
        router = SmartRouter()
        
        # Evaluation (uses Google first, high quota)
        response = router.evaluate(system_prompt, user_prompt)
        
        # CV/Cover letter (uses OpenRouter, rotates keys)
        response = router.generate(system_prompt, user_prompt)
    """

    def __init__(self):
        self._google_client = None
        self._openrouter_keys = self._load_openrouter_keys()
        self._key_cycle = cycle(self._openrouter_keys) if self._openrouter_keys else None
        self._models = self._load_openrouter_models()
        self._request_count = 0

        # Try to init Google client
        try:
            from agents.google_client import GoogleAIClient
            self._google_client = GoogleAIClient()
            logger.info("Google AI Studio: ready (gemma-4-31b-it)")
        except Exception as e:
            logger.warning("Google AI Studio not available: %s", e)

        logger.info(
            "SmartRouter initialized: Google=%s, OpenRouter keys=%d",
            "yes" if self._google_client else "no",
            len(self._openrouter_keys),
        )

    def _load_openrouter_keys(self) -> list[str]:
        """Load all OpenRouter API keys from .env."""
        keys = []
        # Check numbered keys: OpenRouter, OpenRouter2, OpenRouter3, ...
        for suffix in ["", "2", "3", "4", "5", "6", "7", "8"]:
            key = os.getenv(f"OpenRouter{suffix}")
            if key and key.startswith("sk-or-"):
                keys.append(key)

        # Also check the standard name
        std_key = os.getenv("OPENROUTER_API_KEY")
        if std_key and std_key.startswith("sk-or-") and std_key not in keys:
            keys.insert(0, std_key)

        logger.info("Loaded %d OpenRouter API keys", len(keys))
        return keys

    @staticmethod
    def _load_openrouter_models() -> list[str]:
        """Load OpenRouter model chain from tracked profile.yml.

        The gitignored local profile never changes model chains
        (see utils/profile_loader).
        """
        try:
            from utils.profile_loader import load_profile_with_local

            profile = load_profile_with_local()
            models = profile.get("evaluation", {}).get("openrouter_models", [])
            if models:
                return models
        except Exception:
            pass
        # Defaults if profile.yml is missing or has no models
        return [
            "openai/gpt-oss-120b:free",
            "google/gemma-4-31b-it:free",
            "deepseek/deepseek-v4-flash:free",
        ]

    def evaluate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> SmartResponse:
        """Evaluate a job — uses Google AI Studio first (high quota).
        
        Fallback chain: Google -> OpenRouter (rotating keys)
        """
        # Try Google first (1,500 RPD vs OpenRouter's ~200)
        if self._google_client:
            response = self._google_client.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if response.success:
                return SmartResponse(
                    content=response.content,
                    model_used=response.model_used,
                    provider="google",
                    latency_ms=response.latency_ms,
                )
            logger.warning(
                "Google AI failed: %s. Falling back to OpenRouter.",
                response.error,
            )

        # Fallback to OpenRouter
        return self._openrouter_request(
            system_prompt, user_prompt, temperature, max_tokens
        )

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> SmartResponse:
        """Generate content (CV/cover letter) — uses OpenRouter with key rotation.
        
        Key rotation distributes load: 4 keys x 200 RPD = 800 RPD effective.
        """
        return self._openrouter_request(
            system_prompt, user_prompt, temperature, max_tokens
        )

    def _openrouter_request(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> SmartResponse:
        """Make a request to OpenRouter, rotating across keys and models."""
        import httpx

        models = self._models

        # Try each key+model combination
        keys_tried = 0
        max_attempts = min(len(self._openrouter_keys) * len(models), 12)

        for attempt in range(max_attempts):
            if not self._key_cycle:
                return SmartResponse(
                    content="", model_used="none", provider="openrouter",
                    success=False, error="No OpenRouter keys configured",
                )

            api_key = next(self._key_cycle)
            model = models[attempt % len(models)]
            key_idx = self._openrouter_keys.index(api_key) + 1

            start = time.time()
            try:
                with httpx.Client(timeout=90) as client:
                    resp = client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
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
                    retry_after = int(resp.headers.get("retry-after", "5"))
                    logger.warning(
                        "OpenRouter key#%d rate limited on %s. Trying next key...",
                        key_idx, model
                    )
                    time.sleep(min(retry_after, 5))
                    continue

                if resp.status_code != 200:
                    logger.warning(
                        "OpenRouter key#%d %s: HTTP %d. Trying next...",
                        key_idx, model, resp.status_code
                    )
                    continue

                data = resp.json()
                if "error" in data:
                    logger.warning(
                        "OpenRouter key#%d %s: %s. Trying next...",
                        key_idx, model, str(data["error"])[:100]
                    )
                    continue

                choices = data.get("choices", [])
                if not choices:
                    continue

                content = choices[0].get("message", {}).get("content", "")
                usage = data.get("usage", {})

                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
                logger.info(
                    "[OR-key#%d/%s] in=%s out=%s total=%s tokens, %dms",
                    key_idx, model.split("/")[-1],
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    latency,
                )

                self._request_count += 1
                return SmartResponse(
                    content=content,
                    model_used=model,
                    provider="openrouter",
                    latency_ms=latency,
                )

            except Exception as e:
                logger.warning(
                    "OpenRouter key#%d error: %s. Trying next...",
                    key_idx, str(e)[:100]
                )
                continue

        return SmartResponse(
            content="", model_used="none", provider="openrouter",
            success=False,
            error="All OpenRouter keys/models exhausted",
        )

    @property
    def stats(self) -> dict:
        return {
            "google_available": self._google_client is not None,
            "openrouter_keys": len(self._openrouter_keys),
            "total_requests": self._request_count,
        }
