"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  deleteSource,
  getErrorMessage,
  ingestSource,
  listSources,
  uploadSource,
} from "@/lib/api";
import type { Source } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<string, string> = {
  READY: "border-emerald-200 bg-emerald-50 text-emerald-700",
  FAILED: "border-rose-200 bg-rose-50 text-rose-700",
  PROCESSING: "border-amber-200 bg-amber-50 text-amber-700",
  UPLOADED: "border-amber-200 bg-amber-50 text-amber-700",
};
const AUTO_REFRESH_MS = 10_000;
const DEFAULT_MAX_PDF_BYTES = 25_000_000;
const DEFAULT_MAX_PDF_PAGES = 300;
const DEFAULT_MAX_URL_BYTES = 2_000_000;
const DEFAULT_MAX_TEXT_BYTES = 2_000_000;
function parseEnvNumber(raw: string | undefined, fallback: number) {
  if (raw === undefined) {
    return fallback;
  }
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

const MAX_PDF_BYTES = parseEnvNumber(
  process.env.NEXT_PUBLIC_MAX_PDF_BYTES,
  DEFAULT_MAX_PDF_BYTES
);
const MAX_PDF_PAGES = parseEnvNumber(
  process.env.NEXT_PUBLIC_MAX_PDF_PAGES,
  DEFAULT_MAX_PDF_PAGES
);
const MAX_URL_BYTES = parseEnvNumber(
  process.env.NEXT_PUBLIC_MAX_URL_BYTES,
  DEFAULT_MAX_URL_BYTES
);
const MAX_TEXT_BYTES = parseEnvNumber(
  process.env.NEXT_PUBLIC_MAX_TEXT_BYTES,
  DEFAULT_MAX_TEXT_BYTES
);
const URL_INGEST_ENABLED = process.env.NEXT_PUBLIC_URL_INGEST_ENABLED !== "false";

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "unlimited";
  }
  const mb = bytes / (1024 * 1024);
  if (mb >= 1024) {
    return `${(mb / 1024).toFixed(1)} GB`;
  }
  return `${mb >= 10 ? mb.toFixed(0) : mb.toFixed(1)} MB`;
}

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

function formatTime(value: Date | null) {
  if (!value) {
    return "--";
  }
  return value.toLocaleTimeString();
}

