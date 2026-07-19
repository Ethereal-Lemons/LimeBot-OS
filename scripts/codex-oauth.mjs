import { exec } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import readline from 'readline/promises';
import { fileURLToPath } from 'url';

import { openaiCodexProvider } from '@earendil-works/pi-ai/providers/openai-codex';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..');
const providerId = 'openai-codex';
const storePath = path.join(rootDir, 'data', 'oauth_profiles.json');
const codexOAuth = openaiCodexProvider().auth.oauth;

function emptyStore() {
    return { version: 1, providers: {} };
}

function readStore() {
    try {
        if (!fs.existsSync(storePath)) return emptyStore();
        const parsed = JSON.parse(fs.readFileSync(storePath, 'utf-8'));
        if (!parsed || typeof parsed !== 'object') return emptyStore();
        if (!parsed.providers || typeof parsed.providers !== 'object') {
            return { version: 1, providers: {} };
        }
        return parsed;
    } catch {
        return emptyStore();
    }
}

function writeStore(store) {
    fs.mkdirSync(path.dirname(storePath), { recursive: true });
    fs.writeFileSync(storePath, JSON.stringify(store, null, 2), 'utf-8');
}

function normalizeString(value) {
    const trimmed = String(value ?? '').trim();
    return trimmed || null;
}

function decodeJwtPayload(token) {
    try {
        const [, payload] = String(token || '').split('.');
        if (!payload) return {};
        const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
        const padded = normalized + '='.repeat((4 - (normalized.length % 4 || 4)) % 4);
        return JSON.parse(Buffer.from(padded, 'base64').toString('utf-8'));
    } catch {
        return {};
    }
}

function resolveJwtExpiry(token) {
    const exp = Number.parseInt(String(decodeJwtPayload(token).exp ?? ''), 10);
    return Number.isFinite(exp) ? exp : 0;
}

function normalizeExpiry(value, fallback = 0) {
    const parsed = Number.parseInt(String(value ?? ''), 10);
    if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
    // Some OAuth helpers return milliseconds; normalize to Unix seconds.
    if (parsed > 100000000000) {
        return Math.floor(parsed / 1000);
    }
    return parsed;
}

function resolveJwtIdentity(token) {
    const payload = decodeJwtPayload(token);
    return {
        email: normalizeString(
            payload.email || payload.preferred_username || payload.upn
        ),
        displayName: normalizeString(
            payload.name || payload.given_name || payload.nickname
        ),
        accountId: normalizeString(
            payload.account_id || payload.accountId || payload.sub
        ),
    };
}

function normalizeCredential(raw) {
    const credential = {
        ...raw,
        type: 'oauth',
        provider: providerId,
    };
    credential.access = normalizeString(credential.access);
    credential.refresh = normalizeString(credential.refresh);
    credential.email = normalizeString(credential.email);
    credential.displayName = normalizeString(credential.displayName);
    credential.accountId = normalizeString(credential.accountId);
    credential.idToken = normalizeString(credential.idToken);
    credential.expires = normalizeExpiry(
        credential.expires,
        resolveJwtExpiry(credential.access),
    );

    const inferredIdentity = resolveJwtIdentity(credential.access);
    credential.email = credential.email || inferredIdentity.email;
    credential.displayName = credential.displayName || inferredIdentity.displayName;
    credential.accountId = credential.accountId || inferredIdentity.accountId;
    return credential;
}

function storeCredential(credential, source, metadata = {}) {
    const store = readStore();
    store.providers[providerId] = {
        credential: normalizeCredential(credential),
        source,
        updatedAt: new Date().toISOString(),
        ...metadata,
    };
    writeStore(store);
    return summarizeStatus(store.providers[providerId]);
}

function removeCredential() {
    const store = readStore();
    const existed = Boolean(store.providers?.[providerId]);
    if (store.providers) delete store.providers[providerId];
    writeStore(store);
    return existed;
}

