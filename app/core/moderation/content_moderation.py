"""
Content Moderation Service — lightweight text & link filtering.

Designed following SOLID principles:
  - Single Responsibility: Each rule inspects one category of forbidden content.
  - Open/Closed: New rules (e.g., profanity, phone numbers) can be added
    without modifying existing classes.
  - Liskov Substitution: All rules conform to the ModerationRule interface.
  - Interface Segregation: Minimal, focused contract for rule evaluation.
  - Dependency Inversion: ContentModerationService depends on the ModerationRule
    abstraction, allowing dynamic injection of rules.

Features:
  - Detects unsolicited Telegram usernames (@username, t.me/...)
  - Detects external URLs (http://, https://, www....)
  - Precompiled, high-performance regex patterns with zero I/O or LLM overhead.
  - Granular false-positive guards (emails, decimals, abbreviations).
  - Configurable via application settings.
  - Zero storage or logging of blocked message text.
"""
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence

from app.config.settings import settings

logger = logging.getLogger(__name__)


class ViolationType(str, Enum):
    """Classification of content moderation violations."""
    NONE = "none"
    TELEGRAM_USERNAME = "telegram_username"
    EXTERNAL_LINK = "external_link"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class ModerationResult:
    """Immutable result of a content moderation check."""
    is_allowed: bool
    violation_type: ViolationType | str = ViolationType.NONE
    reason: str = ""



class ModerationRule(ABC):
    """
    Abstract Base Class for moderation rules (Open/Closed Principle).
    """

    @property
    @abstractmethod
    def rule_name(self) -> str:
        """Unique identifier for this rule."""
        pass

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """Whether this rule is active."""
        pass

    @abstractmethod
    def check(self, text: str) -> ModerationResult:
        """
        Inspect text content.

        Returns ModerationResult(is_allowed=True) if compliant,
        or ModerationResult(is_allowed=False, violation_type=...) if violated.
        """
        pass


class TelegramUsernameRule(ModerationRule):
    """
    Detects unsolicited Telegram usernames.

    Rules:
      - Telegram usernames are 5-32 characters: [a-zA-Z0-9_]{5,32}
      - Starts with '@' not preceded by an alphanumeric character (avoids email false-positives)
      - Also catches direct Telegram user links: t.me/username or telegram.me/username
    """

    # Telegram handle: (?<!\w)@([a-zA-Z0-9_]{5,32})\b
    # Ensures 'contact@example.com' won't match as '@example' because '@' is preceded by 't'
    _USERNAME_PATTERN = re.compile(
        r"(?<![a-zA-Z0-9_])@([a-zA-Z0-9_]{5,32})\b",
        re.IGNORECASE,
    )

    # Naked telegram links like t.me/username or telegram.me/username
    _TG_LINK_PATTERN = re.compile(
        r"\b(?:t|telegram)\.me\/[a-zA-Z0-9_]{5,32}\b",
        re.IGNORECASE,
    )

    def __init__(self, enabled: bool | None = None) -> None:
        self._enabled = enabled if enabled is not None else settings.MODERATION_BLOCK_USERNAMES

    @property
    def rule_name(self) -> str:
        return "telegram_username"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def check(self, text: str) -> ModerationResult:
        if not self.is_enabled or not text:
            return ModerationResult(is_allowed=True)

        if self._USERNAME_PATTERN.search(text) or self._TG_LINK_PATTERN.search(text):
            return ModerationResult(
                is_allowed=False,
                violation_type=ViolationType.TELEGRAM_USERNAME,
                reason="Telegram handles and t.me links are not permitted in anonymous chats.",
            )

        return ModerationResult(is_allowed=True)


class ExternalLinkRule(ModerationRule):
    """
    Detects external web URLs and hyperlinks.

    Matches:
      - http://, https://, ftp://
      - www.domain.com
    Avoids false positives on decimals (3.14), abbreviations (e.g., i.e.),
    and ordinary filenames (main.py).
    """

    # Matches http://, https://, ftp://, or www. followed by valid domain characters
    _URL_PATTERN = re.compile(
        r"(?i)\b(?:https?:\/\/|ftp:\/\/|www\.)[^\s<>'\"{}|\\^`\[\]()]+",
        re.IGNORECASE,
    )

    def __init__(self, enabled: bool | None = None) -> None:
        self._enabled = enabled if enabled is not None else settings.MODERATION_BLOCK_URLS

    @property
    def rule_name(self) -> str:
        return "external_link"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def check(self, text: str) -> ModerationResult:
        if not self.is_enabled or not text:
            return ModerationResult(is_allowed=True)

        if self._URL_PATTERN.search(text):
            return ModerationResult(
                is_allowed=False,
                violation_type=ViolationType.EXTERNAL_LINK,
                reason="External links and URLs are not permitted in anonymous chats.",
            )

        return ModerationResult(is_allowed=True)


class ContentModerationService:
    """
    High-level content moderation service (Dependency Inversion & Open/Closed).

    Coordinates a sequence of ModerationRule strategies. Rules can be added,
    configured, or substituted without modifying the core moderation loop.
    """

    DEFAULT_BLOCKED_MESSAGE: str = (
        "🚫 <b>Message Blocked:</b> Sharing external links or Telegram handles is not permitted "
        "in anonymous chats to protect your privacy and security."
    )

    def __init__(self, rules: Optional[Sequence[ModerationRule]] = None) -> None:
        if rules is not None:
            self._rules: List[ModerationRule] = list(rules)
        else:
            # Default strategy pipeline
            self._rules = [
                TelegramUsernameRule(),
                ExternalLinkRule(),
            ]

    @property
    def rules(self) -> Sequence[ModerationRule]:
        """Return the current active rules."""
        return tuple(self._rules)

    def register_rule(self, rule: ModerationRule) -> None:
        """Register an additional moderation rule (Open/Closed Principle)."""
        self._rules.append(rule)

    def moderate_text(self, text: str | None) -> ModerationResult:
        """
        Evaluate text content against all enabled moderation rules.

        Returns:
            ModerationResult with is_allowed=True if all rules pass,
            or the first failing ModerationResult.
        """
        if not text:
            return ModerationResult(is_allowed=True)

        for rule in self._rules:
            if not rule.is_enabled:
                continue

            result = rule.check(text)
            if not result.is_allowed:
                # Log violation metadata ONLY — never log message content
                v_type = getattr(result.violation_type, "value", str(result.violation_type))
                logger.warning(
                    "Content moderation violation: rule=%s violation_type=%s",
                    rule.rule_name,
                    v_type,
                )
                return result


        return ModerationResult(is_allowed=True)


# Global singleton instance
content_moderator = ContentModerationService()
