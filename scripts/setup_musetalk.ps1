param(
    [string]$Python310 = ".tools\python310\python.exe"
)

$ErrorActionPreference = "Stop"
# Some NVIDIA installations define a dead extra pip index globally. MuseTalk's
# packages come from PyPI, while the CUDA wheels use an explicit index below.
Remove-Item Env:PIP_EXTRA_INDEX_URL -ErrorAction SilentlyContinue
$env:PIP_CONFIG_FILE = "NUL"
$env:PIP_RETRIES = "0"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonCandidate = if ([System.IO.Path]::IsPathRooted($Python310)) {
    $Python310
} else {
    Join-Path $ProjectRoot $Python310
}
$PythonPath = [System.IO.Path]::GetFullPath($PythonCandidate)
$MuseTalkRoot = Join-Path $ProjectRoot ".tools\MuseTalk"
$VenvPython = Join-Path $ProjectRoot ".venv-musetalk\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python 3.10 was not found at $PythonPath. Install a local Python 3.10 runtime and pass -Python310 with its path."
}

$Version = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $Version.Trim() -ne "3.10") {
    throw "MuseTalk setup requires Python 3.10; found $Version at $PythonPath"
}

if (-not (Test-Path -LiteralPath $MuseTalkRoot -PathType Container)) {
    git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git $MuseTalkRoot
    if ($LASTEXITCODE -ne 0) { throw "Unable to clone MuseTalk" }
}

if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    & $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath (Join-Path $ProjectRoot ".venv-musetalk") -Recurse -Force
    }
}
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    & $PythonPath -m venv (Join-Path $ProjectRoot ".venv-musetalk")
    if ($LASTEXITCODE -ne 0) { throw "Unable to create .venv-musetalk" }
}

& $VenvPython -m pip install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) { throw "Unable to prepare pip" }
& $VenvPython -m pip install --no-deps torch==2.0.1 torchvision==0.15.2 torchaudio==2.0.2 --index-url https://download.pytorch.org/whl/cu118
if ($LASTEXITCODE -ne 0) { throw "Unable to install CUDA PyTorch" }
$RuntimeRequirements = @(
    "diffusers==0.30.2",
    "accelerate==0.28.0",
    "numpy==1.23.5",
    "opencv-python==4.9.0.80",
    "soundfile==0.12.1",
    "transformers==4.39.2",
    "huggingface_hub==0.30.2",
    "hf_xet",
    "librosa==0.11.0",
    "einops==0.8.1",
    "gdown",
    "requests",
    "imageio[ffmpeg]",
    "omegaconf",
    "ffmpeg-python",
    "moviepy==1.0.3"
)
& $VenvPython -m pip install @RuntimeRequirements
if ($LASTEXITCODE -ne 0) { throw "Unable to install MuseTalk inference requirements" }
& $VenvPython -m pip install filelock typing-extensions sympy networkx jinja2 pillow
if ($LASTEXITCODE -ne 0) { throw "Unable to install PyTorch runtime dependencies" }
& $VenvPython -m pip install --no-cache-dir -U openmim
if ($LASTEXITCODE -ne 0) { throw "Unable to install openmim" }
& $VenvPython -m pip install --no-build-isolation "chumpy==0.70"
if ($LASTEXITCODE -ne 0) { throw "Unable to install the legacy chumpy dependency" }
& $VenvPython -m mim install mmengine "mmcv==2.0.1" "mmdet==3.1.0" "mmpose==1.1.0"
if ($LASTEXITCODE -ne 0) { throw "Unable to install MMLab dependencies" }

Remove-Item Env:HF_HUB_DISABLE_XET -ErrorAction SilentlyContinue
Remove-Item Env:HF_ENDPOINT -ErrorAction SilentlyContinue
$ModelsRoot = Join-Path $MuseTalkRoot "models"

function Invoke-HfDownload {
    param(
        [string]$RepoId,
        [string]$LocalDir,
        [string[]]$Include = @()
    )
    $DownloadArgs = @(
        "-m", "huggingface_hub.commands.huggingface_cli",
        "download", $RepoId,
        "--local-dir", $LocalDir,
        "--max-workers", "4"
    )
    if ($Include.Count -gt 0) {
        $DownloadArgs += "--include"
        $DownloadArgs += $Include
    }
    & $VenvPython @DownloadArgs
    if ($LASTEXITCODE -ne 0) { throw "Unable to download weights from $RepoId" }
}

Invoke-HfDownload "TMElyralab/MuseTalk" $ModelsRoot
Invoke-HfDownload "stabilityai/sd-vae-ft-mse" (Join-Path $ModelsRoot "sd-vae") @("config.json", "diffusion_pytorch_model.bin")
Invoke-HfDownload "openai/whisper-tiny" (Join-Path $ModelsRoot "whisper") @("config.json", "pytorch_model.bin", "preprocessor_config.json")
Invoke-HfDownload "yzd-v/DWPose" (Join-Path $ModelsRoot "dwpose") @("dw-ll_ucoco_384.pth")
Invoke-HfDownload "ManyOtherFunctions/face-parse-bisent" (Join-Path $ModelsRoot "face-parse-bisent") @("79999_iter.pth", "resnet18-5c106cde.pth")

$SfdPath = Join-Path $MuseTalkRoot "musetalk\utils\face_detection\detection\sfd\s3fd.pth"
if (-not (Test-Path -LiteralPath $SfdPath -PathType Leaf) -or (Get-Item -LiteralPath $SfdPath).Length -lt 1000000) {
    Invoke-WebRequest -Uri "https://www.adrianbulat.com/downloads/python-fan/s3fd-619a316812.pth" -OutFile $SfdPath
}

& $VenvPython -c "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) { throw "MuseTalk CUDA verification failed" }

Write-Host "MuseTalk 1.5 is ready in $MuseTalkRoot"
