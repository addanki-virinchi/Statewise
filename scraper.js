const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
const { createObjectCsvWriter } = require('csv-writer');
const fs = require('fs');
const path = require('path');
const readline = require('readline');

// Add stealth plugin
puppeteer.use(StealthPlugin());

// License keywords to search
const KEYWORDS = [
  'Licensed Acupuncturist',
  'TEMP-LICENSED ACUPUNCTURIST',
  'Licensed Clinical Social Worker (LCSW)',
  'Licensed Educational Psychologist',
  'Licensed Marriage and Family Therapist (LMFT)',
  'Licensed Professional Clinical Counselor (LPCC)',
  'Chiropractor',
  'Dentist License',
  'Dental Sedation Assistant',
  'Orthodontic Assistant',
  'Registered Dental Assistant License',
  'Registered Dental Assistant in Extended Functions',
  'Registered Dental Hygienist',
  'Registered Dental Hygienist Alternative Practice',
  'Registered Dental Hygienist Extended Function',
  'Physician and Surgeon',
  'Physician and Surgeon from Mexico License',
  'Polysomnographic',
  'Naturopathic Doctor',
  'Occupational Therapist',
  'Occupational Therapist Limited Permit',
  'Occupational Therapy Assistant',
  'Occupational Therapy Assistant Limited Permit',
  'Optometrist',
  'Registered Contact Lens Dispenser',
  'Registered Spectacle Lens Dispenser',
  'Nonresident Ophthalmic Lens Dispensers',
  'Osteopathic Physician and Surgeon',
  'Osteopathic Postgraduate Training License',
  'Advanced Pharmacist Practitioner',
  'Designated Paramedic',
  'Intern Pharmacist',
  'Registered Pharmacist',
  'Pharmacy Technician',
  'Physical Therapist',
  'Physical Therapist Assistant',
  'Physician Assistant',
  'Doctor of Podiatric Medicine',
  'Podiatric Medical Resident',
  'Psychologist',
  'Registered Psychological Associate',
  'Registered Psychological Testing Technician',
  'Registered Psychologist',
  'Research Psychoanalyst',
  'Student Research Psychoanalyst',
  'Registered Nurse (RN)',
  'Clinical Nurse Specialist (CNS)',
  'Nurse Practitioner (NP)',
  'Nurse Practitioner Furnishing (NPF)',
  'Nurse Anesthetist (NA)',
  'Nurse Midwife (NM)',
  'Nurse Midwife Furnishing (NMF)',
  'Psychiatric Mental Health Nurse (PMH)',
  'Public Health Nurse (PHN)',
  'Respiratory Care Practitioner',
  'Hearing Aid Dispenser',
  'Hearing Aid Dispenser Trainee',
  'Audiologist',
  'Speech Pathologist',
  'Speech-Language Pathology Assistant',
  'Speech-Language Pathology and Audiology Aide',
  'Vocational Nurse (LVN/LPN equivalent)',
  'Psychiatric Technician'
];

const BASE_URL = 'https://search.dca.ca.gov/';
const FAKE_IP = '51.81.155.236';
const CSV_FILE = 'dca_licenses.csv';
const LOG_FILE = 'scraper_log.txt';
const API_URL_PREFIX = 'https://search.dca.ca.gov/api';
const SEARCH_FORM_SELECTOR = '#licenseType';
const RESULT_CARD_SELECTOR = 'article.post';
const INDIA_PROXY_SERVER = process.env.PROXY_SERVER || '';
const INDIA_PROXY_USERNAME = process.env.PROXY_USERNAME || '';
const INDIA_PROXY_PASSWORD = process.env.PROXY_PASSWORD || '';
const INDIA_GEOLOCATION = { latitude: 28.6139, longitude: 77.2090, accuracy: 100 };

let scrapedData = [];
let missedKeywords = [];
let failedKeywords = [];

// Logging function
function log(message) {
  const timestamp = new Date().toISOString();
  const logMessage = `[${timestamp}] ${message}`;
  console.log(logMessage);
  fs.appendFileSync(LOG_FILE, logMessage + '\n');
}

// Prompt for user input (for Cloudflare verification)
function askQuestion(query) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  return new Promise(resolve => {
    rl.question(query, (answer) => {
      rl.close();
      resolve(answer);
    });
  });
}

// Wait for Cloudflare verification
async function waitForCloudflareVerification(page) {
  log('⏳ Cloudflare verification detected. Waiting for manual verification...');
  console.log('📋 Please complete the Cloudflare verification in the browser window.');
  
  // Wait for navigation or user to complete verification
  try {
    await page.waitForNavigation({ timeout: 300000 }); // 5 minutes timeout
    log('✅ Cloudflare verification completed. Continuing...');
  } catch (error) {
    log('⚠️ Navigation timeout - assuming verification was completed');
  }
}

