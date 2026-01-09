import type {
  AnswerResponse,
  ApiError,
  QueryRequest,
  QueryResponse,
  Source,
  SourceListResponse,
} from "@/lib/types";

export const DEFAULT_API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const DEFAULT_API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";
const LOCALHOST_BASE_URL = "http://localhost:8000";

const STORAGE_KEYS = {
  baseUrl: "lfcie_api_base_url",
  apiKey: "lfcie_api_key",
};

type ApiConfigSnapshot = {
  baseUrl: string;
  apiKey: string | null;
  guardMessage: string | null;
};

type ApiFetchOptions = {
  method?: string;
  body?: BodyInit | Record<string, unknown> | null;
  headers?: Record<string, string>;
  timeoutMs?: number;
};

let runtimeApiBaseUrl: string | null = null;
let runtimeApiKey: string | null = null;
const configListeners = new Set<() => void>();
let cachedSnapshot: ApiConfigSnapshot | null = null;

function notifyConfigListeners() {
  configListeners.forEach((listener) => listener());
}

export function subscribeToApiConfig(listener: () => void) {
  configListeners.add(listener);
  return () => {
    configListeners.delete(listener);
  };
}

function isBrowser() {
  return typeof window !== "undefined";
}

function readStorage(key: string) {
  if (!isBrowser()) {
    return null;
  }
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string | null) {
  if (!isBrowser()) {
    return;
  }
  try {
    if (!value) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, value);
    }
  } catch {
    // Ignore storage failures (private mode, etc.).
  }
}

function normalizeBaseUrl(value: string) {
  return value.trim().replace(/\/+$/, "");
}

export function validateApiBaseUrl(value: string) {
  const trimmed = value.trim();
  if (!trimmed) {
    return { valid: false, message: "Base URL is required." };
  }
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return { valid: false, message: "Base URL must use http or https." };
    }
    return { valid: true, normalized: normalizeBaseUrl(parsed.toString()) };
  } catch {
    return { valid: false, message: "Base URL must be a valid http(s) URL." };
  }
}

function resolveApiBaseUrl() {
  const fromRuntime = runtimeApiBaseUrl;
  const fromStorage = readStorage(STORAGE_KEYS.baseUrl);
  const resolved = fromRuntime || fromStorage || DEFAULT_API_BASE_URL;
  return normalizeBaseUrl(resolved);
}

function resolveApiKey() {
  const fromRuntime = runtimeApiKey;
  const fromStorage = readStorage(STORAGE_KEYS.apiKey);
  const resolved = fromRuntime ?? fromStorage ?? DEFAULT_API_KEY;
  const trimmed = resolved.trim();
  return trimmed ? trimmed : null;
}

