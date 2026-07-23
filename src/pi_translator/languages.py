from __future__ import annotations

INDIAN_LANGUAGE_HINTS: tuple[str, ...] = (
    "hi",
    "bn",
    "ta",
    "te",
    "gu",
    "kn",
    "ml",
    "mr",
    "pa",
)

LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ka": "Georgian",
    "kn": "Kannada",
    "ko": "Korean",
    "ml": "Malayalam",
    "mr": "Marathi",
    "ms": "Malay",
    "nl": "Dutch",
    "no": "Norwegian",
    "pa": "Punjabi",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sv": "Swedish",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tl": "Tagalog",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh": "Chinese",
}

CARTESIA_SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {
        "en",
        "fr",
        "de",
        "es",
        "pt",
        "zh",
        "ja",
        "hi",
        "it",
        "ko",
        "nl",
        "pl",
        "ru",
        "sv",
        "tr",
        "tl",
        "bg",
        "ro",
        "ar",
        "cs",
        "el",
        "fi",
        "hr",
        "ms",
        "sk",
        "da",
        "ta",
        "uk",
        "hu",
        "no",
        "vi",
        "bn",
        "th",
        "he",
        "ka",
        "id",
        "te",
        "gu",
        "kn",
        "ml",
        "mr",
        "pa",
    }
)

LANGUAGE_ALIASES: dict[str, str] = {
    "english": "en",
    "eng": "en",
    "en-in": "en",
    "en-us": "en",
    "en-gb": "en",
    "hindi": "hi",
    "hin": "hi",
    "hi-in": "hi",
    "bengali": "bn",
    "bangla": "bn",
    "ben": "bn",
    "bn-in": "bn",
    "tamil": "ta",
    "tam": "ta",
    "ta-in": "ta",
    "telugu": "te",
    "tel": "te",
    "te-in": "te",
    "gujarati": "gu",
    "guj": "gu",
    "gu-in": "gu",
    "kannada": "kn",
    "kan": "kn",
    "kn-in": "kn",
    "malayalam": "ml",
    "mal": "ml",
    "ml-in": "ml",
    "marathi": "mr",
    "mar": "mr",
    "mr-in": "mr",
    "punjabi": "pa",
    "pan": "pa",
    "pa-in": "pa",
}

GOOGLE_TTS_LANGUAGE_CODES: dict[str, str] = {
    "ar": "ar-XA",
    "bg": "bg-BG",
    "bn": "bn-IN",
    "cs": "cs-CZ",
    "da": "da-DK",
    "de": "de-DE",
    "el": "el-GR",
    "en": "en-IN",
    "es": "es-ES",
    "fi": "fi-FI",
    "fr": "fr-FR",
    "gu": "gu-IN",
    "he": "he-IL",
    "hi": "hi-IN",
    "hr": "hr-HR",
    "hu": "hu-HU",
    "id": "id-ID",
    "it": "it-IT",
    "ja": "ja-JP",
    "kn": "kn-IN",
    "ko": "ko-KR",
    "ml": "ml-IN",
    "mr": "mr-IN",
    "ms": "ms-MY",
    "nl": "nl-NL",
    "no": "nb-NO",
    "pa": "pa-IN",
    "pl": "pl-PL",
    "pt": "pt-BR",
    "ro": "ro-RO",
    "ru": "ru-RU",
    "sk": "sk-SK",
    "sv": "sv-SE",
    "ta": "ta-IN",
    "te": "te-IN",
    "th": "th-TH",
    "tr": "tr-TR",
    "uk": "uk-UA",
    "vi": "vi-VN",
    "zh": "cmn-CN",
}


def default_indian_language_hints() -> list[str]:
    return list(INDIAN_LANGUAGE_HINTS)


def normalize_cartesia_language(value: str | None) -> str | None:
    if not value:
        return None

    normalized = value.strip().lower().replace("_", "-")
    if not normalized:
        return None
    if normalized in CARTESIA_SUPPORTED_LANGUAGES:
        return normalized

    alias = LANGUAGE_ALIASES.get(normalized)
    if alias is not None:
        return alias

    primary = normalized.split("-", 1)[0]
    if primary in CARTESIA_SUPPORTED_LANGUAGES:
        return primary

    return LANGUAGE_ALIASES.get(primary)


def normalize_language_code(value: str | None) -> str | None:
    if not value:
        return None

    normalized = value.strip().lower().replace("_", "-")
    if not normalized:
        return None

    alias = LANGUAGE_ALIASES.get(normalized)
    if alias is not None:
        return alias

    primary = normalized.split("-", 1)[0]
    alias = LANGUAGE_ALIASES.get(primary)
    if alias is not None:
        return alias

    return primary


def normalize_google_tts_language(value: str | None) -> str | None:
    if not value:
        return None

    normalized = value.strip().replace("_", "-")
    if not normalized:
        return None

    if "-" in normalized:
        parts = normalized.split("-", 1)
        if len(parts[0]) == 2 and parts[1]:
            region = parts[1].upper() if len(parts[1]) == 2 else parts[1]
            return f"{parts[0].lower()}-{region}"

    primary = normalize_language_code(normalized)
    if primary is None:
        return None
    return GOOGLE_TTS_LANGUAGE_CODES.get(primary, primary)


def default_google_tts_voice_name(language_code: str | None) -> str:
    resolved_language = normalize_google_tts_language(language_code) or "en-IN"
    primary = normalize_language_code(resolved_language)
    if primary == "hi":
        return "hi-IN-Standard-E"
    if primary == "te":
        return "te-IN-Standard-A"
    return f"{resolved_language}-Wavenet-A"


def language_display_name(value: str | None) -> str:
    normalized = normalize_language_code(value)
    if normalized is None:
        return "Unknown language"
    return LANGUAGE_DISPLAY_NAMES.get(normalized, normalized.upper())


def language_label(value: str | None) -> str:
    normalized = normalize_language_code(value)
    if normalized is None:
        return "unknown language"
    return f"{language_display_name(normalized)} ({normalized})"
