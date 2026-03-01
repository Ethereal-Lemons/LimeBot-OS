#!/usr/bin/env node

import { spawn, exec, execSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';
import net from 'net';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');
const UPDATE_CHECK_TTL_MS = 1000 * 60 * 60 * 6;
const UPDATE_CHECK_TIMEOUT_MS = 2500;
const UPDATE_CHECK_CACHE_PATH = path.join(rootDir, 'data', 'cli-update-check.json');
const WIN_MAX_PATH_SAFE = 259;
const WIN_VENV_PATH_PROBES = [
    path.join('Lib', 'site-packages'),
    path.join(
        'Lib',
        'site-packages',
        'litellm',
        'proxy',
        'guardrails',
        'guardrail_hooks',
        'litellm_content_filter',
        'guardrail_benchmarks',
        'results',
        'block_claims_prior_auth_gaming_-_contentfilter_(claims_prior_auth_gaming.yaml).json'
    ),
];
let cachedVenvLayout = null;

function hashPath(value) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < value.length; i++) {
        hash ^= value.charCodeAt(i);
        hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(36);
}

function windowsSitePackagesPath(venvDir) {
    return path.join(path.resolve(venvDir), 'Lib', 'site-packages');
}

function windowsMaxProjectedPathLength(venvDir) {
    const base = path.resolve(venvDir);
    let maxLen = 0;
    for (const rel of WIN_VENV_PATH_PROBES) {
        const probeLen = path.join(base, rel).length;
        if (probeLen > maxLen) maxLen = probeLen;
    }
    return maxLen;
}

function windowsFallbackVenvDir() {
    const projectId = hashPath(rootDir);
    const localAppData = process.env.LOCALAPPDATA;
    if (localAppData) {
        return path.join(localAppData, 'LimeBot', 'venvs', projectId);
    }

    const userProfile = process.env.USERPROFILE;
    if (userProfile) {
        return path.join(userProfile, 'AppData', 'Local', 'LimeBot', 'venvs', projectId);
    }

    return path.join(path.parse(rootDir).root || 'C:\\', 'LimeBot', 'venvs', projectId);
}

function resolveVenvLayout() {
    if (cachedVenvLayout) return cachedVenvLayout;

    const defaultVenvDir = path.join(rootDir, '.venv');
    if (process.platform !== 'win32') {
        cachedVenvLayout = {
            venvDir: defaultVenvDir,
            usingFallback: false,
            projectedSitePackagesPath: null,
            defaultProjectedSitePackagesPath: null,
            projectedMaxPathLength: null,
            defaultProjectedMaxPathLength: null,
        };
        return cachedVenvLayout;
    }

    const projectedDefault = windowsSitePackagesPath(defaultVenvDir);
    const projectedDefaultMax = windowsMaxProjectedPathLength(defaultVenvDir);
    cachedVenvLayout = {
        venvDir: defaultVenvDir,
        usingFallback: false,
        projectedSitePackagesPath: projectedDefault,
        defaultProjectedSitePackagesPath: projectedDefault,
        projectedMaxPathLength: projectedDefaultMax,
        defaultProjectedMaxPathLength: projectedDefaultMax,
    };
    return cachedVenvLayout;
}



function venvDirPath() {
    return resolveVenvLayout().venvDir;
}

// ── Logger ────────────────────────────────────────────────────────

const colors = {
    reset: "\x1b[0m", bright: "\x1b[1m", dim: "\x1b[2m",
    green: "\x1b[32m", yellow: "\x1b[33m", blue: "\x1b[34m",
    cyan: "\x1b[36m", gray: "\x1b[90m", red: "\x1b[31m",
    lime: "\x1b[38;5;154m",
};

const LOGO = `${colors.lime}
                                                                                                    
                                 ========                  ==++++**                                 
                               ========----              ---===++****                               
                              ============---          ---===++++*****                              
                              ==========    --       ---    +++++*****                              
                                             --======-==                                            
                                         ==================+                                        
                                      =====================+++                                      
                                    =====+%@@@@*====+%@@@@*++++*                                    
                                   =====#@@@@@@@#==+@@@@@@@%++++*                                   
                                  =====*@@@@@@@@%==#@@@@@@@@#+++**                                  
                                  =====*@@@@@@@@#==*@@@@@@@@%+++**                                   
                                   =====%@@@@@@%====%@@@@@@@++++*                                   
                                    ======#%@%+======+%@@%++++++                                    
                                      =====================+++                                      
                                        ===================+                                        
                                            =============                                           
                                                 ===                                                
                                                                                                    
               ===     ====                               ======                 ==                 
              =====    ===                               ==========             ====                
              =====    ==== ================    =======  ====  =====  =======  =======              
              =====    ==== ================= ========== ========== ==================              
              =====    ==== ====   ====  =============== ========== ====   ==== ====                
              =====    ==== ====   ====  =============== ====   ========   ==== ====                
              ======== ==== ====   ====  =============== =========== =========  ======              
               ======= ==== ====   ====   ===    =====   =========     =====      ====              

${colors.reset}`;

const log = (color, text) => console.log(`${color}${text}${colors.reset}`);
const success = (text) => log(colors.green, `  ✓ ${text}`);
const warning = (text) => log(colors.yellow, `  ⚠ ${text}`);
const error = (text) => log(colors.red, `  ✗ ${text}`);
const info = (text) => log(colors.blue, `  ${text}`);
const step = (text) => log(colors.lime, `  ${colors.bright}→ ${text}${colors.reset}`);

// ── Spinner Utility ────────────────────────────────────────────────

class Spinner {
    constructor(text) {
        this.text = text;
        this.frames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
        this.frameIdx = 0;
        this.interval = null;
    }

    start() {
        process.stdout.write(`  ${colors.lime}${this.frames[0]}${colors.reset} ${this.text}`);
        this.interval = setInterval(() => {
            this.frameIdx = (this.frameIdx + 1) % this.frames.length;
            process.stdout.current_line = `  ${colors.lime}${this.frames[this.frameIdx]}${colors.reset} ${this.text}`;
            process.stdout.write(`\r${process.stdout.current_line}`);
        }, 80);
    }

    stop(msg, success = true) {
        if (this.interval) clearInterval(this.interval);
        process.stdout.write(`\r\x1b[K`); // Clear line
        if (success) {
            console.log(`  ${colors.green}✓${colors.reset} ${msg || this.text}`);
        } else {
            console.log(`  ${colors.red}✗${colors.reset} ${msg || this.text}`);
        }
    }
}