function isLocalHostname(hostname: string) {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

export function getApiConfigGuardMessage(baseUrl = resolveApiBaseUrl()) {
  const normalized = normalizeBaseUrl(baseUrl);
  if (normalized !== LOCALHOST_BASE_URL) {
    return null;
  }
  if (!isBrowser()) {
    return null;
  }
  const isProductionBuild = process.env.NODE_ENV === "production";
  const isNonLocalHost = !isLocalHostname(window.location.hostname);
  if (!isProductionBuild && !isNonLocalHost) {
    return null;
  }
  return "API base URL is still set to http://localhost:8000. Update Settings before making requests.";
}

export function getApiConfigSnapshot(): ApiConfigSnapshot {
  const baseUrl = resolveApiBaseUrl();
  const apiKey = resolveApiKey();
  const guardMessage = getApiConfigGuardMessage(baseUrl);
  if (
    cachedSnapshot &&
    cachedSnapshot.baseUrl === baseUrl &&
    cachedSnapshot.apiKey === apiKey &&
    cachedSnapshot.guardMessage === guardMessage
  ) {
    return cachedSnapshot;
  }
  cachedSnapshot = {
    baseUrl,
    apiKey,
    guardMessage,
  };
  return cachedSnapshot;
}

export function setApiConfig(config: { baseUrl?: string | null; apiKey?: string | null }) {
  if (config.baseUrl !== undefined) {
    const normalized = config.baseUrl ? normalizeBaseUrl(config.baseUrl) : "";
    runtimeApiBaseUrl = normalized ? normalized : null;
    writeStorage(STORAGE_KEYS.baseUrl, runtimeApiBaseUrl);
  }
  if (config.apiKey !== undefined) {
    const trimmed = config.apiKey ? config.apiKey.trim() : "";
    runtimeApiKey = trimmed ? trimmed : null;
    writeStorage(STORAGE_KEYS.apiKey, runtimeApiKey);
  }
  notifyConfigListeners();
}

export function resetApiConfig() {
  runtimeApiBaseUrl = null;
  runtimeApiKey = null;
  writeStorage(STORAGE_KEYS.baseUrl, null);
  writeStorage(STORAGE_KEYS.apiKey, null);
  notifyConfigListeners();
}

function buildUrl(path: string, baseUrl: string) {
  if (!path.startsWith("/")) {
    return `${baseUrl}/${path}`;
  }
  return `${baseUrl}${path}`;
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

function isAbortError(error: unknown) {
  if (!error || typeof error !== "object") {
    return false;
  }
  return "name" in error && (error as { name?: string }).name === "AbortError";
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}) {
  const { method = "GET", body, headers, timeoutMs = 60000 } = options;
  const guardMessage = getApiConfigGuardMessage();
  if (guardMessage) {
    throw { message: guardMessage };
  }
  const baseUrl = resolveApiBaseUrl();
  const init: RequestInit = { method, headers: { ...headers } };
  const apiKey = resolveApiKey();

  if (apiKey) {
    init.headers = {
      ...init.headers,
      "X-API-Key": apiKey,
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

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  init.signal = controller.signal;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, baseUrl), init);
  } catch (error) {
    if (isAbortError(error)) {
      throw { message: "Request timed out" };
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }

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

export async function getSource(sourceId: string) {
  return apiFetch<Source>(`/sources/${sourceId}`);
}

export async function uploadSource(file: File, title?: string | null) {
  const form = new FormData();
  form.append("file", file);
  if (title) {
    form.append("title", title);
  }
  return apiFetch<Source>("/sources/upload", { method: "POST", body: form });
}

export async function ingestSource(payload: {
  text?: string | null;
  url?: string | null;
  title?: string | null;
}) {
  return apiFetch<Source>("/sources/ingest", { method: "POST", body: payload });
}

export async function deleteSource(sourceId: string) {
  return apiFetch<Source>(`/sources/${sourceId}`, { method: "DELETE" });
}

export async function query(payload: QueryRequest) {
  return apiFetch<QueryResponse>("/query", { method: "POST", body: payload });
}

export async function queryGrouped(payload: QueryRequest) {
  return apiFetch<QueryResponse>("/query/grouped", { method: "POST", body: payload });
}

export async function queryVerified(payload: QueryRequest) {
  return apiFetch<QueryResponse>("/query/verified", {
    method: "POST",
    body: payload,
  });
}

export async function queryVerifiedGrouped(payload: QueryRequest) {
  return apiFetch<QueryResponse>("/query/verified/grouped", {
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

export async function queryVerifiedGroupedHighlights(payload: QueryRequest) {
  return apiFetch<QueryResponse>("/query/verified/grouped/highlights", {
    method: "POST",
    body: payload,
  });
}

export async function getAnswer(answerId: string) {
  return apiFetch<AnswerResponse>(`/answers/${answerId}`);
}

export async function getAnswerGrouped(answerId: string) {
  return apiFetch<AnswerResponse>(`/answers/${answerId}/grouped`);
}

export async function getAnswerHighlights(answerId: string) {
  return apiFetch<AnswerResponse>(`/answers/${answerId}/highlights`);
}

export async function getAnswerGroupedHighlights(answerId: string) {
  return apiFetch<AnswerResponse>(`/answers/${answerId}/grouped/highlights`);
}
