# Quick Start Guide - DCA License Scraper

## 🚀 Get Started in 5 Minutes

### Prerequisites
- Node.js installed ([Download here](https://nodejs.org/) - version 14+)
- Internet connection

### Step 1: Install Dependencies (1 minute)
```bash
npm install
```

### Step 2: Run the Scraper (< 5 minutes for setup)
```bash
npm start
```

### Step 3: Handle Cloudflare (if prompted)
- A browser window will open
- If you see "Verify you are human" - complete the challenge
- The scraper automatically continues afterward

### Step 4: Wait for Results
- Scraper processes 63 license keywords sequentially
- Typical runtime: 5-15 minutes depending on results
- Results saved to `dca_licenses.csv`

## 📁 Files You'll Need

```
Scrapers/
├── package.json           # Dependencies
├── scraper.js             # Main scraper
├── README_SCRAPER.md      # Full documentation
├── QUICKSTART.md          # This file
├── keywords.json          # License keywords (editable)
├── dca_licenses.csv       # Output file (generated)
└── scraper_log.txt        # Detailed log (generated)
```

## ⚙️ Common Commands

```bash
# Install dependencies
npm install

# Run scraper
npm start

# Run scraper again
node scraper.js

# View log while running
# (Open scraper_log.txt in your editor)

# View results
# (Open dca_licenses.csv with Excel or text editor)
```

## 🔧 Customize Keywords

Edit the `KEYWORDS` array in `scraper.js`:

```javascript
const KEYWORDS = [
  'Licensed Acupuncturist',
  'Registered Nurse (RN)',
  'Dentist License',
  // Add or remove keywords here
];
```

## 📊 Understanding Output

### dca_licenses.csv columns:
- **Keyword Searched** - Search term used
- **Name** - License holder
- **License Number** - ID
- **License Status** - ACTIVE/EXPIRED/CANCELLED
- **Expiration Date** - When license expires
- **City/State/Zip** - Location info

### scraper_log.txt shows:
- ✅ Successfully matched keywords
- ⚠️ Partial matches and retries
- ❌ Failed keywords with reasons
- 📊 Final statistics

## 🆘 Troubleshooting

| Problem | Solution |
|---------|----------|
| "Module not found" | Run `npm install` |
| Browser won't open | Check Node.js is installed correctly |
| Cloudflare stuck | Complete verification manually in browser |
| No results | Keyword might not exist in dropdown - check logs |
| Script hangs | Press Ctrl+C and restart |

## 💡 Tips

1. **Start Small**: Test with 5-10 keywords first
2. **Check Logs**: Review `scraper_log.txt` for details
3. **Run Off-Peak**: Faster results when site is less busy
4. **Keep Browser Visible**: Don't minimize during Cloudflare verification
5. **Monitor Progress**: Check `dca_licenses.csv` while running

## ✅ Next Steps

After first run:
1. ✅ Review `dca_licenses.csv` for results
2. ✅ Check `scraper_log.txt` for any errors
3. ✅ Note which keywords had no matches
4. ✅ Customize keyword list if needed
5. ✅ Run again with updated keywords

## 📞 Need Help?

1. Check `scraper_log.txt` for error details
2. Read the full `README_SCRAPER.md`
3. Verify all dependencies: `npm list`
4. Ensure DCA website is accessible in your browser

Happy scraping! 🎯
