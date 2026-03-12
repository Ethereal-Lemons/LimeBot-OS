/**
 * web/src/hooks/useIdentity.ts
 * ─────────────────────────────
 * Bot identity state, explicit refresh, and background poll,
 * extracted from App.tsx.
 *
 * Exports:
 *  - useIdentity(lastExplicitFetchRef) — returns { botIdentity, refreshIdentity }
 */

import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { API_BASE_URL } from '@/lib/api';

type BotIdentity = { name: string; avatar: string | null };

const POLL_INTERVAL_MS = 5000;
const POLL_SKIP_WINDOW_MS = 8000;

export function useIdentity() {
    const [botIdentity, setBotIdentity] = useState<BotIdentity>({ name: 'LimeBot', avatar: null });
    const lastExplicitFetch = useRef(0);

    const refreshIdentity = () => {
        lastExplicitFetch.current = Date.now();
        axios
            .get(`${API_BASE_URL}/api/identity`)
            .then(res => setBotIdentity(res.data))
            .catch(err => {
                if (err.response?.status !== 401)
                    console.error('Failed to refresh identity:', err);
            });
    };

    useEffect(() => {
        const interval = setInterval(() => {
            // Skip if an explicit fetch happened very recently
            if (Date.now() - lastExplicitFetch.current < POLL_SKIP_WINDOW_MS) return;
            axios
                .get(`${API_BASE_URL}/api/identity`)
                .then(res => {
                    const data = res.data;
                    setBotIdentity(prev => {
                        if (prev.name !== data.name || prev.avatar !== data.avatar) return data;
                        return prev;
                    });
                })
                .catch(err => {
                    if (err.response?.status !== 401)
                        console.error('Failed to poll identity:', err);
                });
        }, POLL_INTERVAL_MS);
        return () => clearInterval(interval);
    }, []);

    return { botIdentity, setBotIdentity, refreshIdentity, lastExplicitFetch };
}
