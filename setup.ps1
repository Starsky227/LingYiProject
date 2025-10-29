#LingYi 智能安装脚本
# 使用方法：
#   .\setup.ps1         - 智能增量模式（推荐）
#   .\setup.ps1 -Force  - 强制重新创建环境

param(
    [switch]$Force,
    [switch]$Help
)

# Display help information
if ($Help) {
    Write-Host "LingYi Project Setup Script" -ForegroundColor Green
    Write-Host "Usage:" -ForegroundColor Yellow
    Write-Host "  .\setup.ps1         Smart incremental mode (preserve environment, install only missing dependencies)" -ForegroundColor Cyan
    Write-Host "  .\setup.ps1 -Force  Force recreate environment mode" -ForegroundColor Cyan
    Write-Host "  .\setup.ps1 -Help   Display this help information" -ForegroundColor Cyan
    exit 0
}

# 允许当前进程执行脚本
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

Write-Host "=== LingYi Project Smart Setup ===" -ForegroundColor Green
if ($Force) {
    Write-Host "Running mode: Force rebuild" -ForegroundColor Yellow
} else {
    Write-Host "Running mode: Smart incremental update" -ForegroundColor Green
}

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

# Check virtual environment
Write-Host "=== Checking Virtual Environment ===" -ForegroundColor Green
$venvExists = Test-Path ".venv"
$createNewVenv = $false

if ($venvExists) {
    Write-Host "Found existing virtual environment" -ForegroundColor Yellow
    
    if ($Force) {
        Write-Host "Force mode: Will recreate virtual environment" -ForegroundColor Yellow
        Remove-Item -Recurse -Force ".venv"
        Write-Host "Old environment removed" -ForegroundColor Green
        $createNewVenv = $true
    } else {
        # Check virtual environment integrity
        if ((Test-Path ".venv\Scripts\python.exe") -and (Test-Path ".venv\Scripts\pip.exe")) {
            Write-Host "Virtual environment is intact, will reuse existing environment" -ForegroundColor Green
            $createNewVenv = $false
        } else {
            Write-Host "Virtual environment is corrupted, will recreate" -ForegroundColor Yellow
            Remove-Item -Recurse -Force ".venv"
            $createNewVenv = $true
        }
    }
} else {
    Write-Host "No virtual environment found, will create new one" -ForegroundColor Yellow
    $createNewVenv = $true
}

if ($createNewVenv) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
    if (-not $?) {
        Write-Host "[ERROR] Failed to create virtual environment!" -ForegroundColor Red
        Pause
        exit 1
    }
    Write-Host "Virtual environment created successfully" -ForegroundColor Green
}

# Activate environment and smart install dependencies
Write-Host "=== Activating Environment and Checking Dependencies ===" -ForegroundColor Green
try {
    # Ensure to exit any previously activated environment
    if ($env:VIRTUAL_ENV) {
        deactivate
    }
    
    # Activate environment
    & .\.venv\Scripts\Activate.ps1
    
    # Verify environment is correctly activated
    if (-not $env:VIRTUAL_ENV) {
        throw "Failed to activate virtual environment"
    }
    
    Write-Host "Environment activated: $env:VIRTUAL_ENV" -ForegroundColor Green
    
    # Update pip
    Write-Host "Checking and updating pip..." -ForegroundColor Yellow
    python -m pip install --upgrade pip --quiet
    
    if (Test-Path "requirements.txt") {
        Write-Host "Analyzing dependency file..." -ForegroundColor Yellow
        
        # Read requirements.txt
        $requiredPackages = Get-Content "requirements.txt" | Where-Object { 
            $_ -match "^[a-zA-Z]" -and $_ -notmatch "^#" -and $_.Trim() -ne ""
        }
        Write-Host "Required packages count: $($requiredPackages.Count)" -ForegroundColor Cyan
        
        if (-not $createNewVenv) {
            # Get currently installed packages
            Write-Host "Checking installed packages..." -ForegroundColor Yellow
            $installedPackages = @{}
            $pipList = pip list --format=json | ConvertFrom-Json
            foreach ($pkg in $pipList) {
                $installedPackages[$pkg.name.ToLower()] = $pkg.version
            }
            Write-Host "Installed packages count: $($installedPackages.Count)" -ForegroundColor Cyan
            
            # Analyze missing and packages that need updates
            $missingPackages = @()
            $needsUpdate = @()
            
            foreach ($reqPkg in $requiredPackages) {
                $pkgName = ($reqPkg -split '[>=<!=~]')[0].Trim().ToLower()
                
                if (-not $installedPackages.ContainsKey($pkgName)) {
                    $missingPackages += $reqPkg
                    Write-Host "  Missing: $pkgName" -ForegroundColor Red
                } else {
                    Write-Host "  Installed: $pkgName ($($installedPackages[$pkgName]))" -ForegroundColor Green
                }
            }
            
            if ($missingPackages.Count -gt 0) {
                Write-Host "`nFound $($missingPackages.Count) missing packages, installing..." -ForegroundColor Yellow
                
                # Create temporary requirements file containing only missing packages
                $tempReqFile = "temp_requirements.txt"
                $missingPackages | Out-File -FilePath $tempReqFile -Encoding UTF8
                
                pip install -r $tempReqFile
                Remove-Item $tempReqFile
                
                Write-Host "Missing packages installation completed" -ForegroundColor Green
            } else {
                Write-Host "All required packages are already installed" -ForegroundColor Green
            }
            
            # Check if any packages need updates
            Write-Host "`nChecking package updates..." -ForegroundColor Yellow
            pip install -r requirements.txt --upgrade-strategy only-if-needed --quiet
            Write-Host "Package update check completed" -ForegroundColor Green
            
        } else {
            # New environment, install all dependencies
            Write-Host "New environment, installing all dependencies..." -ForegroundColor Yellow
            pip install -r requirements.txt
            Write-Host "All dependencies installation completed" -ForegroundColor Green
        }
        
        # Display final installed packages list
        Write-Host "`n=== Final Installed Packages ===" -ForegroundColor Cyan
        pip list --format=columns
        
    } else {
        Write-Host "[WARNING] requirements.txt file not found" -ForegroundColor Yellow
    }
    
    # Environment health check
    Write-Host "`n=== Environment Health Check ===" -ForegroundColor Green
    Write-Host "Python path: $(where.exe python)" -ForegroundColor Cyan
    Write-Host "Python version: $(python --version)" -ForegroundColor Cyan
    Write-Host "Pip version: $(pip --version)" -ForegroundColor Cyan
    
    Write-Host "`n=== Setup Complete! ===" -ForegroundColor Green
    Write-Host "Virtual environment located at: $env:VIRTUAL_ENV" -ForegroundColor Cyan
    Write-Host "You can now run start.bat to launch the program" -ForegroundColor Cyan
    
} catch {
    $errorMessage = $_.Exception.Message
    Write-Host "[ERROR] Error during installation: $errorMessage" -ForegroundColor Red
    Write-Host "Recommend using .\setup.ps1 -Force to force rebuild environment" -ForegroundColor Yellow
    Pause
    exit 1
}

Write-Host "`nTip: Use .\setup.ps1 -Force to force rebuild environment" -ForegroundColor Yellow
Pause