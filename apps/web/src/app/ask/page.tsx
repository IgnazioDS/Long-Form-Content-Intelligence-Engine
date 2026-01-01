"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import {
  getErrorMessage,
  listSources,
  query,
  queryVerified,
  queryVerifiedHighlights,
} from "@/lib/api";
import type { QueryMode, QueryResponse, Source } from "@/lib/types";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "lfcie.ask.v0";

function getStoredPreferences(): { sourceIds: string[]; mode: QueryMode } | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as { sourceIds?: string[]; mode?: QueryMode };
    return {
      sourceIds: parsed.sourceIds || [],
      mode: parsed.mode || "normal",
    };
  } catch {
    return null;
  }
}

function storePreferences(sourceIds: string[], mode: QueryMode) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ sourceIds, mode })
  );
}

function extractAnswerId(response: QueryResponse) {
  return response.answer_id || response.id || null;
}

export default function AskPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [sources, setSources] = useState<Source[]>([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<QueryMode>("normal");
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const readySources = useMemo(() => {
    return sources.filter((source) =>
      (source.status || "").toUpperCase().includes("READY")
    );
  }, [sources]);

  useEffect(() => {
    const stored = getStoredPreferences();
    if (stored) {
      setSelectedSourceIds(stored.sourceIds);
      setMode(stored.mode);
    }
  }, []);

  useEffect(() => {
    storePreferences(selectedSourceIds, mode);
  }, [selectedSourceIds, mode]);

  useEffect(() => {
    const fetchSources = async () => {
      setIsLoading(true);
      try {
        const data = await listSources();
        setSources(data);
      } catch (err) {
        toast.error(getErrorMessage(err));
      } finally {
        setIsLoading(false);
      }
    };

    fetchSources();
  }, []);

  useEffect(() => {
    setSelectedSourceIds((current) =>
      current.filter((id) => readySources.some((source) => source.id === id))
    );
  }, [readySources]);

  useEffect(() => {
    const preselect = searchParams.get("source");
    if (preselect) {
      setSelectedSourceIds((current) =>
        current.includes(preselect) ? current : [...current, preselect]
      );
    }
  }, [searchParams]);

  const handleToggleSource = (sourceId: string) => {
    setSelectedSourceIds((current) => {
      if (current.includes(sourceId)) {
        return current.filter((id) => id !== sourceId);
      }
      return [...current, sourceId];
    });
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (selectedSourceIds.length === 0) {
      toast.error("Select at least one READY source.");
      return;
    }
    if (question.trim().length < 3) {
      toast.error("Enter a question with at least 3 characters.");
      return;
    }

    setIsSubmitting(true);
    try {
      const payload = {
        question: question.trim(),
        source_ids: selectedSourceIds,
      };
      let response: QueryResponse;
      if (mode === "verified") {
        response = await queryVerified(payload);
      } else if (mode === "verified_highlights") {
        response = await queryVerifiedHighlights(payload);
      } else {
        response = await query(payload);
      }

      const answerId = extractAnswerId(response);
      if (answerId) {
        router.push(`/answer/${answerId}?mode=${mode}`);
      } else {
        window.localStorage.setItem(
          "lfcie.answer.local",
          JSON.stringify({ response, mode, createdAt: new Date().toISOString() })
        );
        router.push(`/answer/local?mode=${mode}&local=1`);
      }
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-foreground">Ask</h2>
          <p className="text-sm text-muted-foreground">
            Build a query against your READY sources and choose verification mode.
          </p>
        </div>
        <Badge className="border border-slate-200 bg-slate-50 text-slate-600">
          Ready sources: {readySources.length}
        </Badge>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        <Card className="border-border/60 bg-white/80 shadow-sm">
          <CardHeader>
            <CardTitle className="text-base">Select sources</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Loading sources...</p>
            ) : readySources.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No READY sources yet. Upload a PDF and wait for ingestion.
              </p>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {readySources.map((source) => {
                  const checked = selectedSourceIds.includes(source.id);
                  return (
                    <label
                      key={source.id}
                      className={cn(
                        "flex items-start gap-3 rounded-lg border border-border/60 bg-white px-3 py-2 text-sm shadow-sm transition",
                        checked && "border-primary/40 bg-primary/5"
                      )}
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => handleToggleSource(source.id)}
                      />
                      <div>
                        <p className="font-medium text-foreground">
                          {source.title || source.original_filename || "Untitled"}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {source.id}
                        </p>
                      </div>
                    </label>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="border-border/60 bg-white/80 shadow-sm">
          <CardHeader>
            <CardTitle className="text-base">Question</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Textarea
              rows={5}
              placeholder="Ask a long-form question for the engine to answer."
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <div className="grid gap-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Mode
              </p>
              <RadioGroup
                value={mode}
                onValueChange={(value) => setMode(value as QueryMode)}
                className="grid gap-3"
              >
                <label className="flex items-center gap-3">
                  <RadioGroupItem value="normal" />
                  <span className="text-sm font-medium">Normal</span>
                  <span className="text-xs text-muted-foreground">/query</span>
                </label>
                <label className="flex items-center gap-3">
                  <RadioGroupItem value="verified" />
                  <span className="text-sm font-medium">Verified</span>
                  <span className="text-xs text-muted-foreground">
                    /query/verified
                  </span>
                </label>
                <label className="flex items-center gap-3">
                  <RadioGroupItem value="verified_highlights" />
                  <span className="text-sm font-medium">
                    Verified + Highlights
                  </span>
                  <span className="text-xs text-muted-foreground">
                    /query/verified/highlights
                  </span>
                </label>
              </RadioGroup>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Submitting..." : "Submit"}
              </Button>
              <span className="text-sm text-muted-foreground">
                {selectedSourceIds.length} source(s) selected
              </span>
            </div>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}
