@echo off
REM DCA Scraper Installation Script for Windows

echo.
echo ========================================
echo  DCA License Scraper - Installation
echo ========================================
echo.

REM Check if Node.js is installed
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Node.js is not installed!
    echo.
    echo Please download and install Node.js from:
    echo https://nodejs.org/
    echo.
    echo Make sure to:
    echo 1. Download the LTS version
    echo 2. Run the installer
    echo 3. Check "Add to PATH" during installation
    echo 4. Restart your terminal/command prompt
    echo 5. Run this script again
    echo.
    pause
    exit /b 1
)

echo ✓ Node.js found: %Node.js%
node --version
echo.

REM Check if npm is installed
npm --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: npm is not installed!
    echo Please reinstall Node.js and ensure npm is included.
    pause
    exit /b 1
)

echo ✓ npm found:
npm --version
echo.

REM Install dependencies
echo Installing dependencies...
echo This may take 2-5 minutes...
echo.

npm install

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to install dependencies!
    echo.
    echo Troubleshooting:
    echo 1. Check internet connection
    echo 2. Try running: npm cache clean --force
    echo 3. Run this script again
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo ✓ Installation Complete!
echo ========================================
echo.
echo Next steps:
echo 1. Run the scraper: npm start
echo 2. Handle Cloudflare verification if prompted
echo 3. Wait for results to be saved
echo.
echo For help, see:
echo - QUICKSTART.md (Quick start guide)
echo - README_SCRAPER.md (Full documentation)
echo.
pause
