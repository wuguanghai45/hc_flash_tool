if (Test-Path "venv") {
    Write-Host "Virtual environment exists."
} else {
    Write-Host "Creating new virtual environment..."
    python -m venv venv
    .\venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    Write-Host "Virtual environment created and dependencies installed!"
}

Write-Host "Please activate it with: .\venv\Scripts\Activate.ps1" 