export default function SourcesPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [ingestTitle, setIngestTitle] = useState("");
  const [ingestText, setIngestText] = useState("");
  const [ingestUrl, setIngestUrl] = useState("");
  const [isIngesting, setIsIngesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Source | null>(null);

  const loadSources = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await listSources();
      setSources(data);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      const message = getErrorMessage(err);
      setError(message);
      toast.error(message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSources();
  }, [loadSources]);

  const hasPendingSources = sources.some((source) =>
    ["UPLOADED", "PROCESSING"].includes((source.status || "").toUpperCase())
  );

  useEffect(() => {
    if (!hasPendingSources && !isUploading && !isIngesting) {
      return;
    }
    const interval = setInterval(() => {
      loadSources();
    }, AUTO_REFRESH_MS);
    return () => clearInterval(interval);
  }, [hasPendingSources, isUploading, isIngesting, loadSources]);

  const handleUpload = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!file) {
      toast.error("Select a PDF file to upload.");
      return;
    }

    if (!file.name.toLowerCase().endsWith(".pdf")) {
      toast.error("Only PDF uploads are supported.");
      return;
    }
    if (MAX_PDF_BYTES > 0 && file.size > MAX_PDF_BYTES) {
      toast.error(
        `File is too large (${formatBytes(file.size)}). Max is ${formatBytes(
          MAX_PDF_BYTES
        )}.`
      );
      return;
    }

    setIsUploading(true);
    try {
      await uploadSource(file, title.trim() || null);
      toast.success("Upload received. Ingestion has started.");
      setTitle("");
      setFile(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      await loadSources();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setIsUploading(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) {
      return;
    }
    try {
      await deleteSource(deleteTarget.id);
      toast.success("Source deleted.");
      setDeleteTarget(null);
      await loadSources();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  };

  const handleIngest = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = ingestText.trim();
    const url = ingestUrl.trim();
    if (!text && !url) {
      toast.error("Provide either text or a URL to ingest.");
      return;
    }
    if (text && url) {
      toast.error("Choose text or URL, not both.");
      return;
    }
    if (text && MAX_TEXT_BYTES > 0) {
      const textBytes = new TextEncoder().encode(text).length;
      if (textBytes > MAX_TEXT_BYTES) {
        toast.error(
          `Text is too large (${formatBytes(textBytes)}). Max is ${formatBytes(
            MAX_TEXT_BYTES
          )}.`
        );
        return;
      }
    }
    if (!URL_INGEST_ENABLED && url) {
      toast.error("URL ingest is disabled in this environment.");
      return;
    }
    setIsIngesting(true);
    try {
      await ingestSource({
        text: text || null,
        url: url || null,
        title: ingestTitle.trim() || null,
      });
      toast.success("Ingestion started.");
      setIngestTitle("");
      setIngestText("");
      setIngestUrl("");
      await loadSources();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setIsIngesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-foreground">Sources</h2>
        <p className="text-sm text-muted-foreground">
          Upload PDFs, ingest text/URLs, and manage sources.
        </p>
      </div>

      <Card className="border-border/60 bg-white/80 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Upload a PDF</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-4 text-xs text-muted-foreground">
            Max size {formatBytes(MAX_PDF_BYTES)} | Max pages{" "}
            {MAX_PDF_PAGES > 0 ? MAX_PDF_PAGES : "unlimited"}
          </p>
          <form
            className="grid gap-4 md:grid-cols-[1.2fr_1fr_auto] md:items-end"
            onSubmit={handleUpload}
          >
            <div className="grid gap-2">
              <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                File (.pdf)
              </label>
              <Input
                ref={fileInputRef}
                type="file"
                accept="application/pdf"
                onChange={(event) => {
                  const nextFile = event.target.files?.[0] || null;
                  setFile(nextFile);
                }}
              />
            </div>
            <div className="grid gap-2">
              <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Title
              </label>
              <Input
                placeholder="Optional display title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </div>
            <Button type="submit" disabled={isUploading || !file}>
              {isUploading ? "Uploading..." : "Upload"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card className="border-border/60 bg-white/80 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Ingest text or URL</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="grid gap-4" onSubmit={handleIngest}>
            <div className="grid gap-2">
              <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Title
              </label>
              <Input
                placeholder="Optional display title"
                value={ingestTitle}
                onChange={(event) => setIngestTitle(event.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Text
              </label>
              <Textarea
                rows={5}
                placeholder="Paste long-form text to ingest."
                value={ingestText}
                onChange={(event) => setIngestText(event.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Max text size {formatBytes(MAX_TEXT_BYTES)}.
              </p>
            </div>
            <div className="grid gap-2">
              <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                URL
              </label>
              <Input
                type="url"
                placeholder={
                  URL_INGEST_ENABLED ? "https://example.com" : "URL ingest disabled"
                }
                value={ingestUrl}
                onChange={(event) => setIngestUrl(event.target.value)}
                disabled={!URL_INGEST_ENABLED}
              />
              <p className="text-xs text-muted-foreground">
                {URL_INGEST_ENABLED
                  ? `Provide either text or a URL. Max URL size ${formatBytes(
                      MAX_URL_BYTES
                    )}.`
                  : "URL ingest is disabled in this environment."}
              </p>
            </div>
            <div>
              <Button type="submit" disabled={isIngesting}>
                {isIngesting ? "Submitting..." : "Ingest"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card className="border-border/60 bg-white/80 shadow-sm">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Your sources</CardTitle>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            {hasPendingSources ? (
              <span>Auto-refreshing every 10s</span>
            ) : (
              <span>Last updated {formatTime(lastUpdated)}</span>
            )}
            <Button variant="outline" size="sm" onClick={loadSources} disabled={isLoading}>
              Refresh
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {error && (
            <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          )}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-8 text-center text-sm text-muted-foreground">
                    Loading sources...
                  </TableCell>
                </TableRow>
              ) : sources.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-8 text-center text-sm text-muted-foreground">
                    No sources uploaded yet.
                  </TableCell>
                </TableRow>
              ) : (
                sources.map((source) => {
                  const status = (source.status || "UNKNOWN").toUpperCase();
                  const isReady = status === "READY";
                  const errorText =
                    status === "FAILED" && source.error ? source.error : null;
                  return (
                    <TableRow key={source.id}>
                      <TableCell className="font-medium text-foreground">
                        <div className="space-y-1">
                          <Link
                            className="hover:underline"
                            href={`/sources/${source.id}`}
                          >
                            {source.title ||
                              source.original_filename ||
                              "Untitled"}
                          </Link>
                          {errorText && (
                            <p className="text-xs text-rose-600">{errorText}</p>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          className={cn(
                            "border text-xs uppercase tracking-wide",
                            STATUS_STYLES[status] ||
                              "border-slate-200 bg-slate-50 text-slate-600"
                          )}
                        >
                          {status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDate(source.created_at)}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDate(source.updated_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <Button
                            variant="secondary"
                            size="sm"
                            asChild={isReady}
                            disabled={!isReady}
                          >
                            {isReady ? (
                              <Link href={`/ask?source=${source.id}`}>Ask</Link>
                            ) : (
                              <span>Ask</span>
                            )}
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => setDeleteTarget(source)}
                          >
                            Delete
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteTarget(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete source?</AlertDialogTitle>
            <AlertDialogDescription>
              This action removes the source and its stored file. It cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
