export const CSS_STORAGE_KEY = 'limebot-custom-css';
export const STYLE_TAG_ID = 'limebot-custom-css-inject';

/**
 * Injects raw CSS into a global style tag in the document head.
 */
export function injectCss(css: string) {
    if (typeof document === 'undefined') return;

    let tag = document.getElementById(STYLE_TAG_ID) as HTMLStyleElement | null;
    if (!tag) {
        tag = document.createElement('style');
        tag.id = STYLE_TAG_ID;
        document.head.appendChild(tag);
    }
    tag.textContent = css;
}
