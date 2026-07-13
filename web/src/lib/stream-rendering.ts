/** Streaming stays literal and cheap; completed content gets one rich render. */
export function shouldRenderRichMarkdown(isStreaming?: boolean): boolean {
    return isStreaming !== true;
}

export type StreamRenderState = {
    key: string | null;
    contentRendered: boolean;
    thinkingRendered: boolean;
};

export function classifyStreamDelta(
    previous: StreamRenderState,
    key: string,
    contentDelta: string,
    thinkingDelta: string,
): { immediate: boolean; next: StreamRenderState } {
    const current = previous.key === key
        ? previous
        : { key, contentRendered: false, thinkingRendered: false };
    return {
        immediate:
            Boolean(contentDelta && !current.contentRendered) ||
            Boolean(thinkingDelta && !current.thinkingRendered),
        next: {
            key,
            contentRendered: current.contentRendered || Boolean(contentDelta),
            thinkingRendered: current.thinkingRendered || Boolean(thinkingDelta),
        },
    };
}
