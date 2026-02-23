#!/usr/bin/env node

import { spawn, exec, execSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';
import net from 'net';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');

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

function isPortReachable(port) {
    return new Promise((resolve) => {
        const socket = new net.Socket();
        const onError = () => { socket.destroy(); resolve(false); };
        socket.setTimeout(1000);
        socket.on('error', onError);
        socket.on('timeout', onError);
        socket.connect(port, '127.0.0.1', () => { socket.end(); resolve(true); });
    });
}

function openBrowser(url) {

    const cmd = process.platform === 'darwin' ? `open "${url}"`
        : process.platform === 'win32' ? `start "" "${url}"`
            : `xdg-open "${url}"`;
    exec(cmd);
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

function commandExists(cmd) {
    return new Promise((resolve) => {
        exec(process.platform === 'win32' ? `where ${cmd}` : `which ${cmd}`,
            (err) => resolve(!err));
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
    return path.join(rootDir, '.venv', bin, exe);
}


function buildChildEnv() {
    const venvDir = path.join(rootDir, '.venv');
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
    try {
        const envPath = path.join(rootDir, '.env');
        if (!fs.existsSync(envPath)) return false;
        for (const line of fs.readFileSync(envPath, 'utf-8').split('\n')) {
            const trimmed = line.trim();
            // FIX: skip comments explicitly before regex matching
            if (trimmed.startsWith('#')) continue;
            const m = trimmed.match(/^ENABLE_WHATSAPP\s*=\s*(true|false)/i);
            if (m) return m[1].toLowerCase() === 'true';
        }
        return false;
    } catch {
        return false;
    }
}


function killProc(proc) {
    if (!proc) return;
    try {
        if (process.platform === 'win32') {
            execSync(`taskkill /PID ${proc.pid} /F`, { stdio: 'ignore' });
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
                    if (!line.includes('LISTENING')) continue;
                    const pid = line.trim().split(/\s+/).pop();
                    if (pid && /^\d+$/.test(pid) && pid !== '0') pids.add(pid);
                }
                let pending = pids.size;
                if (pending === 0) return resolve(false);
                for (const pid of pids) {
                    exec(`taskkill /PID ${pid} /F`, () => { if (--pending === 0) resolve(true); });
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
    ${colors.cyan}doctor${colors.reset}           Diagnose common issues
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
    ${colors.dim}limebot install-browser${colors.reset}
`);
}

async function cmdDoctor() {
    console.log(`${colors.lime}${colors.bright}\n  🍋 LimeBot Doctor${colors.reset}\n`);
    let issues = 0;

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

    fs.existsSync(path.join(rootDir, '.venv'))
        ? success('Python virtual environment exists')
        : warning('Python virtual environment not created (will be created on first start)');

    fs.existsSync(path.join(rootDir, 'web', 'node_modules'))
        ? success('Frontend dependencies installed')
        : warning('Frontend dependencies not installed (will be installed on first start)');

    console.log('');
    info('Checking ports...');
    for (const [port, label] of [[8000, 'backend'], [3000, 'WhatsApp bridge'], [5173, 'frontend']]) {
        (await isPortReachable(port))
            ? warning(`Port ${port} is in use (${label} may already be running)`)
            : success(`Port ${port} is available`);
    }

    console.log('');
    info('Checking optional features...');
    (await checkPlaywrightBrowsers())
        ? success('Browser tool: Chromium installed')
        : info(`Browser tool: Not installed ${colors.dim}(run ${colors.cyan}limebot install-browser${colors.dim} to enable)${colors.reset}`);

    console.log('');
    issues === 0
        ? log(colors.green, `  ${colors.bright}All checks passed!${colors.reset} Run ${colors.cyan}limebot start${colors.reset} to launch.`)
        : log(colors.yellow, `  ${colors.bright}${issues} issue(s) found.${colors.reset} Please resolve before starting.`);
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

    const [backendUp, bridgeUp, frontendUp] = await Promise.all([
        isPortReachable(8000), isPortReachable(3000), isPortReachable(5173),
    ]);

    backendUp ? success('Backend is running (port 8000)') : info('Backend is not running');
    bridgeUp ? success('WhatsApp bridge is running (port 3000)') : info('WhatsApp bridge is not running');
    frontendUp ? success('Frontend is running (port 5173)') : info('Frontend is not running');

    console.log('');
    if (backendUp && frontendUp && bridgeUp) {
        log(colors.green, `  LimeBot is fully operational! Open ${colors.cyan}http://localhost:5173${colors.reset}`);
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

    
    const primaryPorts = [
        [8000, 'backend'],
        [5173, 'frontend'],
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
           
            execSync('wmic process where "commandline like \'%main.py%\'" delete', { stdio: 'ignore' });
          
            execSync('wmic process where "commandline like \'%bridge/dist/index.js%\' or commandline like \'%vite%\' or commandline like \'%bin/cli.js%\'" delete', { stdio: 'ignore' });
            anyKilled = true; 
        } catch (e) {  }
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
    } else {

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

    const venvDir = path.join(rootDir, '.venv');
    const venvPython = venvPythonPath();
    const childEnv = buildChildEnv();

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
                            const p = spawn(py, ['-m', 'venv', '.venv'], { cwd: rootDir, shell: true, stdio: 'pipe' });
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

            info('Waiting for port 8000 to be released...');
            for (let i = 0; i < 10; i++) {
                if (!await isPortReachable(8000)) break;
                await sleep(500);
            }
            (await isPortReachable(8000))
                ? warning('Port 8000 still in use — startup might fail.')
                : success('Port 8000 is free.');
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

    // FIX: check bridge state after backend is started so the restart-on-enable path works correctly
    if (!frontendOnly) await startBackend();

    await updateBridgeState();

    // Watch .env for live changes
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

    if (!backendOnly) {
        info('Starting frontend dev server...');
        frontendProc = spawn('npm', ['run', 'dev'], {
            cwd: path.join(rootDir, 'web'), shell: true, stdio: 'inherit', env: childEnv,
        });
        frontendProc.on('error', (err) => error(`Frontend failed to start: ${err.message}`));
    }

    if (!backendOnly) {
        info('Waiting for UI to be ready...');
        if (await waitForServer(5173)) {
            const url = isConfigured ? 'http://localhost:5173' : 'http://localhost:5173/setup';
            success(`LimeBot is ready at ${url}`);
            openBrowser(url);
        } else {
            error('Timeout waiting for UI. Check logs above.');
        }
    } else {
        info('Backend-only mode. Waiting for backend...');
        (await waitForServer(8000))
            ? success('Backend ready on port 8000')
            : error('Backend did not start in time. Check logs.');
    }

    // FIX: register cleanup once, not inside a helper that could be called multiple times
    const cleanup = () => {
        log(colors.gray, '\n  Stopping LimeBot...');
        killProc(backendProc);
        killProc(frontendProc);
        killProc(bridgeProc);
        // FIX: Don't use process.exit() during SIGINT — on Windows it can
        // exit with code 2 (STATUS_CONTROL_C_EXIT) instead of 0, which
        // causes some terminals (e.g. VS Code) to show an error and close.
        // Setting exitCode and letting Node drain naturally avoids this.
        process.exitCode = 0;
    };
    process.once('SIGINT', cleanup);
    process.once('SIGTERM', cleanup);
}

// ── Entry point ───────────────────────────────────────────────────

async function main() {
    const args = process.argv.slice(2);
    const command = args[0]?.toLowerCase() || 'help';

    switch (command) {
        case 'start': await cmdStart(args.slice(1)); break;
        case 'stop': await cmdStop(); break;
        case 'status': await cmdStatus(); break;
        case 'doctor': await cmdDoctor(); break;
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
