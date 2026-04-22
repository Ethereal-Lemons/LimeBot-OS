import { complete, getModel } from '@mariozechner/pi-ai';
import { fileURLToPath } from 'url';

import { resolveCodexApiKey } from './codex-oauth.mjs';

async function readStdinJson() {
    let input = '';
    for await (const chunk of process.stdin) {
        input += chunk;
    }
    const trimmed = input.trim();
    return trimmed ? JSON.parse(trimmed) : {};
}

function serializeAssistantMessage(message) {
    const content = Array.isArray(message?.content) ? message.content : [];
    const text = content
        .filter((block) => block?.type === 'text')
        .map((block) => String(block.text || ''))
        .join('');
    const thinking = content
        .filter((block) => block?.type === 'thinking')
        .map((block) => String(block.thinking || ''))
        .join('');
    const toolCalls = content
        .filter((block) => block?.type === 'toolCall')
        .map((block) => ({
            id: String(block.id || ''),
            name: String(block.name || ''),
            arguments: block.arguments && typeof block.arguments === 'object'
                ? block.arguments
                : {},
        }));

    return {
        text,
        thinking,
        toolCalls,
        usage: message?.usage || null,
        stopReason: message?.stopReason || null,
        provider: message?.provider || null,
        model: message?.model || null,
        api: message?.api || null,
        responseId: message?.responseId || null,
        timestamp: message?.timestamp || null,
    };
}

async function completeCodex(payload) {
    const modelId = String(payload?.model || '').trim();
    if (!modelId) {
        throw new Error('Missing Codex model id.');
    }

    const model = getModel('openai-codex', modelId);
    if (!model) {
        throw new Error(`Unknown Codex model: ${modelId}`);
    }

    const { apiKey } = await resolveCodexApiKey();
    const result = await complete(model, payload?.context || { messages: [] }, {
        apiKey,
        sessionId: payload?.sessionId || undefined,
    });
    return serializeAssistantMessage(result);
}

async function main() {
    const [command, ...args] = process.argv.slice(2);
    const jsonOutput = args.includes('--json');

    try {
        if (command !== 'complete') {
            throw new Error(
                'Usage: node scripts/codex-chat.mjs complete [--json] < payload.json'
            );
        }

        const payload = await readStdinJson();
        const result = await completeCodex(payload);
        console.log(jsonOutput ? JSON.stringify(result) : JSON.stringify(result, null, 2));
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

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
    await main();
}
