"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { Badge } from "@/components/ui/badge";
import { SettingsDrawer } from "@/components/settings-drawer";
import { getApiConfigSnapshot, getHealth, subscribeToApiConfig } from "@/lib/api";
import { cn } from "@/lib/utils";

type HealthStatus = "loading" | "ok" | "error";

export function TopBar() {
  const [status, setStatus] = useState<HealthStatus>("loading");
  const { baseUrl, guardMessage } = useSyncExternalStore(
    subscribeToApiConfig,
    getApiConfigSnapshot,
    getApiConfigSnapshot
  );

  useEffect(() => {
    let isMounted = true;

    if (guardMessage) {
      setStatus("error");
      return () => {
        isMounted = false;
      };
    }

    const checkHealth = async () => {
      try {
        await getHealth();
        if (isMounted) {
          setStatus("ok");
        }
      } catch {
        if (isMounted) {
          setStatus("error");
        }
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 20000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [baseUrl, guardMessage]);

  return (
    <header className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-3 border-b border-border/70 bg-white/70 px-6 py-4 backdrop-blur">
      <div>
        <p className="text-sm font-medium text-foreground">
          Long-Form Content Intelligence Engine
        </p>
        <p className="text-xs text-muted-foreground">API: {baseUrl}</p>
        {guardMessage && (
          <p className="mt-1 text-xs font-medium text-amber-700">{guardMessage}</p>
        )}
      </div>
      <div className="flex items-center gap-3">
        <Badge
          className={cn(
            "border px-3 py-1 text-xs font-semibold uppercase tracking-wide",
            status === "ok" && "border-emerald-200 bg-emerald-50 text-emerald-700",
            status === "error" && "border-rose-200 bg-rose-50 text-rose-700",
            status === "loading" && "border-amber-200 bg-amber-50 text-amber-700"
          )}
        >
          {status === "ok" && "API Online"}
          {status === "error" && "API Offline"}
          {status === "loading" && "Checking API"}
        </Badge>
        <SettingsDrawer />
      </div>
    </header>
  );
}
