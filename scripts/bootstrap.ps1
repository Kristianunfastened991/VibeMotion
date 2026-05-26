param(
  [switch]$SkipModels,
  [switch]$SkipOllama,
  [switch]$SkipWhisper
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host "[VibeMotion setup] $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
  Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Install([string]$Message) {
  Write-Host "[INSTALL] $Message" -ForegroundColor Yellow
}

function Write-SetupWarning([string]$Message) {
  Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

function Read-EnvFile {
  $envPath = Join-Path $Root ".env"
  $examplePath = Join-Path $Root ".env.example"
  if (-not (Test-Path $envPath) -and (Test-Path $examplePath)) {
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    Write-Ok "Created local .env from .env.example"
  }
  if (-not (Test-Path $envPath)) {
    return
  }
  Get-Content -LiteralPath $envPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
      $parts = $line.Split("=", 2)
      $name = $parts[0].Trim()
      $value = $parts[1].Trim().Trim('"').Trim("'")
      if ($name) {
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
      }
    }
  }
}

function Test-WebEndpoint([string]$Url) {
  try {
    $response = Invoke-WebRequest -UseBasicParsing $Url -TimeoutSec 2
    return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
  } catch {
    return $false
  }
}

function Find-CommandPath([string]$Name, [string[]]$ExtraCandidates = @()) {
  $command = Get-Command $Name -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }
  foreach ($candidate in $ExtraCandidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return $candidate
    }
  }
  return $null
}

function Find-Python {
  $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) {
    return $venvPython
  }

  $candidates = @(
    { py -3.11 -c "import sys; print(sys.executable)" 2>$null },
    { py -3 -c "import sys; print(sys.executable)" 2>$null },
    { python -c "import sys; print(sys.executable)" 2>$null }
  )
  foreach ($candidate in $candidates) {
    try {
      $path = (& $candidate | Select-Object -First 1)
      if ($path -and (Test-Path $path)) {
        return $path
      }
    } catch {}
  }

  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Install "Python 3.11 was not found. Installing Python 3.11 with winget."
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    $path = (py -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
    if ($path -and (Test-Path $path)) {
      return $path
    }
  }

  throw "Python 3.11+ is required. Install Python 3.11, reopen this terminal, and run Launch-VibeMotion.bat again."
}

function Ensure-PythonEnvironment {
  Write-Step "Checking Python and virtual environment."
  $pythonExe = Find-Python
  $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
  if (-not (Test-Path $venvPython)) {
    Write-Install "Creating .venv"
    & $pythonExe -m venv (Join-Path $Root ".venv")
  }
  if (-not (Test-Path $venvPython)) {
    throw "Virtual environment was not created at $venvPython"
  }
  $version = (& $venvPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))")
  Write-Ok "Python $version in .venv"
  return $venvPython
}

function Ensure-PythonDependencies([string]$PythonExe) {
  Write-Step "Checking Python packages."
  & $PythonExe -m pip install --upgrade pip
  & $PythonExe -m pip install -e ".[ltx]"
  & $PythonExe -c "import fastapi, uvicorn, PIL, faster_whisper, torch, transformers, diffusers, accelerate; print('[OK] Python packages import correctly')"
}

function Ensure-CudaTorch([string]$PythonExe) {
  Write-Step "Checking CUDA PyTorch runtime."
  $torchOk = $false
  try {
    $torchOk = (& $PythonExe -c "import torch; print(torch.cuda.is_available() and torch.__version__.startswith('2.7.0+cu128'))" 2>$null) -eq "True"
  } catch {
    $torchOk = $false
  }
  if (-not $torchOk) {
    Write-Install "Installing pinned CUDA PyTorch runtime for LTX."
    & $PythonExe -m pip install --index-url https://download.pytorch.org/whl/cu128 "torch==2.7.0+cu128" "torchaudio==2.7.0+cu128" "torchvision==0.22.0+cu128"
  }
  $cudaSummary = (& $PythonExe -c "import torch; print(f'torch={torch.__version__}; cuda_available={torch.cuda.is_available()}; devices={torch.cuda.device_count()}')")
  if ($cudaSummary -match "cuda_available=True") {
    Write-Ok $cudaSummary
  } else {
    Write-SetupWarning "$cudaSummary. LTX generation requires an NVIDIA GPU, but the rest of VibeMotion can still run."
  }
}

function Ensure-FFmpeg {
  Write-Step "Checking FFmpeg and ffprobe."
  $ffmpeg = Find-CommandPath "ffmpeg"
  $ffprobe = Find-CommandPath "ffprobe"
  if (-not $ffmpeg -or -not $ffprobe) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
      Write-Install "FFmpeg/ffprobe missing. Installing FFmpeg with winget."
      winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
      $ffmpeg = Find-CommandPath "ffmpeg"
      $ffprobe = Find-CommandPath "ffprobe"
    }
  }
  if (-not $ffmpeg -or -not $ffprobe) {
    throw "FFmpeg and ffprobe are required for rendering. Install FFmpeg or reopen the terminal after winget finishes, then run Launch-VibeMotion.bat again."
  }
  Write-Ok "ffmpeg: $ffmpeg"
  Write-Ok "ffprobe: $ffprobe"
}

