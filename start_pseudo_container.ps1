Write-Host "Starting 'container' environment setup..."

# 1. Create a virtual environment if it doesn't exist (simulating isolated filesystem)
if (-not (Test-Path "venv")) {
    Write-Host "Creating isolated Python environment (venv)..."
    python -m venv venv
}

# 2. Activate virtual environment
Write-Host "Activating environment..."
. .\venv\Scripts\Activate.ps1

# 3. Install dependencies (simulating RUN pip install)
Write-Host "Installing dependencies..."
pip install --no-cache-dir -r requirements.txt

# 4. Set Environment variables (simulating ENV)
Write-Host "Setting environment variables..."
$env:PYTHONUNBUFFERED="1"

# 5. Start the application (simulating CMD)
Write-Host "Starting application..."
uvicorn app.main:app --host 0.0.0.0 --port 8000
