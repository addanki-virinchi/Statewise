# 📋 DCA License Scraper - File Index & Guide

## 🎯 Start Here

**First time?** → Read [QUICKSTART.md](QUICKSTART.md) (5 minutes)  
**Need detailed setup?** → Read [SETUP.md](SETUP.md) (complete guide)  
**Need full docs?** → Read [README_SCRAPER.md](README_SCRAPER.md) (reference)  

---

## 📁 Project Files

### 🚀 Executable Files

#### `install.bat` (Windows)
**What**: Batch script to install dependencies  
**When**: First time setup on Windows  
**How**: Double-click `install.bat`  
**Does**: 
- Checks for Node.js
- Runs `npm install`
- Shows success/error messages

#### `install.ps1` (PowerShell)
**What**: PowerShell script to install dependencies  
**When**: First time setup with PowerShell  
**How**: Run `.\install.ps1`  
**Does**: Same as install.bat but in PowerShell

#### `run.bat` (Windows)
**What**: Batch script to run the scraper  
**When**: Every time you want to scrape  
**How**: Double-click `run.bat`  
**Does**:
- Checks dependencies are installed
- Runs `node scraper.js`
- Handles errors gracefully

#### `validate.js`
**What**: Node.js script to validate setup  
**When**: Before running scraper (optional)  
**How**: `node validate.js`  
**Does**:
- Checks Node.js version
- Checks npm installed
- Checks all dependencies
- Provides helpful error messages

---

### 💻 Source Code

#### `package.json`
**What**: NPM project configuration  
**Purpose**: Lists all dependencies and project info  
**Do NOT modify** unless you know what you're doing  

**Contains**:
```json
{
  "dependencies": {
    "puppeteer": "^22.0.0",
    "puppeteer-extra": "^3.3.6",
    "puppeteer-extra-plugin-stealth": "^2.11.2",
    "csv-writer": "^1.6.0"
  }
}
```

#### `scraper.js` ⭐
**What**: Main scraper application (700+ lines)  
**Purpose**: Performs web scraping and data extraction  
**Can modify**:
- License keywords in `KEYWORDS` array
- Timeouts and retry counts
- Output file names
- User agent strings

**Key sections**:
- `KEYWORDS[]` - License types to search (lines ~30-80)
- `BASE_URL` - Website URL (line ~85)
- `runScraper()` - Main execution (line ~240)
- `extractResults()` - Data extraction logic (line ~170)

---

### 📚 Documentation

#### `QUICKSTART.md` ⭐ START HERE
**What**: Quick start guide (5 minutes)  
**Read when**:
- First time using the scraper
- Want quick overview
- Need fast setup instructions

**Contains**:
- ✅ 5-minute setup steps
- 🔧 Common commands
- 📊 Understanding output
- 🆘 Quick troubleshooting

#### `SETUP.md` ⭐ COMPLETE GUIDE
**What**: Comprehensive setup & execution guide  
**Read when**:
- Detailed setup walkthrough needed
- Customization help needed
- Troubleshooting problems
- Want to understand each step

**Contains**:
- 📋 Prerequisites check
- 🚀 Step-by-step installation
- ▶️ Running the scraper
- 📊 Output file explanations
- ⚙️ Customization options
- 🆘 Detailed troubleshooting
- 💡 Performance tips
- 🎓 Next steps

#### `README_SCRAPER.md` ⭐ REFERENCE
**What**: Full technical documentation  
**Read when**:
- Need detailed reference
- Advanced configuration
- Understanding Cloudflare bypass
- Maintenance information

**Contains**:
- ✅ Features list
- 🎯 Comprehensive requirements
- 📝 Installation details
- ⚙️ Configuration options
- 🆘 Detailed troubleshooting
- 🔒 Cloudflare bypass explanation
- 📈 Performance optimization
- 🧹 Maintenance guidelines

#### `INDEX.md` (This File)
**What**: File guide and quick reference  
**Purpose**: Explains what each file does

---

### 📊 Generated Output Files

#### `dca_licenses.csv` (Generated after running)
**What**: Main output file with scraped data  
**Format**: Comma-separated values  
**Open with**: Excel, Google Sheets, text editor  
**Columns** (11 total):
1. Keyword Searched - Search term used
2. Name - License holder name
3. License Number - Unique ID
4. License Type - Type of license
5. License Status - Current status
6. Expiration Date - When it expires
7. Secondary Status - Additional info
8. City - City location
9. State - State
10. County - County
11. Zip - Zip code

**Example**:
```csv
Keyword Searched,Name,License Number,License Type,License Status,Expiration Date,Secondary Status,City,State,County,Zip
Licensed Acupuncturist,JOHN DOE,12345,Licensed Acupuncturist,ACTIVE,12/31/2026,N/A,Los Angeles,California,LOS ANGELES,90001
```

#### `scraper_log.txt` (Generated after running)
**What**: Detailed execution log  
**Format**: Plain text with timestamps  
**Open with**: Notepad, any text editor  
**Contains**:
- ✅ Successfully matched keywords
- ⚠️ Retry attempts
- ❌ Failed keywords with reasons
- 📊 Statistics and summary