function getCredentialEntry() {
    const store = readStore();
    const entry = store.providers?.[providerId];
    if (!entry || typeof entry !== 'object') return null;
    if (!entry.credential || typeof entry.credential !== 'object') return null;
    return {
        ...entry,
        credential: normalizeCredential(entry.credential),
    };
}

function summarizeStatus(entry = null) {
    const credential = entry?.credential || null;
    const expires = normalizeExpiry(credential?.expires, 0);
    const expiresAt = expires > 0
        ? new Date(expires * 1000).toISOString()
        : null;
    return {
        configured: Boolean(credential?.access && credential?.refresh),
        provider: providerId,
        email: normalizeString(credential?.email),
        displayName: normalizeString(credential?.displayName),
        accountId: normalizeString(credential?.accountId),
        source: normalizeString(entry?.source),
        importedFrom: normalizeString(entry?.importedFrom),
        updatedAt: normalizeString(entry?.updatedAt),
        expiresAt,
        expired: Boolean(expiresAt) && new Date(expiresAt).getTime() <= Date.now(),
        storePath,
    };
}

function resolveCodexCliHome(env = process.env) {
    const configured = normalizeString(env.CODEX_HOME);
    if (!configured) return path.join(os.homedir(), '.codex');
    if (configured === '~') return os.homedir();
    if (configured.startsWith('~/')) return path.join(os.homedir(), configured.slice(2));
    return path.resolve(configured);
}

function readCodexCliCredential(env = process.env) {
    const authPath = path.join(resolveCodexCliHome(env), 'auth.json');
    if (!fs.existsSync(authPath)) {
        throw new Error(`No Codex CLI auth file found at ${authPath}.`);
    }

    let parsed;
    try {
        parsed = JSON.parse(fs.readFileSync(authPath, 'utf-8'));
    } catch (error) {
        throw new Error(`Codex CLI auth file is invalid JSON: ${error.message}`);
    }

    if (parsed?.auth_mode !== 'chatgpt') {
        throw new Error('Codex CLI auth is not in ChatGPT OAuth mode.');
    }

    const credential = normalizeCredential({
        access: parsed?.tokens?.access_token,
        refresh: parsed?.tokens?.refresh_token,
        accountId: parsed?.tokens?.account_id,
        idToken: parsed?.tokens?.id_token,
    });

    if (!credential.access || !credential.refresh) {
        throw new Error('Codex CLI auth file is missing access or refresh tokens.');
    }

    return {
        credential,
        importedFrom: authPath,
    };
}

function openBrowser(url) {
    if (!url) return;
    const command = process.platform === 'darwin'
        ? `open "${url}"`
        : process.platform === 'win32'
            ? `start "" "${url}"`
            : `xdg-open "${url}"`;
    exec(command, { windowsHide: true }, () => { });
}

async function promptUser(prompt) {
    const rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout,
    });
    try {
        const suffix = prompt?.placeholder ? ` (${prompt.placeholder})` : '';
        const answer = await rl.question(`${prompt.message}${suffix}\n> `);
        return answer;
    } finally {
        rl.close();
    }
}

async function promptAuthUser(prompt) {
    if (prompt?.type !== 'select') {
        return promptUser(prompt);
    }

    const options = Array.isArray(prompt.options) ? prompt.options : [];
    const rendered = options
        .map((option, index) => `${index + 1}. ${option.label}${option.description ? ` - ${option.description}` : ''}`)
        .join('\n');
    const answer = String(await promptUser({
        message: `${prompt.message}\n${rendered}`,
        placeholder: options[0]?.label,
    })).trim();
    if (!answer) return options[0]?.id || '';
    const numericIndex = Number.parseInt(answer, 10) - 1;
    if (Number.isInteger(numericIndex) && options[numericIndex]) {
        return options[numericIndex].id;
    }
    const selected = options.find(
        (option) => option.id === answer || option.label.toLowerCase() === answer.toLowerCase(),
    );
    return selected?.id || answer;
}

