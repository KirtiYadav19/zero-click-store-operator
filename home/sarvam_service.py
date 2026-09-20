import base64
import logging
import requests
from decouple import config

logger = logging.getLogger(__name__)

SARVAM_API_KEY = config('SARVAM_API_KEY', default='')
STT_URL = "https://api.sarvam.ai/speech-to-text"
TTS_URL = "https://api.sarvam.ai/text-to-speech"

def speech_to_text(audio_file_bytes, filename="audio.wav"):
    """
    Convert browser recorded audio to text using Sarvam Speech-to-Text API.
    Supports Hindi, Hinglish, and English code-mixed speech.
    """
    if not SARVAM_API_KEY:
        logger.warning("SARVAM_API_KEY not found in .env.")
        return None, "SARVAM_API_KEY is not configured in .env."

    try:
        headers = {
            "api-subscription-key": SARVAM_API_KEY
        }
        files = {
            "file": (filename, audio_file_bytes, "audio/wav")
        }

        # Try updated Sarvam STT model (saaras:v3 or saarika:v2.5)
        for model_name in ["saaras:v3", "saarika:v2.5", "saaras:v4"]:
            data = {
                "model": model_name,
                "language_code": "hi-IN",
                "with_timestamps": "false"
            }

            response = requests.post(STT_URL, headers=headers, files=files, data=data, timeout=10)
            if response.status_code == 200:
                res_json = response.json()
                transcript = res_json.get("transcript", "").strip()
                return transcript, None
            elif "Input should be" in response.text or "deprecated" in response.text:
                continue
            else:
                logger.error(f"Sarvam STT Error ({response.status_code}): {response.text}")

        return None, "Sarvam STT model unavailable."

    except Exception as e:
        logger.exception(f"Exception during Sarvam STT: {e}")
        return None, str(e)


def text_to_speech(text, target_language="hi-IN"):
    """
    Convert text response to speech using Sarvam Bulbul v3 Text-to-Speech API.
    Returns base64 data URI for direct browser HTML5 Audio playback.
    """
    if not SARVAM_API_KEY or not text:
        return None

    try:
        headers = {
            "api-subscription-key": SARVAM_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "inputs": [text],
            "target_language_code": target_language if target_language else "hi-IN",
            "speaker": "meera",
            "pitch": 0,
            "pace": 1.0,
            "loudness": 1.5,
            "speech_sample_rate": 8000,
            "enable_preprocessing": True,
            "model": "bulbul:v3"
        }

        response = requests.post(TTS_URL, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            res_json = response.json()
            audios = res_json.get("audios", [])
            if audios and len(audios) > 0:
                base64_audio = audios[0]
                return f"data:audio/wav;base64,{base64_audio}"
        else:
            logger.warning(f"Sarvam TTS Error ({response.status_code}): {response.text}")
            return None

    except Exception as e:
        logger.warning(f"Exception during Sarvam TTS: {e}")
        return None
