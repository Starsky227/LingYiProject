"""PC Assistant voice input service with local VAD segmentation."""

from .vad_voice_input import VoiceInputVDLService, _VadConfig as VadConfig

__all__ = ["VoiceInputVDLService", "VadConfig"]
