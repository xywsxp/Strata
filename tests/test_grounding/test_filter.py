"""Tests for strata.grounding.filter — sensitive information detection.

Phase 10.3 rewrote the filter: regex word-boundary keyword matching plus
concrete secret-shape detection (sk-… / AKIA… / JWT / Bearer …).
"""

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

    def test_extra_pattern_regex(self) -> None:
        assert contains_sensitive("my social is 123", extra_patterns=(r"\bsocial\b",))

    def test_extra_pattern_no_match(self) -> None:
        assert not contains_sensitive("hello world", extra_patterns=(r"foobar",))


class TestWordBoundaryNoFalsePositive:
    def test_tokenization_not_flagged(self) -> None:
        """'tokenization' must not trigger the 'token' keyword (word boundary)."""
        assert not contains_sensitive("use subword tokenization for NLP")

    def test_keyboard_not_flagged(self) -> None:
        assert not contains_sensitive("press any keyboard key")

    def test_passwordless_word_not_flagged(self) -> None:
        """'passwordless' contains 'password' as a prefix but \\bpassword\\b
        requires a non-word boundary on both sides — 'passwordless' does not
        match, which is the intended behavior (word-boundary precision)."""
        assert not contains_sensitive("login with passwordless magic link")


class TestSecretShapeDetection:
    def test_openai_style_api_key(self) -> None:
        assert contains_sensitive("key is sk-1234567890abcdefghijklmnopqrstuvwxyzABC")

    def test_aws_access_key_id(self) -> None:
        assert contains_sensitive("access AKIAIOSFODNN7EXAMPLE here")

    def test_jwt_token(self) -> None:
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        assert contains_sensitive(f"authorization: {jwt}")

    def test_bearer_token(self) -> None:
        assert contains_sensitive("Authorization: Bearer abc123def456ghi789jklmno")

    def test_random_alnum_sequence_not_flagged(self) -> None:
        assert not contains_sensitive("sequence: abc123def456")


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

    def test_redact_api_key_shape(self) -> None:
        s = "use sk-1234567890abcdefghijklmnop1234567890 please"
        result = redact(s)
        assert "sk-1234567890" not in result
        assert "[REDACTED]" in result

    def test_redact_is_idempotent(self) -> None:
        s = "authorization Bearer abc123def456ghi789jklmno and token: X"
        once = redact(s)
        twice = redact(once)
        assert once == twice
        assert not contains_sensitive(once)

    def test_redact_regex_extra_pattern(self) -> None:
        import re as _re

        literal = "foo.bar+baz"
        s = f"contains {literal} literal"
        result = redact(s, extra_patterns=(_re.escape(literal),))
        assert literal not in result
        assert "[REDACTED]" in result
