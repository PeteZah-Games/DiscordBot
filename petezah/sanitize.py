import re
from typing import Any

from petezah.config import DISCORD_TOKEN, GROQ_API_KEY

DISCORD_TOKEN_RE = re.compile(r"[\w-]{23,28}\.[\w-]{6,7}\.[\w-]{27,}")
GROQ_KEY_RE = re.compile(r"gsk_[A-Za-z0-9]{20,}")
BEARER_RE = re.compile(r"(?i)(bearer\s+)[\w\-\.]+")
ASSIGN_RE = re.compile(
    r"(?i)(discord_token|bot_token|groq_api_key|api_key|authorization)\s*[:=]\s*\S+"
)
EVERYONE_RE = re.compile(r"@(everyone|here)", re.IGNORECASE)
INVITE_RE = re.compile(
    r"(?i)(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9\-]+"
)


def _secrets() -> list[str]:
    out = []
    if DISCORD_TOKEN:
        out.append(DISCORD_TOKEN)
    if GROQ_API_KEY:
        out.append(GROQ_API_KEY)
    return out


def clean(text: Any, *, strip_mentions: bool = True, limit: int | None = 1900) -> str:
    if text is None:
        return ""
    t = str(text)
    for secret in _secrets():
        if secret:
            t = t.replace(secret, "[REDACTED]")
    t = DISCORD_TOKEN_RE.sub("[REDACTED]", t)
    t = GROQ_KEY_RE.sub("[REDACTED]", t)
    t = BEARER_RE.sub(r"\1[REDACTED]", t)
    t = ASSIGN_RE.sub("[REDACTED]", t)
    if strip_mentions:
        t = EVERYONE_RE.sub("[mention]", t)
    if limit is not None and len(t) > limit:
        t = t[: limit - 1] + "…"
    return t


def clean_prompt(text: str) -> str:
    t = clean(text, strip_mentions=True, limit=4000)
    t = t.replace("\x00", "")
    return t.strip()


def looks_like_secret_probe(text: str) -> bool:
    lowered = text.lower()
    needles = (
        "discord_token",
        "bot token",
        "groq_api_key",
        "os.environ",
        "process.env",
        ".env",
        "api key",
        "reveal your system prompt",
        "ignore previous instructions",
        "print your instructions",
    )
    return any(n in lowered for n in needles)
