const browserLocation = typeof window !== "undefined" ? window.location : null;
const sameOriginWebSocket = browserLocation
    ? `${browserLocation.protocol === "https:" ? "wss:" : "ws:"}//${browserLocation.host}`
    : "ws://localhost:8000";

// Same-origin defaults work through both Vite's development proxy and the
// production Nginx gateway. Explicit URLs remain available for custom hosts.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";
export const WS_BASE_URL = import.meta.env.VITE_WS_BASE_URL || sameOriginWebSocket;

import axios from "axios";

export const api = axios.create({
    baseURL: API_BASE_URL,
    headers: {
        'Content-Type': 'application/json',
    },
});

// Add interceptor to attach API key if present in localStorage
api.interceptors.request.use((config) => {
    const apiKey = localStorage.getItem('limebot_api_key');
    if (apiKey) {
        config.headers['X-API-Key'] = apiKey;
    }
    return config;
});