async function runWithSpinner(text, fn) {
    const s = new Spinner(text);
    s.start();
    try {
        const result = await fn();
        s.stop();
        return result;
    } catch (e) {
        s.stop(text, false);
        throw e;
    }
}

// ── Utilities ──────────────────────────────────────────────────────

async function isPortReachable(port) {
    const tryConnect = (host) => new Promise((resolve) => {
        const socket = new net.Socket();
        const onError = () => { socket.destroy(); resolve(false); };
        socket.setTimeout(500);
        socket.on('error', onError);
        socket.on('timeout', onError);
        socket.connect(port, host, () => { socket.end(); resolve(true); });
    });

    if (await tryConnect('127.0.0.1')) return true;
    if (await tryConnect('localhost')) return true;
    return false;
}

function openBrowser(url) {
    info(`Opening browser: ${url}`);
    const cmd = process.platform === 'darwin' ? `open "${url}"`
        : process.platform === 'win32' ? `start "" "${url}"`
            : `xdg-open "${url}"`;
    exec(cmd, (err) => {
        if (err) error(`Failed to open browser: ${err.message}`);
    });
}

async function waitForServer(port, maxAttempts = 60) {
    for (let i = 0; i < maxAttempts; i++) {
        if (await isPortReachable(port)) return true;
        await sleep(1000);
    }
    return false;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function isTruthyEnv(value) {
    const normalized = String(value ?? '').trim().toLowerCase();
    return normalized === '1' || normalized === 'true' || normalized === 'yes' || normalized === 'on';
}

function shortSha(sha) {
    return typeof sha === 'string' ? sha.slice(0, 8) : '';
}

function readJsonSafe(filePath) {
    try {
        if (!fs.existsSync(filePath)) return null;
        return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
    } catch {
        return null;
    }
}

function writeJsonSafe(filePath, payload) {
    try {
        fs.mkdirSync(path.dirname(filePath), { recursive: true });
        fs.writeFileSync(filePath, JSON.stringify(payload, null, 2), 'utf-8');
    } catch { }
}

async function runGit(args, timeoutMs = UPDATE_CHECK_TIMEOUT_MS) {
    return new Promise((resolve) => {
        let child;
        try {
            child = spawn('git', args, {
                cwd: rootDir,
                shell: false,
                stdio: ['ignore', 'pipe', 'pipe'],
            });
        } catch (err) {
            resolve({
                code: -1,
                stdout: '',
                stderr: err?.message || String(err),
                timedOut: false,
            });
            return;
        }
        let stdout = '';
        let stderr = '';
        let settled = false;
        let timer = null;

        const finish = (result) => {
            if (settled) return;
            settled = true;
            if (timer) clearTimeout(timer);
            resolve(result);
        };

        if (timeoutMs > 0) {
            timer = setTimeout(() => {
                try { child.kill(); } catch { }
                finish({ code: -1, stdout: stdout.trim(), stderr: stderr.trim(), timedOut: true });
            }, timeoutMs);
        }

        child.stdout.on('data', (d) => { stdout += d.toString(); });
        child.stderr.on('data', (d) => { stderr += d.toString(); });
        child.on('error', (err) => finish({ code: -1, stdout: '', stderr: err.message, timedOut: false }));
        child.on('close', (code) => finish({
            code: code ?? -1,
            stdout: stdout.trim(),
            stderr: stderr.trim(),
            timedOut: false,
        }));
    });
}

function readUpdateCheckCache() {
    const cached = readJsonSafe(UPDATE_CHECK_CACHE_PATH);
    if (!cached || typeof cached !== 'object') return null;
    return cached;
}

function writeUpdateCheckCache(status) {
    writeJsonSafe(UPDATE_CHECK_CACHE_PATH, status);
}

async function getLocalGitSnapshot() {
    const inRepo = await runGit(['rev-parse', '--is-inside-work-tree']);
    if (inRepo.code !== 0 || inRepo.stdout !== 'true') return null;

    const head = await runGit(['rev-parse', 'HEAD']);
    if (head.code !== 0 || !head.stdout) return null;

    const branch = await runGit(['rev-parse', '--abbrev-ref', 'HEAD']);
    const branchName = branch.code === 0 ? branch.stdout.trim() : '';
    if (!branchName || branchName === 'HEAD') return null;

    const remote = await runGit(['config', '--get', `branch.${branchName}.remote`]);
    const remoteName = (remote.code === 0 && remote.stdout) ? remote.stdout.trim() : 'origin';

    const merge = await runGit(['config', '--get', `branch.${branchName}.merge`]);
    const mergeRef = (merge.code === 0 && merge.stdout) ? merge.stdout.trim() : `refs/heads/${branchName}`;
    const remoteBranch = mergeRef.startsWith('refs/heads/')
        ? mergeRef.slice('refs/heads/'.length)
        : mergeRef;

    return {
        localHead: head.stdout.toLowerCase(),
        branchName,
        remoteName,
        remoteBranch,
    };
}

async function getGitUpdateStatus() {
    if (isTruthyEnv(process.env.LIMEBOT_DISABLE_UPDATE_CHECK)) return null;
    if (!fs.existsSync(path.join(rootDir, '.git'))) return null;
    if (!await commandExists('git')) return null;

    const snapshot = await getLocalGitSnapshot();
    if (!snapshot) return null;

    const now = Date.now();
    const cached = readUpdateCheckCache();
    if (
        cached &&
        Number.isFinite(cached.checkedAt) &&
        cached.localHead === snapshot.localHead &&
        cached.branchName === snapshot.branchName &&
        cached.remoteName === snapshot.remoteName &&
        cached.remoteBranch === snapshot.remoteBranch &&
        (now - cached.checkedAt) < UPDATE_CHECK_TTL_MS
    ) {
        return cached;
    }

    const fetch = await runGit(['fetch', '--quiet', snapshot.remoteName, snapshot.remoteBranch]);
    if (fetch.code !== 0) return null;

    const remoteHeadRes = await runGit(['rev-parse', 'FETCH_HEAD']);
    if (remoteHeadRes.code !== 0 || !remoteHeadRes.stdout) return null;

    const counts = await runGit(['rev-list', '--left-right', '--count', 'HEAD...FETCH_HEAD']);
    if (counts.code !== 0 || !counts.stdout) return null;

    const [aheadRaw, behindRaw] = counts.stdout.split(/\s+/);
    const ahead = Number.parseInt(aheadRaw, 10);
    const behind = Number.parseInt(behindRaw, 10);
    if (!Number.isInteger(ahead) || !Number.isInteger(behind)) return null;

    const status = {
        checkedAt: now,
        localHead: snapshot.localHead,
        remoteHead: remoteHeadRes.stdout.toLowerCase(),
        branchName: snapshot.branchName,
        remoteName: snapshot.remoteName,
        remoteBranch: snapshot.remoteBranch,
        ahead,
        behind,
        hasUpdate: behind > 0,
    };
    writeUpdateCheckCache(status);
    return status;
}

async function maybeShowGitUpdateNotice(command) {
    const commandSet = new Set(['start', 'status', 'doctor', 'skill', 'install-browser']);
    if (!commandSet.has(command)) return;

    const status = await getGitUpdateStatus();
    if (!status || !status.hasUpdate) return;

    const commitWord = status.behind === 1 ? 'commit' : 'commits';
    warning(`Update available: ${status.behind} ${commitWord} behind ${status.remoteName}/${status.remoteBranch}.`);
    info(`Current ${shortSha(status.localHead)} -> latest ${shortSha(status.remoteHead)}.`);
    info(`Run 'git pull --ff-only ${status.remoteName} ${status.remoteBranch}' to update.`);
}

function readEnvValue(key) {
    try {
        const envPath = path.join(rootDir, '.env');
        if (!fs.existsSync(envPath)) return null;
        const lines = fs.readFileSync(envPath, 'utf-8').split('\n');
        for (const rawLine of lines) {
            const line = rawLine.trim();
            if (!line || line.startsWith('#')) continue;
            const idx = line.indexOf('=');
            if (idx === -1) continue;
            const k = line.slice(0, idx).trim();
            if (k !== key) continue;
            let value = line.slice(idx + 1).trim();
            if (
                (value.startsWith('"') && value.endsWith('"')) ||
                (value.startsWith("'") && value.endsWith("'"))
            ) {
                value = value.slice(1, -1);
            }
            return value;
        }
    } catch { }
    return null;
}

function getConfiguredPort(key, fallback) {
    const raw = process.env[key] ?? readEnvValue(key);
    const parsed = Number.parseInt(String(raw ?? ''), 10);
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) return fallback;
    return parsed;
}

