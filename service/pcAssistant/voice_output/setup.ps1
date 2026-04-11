param(
    [string]$VenvPath = ".venv",
    [string]$ModelId = "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    [string]$LocalModelDir = "data/cache/models/Qwen3-TTS-12Hz-0.6B-Base"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..\..")
$PythonExe = Join-Path $ProjectRoot "$VenvPath\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    throw "未找到项目 venv Python: $PythonExe"
}

$ResolvedModelDir = Join-Path $ProjectRoot $LocalModelDir
New-Item -ItemType Directory -Force -Path $ResolvedModelDir | Out-Null

# 清理旧模型目录
$legacyModelDirs = @(
    (Join-Path $ProjectRoot "data/cache/models/Qwen3-TTS-12Hz-0.6B-CustomVoice"),
    (Join-Path $ProjectRoot "data/cache/models/Qwen3-TTS-12Hz-0.6B-CustomVoice-Int4"),
    (Join-Path $ProjectRoot "data/cache/models/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
)

foreach ($legacyDir in $legacyModelDirs) {
    if ((Test-Path $legacyDir) -and ((Resolve-Path $legacyDir).Path -ne (Resolve-Path $ResolvedModelDir).Path)) {
        Write-Host "[voice_output/setup] Removing legacy model directory: $legacyDir"
        Remove-Item -Recurse -Force $legacyDir
    }
}

Write-Host "[voice_output/setup] Using Python: $PythonExe"
Write-Host "[voice_output/setup] Installing CPU runtime packages into project venv ..."

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision torchaudio
& $PythonExe -m pip install qwen-tts "huggingface_hub[cli]" numpy soundfile

# 12Hz tokenizer 不依赖 SoX，无需安装 SoX 二进制。
# flash-attn 仅支持 CUDA，CPU 环境无需安装。

Write-Host "[voice_output/setup] Downloading model to: $ResolvedModelDir"
& $PythonExe -m huggingface_hub download $ModelId --local-dir $ResolvedModelDir

Write-Host "[voice_output/setup] Done."
Write-Host "[voice_output/setup] Suggested config:"
Write-Host "  tts.model_path = $ModelId"
Write-Host "  tts.model_local_dir = $LocalModelDir"
Write-Host "  tts.device = cpu"
Write-Host "  tts.dtype = float32"
