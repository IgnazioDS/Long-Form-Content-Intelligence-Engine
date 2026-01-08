export type ApiError = {
  status: number;
  message: string;
  detail?: string;
};

export type Source = {
  id: string;
  title?: string | null;
  source_type?: string;
  original_filename?: string | null;
  status?: string;
  ingest_task_id?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type SourceListResponse = {
  sources?: Source[];
};

export type QueryRequest = {
  question: string;
  source_ids?: string[];
  rerank?: boolean;
};

export type Citation = {
  chunk_id: string;
  source_id: string;
  source_title?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  section_path?: string[] | null;
  snippet: string;
  snippet_start?: number | null;
  snippet_end?: number | null;
  absolute_start?: number | null;
  absolute_end?: number | null;
};

export type CitationGroup = {
  source_id: string;
  source_title?: string | null;
  citations?: Citation[];
};

export type Evidence = {
  chunk_id: string;
  relation?: string;
  snippet: string;
  snippet_start?: number | null;
  snippet_end?: number | null;
  highlight_start?: number | null;
  highlight_end?: number | null;
  highlight_text?: string | null;
  absolute_start?: number | null;
  absolute_end?: number | null;
};

export type Claim = {
  claim_text: string;
  verdict?: string;
  support_score?: number | null;
  contradiction_score?: number | null;
  evidence?: Evidence[];
};

export type VerificationSummary = {
  supported_count?: number;
  weak_support_count?: number;
  unsupported_count?: number;
  contradicted_count?: number;
  conflicting_count?: number;
  has_contradictions?: boolean;
  overall_verdict?: string;
  answer_style?: string;
};

export type AnswerResponse = {
  answer?: string;
  answer_id?: string;
  query_id?: string;
  id?: string;
  citations?: Citation[];
  citation_groups?: CitationGroup[];
  citations_count?: number;
  claims?: Claim[];
  verification_summary?: VerificationSummary;
  answer_style?: string;
};

export type QueryResponse = AnswerResponse;

export type QueryMode = "normal" | "verified" | "verified_highlights";