async function isPortAvailable(port) {
    return new Promise((resolve) => {
        const server = net.createServer();
        const done = (ok) => {
            try {
                server.removeAllListeners();
                if (server.listening) {
                    server.close(() => resolve(ok));
                    return;
                }
            } catch { }
            resolve(ok);
        };

        server.once('error', () => done(false));
        server.once('listening', () => done(true));
        server.listen({ port, host: '0.0.0.0', exclusive: true });
    });
}

async function findAvailablePort(startPort, maxChecks = 100, reservedPorts = new Set()) {
    let port = startPort;
    for (let i = 0; i < maxChecks; i++, port++) {
        if (reservedPorts.has(port)) continue;
        if (await isPortAvailable(port)) return port;
    }
    throw new Error(`Could not find available port near ${startPort} (checked ${maxChecks} ports).`);
}

function commandExists(cmd) {
    return new Promise((resolve) => {
        try {
            exec(
                process.platform === 'win32' ? `where ${cmd}` : `which ${cmd}`,
                (err) => resolve(!err)
            );
        } catch {
            resolve(false);
        }
    });
}

async function getSystemPython() {
    if (process.platform === 'win32' && await commandExists('py')) return 'py';
    if (await commandExists('python3')) return 'python3';
    if (await commandExists('python')) {

        if (process.platform === 'win32') {
            const ver = await getVersion('python');
            if (ver && ver.toLowerCase().includes('python')) return 'python';
        } else {
            return 'python';
        }
    }

    if (process.platform === 'win32') {
        const candidates = [];

        try {
            for (const entry of fs.readdirSync('C:\\')) {
                if (/^Python\d/i.test(entry)) {
                    candidates.push(path.join('C:\\', entry, 'python.exe'));
                }
            }
        } catch { }

        const localAppData = process.env.LOCALAPPDATA;
        if (localAppData) {
            const pyDir = path.join(localAppData, 'Programs', 'Python');
            try {
                for (const entry of fs.readdirSync(pyDir)) {
                    if (/^Python\d/i.test(entry)) {
                        candidates.push(path.join(pyDir, entry, 'python.exe'));
                    }
                }
            } catch { }
        }
        for (const c of candidates) {
            if (fs.existsSync(c)) return c;
        }
    }
    return 'python';
}

function getVersion(cmd, args = ['--version']) {
    return new Promise((resolve) => {

        exec(`${cmd} ${args.join(' ')}`, (err, stdout, stderr) => {
            if (err) resolve(null);
            else resolve((stdout || stderr).trim().split('\n')[0]);
        });
    });
}


async function checkPlaywrightBrowsers() {
    const venvPython = venvPythonPath();
    const systemPython = await getSystemPython();
    const pythonCmd = fs.existsSync(venvPython) ? venvPython : systemPython;

    return new Promise((resolve) => {
        exec(
            `"${pythonCmd}" -c "from playwright.sync_api import sync_playwright; ` +
            `p = sync_playwright().start(); b = p.chromium.launch(); b.close(); p.stop(); print('OK')"`,
            { timeout: 30000 },
            (err, stdout) => resolve(!err && stdout.includes('OK'))
        );
    });
}

/** Resolve the venv Python binary path for the current platform. */
function venvPythonPath() {
    const isWin = process.platform === 'win32';
    const bin = isWin ? 'Scripts' : 'bin';
    const exe = isWin ? 'python.exe' : 'python';
    return path.join(venvDirPath(), bin, exe);
}


function buildChildEnv() {
    const venvDir = venvDirPath();
    const childEnv = { ...process.env };
    if (fs.existsSync(venvDir)) {
        const isWin = process.platform === 'win32';
        const venvBin = path.join(venvDir, isWin ? 'Scripts' : 'bin');
        childEnv.VIRTUAL_ENV = venvDir;
        childEnv.PATH = `${venvBin}${path.delimiter}${process.env.PATH}`;
        delete childEnv.PYTHONHOME;
    }
    return childEnv;
}


