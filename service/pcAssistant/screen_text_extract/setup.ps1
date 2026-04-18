# PaddleOCR 模型安装脚本
# 使用方法：在项目虚拟环境激活后运行
#   cd service\pcAssistant\screen_text_extract
#   .\setup.ps1

param(
    [switch]$Help
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

if ($Help) {
    Write-Host "PaddleOCR Setup Script" -ForegroundColor Green
    Write-Host "Usage:" -ForegroundColor Yellow
    Write-Host "  .\setup.ps1       Install PaddleOCR dependencies and pre-download models" -ForegroundColor Cyan
    Write-Host "  .\setup.ps1 -Help Display this help information" -ForegroundColor Cyan
    exit 0
}

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

Write-Host "=== PaddleOCR Setup ===" -ForegroundColor Green

# ------------------------------------------------------------------
# 1. 检查虚拟环境
# ------------------------------------------------------------------
if (-not $env:VIRTUAL_ENV) {
    # 尝试自动激活项目根目录的虚拟环境
    $projectRoot = Resolve-Path "$PSScriptRoot\..\..\..\"
    $activateScript = Join-Path $projectRoot ".venv\Scripts\Activate.ps1"
    if (Test-Path $activateScript) {
        Write-Host "Activating virtual environment..." -ForegroundColor Yellow
        & $activateScript
    } else {
        Write-Host "[ERROR] Virtual environment not found. Please activate the project venv first." -ForegroundColor Red
        Pause
        exit 1
    }
}
Write-Host "Virtual environment: $env:VIRTUAL_ENV" -ForegroundColor Green

# ------------------------------------------------------------------
# 2. 安装 PaddlePaddle + PaddleOCR
# ------------------------------------------------------------------
Write-Host "`n=== Installing PaddlePaddle & PaddleOCR ===" -ForegroundColor Green

# 安装 paddlepaddle（CPU 版本，如需 GPU 版请手动替换）
Write-Host "Installing paddlepaddle (CPU)..." -ForegroundColor Yellow
pip install paddlepaddle>=2.5.0 --quiet
if (-not $?) {
    Write-Host "[ERROR] Failed to install paddlepaddle" -ForegroundColor Red
    Pause
    exit 1
}
Write-Host "paddlepaddle installed." -ForegroundColor Green

# 安装 paddleocr
Write-Host "Installing paddleocr..." -ForegroundColor Yellow
pip install "paddleocr>=2.7.0" --quiet
if (-not $?) {
    Write-Host "[ERROR] Failed to install paddleocr" -ForegroundColor Red
    Pause
    exit 1
}
Write-Host "paddleocr installed." -ForegroundColor Green

# ------------------------------------------------------------------
# 3. 预下载 PaddleOCR 模型（中文 PP-OCRv4）
# ------------------------------------------------------------------
Write-Host "`n=== Pre-downloading PaddleOCR Models (Chinese) ===" -ForegroundColor Green
Write-Host "This will download detection, recognition and textline orientation models..." -ForegroundColor Yellow

# PaddlePaddle 3.x Windows OneDNN 兼容性修复
$env:FLAGS_enable_pir_api = "0"
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"

$downloadScript = @"
import os, sys
os.environ['FLAGS_enable_pir_api'] = '0'
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
try:
    from paddleocr import PaddleOCR
    print('[INFO] Initializing PaddleOCR (this triggers model download)...')
    ocr = PaddleOCR(use_textline_orientation=True, lang='ch', enable_mkldnn=False)
    print('[OK] Models downloaded successfully.')

    # Quick sanity check with a tiny blank image
    import numpy as np
    blank = np.zeros((64, 200, 3), dtype=np.uint8) + 255  # white image
    result = ocr.predict(blank)
    print('[OK] PaddleOCR inference test passed.')
except Exception as e:
    print(f'[ERROR] {e}', file=sys.stderr)
    sys.exit(1)
"@

python -c $downloadScript
if (-not $?) {
    Write-Host "[ERROR] Model download or verification failed." -ForegroundColor Red
    Pause
    exit 1
}

# ------------------------------------------------------------------
# 4. 完成
# ------------------------------------------------------------------
Write-Host "`n=== PaddleOCR Setup Complete ===" -ForegroundColor Green
Write-Host "Installed packages:" -ForegroundColor Cyan
pip show paddlepaddle paddleocr 2>$null | Select-String "^(Name|Version):" | ForEach-Object { Write-Host "  $_" -ForegroundColor Cyan }

Write-Host "`nYou can now run screen_text_extractor_test.py to verify:" -ForegroundColor Yellow
Write-Host "  python screen_text_extractor_test.py" -ForegroundColor Cyan
Pause
