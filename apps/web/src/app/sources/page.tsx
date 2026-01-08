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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { deleteSource, getErrorMessage, listSources, uploadSource } from "@/lib/api";
import type { Source } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<string, string> = {
  READY: "border-emerald-200 bg-emerald-50 text-emerald-700",
  FAILED: "border-rose-200 bg-rose-50 text-rose-700",
  PROCESSING: "border-amber-200 bg-amber-50 text-amber-700",
  UPLOADED: "border-amber-200 bg-amber-50 text-amber-700",
};

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

export default function SourcesPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Source | null>(null);

  const loadSources = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await listSources();
      setSources(data);
      setError(null);
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

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-foreground">Sources</h2>
        <p className="text-sm text-muted-foreground">
          Upload PDFs, track ingestion status, and manage sources.
        </p>
      </div>

      <Card className="border-border/60 bg-white/80 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Upload a PDF</CardTitle>
        </CardHeader>
        <CardContent>
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
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Your sources</CardTitle>
          <Button variant="outline" size="sm" onClick={loadSources} disabled={isLoading}>
            Refresh
          </Button>
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
                          <p>{source.title || source.original_filename || "Untitled"}</p>
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