function isWhatsAppEnabled() {
    const raw = readEnvValue('ENABLE_WHATSAPP');
    return String(raw || '').toLowerCase() === 'true';
}


function killProc(proc) {
    if (!proc) return;
    try {
        if (process.platform === 'win32') {

            execSync(`taskkill /T /F /PID ${proc.pid}`, { stdio: 'ignore' });
        } else {

            proc.kill('SIGTERM');
        }
    } catch { /* already gone */ }
}


function killPort(port) {
    return new Promise((resolve) => {
        if (process.platform === 'win32') {
            exec(`netstat -ano | findstr :${port}`, (err, stdout) => {
                if (err || !stdout) return resolve(false);
                const pids = new Set();
                for (const line of stdout.split('\n')) {
                    const part = line.trim();
                    if (!part) continue;
                    const pid = part.split(/\s+/).pop();
                    if (pid && /^\d+$/.test(pid) && pid !== '0') pids.add(pid);
                }
                let pending = pids.size;
                if (pending === 0) return resolve(false);
                for (const pid of pids) {
                    exec(`taskkill /T /F /PID ${pid}`, () => { if (--pending === 0) resolve(true); });
                }
            });
        } else {
            exec(`lsof -ti:${port}`, (err, stdout) => {
                if (err || !stdout.trim()) return resolve(false);
                const pids = stdout.trim().split('\n').filter(Boolean);
                let pending = pids.length;
                if (pending === 0) return resolve(false);
                for (const pid of pids) {
                    exec(`kill -9 ${pid}`, () => { if (--pending === 0) resolve(true); });
                }
            });
        }
    });
}

// ── Commands ───────────────────────────────────────────────────────

async function cmdHelp() {
    process.stdout.write(LOGO);
    console.log(`${colors.lime}${colors.bright}
  🍋 LimeBot CLI
${colors.reset}
  ${colors.bright}Usage:${colors.reset} limebot <command> [options]

  ${colors.bright}Commands:${colors.reset}
    ${colors.cyan}start${colors.reset}            Start LimeBot (backend + frontend)
    ${colors.cyan}stop${colors.reset}             Stop all running LimeBot processes
    ${colors.cyan}status${colors.reset}           Check if LimeBot services are running
    ${colors.cyan}skill${colors.reset}            Manage skills (install, uninstall, update, list)
    ${colors.cyan}doctor${colors.reset}           Diagnose common issues + run tests
    ${colors.cyan}logs${colors.reset}             Show recent logs
    ${colors.cyan}install-browser${colors.reset}  Install Chromium for browser tool (optional)
    ${colors.cyan}autorun${colors.reset}          Configure LimeBot to start automatically
    ${colors.cyan}help${colors.reset}             Show this help message

  ${colors.bright}Start Options:${colors.reset}
    ${colors.gray}--quick, -q${colors.reset}        Skip dependency checks
    ${colors.gray}--backend-only${colors.reset}     Start only the Python backend
    ${colors.gray}--frontend-only${colors.reset}    Start only the web frontend

  ${colors.bright}Autorun Commands:${colors.reset}
    ${colors.dim}limebot autorun enable${colors.reset}
    ${colors.dim}limebot autorun disable${colors.reset}

  ${colors.bright}Skill Commands:${colors.reset}
    ${colors.dim}limebot skill list${colors.reset}
    ${colors.dim}limebot skill install <repo-url> [--ref v2.0]${colors.reset}
    ${colors.dim}limebot skill uninstall <name>${colors.reset}
    ${colors.dim}limebot skill update <name>${colors.reset}

  ${colors.bright}Examples:${colors.reset}
    ${colors.dim}limebot start${colors.reset}
    ${colors.dim}limebot start --quick${colors.reset}
    ${colors.dim}limebot doctor${colors.reset}
    ${colors.dim}limebot doctor --skip-tests${colors.reset}
    ${colors.dim}limebot doctor --skip-perf${colors.reset}
    ${colors.dim}limebot install-browser${colors.reset}
`);
}

async function cmdDoctor(args = []) {
    console.log(`${colors.lime}${colors.bright}\n  🍋 LimeBot Doctor${colors.reset}\n`);
    let issues = 0;

    const runTests = !args.includes('--skip-tests');
    const skipPerf = args.includes('--skip-perf');

    const pythonCmd = await getSystemPython();
    if (await commandExists(pythonCmd)) {
        success(`Python installed: ${await getVersion(pythonCmd)} (${pythonCmd})`);
    } else {
        error('Python not found in PATH'); issues++;
    }

    if (await commandExists('node')) {
        success(`Node.js installed: ${await getVersion('node', ['-v'])}`);
    } else {
        error('Node.js not found in PATH'); issues++;
    }

    if (await commandExists('npm')) {
        success(`npm installed: v${await getVersion('npm', ['-v'])}`);
    } else {
        error('npm not found in PATH'); issues++;
    }

    fs.existsSync(path.join(rootDir, '.env'))
        ? success('.env file exists')
        : warning('.env file not found (run setup first)');

    const venvLayout = resolveVenvLayout();
    fs.existsSync(venvLayout.venvDir)
        ? success(`Python virtual environment exists${venvLayout.usingFallback ? ` (${venvLayout.venvDir})` : ''}`)
        : warning(`Python virtual environment not created (will be created on first start${venvLayout.usingFallback ? ` at ${venvLayout.venvDir}` : ''})`);

    fs.existsSync(path.join(rootDir, 'web', 'node_modules'))
        ? success('Frontend dependencies installed')
        : warning('Frontend dependencies not installed (will be installed on first start)');

    console.log('');
    info('Checking ports...');
    const backendPort = getConfiguredPort('WEB_PORT', 8000);
    const frontendPort = getConfiguredPort('FRONTEND_PORT', 5173);
    for (const [port, label] of [[backendPort, 'backend'], [3000, 'WhatsApp bridge'], [frontendPort, 'frontend']]) {
        (await isPortReachable(port))
            ? warning(`Port ${port} is in use (${label} may already be running)`)
            : success(`Port ${port} is available`);
    }

    console.log('');
    info('Checking optional features...');
    (await checkPlaywrightBrowsers())
        ? success('Browser tool: Chromium installed')
        : info(`Browser tool: Not installed ${colors.dim}(run ${colors.cyan}limebot install-browser${colors.dim} to enable)${colors.reset}`);

    if (runTests) {
        console.log('');
        info('Running tests...');
        const venvPython = venvPythonPath();
        const systemPython = await getSystemPython();
        const py = fs.existsSync(venvPython) ? venvPython : systemPython;
        const env = { ...process.env };
        if (skipPerf) env.LIMEBOT_SKIP_PERF = '1';

        const testExit = await new Promise((resolve) => {
            const proc = spawn(py, ['-m', 'unittest', 'discover', '-s', 'tests'], {
                cwd: rootDir,
                stdio: 'inherit',
                env,
            });
            proc.on('close', (code) => resolve(code ?? 1));
            proc.on('error', () => resolve(1));
        });

        if (testExit === 0) {
            success('Tests passed');
        } else {
            error('Tests failed');
            issues++;
        }
    } else {
        info(`Skipping tests ${colors.dim}(--skip-tests)${colors.reset}`);
    }

    console.log('');
    issues === 0
        ? log(colors.green, `  ${colors.bright}All checks passed!${colors.reset} Run ${colors.cyan}limebot start${colors.reset} to launch.`)
        : log(colors.yellow, `  ${colors.bright}${issues} issue(s) found.${colors.reset} Please resolve before starting.`);
    process.exitCode = issues === 0 ? 0 : 1;
    console.log('');
}

