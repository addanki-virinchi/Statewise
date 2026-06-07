# DCA License Scraper - Complete Setup & Execution Guide

## 📋 What's Included

```
Scrapers/
├── package.json              # NPM dependencies
├── scraper.js                # Main scraper code
├── install.bat               # Windows batch installer
├── install.ps1               # PowerShell installer
├── run.bat                   # Windows batch runner
├── README_SCRAPER.md         # Full documentation
├── QUICKSTART.md             # Quick start guide
└── SETUP.md                  # This file
```

---

## 🚀 Installation (Step-by-Step)

### Prerequisites Check
Before starting, verify you have:
- ✅ Windows 7 or later (or Mac/Linux with similar tools)
- ✅ Internet connection
- ✅ Administrator access (for software installation)

### Step 1: Install Node.js

1. Download Node.js LTS from: https://nodejs.org/
2. Run the installer
3. Follow prompts (default options are fine)
4. **Important**: When asked "Add to PATH" - CHECK THIS BOX ✓
5. Click Install
6. Restart your terminal/command prompt

**Verify Installation**:
```bash
node --version
npm --version
```

Both commands should return version numbers.

### Step 2: Install Project Dependencies

**Option A: Using Batch Script (Easiest)**
```bash
Double-click: install.bat
```

**Option B: Using PowerShell**
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\install.ps1
```

**Option C: Manual (npm)**
```bash
npm install
```

This will download and install:
- puppeteer (browser automation)
- puppeteer-extra (extensions)
- puppeteer-extra-plugin-stealth (Cloudflare bypass)
- csv-writer (output formatting)

Installation takes 2-5 minutes. You'll see lots of download messages - this is normal.

### Verify Installation Success

```bash
npm list
```

You should see:
```
├── puppeteer@22.0.0
├── puppeteer-extra@3.3.6
├── puppeteer-extra-plugin-stealth@2.11.2
└── csv-writer@1.6.0
```

---

## ▶️ Running the Scraper

### Quick Run (Easiest)

**Windows**:
```bash
Double-click: run.bat
```

**Mac/Linux/PowerShell**:
```bash
npm start
```

### What Happens Next

1. **Browser Opens** (in ~5 seconds)
   - A Chrome window will appear
   - This is normal and required
   - DO NOT close it

2. **Cloudflare Check** (might appear)
   - If you see "Verify you are human":
     - Complete the challenge (usually just wait or click)
     - The scraper will auto-detect when done
     - Continue is automatic ✓

3. **Scraping Begins** (automated)
   - For each of 63 keywords:
     - Select from dropdown
     - Click search
     - Extract results
     - Save to CSV

4. **Results Saved** (5-15 minutes total)
   - Browser closes automatically
   - Two files created:
     - `dca_licenses.csv` - Your data
     - `scraper_log.txt` - Detailed log

---

## 📊 Output Files

### dca_licenses.csv

Open with Excel or any spreadsheet app.

**Columns**:
| Column | Description | Example |
|--------|-------------|---------|
| Keyword Searched | Search term used | Licensed Acupuncturist |
| Name | License holder | JOHN DOE |
| License Number | Unique ID | 12345 |
| License Type | Specific type | Licensed Acupuncturist |
| License Status | Current status | ACTIVE |
| Expiration Date | When it expires | 12/31/2026 |
| Secondary Status | Additional info | N/A |
| City | License location | Los Angeles |
| State | State | California |
| County | County | LOS ANGELES |
| Zip | Zip code | 90001 |

### scraper_log.txt

Text file with detailed execution log.

**Contains**:
- ✅ Successfully matched keywords
- ⚠️ Partial matches and retries
- ❌ Failed keywords with reasons
- 📊 Statistics and summary

**Example**:
```
[2026-06-04T10:30:00.000Z] 🚀 Starting DCA License Scraper
[2026-06-04T10:30:00.000Z] ✓ Found exact match for "Licensed Acupuncturist"
[2026-06-04T10:30:08.000Z] ✅ Extracted 12 results
```

---

## ⚙️ Customization

### Change License Keywords

Edit `scraper.js` (use any text editor):

1. Open `scraper.js`
2. Find section:
   ```javascript
   const KEYWORDS = [
     'Licensed Acupuncturist',
     'TEMP-LICENSED ACUPUNCTURIST',
     // ... more keywords
   ];
   ```
3. Add, remove, or modify keywords
4. Save the file
5. Run: `npm start`

### Test with Fewer Keywords

For faster testing:
```javascript
const KEYWORDS = [
  'Licensed Acupuncturist',
  'Registered Nurse (RN)',
  'Dentist License'
];
```

### Change Output Filename

In `scraper.js`, find:
```javascript
const CSV_FILE = 'dca_licenses.csv';
```

Change to:
```javascript
const CSV_FILE = 'my_results.csv';
```

---

## 🆘 Troubleshooting

### Issue: "node is not recognized"

**Cause**: Node.js not in PATH
**Solution**:
1. Uninstall Node.js
2. Reinstall from https://nodejs.org/
3. **Check "Add to PATH"** during installation
4. Restart terminal

### Issue: "npm command not found"

**Cause**: npm not installed with Node.js
**Solution**:
```bash
npm install -g npm@latest
```

### Issue: "Module 'puppeteer' not found"

**Cause**: Dependencies not installed
**Solution**:
```bash
npm install
```

### Issue: "Browser won't open"

**Cause**: Puppeteer not downloaded
**Solution**:
```bash
npm install --save puppeteer
```

### Issue: "Cloudflare verification fails"

**Cause**: JavaScript blocked or slow connection
**Solution**:
- Close all other applications
- Check internet connection
- Manually complete verification in browser
- Wait 5 minutes for automatic timeout

### Issue: "Cannot find license keyword"

**Cause**: Keyword doesn't exist in dropdown
**Solution**:
- Check `scraper_log.txt` for exact error
- Try different keyword spelling
- Visit https://search.dca.ca.gov/ manually to verify keyword exists

### Issue: "Script hangs or freezes"

**Cause**: Unknown (typically Cloudflare or network issue)
**Solution**:
1. Press `Ctrl+C` to stop script
2. Close browser window
3. Wait 30 seconds
4. Run: `npm start` again

---

## 📈 Performance Tips

1. **Reduce Keywords** - Start with 5-10 keywords
2. **Run Off-Peak** - Early morning or late night (fewer users)
3. **Good Internet** - Faster speeds = faster scraping
4. **Close Programs** - Free up RAM/CPU
5. **Monitor Progress** - Check browser and log file

---

## 🔒 Cloudflare Bypass Details

The scraper uses multiple techniques:

1. **Stealth Plugin** - Hides automation signals
2. **Custom Headers** - Spoofs legitimate browser
3. **User Agent** - Looks like real Windows browser
4. **Non-Headless** - Browser visible (required for challenges)
5. **IP Spoofing** - Uses fake IP: 15.204.43.201
6. **Manual Intervention** - Pauses for human verification if needed

**Why non-headless?**
- Cloudflare can detect headless browsers
- Non-headless allows JavaScript execution
- Manual verification possible if needed

---

## 🎯 Typical Workflow

```
1. npm install                    (install dependencies - 1 time only)
   ↓
