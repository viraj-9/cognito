"""Safe, Unicode-aware multilingual text preprocessing."""
import re
import unicodedata


LANGUAGE_NAMES = {"en": "English", "es": "Español", "fr": "Français", "hi": "हिन्दी", "hinglish": "Hinglish", "mr": "मराठी", "unknown": "Unknown"}

_HINGLISH_KEYWORDS = {
    "namaste", "namaskar", "kaise", "kaisa", "kaisi", "kya", "kyun", "kyon", "kaha", "kahan", "kab",
    "theek", "thik", "shukriya", "dhanyawad", "dhanyavad", "batao", "bolo", "bataiye", "boliye",
    "aap", "tum", "mera", "meri", "mere", "mujhe", "apka", "aapka", "aapki", "aapke",
    "hum", "hamara", "accha", "achha", "bahut", "thoda", "karo", "kare", "karna", "baat", "shiksha"
}


def clean_text(value: str, max_chars: int = 4000) -> str:
    if not isinstance(value, str):
        raise ValueError("Text must be a string.")
    value = unicodedata.normalize("NFKC", value)
    value = "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch) not in {"Cc", "Cf", "Cs"})
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > max_chars:
        raise ValueError(f"Text must be at most {max_chars} characters.")
    return value


def tokenize(value: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)?", clean_text(value).casefold(), flags=re.UNICODE)


def char_ngrams(value: str, n: int = 3) -> list[str]:
    text = f" {clean_text(value).casefold()} "
    if len(text) < n:
        return [text] if text.strip() else []
    return [text[i:i+n] for i in range(len(text)-n+1) if not text[i:i+n].isspace()]


def script_language(value: str) -> str | None:
    for char in value:
        name = unicodedata.name(char, "")
        if "DEVANAGARI" in name:
            return "hi"
    tokens = set(tokenize(value))
    common = tokens.intersection(_HINGLISH_KEYWORDS)
    if len(common) >= 2 or any(k in common for k in ("namaste", "namaskar", "dhanyawad", "dhanyavad")):
        return "hinglish"
    return None
