"""Tests for strata.grounding.filter — sensitive information detection."""

from __future__ import annotations

from strata.grounding.filter import contains_sensitive, redact


class TestContainsSensitive:
    def test_password_detected(self) -> None:
        assert contains_sensitive("enter my password here")

    def test_token_detected(self) -> None:
        assert contains_sensitive("use this API_KEY for auth")

    def test_clean_text_passes(self) -> None:
        assert not contains_sensitive("click the blue button")

    def test_case_insensitive(self) -> None:
        assert contains_sensitive("my PASSWORD is secret")

    def test_extra_patterns(self) -> None:
        assert contains_sensitive("my social is 123", extra_patterns=("social",))

    def test_extra_patterns_no_match(self) -> None:
        assert not contains_sensitive("hello world", extra_patterns=("foobar",))


class TestRedact:
    def test_replaces_password(self) -> None:
        result = redact("my password is 123")
        assert "password" not in result.lower()
        assert "[REDACTED]" in result

    def test_preserves_clean_text(self) -> None:
        assert redact("click the button") == "click the button"

    def test_case_insensitive_redaction(self) -> None:
        result = redact("PASSWORD and Token found")
        assert "PASSWORD" not in result
        assert "Token" not in result