function isApiUrl(url) {
  return typeof url === 'string' && url.startsWith(API_URL_PREFIX);
}

async function ensureSearchPage(page, reason = 'Preparing search page') {
  const currentUrl = page.url();
  log(`${reason}. Current URL: ${currentUrl}`);

  if (isApiUrl(currentUrl)) {
    log('Redirected to API URL. Navigating back to the main search page.');
    await page.goto(BASE_URL, { waitUntil: 'networkidle2', timeout: 60000 });
  }

  await page.waitForSelector(SEARCH_FORM_SELECTOR, { timeout: 30000 });
}

async function getSearchButtonSelector(page) {
  return page.evaluate(() => {
    if (document.querySelector('#srchSubmit')) {
      return '#srchSubmit';
    }
    if (document.querySelector('#srchSubmitHome')) {
      return '#srchSubmitHome';
    }
    return null;
  });
}

async function waitForResultsToRender(page, timeout = 30000) {
  await page.waitForFunction(
    (resultSelector) => {
      if (document.querySelector(resultSelector)) {
        return true;
      }

      const bodyText = document.body && document.body.innerText ? document.body.innerText : '';
      return bodyText.includes('No records found') || bodyText.includes('0 records');
    },
    { timeout },
    RESULT_CARD_SELECTOR
  );
}

async function scrollToResultsEnd(page, maxIdleRounds = 4) {
  log('Scrolling results until the page reaches the end.');

  let previousCount = -1;
  let previousHeight = -1;
  let idleRounds = 0;

  while (idleRounds < maxIdleRounds) {
    await page.evaluate(() => {
      window.scrollTo(0, document.body.scrollHeight);
    });

    await page.waitForTimeout(1500);

    const metrics = await page.evaluate((resultSelector) => ({
      resultCount: document.querySelectorAll(resultSelector).length,
      scrollHeight: document.body.scrollHeight,
      scrollY: window.scrollY,
      viewportHeight: window.innerHeight
    }), RESULT_CARD_SELECTOR);

    const pageStoppedGrowing =
      metrics.resultCount === previousCount &&
      metrics.scrollHeight === previousHeight;

    const atBottom =
      metrics.scrollY + metrics.viewportHeight >= metrics.scrollHeight - 5;

    if (pageStoppedGrowing && atBottom) {
      idleRounds += 1;
    } else {
      idleRounds = 0;
    }

    previousCount = metrics.resultCount;
    previousHeight = metrics.scrollHeight;

    if (isApiUrl(page.url())) {
      throw new Error('Redirected to API URL while scrolling results');
    }
  }

  await page.evaluate(() => window.scrollTo(0, 0));
}

// Get all options from dropdown
async function getDropdownOptions(page) {
  log('📖 Fetching all dropdown options...');
  
  const options = await page.evaluate(() => {
    const selectElement = document.querySelector('#licenseType');
    if (!selectElement) {
      return [];
    }
    
    const allOptions = [];
    const optgroups = selectElement.querySelectorAll('optgroup');
    const directOptions = Array.from(selectElement.querySelectorAll('option:not(optgroup option)'));
    
    // Add direct options
    directOptions.forEach(option => {
      if (option.value && option.value !== '0') {
        allOptions.push({
          value: option.value,
          text: option.textContent.trim()
        });
      }
    });
    
    // Add optgroup options
    optgroups.forEach(optgroup => {
      const groupOptions = optgroup.querySelectorAll('option');
      groupOptions.forEach(option => {
        if (option.value && option.value !== '0') {
          allOptions.push({
            value: option.value,
            text: option.textContent.trim()
          });
        }
      });
    });
    
    return allOptions;
  });
  
  return options;
}

// Find matching option in dropdown
async function findOptionInDropdown(page, keyword) {
  log(`🔍 Searching for keyword: "${keyword}"`);
  
  const dropdownOptions = await getDropdownOptions(page);
  
  // Try exact match first
  let match = dropdownOptions.find(opt => opt.text === keyword);
  
  if (match) {
    log(`✓ Found exact match for "${keyword}" (value: ${match.value})`);
    return match.value;
  }
  
  // Try partial match if exact doesn't work
  match = dropdownOptions.find(opt => opt.text.includes(keyword) || keyword.includes(opt.text));
  
  if (match) {
    log(`⚠️ Found partial match for "${keyword}": "${match.text}" (value: ${match.value})`);
    return match.value;
  }
  
  log(`❌ No match found for keyword: "${keyword}"`);
  missedKeywords.push(keyword);
  return null;
}