async function cmdInstallBrowser() {
    console.log(`${colors.lime}${colors.bright}\n  🍋 Browser Tool Setup${colors.reset}\n`);

    info('Checking if Chromium is already installed...');
    if (await checkPlaywrightBrowsers()) {
        success('Chromium is already installed and ready.');
        return;
    }

    info('Installing Chromium via Playwright...');
    console.log(`  ${colors.dim}This may take a few minutes...${colors.reset}\n`);

    const venvPython = venvPythonPath();
    const systemPython = await getSystemPython();
    const pythonCmd = fs.existsSync(venvPython) ? venvPython : systemPython;

    await new Promise((resolve) => {
        const proc = spawn(pythonCmd, ['-m', 'playwright', 'install', 'chromium'], {
            cwd: rootDir, stdio: 'inherit',
        });
        proc.on('close', (code) => {
            console.log('');
            code === 0
                ? success('Chromium installed successfully!')
                : error(`Installation failed (exit ${code}). Try: playwright install chromium`);
            resolve();
        });
    });
}

async function cmdSkill(args) {
    const subCommand = args[0]?.toLowerCase() || 'list';

    // FIX: allowlist subcommands to prevent shell injection via user-supplied args
    const VALID_SUBCMDS = new Set(['list', 'install', 'uninstall', 'update']);
    if (!VALID_SUBCMDS.has(subCommand)) {
        error(`Unknown skill subcommand '${subCommand}'. Valid: ${[...VALID_SUBCMDS].join(', ')}`);
        process.exit(1);
    }

    const venvPython = venvPythonPath();
    const systemPython = await getSystemPython();
    const pythonCmd = fs.existsSync(venvPython) ? venvPython : systemPython;

    // FIX: pass args as array (no shell:true) so user input can't be interpreted as shell syntax
    return new Promise((resolve) => {
        const proc = spawn(pythonCmd, ['-m', 'core.skill_installer', subCommand, ...args.slice(1)], {
            cwd: rootDir,
            stdio: 'inherit',
            // shell: false (default) — intentionally NOT using shell to avoid injection
        });
        proc.on('close', (code) => {
            if (code !== 0 && subCommand !== 'list') {
                console.log(`\n  ${colors.dim}Run ${colors.cyan}limebot skill${colors.dim} for usage.${colors.reset}\n`);
            }
            resolve();
        });
        proc.on('error', (err) => {
            error(`Failed to run skill installer: ${err.message}`);
            resolve();
        });
    });
}

async function cmdStatus() {
    console.log(`${colors.lime}${colors.bright}\n  🍋 LimeBot Status${colors.reset}\n`);

    const backendPort = getConfiguredPort('WEB_PORT', 8000);
    const frontendPort = getConfiguredPort('FRONTEND_PORT', 5173);
    const [backendUp, bridgeUp, frontendUp] = await Promise.all([
        isPortReachable(backendPort), isPortReachable(3000), isPortReachable(frontendPort),
    ]);

    backendUp ? success(`Backend is running (port ${backendPort})`) : info('Backend is not running');
    bridgeUp ? success('WhatsApp bridge is running (port 3000)') : info('WhatsApp bridge is not running');
    frontendUp ? success(`Frontend is running (port ${frontendPort})`) : info('Frontend is not running');

    console.log('');
    if (backendUp && frontendUp && bridgeUp) {
        log(colors.green, `  LimeBot is fully operational! Open ${colors.cyan}http://localhost:${frontendPort}${colors.reset}`);
    } else if (!backendUp && !frontendUp && !bridgeUp) {
        log(colors.gray, `  LimeBot is not running. Use ${colors.cyan}limebot start${colors.reset} to launch.`);
    } else {
        log(colors.yellow, '  LimeBot is partially running.');
    }
    console.log('');
}

async function cmdLogs(args) {
    const lines = parseInt(args[0]) || 50;
    console.log(`${colors.lime}${colors.bright}\n  🍋 LimeBot Logs (last ${lines} lines)${colors.reset}\n`);

    const logFile = path.join(rootDir, 'logs', 'limebot.log');
    if (!fs.existsSync(logFile)) {
        info('No log file found. Start LimeBot to generate logs.');
        console.log('');
        return;
    }

    // FIX: read from end of file instead of loading everything into memory
    try {
        const stat = fs.statSync(logFile);
        const chunkSize = Math.min(stat.size, 128 * 1024); // read up to 128 KB from end
        const fd = fs.openSync(logFile, 'r');
        const buf = Buffer.alloc(chunkSize);
        fs.readSync(fd, buf, 0, chunkSize, stat.size - chunkSize);
        fs.closeSync(fd);

        const recent = buf.toString('utf-8').split('\n').filter(l => l.trim()).slice(-lines);
        if (recent.length === 0) {
            info('Log file is empty.');
        } else {
            console.log(colors.gray + '  --- Recent Logs ---' + colors.reset);
            for (const line of recent) console.log(`  ${line}`);
            console.log(colors.gray + '  --- End of Logs ---' + colors.reset);
        }
    } catch (e) {
        error(`Failed to read log file: ${e.message}`);
    }
    console.log('');
}

