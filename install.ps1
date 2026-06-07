# DCA Scraper Installation Script for Windows PowerShell

Write-Host ""
Write-Host "========================================"
Write-Host " DCA License Scraper - Installation" -ForegroundColor Cyan
Write-Host "========================================"
Write-Host ""

# Check if Node.js is installed
try {
    $nodeVersion = node --version
    Write-Host "✓ Node.js found: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ ERROR: Node.js is not installed!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please download and install Node.js from:"
    Write-Host "https://nodejs.org/" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Make sure to:"
    Write-Host "1. Download the LTS version"
    Write-Host "2. Run the installer"
    Write-Host "3. Check 'Add to PATH' during installation"
    Write-Host "4. Restart your PowerShell window"
    Write-Host "5. Run this script again"
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# Check if npm is installed
try {
    $npmVersion = npm --version
    Write-Host "✓ npm found: $npmVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ ERROR: npm is not installed!" -ForegroundColor Red
    Write-Host "Please reinstall Node.js and ensure npm is included."
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Write-Host "This may take 2-5 minutes..."
Write-Host ""

# Install dependencies
npm install

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "❌ ERROR: Failed to install dependencies!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Troubleshooting:"
    Write-Host "1. Check your internet connection"
    Write-Host "2. Try running: npm cache clean --force"
    Write-Host "3. Run this script again"
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "========================================"
Write-Host "✓ Installation Complete!" -ForegroundColor Green
Write-Host "========================================"
Write-Host ""
Write-Host "Next steps:"
Write-Host "1. Run the scraper: npm start"
Write-Host "2. Handle Cloudflare verification if prompted"
Write-Host "3. Wait for results to be saved"
Write-Host ""
Write-Host "For help, see:"
Write-Host "- QUICKSTART.md (Quick start guide)"
Write-Host "- README_SCRAPER.md (Full documentation)"
Write-Host ""
Read-Host "Press Enter to close"
