"""
StepFun 音色复刻工具

流程：
  1. 上传 WAV 文件到 StepFun（POST /v1/files, purpose=storage）→ 获取 file_id
  2. 创建复刻音色（POST /v1/audio/voices）→ 获取 voice_id

获取到的 voice_id 可填入 config.json 的 tts.tts_voice_id 字段，
供 StepFunTTSService 使用。

用法：
  python create_stepfun_voice.py                          # 使用默认 LingYiVoice.wav
  python create_stepfun_voice.py --wav path/to/audio.wav  # 指定音频文件
  python create_stepfun_voice.py --api-key YOUR_KEY       # 指定 API Key（否则读 config.json）

要求：音频时长 5~10 秒，格式 wav/mp3。
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("需要 requests 库，请运行: pip install requests")
    sys.exit(1)

# ---- 项目根目录 & 默认路径 ----
_THIS_DIR = Path(__file__).resolve().parent
_VOICE_OUTPUT_DIR = _THIS_DIR.parent  # voice_output/
_PROJECT_ROOT = _VOICE_OUTPUT_DIR.parent.parent.parent
_DEFAULT_WAV = _VOICE_OUTPUT_DIR / "LingYiVoice.wav"

_STEPFUN_FILES_URL = "https://api.stepfun.com/v1/files"
_STEPFUN_VOICES_URL = "https://api.stepfun.com/v1/audio/voices"
_MODEL = "step-tts-2"

# 声源对应的文本（用于复刻质量优化，建议与音频内容一致）
_DEFAULT_TEXT = (
    "你好，我是铃依。我喜欢和人聊天，也很爱观察生活里那些细小但真实的瞬间。"
    "平时说话可能会有一点慢热，但熟悉之后，会很愿意陪大家聊很多东西——"
    "比如日常、心情、故事，还有那些说不清却很在意的事。"
)


def _load_api_key_from_config() -> str:
    """从项目 config.json 读取 StepFun API Key。"""
    config_path = _PROJECT_ROOT / "config.json"
    if not config_path.exists():
        return ""
    try:
        import json5  # type: ignore[import-not-found]
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json5.load(f)
    except Exception:
        # 回退到标准 json（不支持注释）
        with open(config_path, "r", encoding="utf-8") as f:
            text = f.read()
        # 简单去除行注释
        import re
        text = re.sub(r'//.*', '', text)
        cfg = json.loads(text)
    return cfg.get("tts", {}).get("tts_api_key", "")


def upload_file(api_key: str, wav_path: Path) -> str:
    """上传音频文件到 StepFun，返回 file_id。"""
    print(f"[1/2] 上传文件: {wav_path.name} ...")
    headers = {"Authorization": f"Bearer {api_key}"}
    with open(wav_path, "rb") as f:
        resp = requests.post(
            _STEPFUN_FILES_URL,
            headers=headers,
            files={"file": (wav_path.name, f, "audio/wav")},
            data={"purpose": "storage"},
            timeout=60,
        )
    if resp.status_code != 200:
        print(f"  上传失败 (HTTP {resp.status_code}): {resp.text[:300]}")
        sys.exit(1)

    result = resp.json()
    file_id = result.get("id", "")
    print(f"  上传成功! file_id = {file_id}")
    return file_id


def create_voice(api_key: str, file_id: str, text: str) -> str:
    """调用复刻音色 API，返回 voice_id。"""
    print(f"[2/2] 创建复刻音色 ...")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": _MODEL,
        "file_id": file_id,
        "text": text,
    }
    resp = requests.post(
        _STEPFUN_VOICES_URL,
        headers=headers,
        json=body,
        timeout=60,
    )
    if resp.status_code != 200:
        print(f"  复刻失败 (HTTP {resp.status_code}): {resp.text[:300]}")
        sys.exit(1)

    result = resp.json()
    voice_id = result.get("id", "")
    duplicated = result.get("duplicated", False)

    if duplicated:
        print(f"  该音频已创建过音色，返回已有 voice_id")
    print(f"  复刻成功! voice_id = {voice_id}")
    return voice_id


def main():
    parser = argparse.ArgumentParser(description="StepFun 音色复刻工具")
    parser.add_argument(
        "--wav",
        type=str,
        default=str(_DEFAULT_WAV),
        help=f"音频文件路径（默认: {_DEFAULT_WAV.name}）",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default="",
        help="StepFun API Key（不指定则从 config.json 读取）",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=_DEFAULT_TEXT,
        help="音频对应的文本内容（可选，用于提升复刻质量）",
    )
    args = parser.parse_args()

    # 获取 API Key
    api_key = args.api_key or _load_api_key_from_config()
    if not api_key:
        print("错误: 未提供 API Key，请通过 --api-key 参数或 config.json 中 tts.tts_api_key 配置")
        sys.exit(1)

    # 检查音频文件
    wav_path = Path(args.wav).resolve()
    if not wav_path.exists():
        print(f"错误: 音频文件不存在: {wav_path}")
        sys.exit(1)

    print(f"=== StepFun 音色复刻 ===")
    print(f"音频文件: {wav_path}")
    print(f"模型: {_MODEL}")
    print()

    # Step 1: 上传文件
    file_id = upload_file(api_key, wav_path)

    # Step 2: 创建复刻音色
    voice_id = create_voice(api_key, file_id, args.text)

    print()
    print(f"========================================")
    print(f"  voice_id: {voice_id}")
    print(f"========================================")
    print()
    print(f"请将此 voice_id 填入 config.json 的 tts.tts_voice_id 字段：")
    print(f'  "tts_voice_id": "{voice_id}"')


if __name__ == "__main__":
    main()
