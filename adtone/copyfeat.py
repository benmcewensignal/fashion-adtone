"""Surface features of ad copy.

Ad copy is the advertiser's text, so it follows the T1 rule: features and a hash are
kept, the text is not. Anything needing the words themselves (a copy-register rubric)
has to run at collection time while the ad is still in the repository.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_WS = re.compile(r"\s+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")
_PRICE = re.compile(r"(?:[€£$¥]\s?\d)|(?:\d[\d.,\s]*\s?(?:€|£|\$|EUR|GBP|USD|CHF|SEK|DKK|PLN|CZK)\b)", re.I)
_URL = re.compile(r"https?://|www\.", re.I)
_HASHTAG = re.compile(r"(?<!\w)#\w+")
_MENTION = re.compile(r"(?<!\w)@\w+")
_TEMPLATE = re.compile(r"\{\{[^}]*\}\}")   # dynamic-creative placeholders such as {{product.name}}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return _WS.sub(" ", text).strip().lower()


def text_sha(texts: list[str]) -> str | None:
    parts = sorted({normalise(t) for t in texts if t and t.strip()})
    if not parts:
        return None
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def features(texts: list[str] | None) -> dict:
    texts = [t for t in (texts or []) if t and t.strip()]
    uniq = list(dict.fromkeys(texts))
    joined = "\n".join(uniq)
    letters = [c for c in joined if c.isalpha()]
    upper = sum(1 for c in letters if c.isupper())
    words = re.findall(r"\w+", joined)
    return {
        "n_variants": len(uniq),
        "n_chars": len(joined),
        "n_words": len(words),
        "n_lines": joined.count("\n") + 1 if joined else 0,
        "n_emoji": len(_EMOJI.findall(joined)),
        "n_excl": joined.count("!"),
        "n_question": joined.count("?"),
        "n_hashtags": len(_HASHTAG.findall(joined)),
        "n_mentions": len(_MENTION.findall(joined)),
        "upper_ratio": round(upper / len(letters), 3) if letters else 0.0,
        "has_price": bool(_PRICE.search(joined)),
        "has_url": bool(_URL.search(joined)),
        "is_template": bool(_TEMPLATE.search(joined)),
        "sha": text_sha(uniq),
    }
