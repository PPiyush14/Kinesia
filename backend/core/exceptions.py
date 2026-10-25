"""Custom exception hierarchy for the translator system.

All exceptions raised by translator modules inherit from ``TranslatorError``.
Catch specific subclasses for targeted error handling; catch ``TranslatorError``
to handle any translator failure generically.
"""

from __future__ import annotations


class TranslatorError(Exception):
    """Base class for all translator exceptions."""


class InvalidInputError(ValueError, TranslatorError):
    """Expected invalid external input, handled as a client validation error."""


class AudioError(TranslatorError):
    """Raised when audio capture or playback fails."""


class SttError(TranslatorError):
    """Raised when speech-to-text transcription fails."""


class TranslationError(TranslatorError):
    """Raised when LLM translation fails."""


class TtsError(TranslatorError):
    """Raised when text-to-speech synthesis fails."""


class EntityExtractionError(TranslatorError):
    """Raised when NLP entity extraction fails."""


class SummarizationError(TranslatorError):
    """Raised when session summarization fails."""


class StorageError(TranslatorError):
    """Raised when reading from or writing to session storage fails."""


class ConfigurationError(TranslatorError):
    """Raised when required configuration values are missing or invalid."""


class WebhookError(TranslatorError):
    """Raised when a webhook delivery attempt fails."""
