"""Tests for UTF-8 safe logging (utils/utf8_logging.py).

Regression test for the UnicodeEncodeError bug: on Windows cp1252
consoles, logging a job title containing characters like \\ufeff (BOM)
or emoji would crash with UnicodeEncodeError. The
get_utf8_stream_handler utility must allow any character to be logged
without raising.
"""

from __future__ import annotations

import io
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.utf8_logging import get_utf8_stream_handler, safe_log


class TestUtf8StreamHandler:
    def test_logs_bom_character_without_crash(self) -> None:
        """\\ufeff (BOM) must not crash logging."""
        stream = io.StringIO()
        handler = get_utf8_stream_handler(stream)
        logger = logging.getLogger("test_bom")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(handler)
        # This must not raise UnicodeEncodeError.
        logger.info("Job title with BOM: \ufeff Senior Engineer \ufeff")
        output = stream.getvalue()
        assert "Senior Engineer" in output

    def test_logs_emoji_without_crash(self) -> None:
        stream = io.StringIO()
        handler = get_utf8_stream_handler(stream)
        logger = logging.getLogger("test_emoji")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.info("Job with emoji: 🚀 Senior Dev 🎯")
        output = stream.getvalue()
        assert "Senior Dev" in output

    def test_logs_german_umlauts(self) -> None:
        stream = io.StringIO()
        handler = get_utf8_stream_handler(stream)
        logger = logging.getLogger("test_umlaut")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.info("Werkstudent für Künstliche Intelligenz")
        output = stream.getvalue()
        assert "Werkstudent" in output
        assert "für" in output

    def test_logs_chinese_chars(self) -> None:
        stream = io.StringIO()
        handler = get_utf8_stream_handler(stream)
        logger = logging.getLogger("test_chinese")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.info("软件工程师 (Software Engineer)")
        output = stream.getvalue()
        assert "软件工程师" in output

    def test_logs_arabic_chars(self) -> None:
        stream = io.StringIO()
        handler = get_utf8_stream_handler(stream)
        logger = logging.getLogger("test_arabic")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.info("مهندس برمجيات (Software Engineer)")
        output = stream.getvalue()
        assert "مهندس برمجيات" in output


class TestSafeLog:
    def test_safe_log_returns_original_for_ascii(self) -> None:
        assert safe_log("plain ascii") == "plain ascii"

    def test_safe_log_handles_unicode(self) -> None:
        # Should not raise; may replace chars depending on stdout encoding.
        result = safe_log("title with \ufeff bom")
        assert isinstance(result, str)
        assert "title with" in result
