"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getErrorMessage, getSource } from "@/lib/api";
import type { Source } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<string, string> = {
  READY: "border-emerald-200 bg-emerald-50 text-emerald-700",
  FAILED: "border-rose-200 bg-rose-50 text-rose-700",
  PROCESSING: "border-amber-200 bg-amber-50 text-amber-700",
  UPLOADED: "border-amber-200 bg-amber-50 text-amber-700",
};
const AUTO_REFRESH_MS = 8_000;

function formatDate(value?: string) {
  if (!value) {
    return "--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return "--";
  }
  return date.toLocaleString();
}

export default function SourceDetailPage() {
  const params = useParams();
  const sourceId = useMemo(() => {
    const raw = params?.id;
    if (Array.isArray(raw)) {
      return raw[0];
    }
    return raw;
  }, [params]);
  const [source, setSource] = useState<Source | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const loadSource = useCallback(async () => {
    if (!sourceId) {
      setError("Missing source ID.");
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const payload = await getSource(sourceId);
      setSource(payload);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      const message = getErrorMessage(err);
      setError(message);
      toast.error(message);
    } finally {
      setIsLoading(false);
    }
  }, [sourceId]);

  useEffect(() => {
    loadSource();
  }, [loadSource]);

  const status = (source?.status || "UNKNOWN").toUpperCase();
  const hasPending = source
    ? ["UPLOADED", "PROCESSING"].includes(status)
    : false;
  const canAsk = status === "READY" && !!sourceId;

  useEffect(() => {
    if (!hasPending) {
      return;
    }
    const interval = setInterval(() => {
      loadSource();
    }, AUTO_REFRESH_MS);
    return () => clearInterval(interval);
  }, [hasPending, loadSource]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold text-foreground">Source detail</h2>
          <p className="text-sm text-muted-foreground">
            Monitor ingestion status and jump into Q&A when ready.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" asChild>
            <Link href="/sources">Back to sources</Link>
          </Button>
          <Button
            variant="secondary"
            asChild={canAsk}
            disabled={!canAsk}
          >
            {canAsk ? (
              <Link href={`/ask?source=${sourceId}`}>Ask</Link>
            ) : (
              <span>Ask</span>
            )}
          </Button>
          <Button variant="outline" onClick={loadSource} disabled={isLoading}>
            Refresh
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}

      <Card className="border-border/60 bg-white/80 shadow-sm">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Ingestion status</CardTitle>
          <div className="text-xs text-muted-foreground">
            {hasPending
              ? "Auto-refreshing every 8s"
              : `Last updated ${lastUpdated?.toLocaleTimeString() ?? "--"}`}
          </div>
        </CardHeader>
        <CardContent className="grid gap-4">
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading source details...</p>
          ) : source ? (
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Title
                </p>
                <p className="text-sm text-foreground">
                  {source.title || source.original_filename || "Untitled"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Status
                </p>
                <Badge
                  className={cn(
                    "border text-xs uppercase tracking-wide",
                    STATUS_STYLES[status] ||
                      "border-slate-200 bg-slate-50 text-slate-600"
                  )}
                >
                  {status}
                </Badge>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Source type
                </p>
                <p className="text-sm text-foreground">
                  {source.source_type || "--"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Original filename / URL
                </p>
                <p className="text-sm text-foreground">
                  {source.original_filename || "--"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Created
                </p>
                <p className="text-sm text-foreground">{formatDate(source.created_at)}</p>
              </div>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Updated
                </p>
                <p className="text-sm text-foreground">{formatDate(source.updated_at)}</p>
              </div>
              {source.error && (
                <div className="md:col-span-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Error
                  </p>
                  <p className="text-sm text-rose-700">{source.error}</p>
                </div>
              )}
              {source.ingest_task_id && (
                <div className="md:col-span-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Ingest task ID
                  </p>
                  <p className="text-sm text-foreground">{source.ingest_task_id}</p>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Source not found.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
