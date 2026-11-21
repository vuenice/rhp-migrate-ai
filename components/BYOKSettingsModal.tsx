"use client";

import React, { useState, useEffect } from "react";
import {
  X,
  Key,
  Cpu,
  Globe,
  Sliders,
  CheckCircle2,
  AlertCircle,
  Eye,
  EyeOff,
  Server,
  Sparkles,
} from "lucide-react";
import {
  BYOKConfig,
  LLMProvider,
} from "@/types/workbench";
import {
  PROVIDERS,
  getStoredBYOKConfig,
  saveBYOKConfig,
  buildBYOKHeaders,
} from "@/lib/byok";

interface BYOKSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfigSaved?: (config: BYOKConfig) => void;
}

export function BYOKSettingsModal({
  isOpen,
  onClose,
  onConfigSaved,
}: BYOKSettingsModalProps) {
  const [config, setConfig] = useState<BYOKConfig>(getStoredBYOKConfig());
  const [showApiKey, setShowApiKey] = useState(false);
  const [customModel, setCustomModel] = useState(false);
  const [testingStatus, setTestingStatus] = useState<
    "idle" | "testing" | "success" | "error"
  >("idle");
  const [statusMessage, setStatusMessage] = useState("");
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    if (isOpen) {
      const stored = getStoredBYOKConfig();
      setConfig(stored);
      setSaveSuccess(false);
      setTestingStatus("idle");
      setStatusMessage("");

      // check if stored model is custom
      const currentProviderMeta = PROVIDERS.find((p) => p.id === stored.provider);
      if (
        currentProviderMeta &&
        !currentProviderMeta.models.includes(stored.modelName)
      ) {
        setCustomModel(true);
      } else {
        setCustomModel(false);
      }
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const currentProvider =
    PROVIDERS.find((p) => p.id === config.provider) || PROVIDERS[0];

  const handleProviderChange = (providerId: LLMProvider) => {
    const meta = PROVIDERS.find((p) => p.id === providerId) || PROVIDERS[0];
    setConfig((prev) => ({
      ...prev,
      provider: providerId,
      modelName: meta.defaultModel,
      apiBase: meta.defaultBaseUrl || "",
    }));
    setCustomModel(false);
    setTestingStatus("idle");
    setStatusMessage("");
  };

  const handleSave = () => {
    saveBYOKConfig(config);
    setSaveSuccess(true);
    if (onConfigSaved) {
      onConfigSaved(config);
    }
    setTimeout(() => {
      setSaveSuccess(false);
      onClose();
    }, 600);
  };

  const handleTestConnection = async () => {
    setTestingStatus("testing");
    setStatusMessage("Testing connection to LLM provider...");

    try {
      const headers = buildBYOKHeaders(config);
      // Quick test using the backend analyze snippet endpoint
      const res = await fetch("/api/analyze", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...headers,
        },
        body: JSON.stringify({
          code: "<?php class HealthCheck { public function ping(): string { return 'pong'; } }",
          file_name: "HealthCheck.php",
        }),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => null);
        throw new Error(errJson?.detail || `HTTP ${res.status}: ${res.statusText}`);
      }

      setTestingStatus("success");
      setStatusMessage("Connection verified! Backend accepted BYOK headers.");
    } catch (err: any) {
      setTestingStatus("error");
      setStatusMessage(err.message || "Failed to connect with provided credentials.");
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4 overflow-y-auto animate-in fade-in duration-200"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      <div className="relative w-full max-w-xl rounded-xl border border-neutral-800 bg-neutral-900 text-neutral-100 shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-neutral-800 px-6 py-4 bg-neutral-950/50">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <Key className="h-5 w-5" />
            </div>
            <div>
              <h2 id="modal-title" className="text-base font-semibold text-neutral-100">
                AI Provider & BYOK Settings
              </h2>
              <p className="text-xs text-neutral-400">
                Bring Your Own Key — zero server costs, credentials stored locally
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close settings modal"
            className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="space-y-5 px-6 py-5 max-h-[75vh] overflow-y-auto">
          {/* Provider Selection */}
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
              Select LLM Provider
            </label>
            <div className="grid grid-cols-3 gap-2.5">
              {PROVIDERS.map((p) => {
                const isSelected = config.provider === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => handleProviderChange(p.id)}
                    className={`flex flex-col items-start p-3 rounded-lg border text-left transition-all ${
                      isSelected
                        ? "border-emerald-500 bg-emerald-950/20 text-neutral-100 ring-1 ring-emerald-500/50"
                        : "border-neutral-800 bg-neutral-950/40 text-neutral-400 hover:border-neutral-700 hover:text-neutral-300"
                    }`}
                  >
                    <div className="flex items-center gap-1.5 w-full">
                      <Cpu className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
                      <span className="text-xs font-medium truncate">{p.name}</span>
                    </div>
                    <span className="text-[10px] text-neutral-500 mt-1 truncate w-full">
                      {p.id === "ollama" || p.id === "mock" ? "Zero API Key" : "Requires Key"}
                    </span>
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-neutral-400 mt-1">
              {currentProvider.description}
            </p>
          </div>

          {/* API Key */}
          {currentProvider.requiresApiKey && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
                  {currentProvider.name} API Key
                </label>
                <span className="text-[10px] text-neutral-500">Stored in localStorage</span>
              </div>
              <div className="relative">
                <input
                  type={showApiKey ? "text" : "password"}
                  value={config.apiKey}
                  onChange={(e) =>
                    setConfig((prev) => ({ ...prev, apiKey: e.target.value }))
                  }
                  placeholder={
                    config.provider === "gemini"
                      ? "AIzaSy..."
                      : config.provider === "openai"
                      ? "sk-proj-..."
                      : "Enter your secret key"
                  }
                  className="w-full rounded-lg border border-neutral-800 bg-neutral-950 px-3.5 py-2.5 pr-10 text-xs font-mono text-neutral-100 placeholder-neutral-600 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey(!showApiKey)}
                  className="absolute right-3 top-2.5 text-neutral-400 hover:text-neutral-200"
                  aria-label={showApiKey ? "Hide API key" : "Show API key"}
                >
                  {showApiKey ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
          )}

          {/* Model Selection */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold uppercase tracking-wider text-neutral-400">
                Model Identifier
              </label>
              <button
                type="button"
                onClick={() => setCustomModel(!customModel)}
                className="text-[11px] text-emerald-400 hover:underline flex items-center gap-1"
              >
                <Sparkles className="h-3 w-3" />
                {customModel ? "Select from list" : "Use custom model string"}
              </button>
            </div>

            {customModel ? (
              <input
                type="text"
                value={config.modelName}
                onChange={(e) =>
                  setConfig((prev) => ({ ...prev, modelName: e.target.value }))
                }
                placeholder="e.g. gemini-2.5-pro or llama3.3:70b"
                className="w-full rounded-lg border border-neutral-800 bg-neutral-950 px-3.5 py-2 text-xs font-mono text-neutral-100 placeholder-neutral-600 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              />
            ) : (
              <select
                value={config.modelName}
                onChange={(e) =>
                  setConfig((prev) => ({ ...prev, modelName: e.target.value }))
                }
                className="w-full rounded-lg border border-neutral-800 bg-neutral-950 px-3 py-2 text-xs font-mono text-neutral-100 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              >
                {currentProvider.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Custom Base URL (Ollama or Proxy) */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold uppercase tracking-wider text-neutral-400 flex items-center gap-1.5">
                <Globe className="h-3.5 w-3.5 text-neutral-400" />
                Custom Base URL (Optional)
              </label>
              <span className="text-[10px] text-neutral-500">
                For Ollama, vLLM or LiteLLM proxy
              </span>
            </div>
            <input
              type="text"
              value={config.apiBase || ""}
              onChange={(e) =>
                setConfig((prev) => ({ ...prev, apiBase: e.target.value }))
              }
              placeholder={currentProvider.defaultBaseUrl || "https://api.openai.com/v1"}
              className="w-full rounded-lg border border-neutral-800 bg-neutral-950 px-3.5 py-2 text-xs font-mono text-neutral-100 placeholder-neutral-600 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
            />
          </div>

          {/* Temperature Slider */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold uppercase tracking-wider text-neutral-400 flex items-center gap-1.5">
                <Sliders className="h-3.5 w-3.5 text-neutral-400" />
                Sampling Temperature: {config.temperature ?? 0.1}
              </label>
              <span className="text-[10px] text-neutral-500">
                Lower = More deterministic RHP types
              </span>
            </div>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={config.temperature ?? 0.1}
              onChange={(e) =>
                setConfig((prev) => ({
                  ...prev,
                  temperature: parseFloat(e.target.value),
                }))
              }
              className="w-full accent-emerald-500 h-1.5 bg-neutral-800 rounded-lg cursor-pointer"
            />
          </div>

          {/* Test Status feedback */}
          {testingStatus !== "idle" && (
            <div
              className={`flex items-start gap-2.5 p-3 rounded-lg border text-xs ${
                testingStatus === "testing"
                  ? "border-sky-800 bg-sky-950/30 text-sky-300"
                  : testingStatus === "success"
                  ? "border-emerald-800 bg-emerald-950/30 text-emerald-300"
                  : "border-rose-800 bg-rose-950/30 text-rose-300"
              }`}
            >
              {testingStatus === "testing" ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-sky-400 border-t-transparent mt-0.5" />
              ) : testingStatus === "success" ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" />
              ) : (
                <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
              )}
              <div className="flex-1">
                <p className="font-medium">{statusMessage}</p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-neutral-800 px-6 py-4 bg-neutral-950/50">
          <button
            type="button"
            onClick={handleTestConnection}
            disabled={testingStatus === "testing"}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-neutral-700 bg-neutral-800 text-neutral-300 hover:bg-neutral-700 hover:text-neutral-100 disabled:opacity-50 transition-colors"
          >
            <Server className="h-3.5 w-3.5" />
            Test Connection
          </button>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3.5 py-1.5 text-xs font-medium rounded-lg text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200 transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSave}
              className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 shadow-sm transition-colors"
            >
              {saveSuccess ? (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Saved!
                </>
              ) : (
                "Save & Apply"
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
