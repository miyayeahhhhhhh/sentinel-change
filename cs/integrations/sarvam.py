"""Sarvam AI language services. API key is read only from SARVAM_API_KEY."""
from __future__ import annotations
import json
import os
import urllib.request

LANGUAGES = {
    "Hindi": "hi-IN", "Telugu": "te-IN", "Marathi": "mr-IN", "Tamil": "ta-IN",
    "Kannada": "kn-IN", "Bengali": "bn-IN", "Gujarati": "gu-IN", "Malayalam": "ml-IN",
    "Punjabi": "pa-IN", "Odia": "od-IN", "Urdu": "ur-IN", "English": "en-IN",
}


def translate_text(text: str, target_language: str = "te-IN", source_language: str = "en-IN") -> str:
    key = os.getenv("SARVAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Set SARVAM_API_KEY to enable Sarvam translation.")
    if not text.strip():
        return text
    payload = {"input": text, "source_language_code": source_language,
               "target_language_code": target_language, "speaker_gender": "Male",
               "mode": "formal", "model": "mayura:v1", "enable_preprocessing": False}
    req = urllib.request.Request("https://api.sarvam.ai/translate", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "api-subscription-key": key}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Sarvam translation request failed: {type(exc).__name__}: {exc}") from exc
    result = data.get("translated_text")
    if not isinstance(result, str) or not result.strip():
        raise RuntimeError("Sarvam returned no translated_text.")
    return result
