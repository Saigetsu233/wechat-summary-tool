$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller..."
    python -m pip install "pyinstaller>=6,<7"
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "ChatroomDigest" `
    --icon "icon.ico" `
    --add-data "icon.ico;." `
    --collect-all "tkcalendar" `
    --hidden-import "Crypto.Cipher.AES" `
    "wechat_gui.py"

$exePath = Join-Path $projectDir "dist\ChatroomDigest.exe"
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "Build finished without producing $exePath"
}

Write-Host ""
Write-Host "EXE ready: $exePath"
