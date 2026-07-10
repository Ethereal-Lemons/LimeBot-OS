import http from 'http';

function appendBounded(current, chunk, maxChars) {
    const combined = current + String(chunk || '');
    return combined.length > maxChars ? combined.slice(-maxChars) : combined;
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function requestLocalJson(
    port,
    pathname,
    apiKey = null,
    { timeoutMs = 1000 } = {},
) {
    return new Promise((resolve) => {
        const headers = apiKey ? { 'X-API-Key': apiKey } : {};
        const request = http.get({
            hostname: '127.0.0.1',
            port,
            path: pathname,
            headers,
            timeout: timeoutMs,
        }, (response) => {
            let body = '';
            response.setEncoding('utf8');
            response.on('data', (chunk) => {
                body = appendBounded(body, chunk, 16000);
            });
            response.on('end', () => {
                try {
                    resolve({ statusCode: response.statusCode || 0, body: JSON.parse(body) });
                } catch {
                    resolve({ statusCode: response.statusCode || 0, body: null });
                }
            });
        });
        request.on('timeout', () => request.destroy());
        request.on('error', () => resolve(null));
    });
}

export async function waitForBackendReadiness(
    port,
    {
        configured,
        apiKey = null,
        maxAttempts = 30,
        intervalMs = 1000,
        requestTimeoutMs = 1000,
        onPhase = null,
    } = {},
) {
    let lastPhase = null;
    let sawLiveness = false;

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
        const live = await requestLocalJson(port, '/api/live', null, {
            timeoutMs: requestTimeoutMs,
        });
        if (live?.statusCode === 200 && live.body?.status === 'live') {
            sawLiveness = true;
            if (!configured) {
                return { live: true, ready: false, status: 'setup', phase: 'setup' };
            }

            const ready = await requestLocalJson(port, '/api/ready', apiKey, {
                timeoutMs: requestTimeoutMs,
            });
            const phase = ready?.body?.phase || 'agent';
            if (phase !== lastPhase) {
                lastPhase = phase;
                onPhase?.(phase);
            }
            if (ready?.statusCode === 200 && ready.body?.ready) {
                return { live: true, ...ready.body };
            }
            if (ready?.body?.status === 'failed') {
                return { live: true, ...ready.body };
            }
            if (ready?.statusCode === 401) {
                return {
                    live: true,
                    ready: false,
                    status: 'failed',
                    phase: 'auth',
                    failure_code: 'readiness_auth_failed',
                };
            }
        }
        await delay(intervalMs);
    }

    return {
        live: sawLiveness,
        ready: false,
        status: 'timeout',
        phase: lastPhase || (sawLiveness ? 'agent' : 'process'),
        failure_code: 'backend_readiness_timeout',
    };
}

export async function waitForBackendLiveness(
    port,
    {
        maxAttempts = 60,
        intervalMs = 250,
        requestTimeoutMs = 250,
    } = {},
) {
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
        const live = await requestLocalJson(port, '/api/live', null, {
            timeoutMs: requestTimeoutMs,
        });
        if (live?.statusCode === 200 && live.body?.status === 'live') {
            return { live: true, status: 'live', phase: 'process', failure_code: null };
        }
        if (attempt + 1 < maxAttempts) await delay(intervalMs);
    }
    return {
        live: false,
        status: 'timeout',
        phase: 'process',
        failure_code: 'backend_liveness_timeout',
    };
}