async function cmdStop() {
    console.log(`${colors.lime}${colors.bright}\n  🍋 Stopping LimeBot${colors.reset}\n`);

    const backendPort = getConfiguredPort('WEB_PORT', 8000);
    const frontendPort = getConfiguredPort('FRONTEND_PORT', 5173);

    // 1. Stop primary services by port (standard behavior)
    const primaryPorts = [
        [backendPort, 'backend'],
        [frontendPort, 'frontend'],
        [3000, 'WhatsApp bridge']
    ];

    let anyKilled = false;
    for (const [port, label] of primaryPorts) {
        const killed = await killPort(port);
        killed ? success(`Stopped ${label} (port ${port})`) : info(`${label} was not running`);
        if (killed) anyKilled = true;
    }




    if (process.platform === 'win32') {
        try {
            // Aggressive pattern-based cleanup for Windows orphans
            // Using 'call terminate' is often more reliable than 'delete'
            execSync('wmic process where "commandline like \'%main.py%\' or commandline like \'%bridge/dist/index.js%\' or commandline like \'%vite%\'" call terminate', { stdio: 'ignore' });
        } catch (e) { /* ignore */ }
    } else {

        try {
            execSync('pkill -f "main.py"', { stdio: 'ignore' });
            execSync('pkill -f "bridge/dist/index.js"', { stdio: 'ignore' });
            execSync('pkill -f "vite"', { stdio: 'ignore' });
            anyKilled = true;
        } catch (e) { }
    }

    if (!anyKilled) {
        info('No running LimeBot processes found.');
    } else {
        success('Deep clean complete: All LimeBot instances terminated.');
    }

    // Give OS time to release ports
    await sleep(500);
    console.log('');
}

