/** Streaming stays literal and cheap; completed content gets one rich render. */
export function shouldRenderRichMarkdown(isStreaming?: boolean): boolean {
    return isStreaming !== true;
}
