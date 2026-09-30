"""
Comprehensive Unit Test Suite for Content Moderation Service.

Verifies:
  1. Valid, compliant conversational text passes cleanly.
  2. Telegram usernames (@username, t.me/...) are detected and blocked.
  3. External URLs (http://, https://, www....) are detected and blocked.
  4. Multiple links and mixed username/URL messages.
  5. False positives (email addresses, decimals, abbreviations, filenames) are NOT blocked.
  6. Configurable rule toggles (enable/disable rules).
  7. Extensibility via custom ModerationRule registration (SOLID Open/Closed Principle).
  8. Edge cases (None, empty strings, multiline strings, wrapped punctuation).
"""
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

from app.core.moderation.content_moderation import (
    ContentModerationService,
    ExternalLinkRule,
    ModerationResult,
    ModerationRule,
    TelegramUsernameRule,
    ViolationType,
    content_moderator,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Valid Conversational Messages
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "Hello! How are you doing today?",
        "I love playing acoustic guitar and watching movies.",
        "What's your favorite cuisine? I really enjoy Italian.",
        "Nice to meet you! How long have you lived in Mumbai?",
        "Haha, that was hilarious! Tell me another story.",
        "Let's meet up at 5pm tomorrow if you're free!",
        "Yes, absolutely 100% agreed with that.",
    ],
)
def test_valid_text_passes_moderation(text: str):
    """Everyday text without external links or handles must be allowed."""
    result = content_moderator.moderate_text(text)
    assert result.is_allowed is True
    assert result.violation_type == ViolationType.NONE


# ─────────────────────────────────────────────────────────────────────────────
# 2. Telegram Username Detection
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "@alice_smith",
        "Add me on @cool_guy123 for later",
        "Hey contact @JohnDoe99 now",
        "My username is @a_very_long_telegram_handle_123 on tg",
        "Ping @super_bot please",
        "Message me: @steve!",
        "Check this (@valid_handle)",
        "Join t.me/awesome_channel",
        "Visit telegram.me/my_group for more",
    ],
)
def test_telegram_usernames_blocked(text: str):
    """Unsolicited Telegram usernames and t.me handles must be blocked."""
    result = content_moderator.moderate_text(text)
    assert result.is_allowed is False
    assert result.violation_type == ViolationType.TELEGRAM_USERNAME
    assert "Telegram" in result.reason or "t.me" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# 3. External URLs Detection
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "Check out https://google.com for answers",
        "http://phishing.site/login?redirect=true",
        "Go to www.example.org right now",
        "Download it from ftp://files.server.net/pub/archive.zip",
        "Look here: https://sub.domain.co.uk/path/to/page#anchor",
        "Multiple links: check https://site1.com and also http://site2.org",
        "Wrapped in brackets: (https://secure.payment.gateway.com)",
        "Markdown link format: [click here](http://malicious-link.cc)",
        "WWW in capitals: WWW.EXTERNAL-PROMO.COM/JOIN",
    ],
)
def test_external_urls_blocked(text: str):
    """External URLs and hyperlinks must be blocked."""
    result = content_moderator.moderate_text(text)
    assert result.is_allowed is False
    assert result.violation_type == ViolationType.EXTERNAL_LINK
    assert "External" in result.reason or "URLs" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# 4. False Positive Prevention
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        # Email addresses (should not trigger Telegram @username rule)
        "You can email info@company.com for inquiries",
        "My work email is contact@my-domain.org",
        # Decimal numbers and version numbers
        "Pi is approximately 3.14159",
        "We are running software version 1.0.4 today",
        "It costs $19.99 right now",
        # Abbreviations and everyday sentence structure
        "I like sports, e.g. football and tennis.",
        "You should bring tools, i.e. a screwdriver.",
        "Good morning, Dr. Watson and Mr. Holmes.",
        "Please visit tomorrow etc. and we'll chat.",
        # Ordinary filenames without protocol
        "Check out main.py and docker-compose.yml in the repository.",
        # '@' symbol with short words or math/time
        "See you @ 5pm at the cafe",
        "Discount is @2x the usual rate",
        "I am currently @home relaxing",  # 4 chars, below 5-char username threshold
    ],
)
def test_false_positives_not_blocked(text: str):
    """Common text patterns, decimals, emails, and abbreviations must NOT be falsely blocked."""
    result = content_moderator.moderate_text(text)
    assert result.is_allowed is True, f"Failed false-positive check for: {text!r}"
    assert result.violation_type == ViolationType.NONE


# ─────────────────────────────────────────────────────────────────────────────
# 5. Rule Configurability & Disabling
# ─────────────────────────────────────────────────────────────────────────────

def test_disable_telegram_username_rule():
    """When username rule is disabled, usernames should be permitted."""
    service = ContentModerationService(
        rules=[
            TelegramUsernameRule(enabled=False),
            ExternalLinkRule(enabled=True),
        ]
    )
    # Username allowed
    assert service.moderate_text("Contact me @cool_user").is_allowed is True
    # URL still blocked
    assert service.moderate_text("Visit https://example.com").is_allowed is False


def test_disable_external_link_rule():
    """When link rule is disabled, URLs should be permitted."""
    service = ContentModerationService(
        rules=[
            TelegramUsernameRule(enabled=True),
            ExternalLinkRule(enabled=False),
        ]
    )
    # URL allowed
    assert service.moderate_text("Visit https://example.com").is_allowed is True
    # Username still blocked
    assert service.moderate_text("Contact @cool_user").is_allowed is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. Extensibility via Custom Rules (SOLID Open/Closed Principle)
# ─────────────────────────────────────────────────────────────────────────────

class ForbiddenKeywordRule(ModerationRule):
    """Example custom rule demonstrating easy extensibility."""

    def __init__(self, keyword: str) -> None:
        self._keyword = keyword.lower()

    @property
    def rule_name(self) -> str:
        return "forbidden_keyword"

    @property
    def is_enabled(self) -> bool:
        return True

    def check(self, text: str) -> ModerationResult:
        if self._keyword in text.lower():
            return ModerationResult(
                is_allowed=False,
                violation_type="forbidden_keyword",
                reason=f"Found forbidden keyword: {self._keyword}",
            )
        return ModerationResult(is_allowed=True)



def test_extensibility_custom_rule():
    """Verify new rules can be added seamlessly without modifying existing classes."""
    service = ContentModerationService()
    service.register_rule(ForbiddenKeywordRule("crypto_giveaway"))

    # Normal text passes
    assert service.moderate_text("Hello there!").is_allowed is True

    # Custom rule blocks prohibited keyword
    res = service.moderate_text("Join our crypto_giveaway today!")
    assert res.is_allowed is False
    assert res.violation_type == "forbidden_keyword"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

def test_edge_cases():
    """Verify None, empty strings, and multiline inputs."""
    # Empty inputs
    assert content_moderator.moderate_text(None).is_allowed is True
    assert content_moderator.moderate_text("").is_allowed is True
    assert content_moderator.moderate_text("   ").is_allowed is True

    # Multiline text with link on subsequent line
    multiline_text = "Line 1: clean hello\nLine 2: everyday talk\nLine 3: https://phishing.site"
    res = content_moderator.moderate_text(multiline_text)
    assert res.is_allowed is False
    assert res.violation_type == ViolationType.EXTERNAL_LINK

    # Mixed username and link (first failing rule halts)
    mixed_text = "Check @my_cool_handle on https://twitter.com"
    res_mixed = content_moderator.moderate_text(mixed_text)
    assert res_mixed.is_allowed is False


if __name__ == "__main__":
    pytest.main(["-v", __file__])
