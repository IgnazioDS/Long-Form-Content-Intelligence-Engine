"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getAnswer, getAnswerHighlights, getErrorMessage } from "@/lib/api";
import type {
  AnswerResponse,
  Citation,
  CitationGroup,
  Claim,
  Evidence,
  QueryMode,
  VerificationSummary,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const LOCAL_ANSWER_KEY = "lfcie.answer.local";

function formatScore(value?: number | null) {
  if (value === null || value === undefined) {
    return "--";
  }
  return value.toFixed(2);
}

function verdictStyle(verdict?: string) {
  if (!verdict) {
    return "border-slate-200 bg-slate-50 text-slate-600";
  }
  const normalized = verdict.toUpperCase();
  if (normalized === "OK") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (normalized.includes("SUPPORT")) {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (normalized.includes("CONTRADICT")) {
    return "border-rose-200 bg-rose-50 text-rose-700";
  }
  if (normalized.includes("INSUFFICIENT") || normalized.includes("UNSUPPORTED")) {
    return "border-amber-200 bg-amber-50 text-amber-700";
  }
  return "border-slate-200 bg-slate-50 text-slate-600";
}

function renderSummary(summary?: VerificationSummary) {
  if (!summary) {
    return null;
  }
  return (
    <Card className="border-border/60 bg-white/80 shadow-sm">
      <CardHeader className="flex flex-row items-start justify-between">
        <CardTitle className="text-base">Verification summary</CardTitle>
        <Badge className={cn("border text-xs uppercase", verdictStyle(summary.overall_verdict))}>
          {summary.overall_verdict || "Unknown"}
        </Badge>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm text-muted-foreground md:grid-cols-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Counts</p>
          <p>
            Supported: {summary.supported_count ?? 0} · Weak: {summary.weak_support_count ?? 0}
          </p>
          <p>
            Unsupported: {summary.unsupported_count ?? 0} · Contradicted: {summary.contradicted_count ?? 0}
          </p>
          <p>Conflicting: {summary.conflicting_count ?? 0}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Flags</p>
          <p>Has contradictions: {summary.has_contradictions ? "Yes" : "No"}</p>
          <p>Answer style: {summary.answer_style || "Unknown"}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function renderClaims(claims?: Claim[]) {
  if (!claims || claims.length === 0) {
    return null;
  }

  return (
    <Card className="border-border/60 bg-white/80 shadow-sm">
      <CardHeader>
        <CardTitle className="text-base">Claims</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Claim</TableHead>
              <TableHead>Verdict</TableHead>
              <TableHead>Support</TableHead>
              <TableHead>Contradiction</TableHead>
              <TableHead>Evidence</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {claims.map((claim, index) => (
              <TableRow key={`${claim.claim_text}-${index}`}>
                <TableCell className="max-w-[420px] text-sm text-foreground">
                  {claim.claim_text}
                </TableCell>
                <TableCell>
                  <Badge className={cn("border text-xs", verdictStyle(claim.verdict))}>
                    {claim.verdict || "Unknown"}
                  </Badge>
                </TableCell>
                <TableCell className="text-sm">{formatScore(claim.support_score)}</TableCell>
                <TableCell className="text-sm">{formatScore(claim.contradiction_score)}</TableCell>
                <TableCell className="text-sm">
                  {claim.evidence?.length ?? 0}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function renderEvidence(claims?: Claim[]) {
  if (!claims) {
    return null;
  }
  const claimsWithEvidence = claims.filter(
    (claim) => claim.evidence && claim.evidence.length > 0
  );
  if (claimsWithEvidence.length === 0) {
    return null;
  }

  return (
    <Card className="border-border/60 bg-white/80 shadow-sm">
      <CardHeader>
        <CardTitle className="text-base">Evidence</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {claimsWithEvidence.map((claim, claimIndex) => (
          <div key={`${claim.claim_text}-${claimIndex}`} className="space-y-2">
            <p className="text-sm font-semibold text-foreground">{claim.claim_text}</p>
            <div className="space-y-2">
              {claim.evidence?.map((evidence: Evidence, evidenceIndex) => (
                <div
                  key={`${evidence.chunk_id}-${evidenceIndex}`}
                  className="rounded-md border border-border/60 bg-white px-3 py-2 text-xs text-muted-foreground"
                >
                  <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-wide">
                    <Badge className={cn("border text-[10px]", verdictStyle(evidence.relation))}>
                      {evidence.relation || "RELATED"}
                    </Badge>
                    <span>Chunk {evidence.chunk_id}</span>
                    {evidence.highlight_start !== null && evidence.highlight_start !== undefined && (
                      <span>
                        Highlight {evidence.highlight_start}-{evidence.highlight_end}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-sm text-foreground">{evidence.snippet}</p>
                  {evidence.highlight_text && (
                    <p className="mt-1 text-sm text-foreground">
                      Highlight: {evidence.highlight_text}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function renderCitations(
  citations?: Citation[],
  citationGroups?: CitationGroup[],
  citationsCount?: number
) {
  if (citationGroups && citationGroups.length > 0) {
    return (
      <Card className="border-border/60 bg-white/80 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Citations (grouped)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {citationGroups.map((group, index) => (
            <div key={`${group.source_id}-${index}`} className="space-y-2">
              <p className="text-sm font-semibold text-foreground">
                {group.source_title || group.source_id}
              </p>
              <div className="space-y-2">
                {group.citations?.map((citation, citationIndex) => (
                  <CitationItem
                    key={`${citation.chunk_id}-${citationIndex}`}
                    citation={citation}
                  />
                ))}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    );
  }

  if (citations && citations.length > 0) {
    return (
      <Card className="border-border/60 bg-white/80 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Citations</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {citations.map((citation, index) => (
            <CitationItem key={`${citation.chunk_id}-${index}`} citation={citation} />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (citationsCount && citationsCount > 0) {
    return (
      <Card className="border-border/60 bg-white/80 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Citations</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Citations referenced: {citationsCount}
          </p>
        </CardContent>
      </Card>
    );
  }

  return null;
}

function CitationItem({ citation }: { citation: Citation }) {
  return (
    <div className="rounded-md border border-border/60 bg-white px-3 py-2 text-sm text-muted-foreground">
      <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-wide">
        <span>{citation.source_title || citation.source_id}</span>
        {citation.page_start !== null && citation.page_start !== undefined && (
          <span>
            Pages {citation.page_start}
            {citation.page_end ? `-${citation.page_end}` : ""}
          </span>
        )}
        <span>Chunk {citation.chunk_id}</span>
      </div>
      <p className="mt-2 text-sm text-foreground">{citation.snippet}</p>
    </div>
  );
}

export default function AnswerPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const [answer, setAnswer] = useState<AnswerResponse | null>(null);
  const [mode, setMode] = useState<QueryMode>("normal");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const answerId = params.id;
  const isLocal = searchParams.get("local") === "1" || answerId === "local";

  useEffect(() => {
    const paramMode = searchParams.get("mode");
    if (paramMode === "verified" || paramMode === "verified_highlights") {
      setMode(paramMode);
      return;
    }
    if (paramMode === "normal") {
      setMode("normal");
      return;
    }
    try {
      const raw = window.localStorage.getItem("lfcie.ask.v0");
      if (raw) {
        const parsed = JSON.parse(raw) as { mode?: QueryMode };
        if (parsed.mode) {
          setMode(parsed.mode);
        }
      }
    } catch {
      setMode("normal");
    }
  }, [searchParams]);

  useEffect(() => {
    if (!answerId) {
      return;
    }

    const loadAnswer = async () => {
      setIsLoading(true);
      setError(null);

      if (isLocal) {
        try {
          const raw = window.localStorage.getItem(LOCAL_ANSWER_KEY);
          if (!raw) {
            setError("No local answer was found.");
            setAnswer(null);
            return;
          }
          const parsed = JSON.parse(raw) as { response?: AnswerResponse };
          setAnswer(parsed.response || null);
        } catch {
          setError("Unable to read local answer.");
          setAnswer(null);
        } finally {
          setIsLoading(false);
        }
        return;
      }

      try {
        const data =
          mode === "verified_highlights"
            ? await getAnswerHighlights(answerId)
            : await getAnswer(answerId);
        setAnswer(data);
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setIsLoading(false);
      }
    };

    loadAnswer();
  }, [answerId, isLocal, mode]);

  const summary = answer?.verification_summary;
  const claims = answer?.claims;

  const citationsBlock = useMemo(() => {
    return renderCitations(
      answer?.citations,
      answer?.citation_groups,
      answer?.citations_count
    );
  }, [answer]);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-semibold text-foreground">Answer</h2>
        <p className="text-sm text-muted-foreground">Loading answer...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-semibold text-foreground">Answer</h2>
        <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
        <Button onClick={() => window.location.reload()}>Retry</Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-foreground">Answer</h2>
          <p className="text-sm text-muted-foreground">
            Mode: {mode.split("_").join(" ")}
          </p>
        </div>
        <Badge className="border border-slate-200 bg-slate-50 text-slate-600">
          Answer ID: {answerId}
        </Badge>
      </div>

      <Card className="border-border/60 bg-white/80 shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">Response</CardTitle>
        </CardHeader>
        <CardContent className="whitespace-pre-wrap text-sm text-foreground">
          {answer?.answer || "No answer text returned."}
        </CardContent>
      </Card>

      {renderSummary(summary)}
      {renderClaims(claims)}
      {renderEvidence(claims)}
      {citationsBlock}
    </div>
  );
}
