import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'http';

import { waitForBackendReadiness } from '../bin/readiness-client.js';

async function withServer(handler, run) {
    const server = http.createServer(handler);
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    try {
        await run(server.address().port);
    } finally {
        await new Promise((resolve) => server.close(resolve));
    }
}

function json(response, statusCode, payload) {
    response.writeHead(statusCode, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(payload));
}

test('unconfigured startup stops at liveness', async () => {
    let readyRequests = 0;
    await withServer((request, response) => {
        if (request.url === '/api/live') return json(response, 200, { status: 'live' });
        readyRequests += 1;
        return json(response, 500, {});
    }, async (port) => {
        const result = await waitForBackendReadiness(port, {
            configured: false,
            intervalMs: 1,
            requestTimeoutMs: 50,
        });
        assert.equal(result.status, 'setup');
        assert.equal(result.live, true);
        assert.equal(readyRequests, 0);
    });
});

test('configured startup authenticates and follows phases to degraded-ready', async () => {
    let readinessCalls = 0;
    const phases = [];
    await withServer((request, response) => {
        if (request.url === '/api/live') return json(response, 200, { status: 'live' });
        assert.equal(request.headers['x-api-key'], 'local-secret');
        readinessCalls += 1;
        if (readinessCalls === 1) {
            return json(response, 503, {
                status: 'starting', phase: 'skills', ready: false,
            });
        }
        return json(response, 200, {
            status: 'degraded',
            phase: 'degraded',
            ready: true,
            degraded_reasons: ['mcp_unavailable'],
        });
    }, async (port) => {
        const result = await waitForBackendReadiness(port, {
            configured: true,
            apiKey: 'local-secret',
            intervalMs: 1,
            requestTimeoutMs: 50,
            onPhase: (phase) => phases.push(phase),
        });
        assert.equal(result.ready, true);
        assert.equal(result.status, 'degraded');
        assert.deepEqual(phases, ['skills', 'degraded']);
        assert.equal(JSON.stringify(result).includes('local-secret'), false);
    });
});

test('authentication failure is terminal and redacted', async () => {
    await withServer((request, response) => {
        if (request.url === '/api/live') return json(response, 200, { status: 'live' });
        return json(response, 401, { detail: 'bad key' });
    }, async (port) => {
        const result = await waitForBackendReadiness(port, {
            configured: true,
            apiKey: 'wrong-secret',
            intervalMs: 1,
            requestTimeoutMs: 50,
        });
        assert.equal(result.status, 'failed');
        assert.equal(result.failure_code, 'readiness_auth_failed');
        assert.equal(JSON.stringify(result).includes('wrong-secret'), false);
    });
});

test('unreachable process returns a bounded timeout', async () => {
    const probe = http.createServer();
    await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
    const port = probe.address().port;
    await new Promise((resolve) => probe.close(resolve));

    const result = await waitForBackendReadiness(port, {
        configured: true,
        maxAttempts: 2,
        intervalMs: 1,
        requestTimeoutMs: 10,
    });
    assert.equal(result.status, 'timeout');
    assert.equal(result.live, false);
    assert.equal(result.phase, 'process');
});
