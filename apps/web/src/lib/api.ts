import type {
  AnswerResponse,
  ApiError,
  QueryRequest,
  QueryResponse,
  Source,
  SourceListResponse,
} from "@/lib/types";

const RAW_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export const API_BASE_URL = RAW_BASE_URL.replace(/\/+$/, "");

const API_KEY = process.env.NEXT_PUBLIC_API_KEY;

type ApiFetchOptions = {
  method?: string;
  body?: BodyInit | Record<string, unknown> | null;
  headers?: Record<string, string>;
};

function buildUrl(path: string) {
  if (!path.startsWith("/")) {
    return `${API_BASE_URL}/${path}`;
  }
  return `${API_BASE_URL}${path}`;
}

function normalizeError(status: number, payload: unknown): ApiError {
  const detail =
    typeof payload === "string"
      ? payload
      : (payload as { detail?: string; message?: string })?.detail ||
        (payload as { detail?: string; message?: string })?.message;

  if (status === 401) {
    return {
      status,
      message: "API key required or invalid",
      detail,
    };
  }

  if (status === 413) {
    return {
      status,
      message: "Upload too large",
      detail,
    };
  }

  if (status >= 500) {
    return {
      status,
      message: "Server error. Please try again.",
      detail,
    };
  }

  return {
    status,
    message: detail || "Request failed",
    detail,
  };
}

export function getErrorMessage(error: unknown) {
  if (!error) {
    return "Unknown error";
  }

  if (typeof error === "string") {
    return error;
  }

  if (typeof error === "object" && "message" in error) {
    const maybe = error as { message?: string };
    if (maybe.message) {
      return maybe.message;
    }
  }

  return "Request failed";
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}) {
  const { method = "GET", body, headers } = options;
  const init: RequestInit = { method, headers: { ...headers } };

  if (API_KEY) {
    init.headers = {
      ...init.headers,
      "X-API-Key": API_KEY,
    };
  }

  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined && body !== null) {
    init.body = JSON.stringify(body);
    init.headers = {
      ...init.headers,
      "Content-Type": "application/json",
    };
  }

  const response = await fetch(buildUrl(path), init);

  if (response.status === 204) {
    return null as T;
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : await response.text().catch(() => null);

  if (!response.ok) {
    throw normalizeError(response.status, payload);
  }

  return payload as T;
}

export async function getHealth() {
  return apiFetch<{ status?: string }>("/health");
}

export async function listSources() {
  const payload = await apiFetch<SourceListResponse>("/sources");
  return payload.sources ?? [];
}

export async function uploadSource(file: File, title?: string | null) {
  const form = new FormData();
  form.append("file", file);
  if (title) {
    form.append("title", title);
  }
  return apiFetch<Source>("/sources/upload", { method: "POST", body: form });
}

export async function deleteSource(sourceId: string) {
  return apiFetch<Source>(`/sources/${sourceId}`, { method: "DELETE" });
}

export async function query(payload: QueryRequest) {
  return apiFetch<QueryResponse>("/query", { method: "POST", body: payload });
}

export async function queryVerified(payload: QueryRequest) {
  return apiFetch<QueryResponse>("/query/verified", {
    method: "POST",
    body: payload,
  });
}

export async function queryVerifiedHighlights(payload: QueryRequest) {
  return apiFetch<QueryResponse>("/query/verified/highlights", {
    method: "POST",
    body: payload,
  });
}

export async function getAnswer(answerId: string) {
  return apiFetch<AnswerResponse>(`/answers/${answerId}`);
}

export async function getAnswerHighlights(answerId: string) {
  return apiFetch<AnswerResponse>(`/answers/${answerId}/highlights`);
}
