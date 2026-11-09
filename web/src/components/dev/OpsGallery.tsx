import { lazy, Suspense, useEffect, useMemo } from "react";
import { applyThemeWithSchedule } from "@/hooks/useTheme";
import { AppLayout } from "@/components/layout/AppLayout";
import { Toaster } from "@/components/ui/sonner";
import { installOpsFixtures, type OpsState } from "./ops-fixtures";

/**
 * Runs the real operations pages against canned data. Open the app with
 * `?ui=ops&page=overview|queue|logs|instances&state=data|empty|loading|error|error401|error500&theme=<name>`.
 */

const PAGES = {
    overview: { title: "Overview", Page: lazy(() => import("@/components/overview/OverviewPage").then((m) => ({ default: m.OverviewPage }))) },
    queue: { title: "Task Queue", Page: lazy(() => import("@/components/queue/TaskQueuePanel").then((m) => ({ default: m.TaskQueuePanel }))) },
    logs: { title: "System Logs", Page: lazy(() => import("@/components/logs/LogsPage").then((m) => ({ default: m.LogsPage }))) },
    instances: { title: "Instances", Page: lazy(() => import("@/components/sessions/SessionsList").then((m) => ({ default: m.InstancesList }))) },
} as const;

type PageKey = keyof typeof PAGES;

const STATES: OpsState[] = ["data", "empty", "loading", "error", "error401", "error500"];

export function OpsGallery() {
    const params = new URLSearchParams(window.location.search);
    const page = (params.get("page") as PageKey) in PAGES ? (params.get("page") as PageKey) : "overview";
    const rawState = params.get("state") as OpsState;
    const state: OpsState = STATES.includes(rawState) ? rawState : "data";
    const theme = params.get("theme") || "lime";

    // Must run before the page's first request.
    useMemo(() => installOpsFixtures(state), [state]);
    useEffect(() => {
        applyThemeWithSchedule(theme);
        document.documentElement.classList.add("dark");
    }, [theme]);

    const { Page, title } = PAGES[page];
    return (
        <>
            <AppLayout
                botIdentity={{ name: "Jennie", avatar: null }}
                activeView={page}
                onNavigate={(view) => {
                    const next = new URLSearchParams(window.location.search);
                    next.set("page", view);
                    window.location.search = next.toString();
                }}
                pageTitle={title}
                runtimeStatus={{ isConnected: true, autonomousMode: false, pendingApprovals: 0 }}
            >
                <Suspense fallback={<div className="h-full min-h-[40vh] animate-pulse bg-card/10" />}>
                    <Page key={`${page}-${state}`} />
                </Suspense>
            </AppLayout>
            <Toaster position="top-right" />
        </>
    );
}