function handleAuthEvent(event) {
    if (!event || typeof event !== 'object') return;
    if (event.type === 'auth_url') {
        console.log('\nOpen this URL to continue Codex sign-in:\n');
        console.log(event.url);
        if (event.instructions) console.log(`\n${event.instructions}\n`);
        openBrowser(event.url);
        return;
    }
    if (event.type === 'device_code') {
        console.log(`\nOpen ${event.verificationUri} and enter code ${event.userCode}.\n`);
        openBrowser(event.verificationUri);
        return;
    }
    if ((event.type === 'progress' || event.type === 'info') && event.message) {
        console.log(event.message);
    }
}

function toRuntimeOAuthCredential(credential) {
    const expiresSeconds = normalizeExpiry(credential?.expires, 0);
    return {
        ...credential,
        type: 'oauth',
        expires: expiresSeconds > 0 ? expiresSeconds * 1000 : 0,
    };
}

export async function loginCodexAuth() {
    if (!codexOAuth) {
        throw new Error('The installed pi-ai package does not provide Codex OAuth.');
    }
    const credential = await codexOAuth.login({
        prompt: promptAuthUser,
        notify: handleAuthEvent,
    });
    return storeCredential(credential, 'cli-login');
}

export async function importCodexCliAuth() {
    const imported = readCodexCliCredential(process.env);
    return storeCredential(imported.credential, 'imported-codex-cli', {
        importedFrom: imported.importedFrom,
    });
}

export async function getCodexAuthStatus() {
    return summarizeStatus(getCredentialEntry());
}

export async function logoutCodexAuth() {
    return removeCredential();
}

export async function resolveCodexApiKey() {
    const entry = getCredentialEntry();
    if (!entry) {
        throw new Error('No stored Codex OAuth profile found. Run `limebot auth codex login` or `limebot auth codex import` first.');
    }

    if (!codexOAuth) {
        throw new Error('The installed pi-ai package does not provide Codex OAuth.');
    }

    let credential = toRuntimeOAuthCredential(entry.credential);
    if (Date.now() >= credential.expires) {
        credential = await codexOAuth.refresh(credential);
    }
    const auth = await codexOAuth.toAuth(credential);
    if (!auth?.apiKey) {
        throw new Error('Stored Codex OAuth profile could not produce an API key.');
    }

    const status = storeCredential(credential, entry.source || 'cli-login', {
        importedFrom: entry.importedFrom,
    });

    return {
        apiKey: auth.apiKey,
        status,
    };
}

async function main() {
    const [command, ...args] = process.argv.slice(2);
    const jsonOutput = args.includes('--json');

    try {
        if (command === 'login') {
            const status = await loginCodexAuth();
            console.log(jsonOutput ? JSON.stringify(status) : `Signed in to Codex as ${status.email || status.displayName || 'unknown user'}.`);
            return;
        }
        if (command === 'import') {
            const status = await importCodexCliAuth();
            console.log(jsonOutput ? JSON.stringify(status) : `Imported Codex CLI login${status.importedFrom ? ` from ${status.importedFrom}` : ''}.`);
            return;
        }
        if (command === 'status') {
            const status = await getCodexAuthStatus();
            console.log(jsonOutput ? JSON.stringify(status) : JSON.stringify(status, null, 2));
            return;
        }
        if (command === 'logout') {
            const removed = await logoutCodexAuth();
            console.log(jsonOutput ? JSON.stringify({ removed }) : removed ? 'Removed stored Codex OAuth profile.' : 'No stored Codex OAuth profile to remove.');
            return;
        }
        if (command === 'get-api-key') {
            const result = await resolveCodexApiKey();
            console.log(jsonOutput ? JSON.stringify(result) : result.apiKey);
            return;
        }

        console.error('Usage: node scripts/codex-oauth.mjs <login|import|status|logout|get-api-key> [--json]');
        process.exit(1);
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (jsonOutput) {
            console.error(JSON.stringify({ error: message }));
        } else {
            console.error(message);
        }
        process.exit(1);
    }
}

if (process.argv[1] && path.resolve(process.argv[1]) === __filename) {
    await main();
}