// Select dropdown option
async function selectDropdownOption(page, optionValue, retries = 3) {
  for (let i = 0; i < retries; i++) {
    try {
      await page.select('#licenseType', optionValue);
      log(`✓ Selected dropdown option: ${optionValue}`);
      return true;
    } catch (error) {
      log(`⚠️ Retry ${i + 1}/${retries} - Failed to select dropdown: ${error.message}`);
      await page.waitForTimeout(1000);
    }
  }
  return false;
}

// Click search button
async function clickSearch(page, isHomepage = true, retries = 3) {
  const searchButtonId = isHomepage ? '#srchSubmitHome' : '#srchSubmit';
  
  for (let i = 0; i < retries; i++) {
    try {
      await page.click(searchButtonId);
      log(`✓ Clicked search button`);
      await page.waitForTimeout(2000); // Wait for results to load
      return true;
    } catch (error) {
      log(`⚠️ Retry ${i + 1}/${retries} - Failed to click search: ${error.message}`);
      await page.waitForTimeout(1000);
    }
  }
  return false;
}

async function clickSearchDynamic(page, retries = 3) {
  for (let i = 0; i < retries; i++) {
    try {
      const searchButtonId = await getSearchButtonSelector(page);
      if (!searchButtonId) {
        throw new Error('Search button not found on page');
      }

      await page.click(searchButtonId);
      log(`Clicked search button: ${searchButtonId}`);
      await page.waitForTimeout(2000);
      return true;
    } catch (error) {
      log(`Retry ${i + 1}/${retries} - Failed to click detected search button: ${error.message}`);
      await page.waitForTimeout(1000);
    }
  }

  return false;
}

// Extract all result cards from current page
async function extractResults(page, keyword) {
  log(`📊 Extracting results for keyword: "${keyword}"`);
  
  const results = await page.evaluate(() => {
    const articles = document.querySelectorAll('article.post');
    const results = [];
    
    articles.forEach((article) => {
      try {
        // Extract name
        const nameElement = article.querySelector('h3');
        const name = nameElement ? nameElement.textContent.trim() : 'N/A';
        
        // Extract license number
        const licenseNumberLink = article.querySelector('a.newTab');
        const licenseNumber = licenseNumberLink ? licenseNumberLink.textContent.trim() : 'N/A';
        
        // Extract all list items
        const listItems = article.querySelectorAll('li');
        let licenseType = 'N/A';
        let licenseStatus = 'N/A';
        let expirationDate = 'N/A';
        let secondaryStatus = 'N/A';
        let city = 'N/A';
        let state = 'N/A';
        let county = 'N/A';
        let zip = 'N/A';
        
        listItems.forEach((li) => {
          const text = li.textContent;
          if (text.includes('License Type:')) {
            licenseType = text.replace(/License Type:\s*/i, '').trim();
          } else if (text.includes('License Status:')) {
            licenseStatus = text.split('License Status:')[1].split('Licenses are')[0].trim();
          } else if (text.includes('Expiration Date:')) {
            expirationDate = text.replace(/Expiration Date:\s*/i, '').trim();
          } else if (text.includes('Secondary Status:')) {
            secondaryStatus = text.replace(/Secondary Status:\s*/i, '').trim();
          } else if (text.includes('City:')) {
            city = text.replace(/City:\s*/i, '').trim();
          } else if (text.includes('State:')) {
            state = text.replace(/State:\s*/i, '').trim();
          } else if (text.includes('County:')) {
            county = text.replace(/County:\s*/i, '').trim();
          } else if (text.includes('Zip:')) {
            zip = text.replace(/Zip:\s*/i, '').trim();
          }
        });
        
        results.push({
          name,
          licenseNumber,
          licenseType,
          licenseStatus,
          expirationDate,
          secondaryStatus,
          city,
          state,
          county,
          zip
        });
      } catch (error) {
        console.error('Error extracting result:', error);
      }
    });
    
    return results;
  });
  
  log(`✅ Extracted ${results.length} results`);
  return results;
}

