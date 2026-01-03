"use client";

import { useState, useSyncExternalStore } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  DEFAULT_API_BASE_URL,
  getApiConfigSnapshot,
  resetApiConfig,
  setApiConfig,
  subscribeToApiConfig,
  validateApiBaseUrl,
} from "@/lib/api";

export function SettingsDrawer() {
  const config = useSyncExternalStore(
    subscribeToApiConfig,
    getApiConfigSnapshot,
    getApiConfigSnapshot
  );
  const [open, setOpen] = useState(false);
  const [baseUrl, setBaseUrl] = useState(config.baseUrl);
  const [apiKey, setApiKey] = useState(config.apiKey ?? "");
  const [showApiKey, setShowApiKey] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (!nextOpen) {
      return;
    }
    setBaseUrl(config.baseUrl);
    setApiKey(config.apiKey ?? "");
    setError(null);
  };

  const handleSave = () => {
    const validation = validateApiBaseUrl(baseUrl);
    if (!validation.valid || !validation.normalized) {
      setError(validation.message ?? "Base URL is invalid.");
      return;
    }
    setApiConfig({
      baseUrl: validation.normalized,
      apiKey: apiKey.trim(),
    });
    toast.success("Settings saved.");
    setOpen(false);
  };

  const handleReset = () => {
    resetApiConfig();
    toast.success("Settings reset to defaults.");
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          Settings
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>API Settings</DialogTitle>
          <DialogDescription>
            Stored locally in your browser for this device. Environment variables are
            used only as defaults for local development.
          </DialogDescription>
        </DialogHeader>
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            handleSave();
          }}
        >
          <div className="grid gap-2">
            <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              API Base URL
            </label>
            <Input
              value={baseUrl}
              onChange={(event) => {
                setBaseUrl(event.target.value);
                setError(null);
              }}
              placeholder={DEFAULT_API_BASE_URL}
              inputMode="url"
            />
            <p className="text-xs text-muted-foreground">
              Use http:// or https://. Trailing slashes are removed automatically.
            </p>
          </div>
          <div className="grid gap-2">
            <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              API Key
            </label>
            <div className="flex items-center gap-2">
              <Input
                className="flex-1"
                type={showApiKey ? "text" : "password"}
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Optional"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowApiKey((prev) => !prev)}
              >
                {showApiKey ? "Hide" : "Show"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              API keys are visible in the browser. Use a limited, rotateable key.
            </p>
          </div>
          {error && (
            <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={handleReset}>
              Reset defaults
            </Button>
            <Button type="submit">Save</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
