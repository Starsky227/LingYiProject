import inspect
import logging
import wave
from pathlib import Path
from typing import Any, Optional

import numpy as np

try:
	from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
	Qwen3TTSModel = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)

instruct = "年轻女性声音，柔和、轻声、略带气声，语气温柔自然，带一点内向和细腻情绪，语速适中，语气轻快干净，有亲近感"
prompt = "重要的不是他们变成什么样子，而是你仍然对他们抱有同一份真实的感情。"


class VoiceDesignService:
	"""VoiceDesign helper for local Qwen3-TTS model.

	默认生源文件应放在当前目录，默认文件名: source_voice.wav。
	"""

	def __init__(
		self,
		model_id: str = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
		model_local_dir: str = "data/cache/models/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
		device: str = "cpu",
		dtype: Any = None,
	):
		self._model_id = model_id
		self._model_local_dir = Path(model_local_dir)
		self._device = device
		self._dtype = dtype
		self._model = None

		self._base_dir = Path(__file__).resolve().parent
		self._source_voice = self._base_dir / "source_voice.wav"

	def _load_model(self) -> None:
		if self._model is not None:
			return
		if Qwen3TTSModel is None:
			raise RuntimeError("qwen-tts 未安装，无法使用 VoiceDesign")

		source = str(self._model_local_dir) if self._model_local_dir.exists() and any(self._model_local_dir.iterdir()) else self._model_id
		logger.info(f"加载模型: {source}")
		self._model = Qwen3TTSModel.from_pretrained(
			source,
			device_map=self._device,
			dtype=self._dtype,
			local_files_only=False,
		)

	@staticmethod
	def _write_wav(path: Path, wav: np.ndarray, sample_rate: int) -> None:
		audio = np.clip(wav, -1.0, 1.0)
		audio_i16 = (audio * 32767.0).astype(np.int16)
		with wave.open(str(path), "wb") as wf:
			wf.setnchannels(1)
			wf.setsampwidth(2)
			wf.setframerate(sample_rate)
			wf.writeframes(audio_i16.tobytes())

	def _call_voice_design(
		self,
		prompt: str,
		instruct: str = "",
	):
		"""调用 generate_voice_design: 根据文字描述(instruct)设计全新声音。"""
		method = getattr(self._model, "generate_voice_design", None)
		if method is None or not callable(method):
			raise RuntimeError("当前 qwen-tts 版本未找到 generate_voice_design 方法")

		return method(
			text=prompt,
			instruct=instruct,
			language="Chinese",
			non_streaming_mode=True,
		)

	def design_voice(
		self,
		prompt: str,
		instruct: str = "",
		output_filename: Optional[str] = None,
	) -> Path:
		"""根据 prompt 与 instruct 生成 VoiceDesign 音频并返回输出路径。

		Args:
			prompt: 要合成的文本内容。
			instruct: 声音风格/角色描述，如 "年轻女性声音，温柔自然"。
			output_filename: 输出文件名，默认 voice_output.wav。
		"""
		clean_prompt = (prompt or "").strip()
		if not clean_prompt:
			raise ValueError("prompt 不能为空")

		self._load_model()
		result = self._call_voice_design(clean_prompt, instruct=instruct)

		if output_filename:
			output_path = self._base_dir / output_filename
		else:
			output_path = self._base_dir / "voice_output.wav"

		wavs, sample_rate = result
		if not wavs:
			raise RuntimeError("VoiceDesign 生成结果为空")

		wav = np.asarray(wavs[0], dtype=np.float32)
		self._write_wav(output_path, wav, int(sample_rate))
		logger.info(f"VoiceDesign 输出文件: {output_path}")
		return output_path


def main() -> None:
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
	service = VoiceDesignService()
	output = service.design_voice(
		prompt=prompt,
		instruct=instruct,
		output_filename="voice_output.wav",
	)
	print(f"完成，输出文件: {output}")


if __name__ == "__main__":
	main()
