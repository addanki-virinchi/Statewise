# DCA License Scraper

A Puppeteer-based web scraper for California DCA (Department of Consumer Affairs) license search platform with Cloudflare bypass support.

## Features

✅ **Puppeteer with Stealth Plugin** - Bypass anti-bot detection  
✅ **Cloudflare Support** - Manual verification handling with non-headless mode  
✅ **Sequential Processing** - Process license types one at a time  
✅ **Automatic Extraction** - Extract name, license number, type, status, dates, and location  
✅ **CSV Output** - Structured data export  
✅ **Comprehensive Logging** - Track all actions and errors  
✅ **Retry Logic** - Automatic retries for dropdown selection and search  
✅ **Error Handling** - Continues processing even if a keyword fails  

## Prerequisites

- **Node.js** (v14 or higher) - [Download](https://nodejs.org/)
- **npm** (comes with Node.js)
- **Internet Connection** with access to https://search.dca.ca.gov/

## Installation

### Step 1: Install Node.js Dependencies

```bash
npm install
```

This will install:
- `puppeteer` - Headless browser automation
- `puppeteer-extra` - Extended Puppeteer functionality
- `puppeteer-extra-plugin-stealth` - Cloudflare bypass plugin
- `csv-writer` - CSV file creation

### Step 2: Verify Installation

```bash
npm list
```

You should see all dependencies installed without errors.

## Configuration

The scraper uses the following configuration (in `scraper.js`):

- **Website**: https://search.dca.ca.gov/
- **IP Address**: 15.204.43.201 (configured as X-Forwarded-For header)
- **Mode**: Non-headless (browser window visible)
- **Timeout**: 5 minutes for Cloudflare verification
- **CSV Output**: `dca_licenses.csv`
- **Log File**: `scraper_log.txt`

### Modifying Keywords

To change the license keywords to search, edit the `KEYWORDS` array in `scraper.js`:

```javascript
const KEYWORDS = [
  'Licensed Acupuncturist',
  'Registered Nurse (RN)',
  // ... add more keywords
];
```

## Usage

### Run the Scraper

```bash
npm start
```

or

```bash
node scraper.js
```

### What Happens During Execution

1. **Browser Opens** - A Chrome/Chromium window opens (non-headless mode)
2. **Page Loads** - Navigates to https://search.dca.ca.gov/
3. **Cloudflare Check** - If a Cloudflare challenge appears:
   - The scraper pauses and waits
   - Complete the verification in the browser window (usually just waiting or clicking)
   - The scraper automatically continues after verification
4. **Sequential Searching** - For each license keyword:
   - Finds the keyword in the License Type dropdown
   - Selects it
   - Clicks the SEARCH button
   - Extracts all result cards
   - Proceeds to the next keyword
5. **Results Saved** - When complete, results are saved to `dca_licenses.csv`

## Output Files

### dca_licenses.csv

Contains all scraped license records with columns:
- `Keyword Searched` - The search term used
- `Name` - License holder name
- `License Number` - Unique license identifier
- `License Type` - Type of license
- `License Status` - Current status (ACTIVE, EXPIRED, CANCELLED, etc.)
- `Expiration Date` - License expiration date
- `Secondary Status` - Additional status information
- `City` - City of residence/business
- `State` - State
- `County` - County
- `Zip` - Zip code

### scraper_log.txt

Detailed log of all scraper actions:
- Successfully matched keywords
- Failed keyword matches
- Extraction counts
- Errors and retry attempts
- Final summary statistics

## Troubleshooting

### Issue: "Page failed to load"

**Solution**: 
- Check your internet connection
- Try running the scraper again
- Ensure the IP address is correct in the script

### Issue: "Cloudflare verification stuck"

**Solution**:
- Manually complete the Cloudflare verification in the browser window
- The scraper will automatically detect when it's done and continue
- If stuck, close the browser and restart the scraper

### Issue: "Dropdown selection failed"

**Solution**:
- The script automatically retries 3 times
- If it continues failing, check that the keyword exists in the dropdown
- Review the `scraper_log.txt` for specific error messages

### Issue: "No results found"

**Solution**:
- Some keywords may not have results on the first page
- The scraper only processes the first page (no pagination)
- Check the CSV file - it will show how many results were found per keyword
- Review the log for any keywords that weren't found in the dropdown

### Issue: "Module not found" errors

**Solution**:
```bash
# Reinstall dependencies
rm -r node_modules
npm install
```

On Windows PowerShell:
```powershell
Remove-Item -Recurse -Force node_modules
npm install
```

## Advanced Configuration

### Changing Browser Behavior

Edit these lines in `scraper.js` to modify browser settings:

```javascript
// For headless mode (faster but can't handle Cloudflare manually):
headless: true,

// Add proxy support:
args: [
  '--proxy-server=socks5://127.0.0.1:9050'
]

// Disable images for faster loading:
await page.setRequestInterception(true);
page.on('request', request => {
  if (request.resourceType() === 'image') request.abort();
  else request.continue();
});
```

### Increasing Timeouts

If results take longer to load, increase the wait time:

```javascript
// From: await page.waitForTimeout(2000);
// To: await page.waitForTimeout(5000); // 5 seconds
```

## Performance Tips

1. **Reduce Keyword List** - Start with fewer keywords to test
2. **Run During Off-Peak Hours** - Fewer visitors = faster responses
3. **Check System Resources** - Close other applications
4. **Disable Images** - Uncomment the image blocking code above

## Example Output

**scraper_log.txt**:
```
[2026-06-04T10:30:00.000Z] 🚀 Starting DCA License Scraper
[2026-06-04T10:30:00.000Z] 📍 Using IP: 15.204.43.201
[2026-06-04T10:30:00.000Z] 🎯 Total keywords to search: 63
[2026-06-04T10:30:05.000Z] ✅ Page loaded successfully
[2026-06-04T10:30:06.000Z] 🔍 Searching for keyword: "Licensed Acupuncturist"
[2026-06-04T10:30:07.000Z] ✓ Found exact match for "Licensed Acupuncturist"
[2026-06-04T10:30:08.000Z] ✓ Clicked search button
[2026-06-04T10:30:10.000Z] 📊 Extracting results for keyword: "Licensed Acupuncturist"
[2026-06-04T10:30:11.000Z] ✅ Extracted 12 results
```

**dca_licenses.csv** (sample rows):
```
Keyword Searched,Name,License Number,License Type,License Status,Expiration Date,Secondary Status,City,State,County,Zip
Licensed Acupuncturist,JOHN DOE,12345,Licensed Acupuncturist,ACTIVE,12/31/2026,N/A,Los Angeles,California,LOS ANGELES,90001
Licensed Acupuncturist,JANE SMITH,12346,Licensed Acupuncturist,ACTIVE,06/30/2025,N/A,San Francisco,California,SAN FRANCISCO,94102
```

## Cloudflare Bypass Notes

The scraper uses several techniques to bypass Cloudflare protection:

1. **Stealth Plugin** - Hides automation signals
2. **Custom Headers** - Includes fake IP address headers
3. **Non-headless Mode** - Browser window visible (can handle challenges)
4. **Real User Agent** - Spoofs legitimate browser
5. **Manual Intervention** - Pauses for user verification if needed

## Rate Limiting

If you get rate-limited (too many requests):
- Add delays between searches
- Reduce the keyword list
- Run the scraper at different times
- Use different IP addresses/proxies

## Limitations

- ⚠️ Only scrapes first page of results (no pagination)
- ⚠️ Does not scrape "More Detail" pages
- ⚠️ Requires manual Cloudflare verification if prompted
- ⚠️ Some keywords may not exist in the dropdown
- ⚠️ Website structure changes may require script updates

## Maintenance

To keep the scraper working:

1. **Monitor Cloudflare Changes** - If challenges change, update stealth plugin
2. **Update Dependencies** - Regularly run `npm update`
3. **Check Website Structure** - If DCA redesigns their site, CSS selectors may need updating
4. **Review Logs** - Check `scraper_log.txt` for patterns in failures

## Support

For issues or questions:

1. Check the **Troubleshooting** section above
2. Review `scraper_log.txt` for error details
3. Ensure all dependencies are installed
4. Verify internet connection and DCA website accessibility
5. Test with a single keyword first to isolate issues

## License

MIT

## Disclaimer

This scraper is for educational and research purposes. Ensure you comply with:
- The website's terms of service
- Robots.txt file
- Local and regional web scraping regulations
- Rate limiting and server load considerations

Use responsibly and ethically.