**Example**:
```
[2026-06-04T10:30:00.000Z] 🚀 Starting DCA License Scraper
[2026-06-04T10:30:05.000Z] ✅ Page loaded successfully
[2026-06-04T10:30:06.000Z] ✓ Found exact match for "Licensed Acupuncturist"
[2026-06-04T10:30:08.000Z] ✅ Extracted 12 results
```

---

## 📖 Quick Reference Guide

### First Time Setup

```
1. Download Node.js from https://nodejs.org/
2. Install Node.js (check "Add to PATH")
3. Double-click install.bat
4. Wait for "Installation Complete!" message
5. Done! Ready to run.
```

### Running the Scraper

```
1. Double-click run.bat
2. Browser window opens
3. Handle Cloudflare if needed
4. Wait 5-15 minutes
5. Check dca_licenses.csv for results
```

### If Something Goes Wrong

| Problem | Solution |
|---------|----------|
| "Node.js not found" | Reinstall Node.js, restart terminal |
| "Module not found" | Run `npm install` |
| Browser won't open | Check Node.js installation |
| Cloudflare stuck | Manually verify in browser window |
| No results | Check scraper_log.txt for errors |

### Customizing Keywords

Edit `scraper.js`:
1. Open `scraper.js` in text editor
2. Find: `const KEYWORDS = [`
3. Add/remove/modify keywords
4. Save file
5. Run: `npm start`

---

## 🗂️ File Dependency Tree

```
┌─ START HERE
│  ├─ QUICKSTART.md (5 min overview)
│  ├─ SETUP.md (detailed guide)
│  └─ README_SCRAPER.md (full reference)
│
├─ INSTALLATION
│  ├─ install.bat → npm install → node_modules/
│  └─ install.ps1 → npm install → node_modules/
│
├─ EXECUTION
│  ├─ run.bat
│  └─ scraper.js (uses)
│     ├─ package.json (dependencies from)
│     └─ node_modules/ (installed deps)
│
├─ VALIDATION (Optional)
│  └─ validate.js → checks setup
│
└─ OUTPUT
   ├─ dca_licenses.csv (main data)
   └─ scraper_log.txt (detailed log)
```

---

## ⚡ Command Cheat Sheet

```bash
# Installation
npm install                    # Install all dependencies
npm list                       # Verify dependencies installed

# Running
npm start                      # Run the scraper
node scraper.js                # Alternative run command
node validate.js               # Check if setup is correct

# Debugging
node --version                 # Check Node.js version
npm --version                  # Check npm version
cat scraper_log.txt            # View log (Mac/Linux)
type scraper_log.txt           # View log (Windows)
```

---

## 🎯 Typical Workflow

```
Week 1:
- Read QUICKSTART.md (5 min)
- Run install.bat (2-5 min)
- Run run.bat (10-15 min)
- Review dca_licenses.csv

Week 2+:
- Modify keywords if needed
- Run npm start again
- Combine multiple CSVs
- Analyze results
```

---

## 📞 Getting Help

**Check these in order:**

1. **Quick Help?**
   - Read [QUICKSTART.md](QUICKSTART.md)
   - Check common issues section

2. **Setup Problems?**
   - Read [SETUP.md](SETUP.md) troubleshooting section
   - Run `node validate.js`

3. **Technical Details?**
   - Read [README_SCRAPER.md](README_SCRAPER.md)
   - Check scraper_log.txt for specific errors

4. **Still Stuck?**
   - Review scraper_log.txt for error details
   - Check all prerequisites in SETUP.md
   - Try running validate.js

---

## ✅ Setup Checklist

Before running scraper, confirm:

- [ ] Node.js v14+ installed
- [ ] npm installed
- [ ] `npm install` completed successfully
- [ ] All files present (scraper.js, package.json)
- [ ] Internet connection working
- [ ] Can access https://search.dca.ca.gov/
- [ ] Know license keywords to search

---

## 🎓 Learning Path

1. **Beginner** → QUICKSTART.md
2. **Intermediate** → SETUP.md
3. **Advanced** → README_SCRAPER.md + scraper.js source code
4. **Expert** → Modify scraper.js directly, add features

---

## 📝 File Sizes (Approximate)

| File | Size | Type |
|------|------|------|
| package.json | 0.3 KB | Config |
| scraper.js | 18 KB | Source |
| README_SCRAPER.md | 25 KB | Docs |
| SETUP.md | 20 KB | Docs |
| QUICKSTART.md | 8 KB | Docs |
| INDEX.md | 8 KB | Reference |
| validate.js | 6 KB | Tool |
| install.bat | 2 KB | Script |
| install.ps1 | 2 KB | Script |
| run.bat | 1.5 KB | Script |

---

## 🚀 Ready to Start?

1. **First time?** → Open [QUICKSTART.md](QUICKSTART.md)
2. **Need setup help?** → Open [SETUP.md](SETUP.md)
3. **Want details?** → Open [README_SCRAPER.md](README_SCRAPER.md)
4. **Ready to run?** → Double-click `install.bat` then `run.bat`

Happy scraping! 🎯