async function cmdAutorun(args) {
    const action = args[0]?.toLowerCase();
    if (action !== 'enable' && action !== 'disable') {
        error("Usage: limebot autorun <enable|disable>");
        process.exit(1);
    }

    console.log(`${colors.lime}${colors.bright}\n  🍋 LimeBot Autorun Configuration${colors.reset}\n`);

    if (process.platform === 'win32') {
        const taskName = "LimeBotGateway";
        const gatewayPath = path.join(rootDir, 'bin', 'gateway.cmd');

        if (action === 'enable') {
            info(`Creating Windows Scheduled Task: ${taskName}`);
            try {
                execSync(`schtasks /create /tn "${taskName}" /tr "${gatewayPath}" /sc onlogon /f`, { stdio: 'pipe' });
                success("Autorun enabled! LimeBot will start whenever you log in.");
            } catch (e) {
                const errorLog = (e.stdout?.toString() || "") + (e.stderr?.toString() || "");
                if (errorLog.toLowerCase().includes('acceso denegado') || errorLog.toLowerCase().includes('access is denied') || e.status === 1) {
                    error("Access Denied: Creating a Scheduled Task requires Administrator privileges.");
                    log(colors.yellow, "  Please restart your terminal (PowerShell/CMD) as Administrator and run the command again.");
                } else {
                    error(`Failed to enable autorun: ${e.message}`);
                }
            }
        } else {
            info(`Removing Windows Scheduled Task: ${taskName}`);
            try {
                execSync(`schtasks /delete /tn "${taskName}" /f`, { stdio: 'inherit' });
                success("Autorun disabled (Scheduled Task removed).");
            } catch (e) {
                error(`Could not disable autorun: ${e.message}`);
                log(colors.yellow, "  Note: You may need to run this command as Administrator.");
            }
        }
    } else if (process.platform === 'darwin') {
        const label = "com.limebot.gateway";
        const plistPath = path.join(process.env.HOME, 'Library', 'LaunchAgents', `${label}.plist`);
        const gatewayPath = path.join(rootDir, 'bin', 'gateway.sh');

        if (action === 'enable') {
            info(`Creating macOS LaunchAgent: ${label}`);
            const plistContent = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${gatewayPath}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>${rootDir}</string>
    <key>StandardOutPath</key>
    <string>${path.join(rootDir, 'logs', 'gateway.log')}</string>
    <key>StandardErrorPath</key>
    <string>${path.join(rootDir, 'logs', 'gateway.log')}</string>
</dict>
</plist>`;
            try {
                const launchAgentsDir = path.join(process.env.HOME, 'Library', 'LaunchAgents');
                if (!fs.existsSync(launchAgentsDir)) fs.mkdirSync(launchAgentsDir, { recursive: true });
                fs.writeFileSync(plistPath, plistContent);
                execSync(`launchctl load "${plistPath}"`, { stdio: 'inherit' });
                success("Autorun enabled! LimeBot will start automatically on login.");
            } catch (e) {
                error(`Failed to enable autorun: ${e.message}`);
            }
        } else {
            info(`Removing macOS LaunchAgent: ${label}`);
            try {
                if (fs.existsSync(plistPath)) {
                    execSync(`launchctl unload "${plistPath}"`, { stdio: 'inherit' });
                    fs.unlinkSync(plistPath);
                }
                success("Autorun disabled.");
            } catch (e) {
                error(`Failed to disable autorun: ${e.message}`);
            }
        }
    } else {
        // Linux (systemd)
        const serviceName = "limebot.service";
        const homeDir = process.env.HOME;
        const systemdDir = path.join(homeDir, '.config', 'systemd', 'user');
        const servicePath = path.join(systemdDir, serviceName);
        const gatewayPath = path.join(rootDir, 'bin', 'gateway.sh');

        if (action === 'enable') {
            info(`Creating systemd user service: ${serviceName}`);
            const serviceContent = `[Unit]
Description=LimeBot Gateway
After=network.target

[Service]
ExecStart=${gatewayPath}
Restart=always

[Install]
WantedBy=default.target
`;
            try {
                if (!fs.existsSync(systemdDir)) fs.mkdirSync(systemdDir, { recursive: true });
                fs.writeFileSync(servicePath, serviceContent);
                execSync('systemctl --user daemon-reload', { stdio: 'inherit' });
                execSync('systemctl --user enable limebot', { stdio: 'inherit' });
                execSync('systemctl --user start limebot', { stdio: 'inherit' });
                success("Autorun enabled! LimeBot is now running as a systemd user service.");
            } catch (e) {
                error(`Failed to enable autorun: ${e.message}`);
            }
        } else {
            info(`Removing systemd user service: ${serviceName}`);
            try {
                execSync('systemctl --user stop limebot', { stdio: 'inherit' });
                execSync('systemctl --user disable limebot', { stdio: 'inherit' });
                if (fs.existsSync(servicePath)) fs.unlinkSync(servicePath);
                execSync('systemctl --user daemon-reload', { stdio: 'inherit' });
                success("Autorun disabled.");
            } catch (e) {
                error(`Failed to disable autorun: ${e.message}`);
            }
        }
    }
    console.log('');
}

async function cmdStart(args) {
    process.stdout.write(LOGO);
    const quickMode = args.includes('--quick') || args.includes('-q');
    const backendOnly = args.includes('--backend-only');
    const frontendOnly = args.includes('--frontend-only');

    if (backendOnly && frontendOnly) {
        error('--backend-only and --frontend-only cannot be used together.');
        process.exit(1);
    }

    console.log(`${colors.lime}${colors.bright}\n  🍋 Starting LimeBot${colors.reset}\n`);
    log(colors.gray, `  ${colors.bright}Tip:${colors.reset} Run ${colors.cyan}npm run lime-bot help${colors.reset} to see all available CLI commands.`);
    if (quickMode) info('Quick mode: Skipping dependency checks...');

    const envFile = path.join(rootDir, '.env');
    const isConfigured = fs.existsSync(envFile);
    if (!isConfigured) warning('Initial configuration not found. Setup wizard will open.');

    const venvLayout = resolveVenvLayout();
    const venvDir = venvLayout.venvDir;
    const venvPython = venvPythonPath();
    const childEnv = buildChildEnv();
    const configuredBackendPort = getConfiguredPort('WEB_PORT', 8000);
    const configuredFrontendPort = getConfiguredPort('FRONTEND_PORT', 5173);
    const backendPort = frontendOnly
        ? configuredBackendPort
        : await findAvailablePort(configuredBackendPort, 100);
    const frontendReservedPorts = new Set([backendPort]);
    const frontendPort = backendOnly
        ? configuredFrontendPort
        : await findAvailablePort(configuredFrontendPort, 100, frontendReservedPorts);

    if (backendPort !== configuredBackendPort) {
        warning(`Backend port ${configuredBackendPort} is busy. Using ${backendPort}.`);
    }
    if (frontendPort !== configuredFrontendPort) {
        warning(`Frontend port ${configuredFrontendPort} is busy. Using ${frontendPort}.`);
    }
    if (process.platform === 'win32' && venvLayout.projectedMaxPathLength >= WIN_MAX_PATH_SAFE) {
        warning(`\n====== WINDOWS PATH LIMIT WARNING ======`);
        warning(`Your project is located at a very long path (${venvLayout.projectedMaxPathLength}/${WIN_MAX_PATH_SAFE} chars max).`);
        warning(`Installing Python packages may fail with "[Errno 2] No such file or directory".\n`);
        info(`To fix this, choose ONE of the following:`);
        info(`1. Move your project to a shorter path (e.g. C:\\Bots\\LimeBot-OS)`);
        info(`2. Enable Long Paths in Windows by running this in an Administrator PowerShell:`);
        console.log(`   ${colors.cyan}New-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force${colors.reset}`);
        warning(`========================================\n`);
    }

    childEnv.WEB_PORT = String(backendPort);
    childEnv.PORT = String(backendPort);
    childEnv.VITE_DEV_SERVER_PORT = String(frontendPort);
    childEnv.VITE_BACKEND_URL = `http://127.0.0.1:${backendPort}`;
    childEnv.VITE_BACKEND_WS_URL = `ws://127.0.0.1:${backendPort}`;

    // ── Dependency installation ──────────────────────────────────

    if (!quickMode) {
        const nodeModules = path.join(rootDir, 'node_modules');
        const webModules = path.join(rootDir, 'web', 'node_modules');
        const bridgeModules = path.join(rootDir, 'bridge', 'node_modules');
        const needsBridge = !backendOnly && !fs.existsSync(bridgeModules);

        if (!fs.existsSync(nodeModules) || !fs.existsSync(webModules) || needsBridge) {
            await runWithSpinner('Installing NPM dependencies (root & workspaces)...', () => {
                return new Promise((resolve, reject) => {
                    const p = spawn('npm', ['install'], { cwd: rootDir, shell: true, stdio: 'pipe', env: childEnv });
                    let stderr = '';
                    p.stderr.on('data', (d) => { stderr += d.toString(); });
                    p.on('close', (code) => code === 0 ? resolve() : reject(new Error(stderr || `npm install exited ${code}`)));
                });
            });
        } else {
            success('NPM dependencies up to date.');
        }

        // FIX: venv is for the backend — only needed when NOT frontend-only
        if (!frontendOnly) {
            if (!fs.existsSync(venvDir)) {
                await runWithSpinner('Creating Python virtual environment...', () => {
                    return new Promise((resolve, reject) => {
                        const systemPython = getSystemPython();
                        systemPython.then(py => {
                            const p = spawn(py, ['-m', 'venv', venvDir], { cwd: rootDir, shell: true, stdio: 'pipe' });
                            p.on('close', (code) => code === 0 ? resolve() : reject(new Error(`venv creation failed (code ${code})`)));
                        });
                    });
                });
            }

            const systemPython = await getSystemPython();
            const pythonCmd = fs.existsSync(venvPython) ? venvPython : systemPython;

            await runWithSpinner('Checking backend requirements...', () => {
                return new Promise((resolve, reject) => {
                    const p = spawn(pythonCmd, ['-m', 'pip', 'install', '-r', 'requirements.txt', '--quiet'], {
                        cwd: rootDir, shell: true, stdio: 'pipe', env: childEnv,
                    });
                    let stderr = '';
                    p.stderr.on('data', (d) => { stderr += d.toString(); });
                    p.on('close', (code) => code === 0 ? resolve() : reject(new Error(stderr || `pip install exited ${code}`)));
                });
            });
        }
    }

    // ── Process management ────────────────────────────────────────

    let backendProc = null;
    let frontendProc = null;
    let bridgeProc = null;

    const startBackend = async () => {
        // FIX: removed duplicate info('Starting backend...') line
        if (backendProc) {
            info('Stopping existing backend...');
            killProc(backendProc);
            backendProc = null;

            info(`Waiting for port ${backendPort} to be released...`);
            for (let i = 0; i < 10; i++) {
                if (!await isPortReachable(backendPort)) break;
                await sleep(500);
            }
            (await isPortReachable(backendPort))
                ? warning(`Port ${backendPort} still in use — startup might fail.`)
                : success(`Port ${backendPort} is free.`);
        }

        info('Starting backend...');
        const systemPython = await getSystemPython();
        const cmd = fs.existsSync(venvPython) ? venvPython : systemPython;

        backendProc = spawn(cmd, ['main.py'], { cwd: rootDir, shell: true, stdio: 'inherit', env: childEnv });
        backendProc.on('error', (err) => error(`Backend failed to start: ${err.message}`));
        backendProc.on('exit', (code) => {
            if (code !== null && code !== 0) error(`Backend exited with code ${code}`);
            else info('Backend stopped.');
        });
    };

    const updateBridgeState = async () => {
        const enabled = isWhatsAppEnabled();

        if (enabled && !bridgeProc) {
            const bridgeDir = path.join(rootDir, 'bridge');
            if (fs.existsSync(bridgeDir) && !fs.existsSync(path.join(bridgeDir, 'dist', 'index.js'))) {
                await runWithSpinner('Building WhatsApp bridge...', () => {
                    return new Promise((resolve, reject) => {
                        const p = spawn('npm', ['run', 'build'], { cwd: bridgeDir, shell: true, stdio: 'pipe', env: childEnv });
                        p.on('close', (code) => code === 0 ? resolve() : reject(new Error(`bridge build failed (code ${code})`)));
                    });
                });
            }
            info('WhatsApp enabled. Starting bridge...');

            bridgeProc = spawn('node', ['dist/index.js'], {
                cwd: path.join(rootDir, 'bridge'), shell: true, stdio: 'inherit', env: childEnv,
            });
            bridgeProc.on('error', (err) => { error(`WhatsApp bridge error: ${err.message}`); bridgeProc = null; });
            bridgeProc.on('exit', () => { bridgeProc = null; });

            if (backendProc) {
                info('Restarting backend to connect to bridge...');
                await startBackend();
            }

        } else if (!enabled && bridgeProc) {
            info('WhatsApp disabled. Stopping bridge...');
            killProc(bridgeProc);
            bridgeProc = null;
        }
    };

    // ── Startup sequence ──────────────────────────────────────────

    await updateBridgeState();
    if (!frontendOnly && !backendProc) await startBackend();
    let debounceTimer;
    try {
        fs.watch(path.join(rootDir, '.env'), () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(updateBridgeState, 1000);
        });
        info('Watching .env for configuration changes...');
    } catch {
        warning('Could not watch .env — restart to apply config changes.');
    }

    // ── Wait for backend before starting frontend ─────────────────
    // Vite proxies /api and /ws immediately on startup. If the backend
    // isn't listening yet, every request gets ECONNREFUSED. We wait up
    // to 30 s for the backend port before spawning Vite so the proxy is warm.
    if (!frontendOnly && !backendOnly) {
        const spinner = new Spinner('Waiting for backend to be ready...');
        spinner.start();
        const backendReady = await waitForServer(backendPort, 30);
        if (backendReady) {
            spinner.stop('Backend is ready.');
        } else {
            spinner.stop('Backend did not respond in 30 s — starting frontend anyway.', false);
        }
    }

    let actualFrontendPort = frontendPort;

    if (!backendOnly) {
        info('Starting frontend dev server...');
        frontendProc = spawn('npm', ['run', 'dev'], {
            cwd: path.join(rootDir, 'web'), shell: true, stdio: 'pipe', env: childEnv,
        });
        frontendProc.on('error', (err) => error(`Frontend failed to start: ${err.message}`));


        const portPattern = /localhost:(\d+)/;
        for (const stream of [frontendProc.stdout, frontendProc.stderr]) {
            if (!stream) continue;
            stream.on('data', (data) => {
                const text = data.toString();
                process.stdout.write(text);
                const match = text.match(portPattern);
                if (match) actualFrontendPort = parseInt(match[1], 10);
            });
        }
    }

    if (!backendOnly) {
        info('Waiting for UI to be ready...');
        // Wait briefly for Vite to print its port, then poll the detected port
        await sleep(3000);
        if (await waitForServer(actualFrontendPort)) {
            const url = isConfigured
                ? `http://localhost:${actualFrontendPort}`
                : `http://localhost:${actualFrontendPort}/setup`;
            success(`LimeBot is ready at ${url}`);
            openBrowser(url);
        } else {
            error('Timeout waiting for UI. Check logs above.');
        }
    } else {
        info('Backend-only mode. Waiting for backend...');
        (await waitForServer(backendPort))
            ? success(`Backend ready on port ${backendPort}`)
            : error('Backend did not start in time. Check logs.');
    }

    let cleaned = false;
    const cleanup = () => {
        if (cleaned) return;
        cleaned = true;

        log(colors.gray, '\n  Stopping LimeBot...');
        killProc(backendProc);
        killProc(frontendProc);
        killProc(bridgeProc);
        process.exitCode = 0;
    };
    process.once('SIGINT', cleanup);
    process.once('SIGTERM', cleanup);
    process.once('exit', cleanup);
}

// ── Entry point ───────────────────────────────────────────────────

async function main() {
    const args = process.argv.slice(2);
    const command = args[0]?.toLowerCase() || 'help';
    await maybeShowGitUpdateNotice(command);

    switch (command) {
        case 'start': await cmdStart(args.slice(1)); break;
        case 'stop': await cmdStop(); break;
        case 'status': await cmdStatus(); break;
        case 'doctor': await cmdDoctor(args.slice(1)); break;
        case 'logs': await cmdLogs(args.slice(1)); break;
        case 'install-browser': await cmdInstallBrowser(); break;
        case 'skill': await cmdSkill(args.slice(1)); break;
        case 'autorun': await cmdAutorun(args.slice(1)); break;
        case 'help': case '--help': case '-h':
            await cmdHelp(); break;
        default:
            error(`Unknown command '${command}'`);
            console.log(`  Run ${colors.cyan}limebot help${colors.reset} for available commands.\n`);
            process.exit(1);
    }
}

main().catch(err => {
    error(`Fatal: ${err.message}`);
    process.exit(1);
});


