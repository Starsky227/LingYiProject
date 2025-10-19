#TestAI 安装脚本，通过在cmd输入【.\setup.ps1】来运行

# 允许当前进程执行脚本
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

# 检查Python安装
Write-Host "=== Checking Python Installation ===" -ForegroundColor Green
try {
    $pythonVersion = python --version
    Write-Host "Found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python not found! Please install Python and add it to PATH" -ForegroundColor Red
    Pause
    exit 1
}

# 检查虚拟环境
Write-Host "=== Checking Virtual Environment ===" -ForegroundColor Green
if (Test-Path ".venv") {
    Write-Host "Existing virtual environment found, removing..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force ".venv"
    Write-Host "Old virtual environment removed" -ForegroundColor Green
}

Write-Host "Creating new virtual environment..." -ForegroundColor Yellow
python -m venv .venv
if (-not $?) {
    Write-Host "[ERROR] Failed to create virtual environment!" -ForegroundColor Red
    Pause
    exit 1
}
Write-Host "Virtual environment created successfully" -ForegroundColor Green

# 激活环境并安装依赖
Write-Host "=== Activating Environment and Installing Dependencies ===" -ForegroundColor Green
try {
    # 确保在激活环境前退出可能已经激活的环境
    if ($env:VIRTUAL_ENV) {
        deactivate
    }
    
    # 激活新环境
    & .\.venv\Scripts\Activate.ps1
    
    # 验证环境是否正确激活
    if (-not $env:VIRTUAL_ENV) {
        throw "Failed to activate virtual environment"
    }
    
    Write-Host "Updating pip..." -ForegroundColor Yellow
    python -m pip install --upgrade pip
    
    if (Test-Path "requirements.txt") {
        Write-Host "Installing dependencies..." -ForegroundColor Yellow
        pip install -r requirements.txt
        Write-Host "Dependencies installation completed" -ForegroundColor Green
    } else {
        Write-Host "[WARNING] requirements.txt not found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[ERROR] Error installing dependencies: $_" -ForegroundColor Red
    Pause
    exit 1
} finally {
    Write-Host "`n=== Setup Complete! ===" -ForegroundColor Green
    Write-Host "You can now run start.bat to launch the program" -ForegroundColor Cyan
    Pause
}