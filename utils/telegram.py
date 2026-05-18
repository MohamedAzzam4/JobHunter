"""
Telegram notification sender.

Sends job evaluation results and generated CVs to your Telegram chat.
Uses raw HTTP (httpx) — no extra bot framework needed.

Messages:
- Every evaluated job: score + title + company + link
- High-scoring jobs: full evaluation + CV PDF attachment
"""

import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Send notifications and files to Telegram."""

    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id)

        if not self.enabled:
            logger.info("Telegram not configured (missing token or chat_id)")

    def _base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def send_message(self, text: str) -> bool:
        """Send a text message to Telegram."""
        if not self.enabled:
            return False

        try:
            resp = httpx.post(
                f"{self._base_url()}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                logger.info("Telegram message sent")
                return True
            else:
                logger.warning("Telegram send failed: HTTP %d", resp.status_code)
                return False
        except Exception as e:
            logger.warning("Telegram error: %s", e)
            return False

    def send_document(self, file_path: str, caption: str = "") -> bool:
        """Send a file (PDF, etc.) to Telegram."""
        if not self.enabled:
            return False

        path = Path(file_path)
        if not path.exists():
            logger.warning("Telegram: file not found: %s", file_path)
            return False

        try:
            with open(path, "rb") as f:
                resp = httpx.post(
                    f"{self._base_url()}/sendDocument",
                    data={
                        "chat_id": self.chat_id,
                        "caption": caption[:1024],  # Telegram caption limit
                        "parse_mode": "HTML",
                    },
                    files={"document": (path.name, f)},
                    timeout=30,
                )
            if resp.status_code == 200:
                logger.info("Telegram document sent: %s", path.name)
                return True
            else:
                logger.warning("Telegram doc send failed: HTTP %d", resp.status_code)
                return False
        except Exception as e:
            logger.warning("Telegram doc error: %s", e)
            return False

    def notify_evaluation(self, evaluation: dict) -> bool:
        """Send a formatted evaluation summary."""
        score = evaluation.get("global_score", "?")
        company = evaluation.get("company", "Unknown")
        title = evaluation.get("title", "Unknown")
        url = evaluation.get("url", "")
        recommendation = evaluation.get("recommendation", "?")
        german = "Yes" if evaluation.get("german_required") else "No"
        summary = evaluation.get("summary", "")

        # Score emoji
        if isinstance(score, (int, float)):
            if score >= 4.0:
                icon = "🟢"
            elif score >= 3.5:
                icon = "🟡"
            elif score >= 3.0:
                icon = "🟠"
            else:
                icon = "🔴"
        else:
            icon = "⚪"

        text = (
            f"{icon} <b>{score}/5</b> — {recommendation.upper()}\n"
            f"<b>{company}</b> — {title}\n"
            f"German required: {german}\n"
        )
        if summary:
            text += f"\n{summary}\n"
        if url:
            text += f"\n<a href=\"{url}\">View Job</a>"

        return self.send_message(text)

    def notify_cv_generated(self, evaluation: dict, pdf_path: str | None = None) -> bool:
        """Notify that a CV + cover letter were generated, optionally attach PDF."""
        score = evaluation.get("global_score", "?")
        company = evaluation.get("company", "Unknown")
        title = evaluation.get("title", "Unknown")

        caption = (
            f"📄 CV Generated — {score}/5\n"
            f"{company} — {title}"
        )

        if pdf_path and Path(pdf_path).exists():
            return self.send_document(pdf_path, caption)
        else:
            return self.send_message(caption)