function Get-EnvValue([string]$Name, [string]$Fallback) {
  $value = [Environment]::GetEnvironmentVariable($Name, "Process")
  if ([string]::IsNullOrWhiteSpace($value)) {
    return $Fallback
  }
  return $value.Trim()
}

function Ensure-OllamaRunning([string]$OllamaExe, [string]$BaseUrl) {
  $tagsUrl = ($BaseUrl.TrimEnd("/") + "/api/tags")
  if (Test-WebEndpoint $tagsUrl) {
    Write-Ok "Ollama API is reachable at $BaseUrl"
    return
  }
  Write-Install "Starting Ollama local server."
  Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Minimized
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 750
    if (Test-WebEndpoint $tagsUrl) {
      Write-Ok "Ollama API is reachable at $BaseUrl"
      return
    }
  }
  throw "Ollama is installed but the local API did not start at $BaseUrl"
}

function Test-OllamaModel([string]$OllamaExe, [string]$Model) {
  $list = & $OllamaExe list 2>$null
  foreach ($line in $list) {
    if ($line -match ("^" + [regex]::Escape($Model) + "\s")) {
      return $true
    }
  }
  return $false
}

function Ensure-OllamaModel([string]$OllamaExe, [string]$Model) {
  if ([string]::IsNullOrWhiteSpace($Model)) {
    return
  }
  if (Test-OllamaModel $OllamaExe $Model) {
    Write-Ok "Ollama model ready: $Model"
    return
  }
  Write-Install "Pulling Ollama model: $Model"
  & $OllamaExe pull $Model
  if (-not (Test-OllamaModel $OllamaExe $Model)) {
    throw "Ollama model was not found after pull: $Model"
  }
  Write-Ok "Ollama model ready: $Model"
}

function Ensure-Ollama {
  if ($SkipOllama) {
    Write-Step "Skipping Ollama check by request."
    return
  }
  Write-Step "Checking Ollama and local LLM models."
  $ollamaCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
    (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
  )
  $ollama = Find-CommandPath "ollama" $ollamaCandidates
  if (-not $ollama) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
      Write-Install "Ollama missing. Installing Ollama with winget."
      winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
      $ollama = Find-CommandPath "ollama" $ollamaCandidates
    }
  }
  if (-not $ollama) {
    throw "Ollama is required for agent planning and vision features. Install Ollama, reopen the terminal, and run Launch-VibeMotion.bat again."
  }
  Write-Ok "ollama: $ollama"
  $baseUrl = Get-EnvValue "OLLAMA_BASE_URL" "http://127.0.0.1:11434"
  if ($baseUrl -notmatch "127\.0\.0\.1|localhost") {
    Write-SetupWarning "OLLAMA_BASE_URL points to a custom endpoint ($baseUrl). Skipping local Ollama model pulls."
    return
  }
  Ensure-OllamaRunning $ollama $baseUrl
  if ((Get-EnvValue "VIBEMOTION_SKIP_OLLAMA_PULL" "") -eq "1") {
    Write-SetupWarning "VIBEMOTION_SKIP_OLLAMA_PULL=1, so Ollama model pulls were skipped."
    return
  }
  $textModel = Get-EnvValue "OLLAMA_MODEL" "qwen3.5:9b"
  $visionModel = Get-EnvValue "OLLAMA_VISION_MODEL" "qwen2.5vl:7b"
  Ensure-OllamaModel $ollama $textModel
  if ($visionModel -ne $textModel) {
    Ensure-OllamaModel $ollama $visionModel
  }
}

function Ensure-WhisperModel([string]$PythonExe) {
  if ($SkipWhisper) {
    Write-Step "Skipping Whisper model cache by request."
    return
  }
  Write-Step "Checking local Whisper STT model cache."
  & $PythonExe -c "import os; from faster_whisper import WhisperModel; model=os.getenv('WHISPER_MODEL','turbo'); print(f'[models] Loading faster-whisper model: {model}'); WhisperModel(model, device='cpu', compute_type=os.getenv('WHISPER_CPU_COMPUTE_TYPE','int8')); print(f'[OK] faster-whisper model ready: {model}')"
}

function Ensure-FigmaPlugin([string]$PythonExe) {
  Write-Step "Registering local Figma plugin."
  & $PythonExe (Join-Path $Root "scripts\register_figma_plugin.py")
  Write-Ok "Figma plugin registered as VibeMotion Export"
}

function Ensure-LtxModels([string]$PythonExe) {
  if ($SkipModels) {
    Write-Step "Skipping LTX model download by request."
    return
  }
  Write-Step "Checking/downloading LTX 2.3 model files."
  & $PythonExe (Join-Path $Root "scripts\download_ltx_models.py") --root $Root
}

Write-Host ""
Write-Host "============================================"
Write-Host " VibeMotion v1.0 first-run setup"
Write-Host "============================================"
Write-Host "This setup checks dependencies and downloads local models when needed."
Write-Host "First launch can take a long time. Later launches are much faster."

Read-EnvFile
$VenvPython = Ensure-PythonEnvironment
Ensure-PythonDependencies $VenvPython
Ensure-CudaTorch $VenvPython
Ensure-FFmpeg
Ensure-Ollama
Ensure-WhisperModel $VenvPython
Ensure-FigmaPlugin $VenvPython
Ensure-LtxModels $VenvPython

Write-Step "Setup complete."
Write-Ok "Run Launch-VibeMotion.bat again any time. Existing dependencies and models will be reused."