// Main scraper function
async function runScraper() {
  let browser;
  
  try {
    log('🚀 Starting DCA License Scraper');
    log(`📍 Using IP: ${FAKE_IP}`);
    log(`🎯 Total keywords to search: ${KEYWORDS.length}`);
    
    // Launch browser with custom headers
    const launchArgs = [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--single-process',
      '--disable-dev-shm-usage'
    ];

    if (INDIA_PROXY_SERVER) {
      launchArgs.push(`--proxy-server=${INDIA_PROXY_SERVER}`);
    }

    browser = await puppeteer.launch({
      headless: false, // Non-headless for manual Cloudflare verification
      args: launchArgs
    });
    
    const page = await browser.newPage();

    if (INDIA_PROXY_USERNAME && INDIA_PROXY_PASSWORD) {
      await page.authenticate({
        username: INDIA_PROXY_USERNAME,
        password: INDIA_PROXY_PASSWORD
      });
    }
    
    // Set custom headers with fake IP
    await page.setExtraHTTPHeaders({
      'X-Forwarded-For': FAKE_IP,
      'CF-Connecting-IP': FAKE_IP,
      'Accept-Language': 'en-IN,en;q=0.9'
    });
    
    // Set user agent
    await page.setUserAgent(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    );
    
    // Enable JavaScript
    await page.setJavaScriptEnabled(true);
    await page.emulateTimezone('Asia/Kolkata');
    await page.setGeolocation(INDIA_GEOLOCATION);
    
    log('🌐 Opening DCA search page...');
    
    // Navigate to page
    try {
      await page.goto(BASE_URL, { waitUntil: 'networkidle2', timeout: 60000 });
    } catch (error) {
      log(`⚠️ Page load timeout: ${error.message}`);
    }
    
    // Check for Cloudflare challenge
    const cloudflarePresent = await page.evaluate(() => {
      return !!(
        document.querySelector('input[value="Challenge"]') ||
        document.body.textContent.includes('Verifying your browser')
      );
    });
    
    if (cloudflarePresent) {
      await waitForCloudflareVerification(page);
    }
    
    await ensureSearchPage(page, 'Initial page check');
    log('✅ Page loaded successfully');
    
    // Process each keyword
    for (let i = 0; i < KEYWORDS.length; i++) {
      const keyword = KEYWORDS[i];
      log(`\n⏩ Processing keyword ${i + 1}/${KEYWORDS.length}: "${keyword}"`);
      
      try {
        await ensureSearchPage(page, `Preparing keyword "${keyword}"`);

        // Find option value
        const optionValue = await findOptionInDropdown(page, keyword);
        
        if (!optionValue) {
          failedKeywords.push({ keyword, reason: 'Not found in dropdown' });
          continue;
        }
        
        // Select the option
        const selected = await selectDropdownOption(page, optionValue);
        if (!selected) {
          failedKeywords.push({ keyword, reason: 'Failed to select dropdown' });
          continue;
        }
        
        // Click search
        const clicked = await clickSearchDynamic(page);
        if (!clicked) {
          failedKeywords.push({ keyword, reason: 'Failed to click search' });
          continue;
        }
        
        // Wait for results
        await waitForResultsToRender(page);
        await scrollToResultsEnd(page);
        
        // Extract results
        const results = await extractResults(page, keyword);
        
        // Add keyword to each result
        const resultsWithKeyword = results.map(result => ({
          keyword_searched: keyword,
          ...result
        }));
        
        scrapedData.push(...resultsWithKeyword);
        log(`💾 Added ${results.length} results to data collection`);
        
      } catch (error) {
        log(`❌ Error processing keyword "${keyword}": ${error.message}`);
        failedKeywords.push({ keyword, reason: error.message });
      }
    }
    
    // Save results to CSV
    log('\n📝 Saving results to CSV...');
    await saveToCSV();
    
    // Log summary
    log('\n📊 === SCRAPING COMPLETE ===');
    log(`✅ Total results scraped: ${scrapedData.length}`);
    log(`⚠️ Missed keywords: ${missedKeywords.length}`);
    log(`❌ Failed keywords: ${failedKeywords.length}`);
    
    if (missedKeywords.length > 0) {
      log('\n❌ Missed keywords:');
      missedKeywords.forEach(k => log(`  - ${k}`));
    }
    
    if (failedKeywords.length > 0) {
      log('\n❌ Failed keywords:');
      failedKeywords.forEach(item => log(`  - ${item.keyword}: ${item.reason}`));
    }
    
    log(`\n✅ Results saved to: ${CSV_FILE}`);
    log(`✅ Log saved to: ${LOG_FILE}`);
    
  } catch (error) {
    log(`💥 Fatal error: ${error.message}`);
    console.error(error);
  } finally {
    if (browser) {
      await browser.close();
      log('🛑 Browser closed');
    }
  }
}

// Save to CSV
async function saveToCSV() {
  const csvWriter = createObjectCsvWriter({
    path: CSV_FILE,
    header: [
      { id: 'keyword_searched', title: 'Keyword Searched' },
      { id: 'name', title: 'Name' },
      { id: 'licenseNumber', title: 'License Number' },
      { id: 'licenseType', title: 'License Type' },
      { id: 'licenseStatus', title: 'License Status' },
      { id: 'expirationDate', title: 'Expiration Date' },
      { id: 'secondaryStatus', title: 'Secondary Status' },
      { id: 'city', title: 'City' },
      { id: 'state', title: 'State' },
      { id: 'county', title: 'County' },
      { id: 'zip', title: 'Zip' }
    ]
  });
  
  await csvWriter.writeRecords(scrapedData);
}

// Run the scraper
runScraper().catch(error => {
  log(`❌ Unhandled error: ${error.message}`);
  console.error(error);
  process.exit(1);
});
