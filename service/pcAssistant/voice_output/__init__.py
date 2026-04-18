from system.config import config as _cfg

_backend = getattr(_cfg.tts, "backend", "qwen")
if _backend == "stepfun":
    from .stepfun_tts_service import StepFunTTSService as TTSOutputService
else:
    from .qwen_tts_service import QwenTTSOutputService as TTSOutputService

# 保留原始导出以兼容直接引用
from .qwen_tts_service import QwenTTSOutputService
from .stepfun_tts_service import StepFunTTSService

__all__ = ["TTSOutputService", "QwenTTSOutputService", "StepFunTTSService"]
