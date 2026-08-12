import anthropic

from app.config import get_settings
from app.models import ReplyLabel

MAX_TOKENS = 16

_PROMPT = (
    "Classify this reply to a cold internship-outreach email into exactly one "
    "label:\n"
    "  interested — the recipient is open, wants to talk, or asks for a call or "
    "more info.\n"
    "  rejection — a clear no: not hiring, not a fit, or a decline.\n"
    "  auto_reply — an automated out-of-office / autoresponder / no-reply notice.\n"
    "  other — anything else.\n"
    "Reply with ONLY the single label word.\n\n"
    "EMAIL:\n{text}"
)

# Ordered substring probes — tolerant of the model's punctuation/casing
# ("auto_reply" vs "auto-reply" vs "auto reply").
_KEYWORDS = (
    ("interest", ReplyLabel.INTERESTED),
    ("reject", ReplyLabel.REJECTION),
    ("auto", ReplyLabel.AUTO_REPLY),
)


def classify_reply(client, text: str, *, model: str | None = None) -> ReplyLabel:
    model = model or get_settings().anthropic_model
    prompt = _PROMPT.format(text=(text or "")[:4000])
    try:
        resp = client.messages.create(
            model=model, max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip().lower()
    except (anthropic.AnthropicError, ValueError):
        return ReplyLabel.OTHER
    for needle, label in _KEYWORDS:
        if needle in raw:
            return label
    return ReplyLabel.OTHER
