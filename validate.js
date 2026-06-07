#!/usr/bin/env node

/**
 * DCA Scraper - Setup Validator
 * Checks if all dependencies are correctly installed
 * Run with: node validate.js
 */

const fs = require('fs');
const path = require('path');
const { exec } = require('child_process');
const { promisify } = require('util');

const execPromise = promisify(exec);

const colors = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m'
};

function log(text, color = 'reset') {
  console.log(`${colors[color]}${text}${colors.reset}`);
}

async function validateSetup() {
  log('\n========================================', 'cyan');
  log(' DCA Scraper - Setup Validator', 'cyan');
  log('========================================\n', 'cyan');

  let hasErrors = false;

  // 1. Check Node.js
  log('1️⃣  Checking Node.js...', 'blue');
  try {
    const { stdout: nodeVersion } = await execPromise('node --version');
    const version = nodeVersion.trim();
    const major = parseInt(version.split('.')[0].substring(1));
    
    if (major >= 14) {
      log(`   ✓ Node.js ${version} installed`, 'green');
    } else {
      log(`   ❌ Node.js ${version} too old (need v14+)`, 'red');
      hasErrors = true;
    }
  } catch (error) {
    log('   ❌ Node.js not installed', 'red');
    hasErrors = true;
  }

  // 2. Check npm
  log('\n2️⃣  Checking npm...', 'blue');
  try {
    const { stdout: npmVersion } = await execPromise('npm --version');
    log(`   ✓ npm ${npmVersion.trim()} installed`, 'green');
  } catch (error) {
    log('   ❌ npm not installed', 'red');
    hasErrors = true;
  }

  // 3. Check package.json
  log('\n3️⃣  Checking package.json...', 'blue');
  if (fs.existsSync('package.json')) {
    log('   ✓ package.json found', 'green');
  } else {
    log('   ❌ package.json not found', 'red');
    hasErrors = true;
  }

  // 4. Check node_modules
  log('\n4️⃣  Checking dependencies...', 'blue');
  const dependencies = [
    'puppeteer',
    'puppeteer-extra',
    'puppeteer-extra-plugin-stealth',
    'csv-writer'
  ];

  let allDepsFound = true;
  for (const dep of dependencies) {
    const depPath = path.join('node_modules', dep);
    if (fs.existsSync(depPath)) {
      try {
        const pkg = JSON.parse(fs.readFileSync(path.join(depPath, 'package.json'), 'utf8'));
        log(`   ✓ ${dep} v${pkg.version}`, 'green');
      } catch (e) {
        log(`   ✓ ${dep} (version unknown)`, 'green');
      }
    } else {
      log(`   ❌ ${dep} not installed`, 'red');
      allDepsFound = false;
    }
  }

  if (!allDepsFound) {
    log('\n   Run: npm install', 'yellow');
    hasErrors = true;
  }

  // 5. Check scraper.js
  log('\n5️⃣  Checking scraper file...', 'blue');
  if (fs.existsSync('scraper.js')) {
    const size = fs.statSync('scraper.js').size;
    log(`   ✓ scraper.js found (${size} bytes)`, 'green');
  } else {
    log('   ❌ scraper.js not found', 'red');
    hasErrors = true;
  }

  // 6. Check documentation
  log('\n6️⃣  Checking documentation...', 'blue');
  const docs = ['README_SCRAPER.md', 'QUICKSTART.md', 'SETUP.md'];
  let allDocsFound = true;
  for (const doc of docs) {
    if (fs.existsSync(doc)) {
      log(`   ✓ ${doc} found`, 'green');
    } else {
      log(`   ⚠️  ${doc} not found`, 'yellow');
    }
  }

  // 7. Check scripts
  log('\n7️⃣  Checking helper scripts...', 'blue');
  const scripts = ['install.bat', 'install.ps1', 'run.bat'];
  for (const script of scripts) {
    if (fs.existsSync(script)) {
      log(`   ✓ ${script} found`, 'green');
    } else {
      log(`   ⚠️  ${script} not found`, 'yellow');
    }
  }

  // Summary
  log('\n========================================', 'cyan');
  if (hasErrors) {
    log(' ❌ Setup validation FAILED', 'red');
    log('========================================\n', 'cyan');
    log('Issues found:', 'red');
    log('1. Run: npm install', 'yellow');
    log('2. Ensure Node.js v14+ is installed', 'yellow');
    log('3. Check internet connection', 'yellow');
    log('4. Restart terminal if you just installed Node.js', 'yellow');
    log('');
  } else {
    log(' ✓ Setup validation PASSED', 'green');
    log('========================================\n', 'cyan');
    log('You are ready to run the scraper!', 'green');
    log('');
    log('Next step:', 'blue');
    log('  npm start', 'cyan');
    log('');
  }
}

// Run validation
validateSetup().catch(error => {
  log(`\n❌ Validation error: ${error.message}`, 'red');
  process.exit(1);
});
