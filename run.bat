@echo off
REM DCA Scraper Run Script

echo.
echo ========================================
echo  DCA License Scraper - Starting
echo ========================================
echo.

REM Check if node_modules exists
if not exist "node_modules" (
    echo ERROR: Dependencies not installed!
    echo.
    echo Please run: install.bat
    echo Or: npm install
    echo.
    pause
    exit /b 1
)

echo Starting scraper...
echo Monitor the browser window and log file for progress.
echo.
echo This will open:
echo 1. Browser window (non-headless)
echo 2. Handle any Cloudflare verification
echo 3. Extract license data sequentially
echo.
echo Output files:
echo - dca_licenses.csv (results)
echo - scraper_log.txt (detailed log)
echo.

node scraper.js

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Scraper failed with exit code %errorlevel%
    echo.
    echo Check scraper_log.txt for details
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo ✓ Scraper Complete!
echo ========================================
echo.
echo Results saved to:
echo - dca_licenses.csv
echo.
echo Logs saved to:
echo - scraper_log.txt
echo.
pause