2. npm start                      (start scraper)
   ↓
3. Handle Cloudflare (if needed)  (wait for or manually verify)
   ↓
4. Scraper runs                   (automatic - 5-15 minutes)
   ↓
5. Results saved                  (dca_licenses.csv created)
   ↓
6. Review logs                    (check scraper_log.txt for details)
   ↓
7. Repeat with new keywords       (optional)
```

---

## 📞 Getting Help

**Check these files first**:
1. `QUICKSTART.md` - Quick troubleshooting
2. `README_SCRAPER.md` - Full documentation
3. `scraper_log.txt` - Error details from last run

**Common issues**:
| Problem | Check | Solution |
|---------|-------|----------|
| Won't start | Node.js installed? | Reinstall Node.js |
| Errors during npm install | Internet? | Check connection, retry |
| Scraper hangs | Cloudflare? | Manually verify, wait, or restart |
| No results | Keywords valid? | Check manually on website |
| CSV file empty | Any results found? | Review log for errors |

---

## ✅ Success Checklist

Before running, confirm:
- ✅ Node.js v14+ installed (`node --version`)
- ✅ npm installed (`npm --version`)
- ✅ Dependencies installed (`npm list`)
- ✅ `scraper.js` file exists
- ✅ Keywords customized (if desired)
- ✅ Internet connection working
- ✅ Can access https://search.dca.ca.gov/ in browser

---

## 🎓 Next Steps After First Run

1. **Review Results**
   - Open `dca_licenses.csv` in Excel
   - Count results per keyword
   - Check data quality

2. **Check Logs**
   - Review `scraper_log.txt`
   - Note any warnings or errors
   - Identify missed keywords

3. **Optimize**
   - Remove failed keywords
   - Rerun with better keywords
   - Adjust timeouts if needed

4. **Automate** (Advanced)
   - Schedule runs with Task Scheduler
   - Combine multiple CSV files
   - Email results automatically

---

## 📝 License & Legal

This scraper is for educational/research purposes only.

**Responsibilities**:
- Follow DCA website terms of service
- Respect robots.txt
- Don't overload the server (reasonable delays)
- Comply with local laws on web scraping
- Don't redistribute scraped data commercially

---

## 🎉 Ready to Start?

1. Run: `install.bat` (or `npm install`)
2. Run: `run.bat` (or `npm start`)
3. Let the browser do the work
4. Check `dca_licenses.csv` for results

**Questions?** Check the documentation files included!

Happy scraping! 🚀
