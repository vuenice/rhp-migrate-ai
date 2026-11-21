import { BYOKConfig, LLMProvider } from "@/types/workbench";

export const STORAGE_KEY = "rhp_migrate_byok_config";

export interface ProviderMeta {
  id: LLMProvider;
  name: string;
  defaultModel: string;
  models: string[];
  requiresApiKey: boolean;
  defaultBaseUrl?: string;
  description: string;
}

export const PROVIDERS: ProviderMeta[] = [
  {
    id: "gemini",
    name: "Google Gemini",
    defaultModel: "gemini-2.5-pro",
    models: ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-1.5-pro"],
    requiresApiKey: true,
    description: "High context window & rapid code generation via Google AI Studio",
  },
  {
    id: "openai",
    name: "OpenAI",
    defaultModel: "gpt-4o",
    models: ["gpt-4o", "gpt-4o-mini", "o3-mini", "o1"],
    requiresApiKey: true,
    description: "Industry-standard GPT models for deterministic refactoring",
  },
  {
    id: "anthropic",
    name: "Anthropic Claude",
    defaultModel: "claude-3-7-sonnet",
    models: ["claude-3-7-sonnet", "claude-3-5-sonnet"],
    requiresApiKey: true,
    description: "Nuanced reasoning and architectural understanding",
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    defaultModel: "deepseek-chat",
    models: ["deepseek-chat", "deepseek-reasoner"],
    requiresApiKey: true,
    defaultBaseUrl: "https://api.deepseek.com",
    description: "Cost-effective high-capability coding models",
  },
  {
    id: "ollama",
    name: "Local Ollama",
    defaultModel: "llama3",
    models: ["llama3", "qwen2.5-coder", "codellama", "mistral"],
    requiresApiKey: false,
    defaultBaseUrl: "http://localhost:11434",
    description: "Run models 100% locally with zero cloud API costs",
  },
  {
    id: "mock",
    name: "Mock Offline Mode",
    defaultModel: "mock-deterministic",
    models: ["mock-deterministic"],
    requiresApiKey: false,
    description: "Local simulation without external LLM calls",
  },
];

export const DEFAULT_BYOK_CONFIG: BYOKConfig = {
  provider: "gemini",
  apiKey: "",
  modelName: "gemini-2.5-pro",
  apiBase: "",
  temperature: 0.1,
};

export function getStoredBYOKConfig(): BYOKConfig {
  if (typeof window === "undefined") {
    return DEFAULT_BYOK_CONFIG;
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_BYOK_CONFIG;
    const parsed = JSON.parse(raw);
    return {
      ...DEFAULT_BYOK_CONFIG,
      ...parsed,
    };
  } catch {
    return DEFAULT_BYOK_CONFIG;
  }
}

export function saveBYOKConfig(config: BYOKConfig): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch (err) {
    console.error("Failed to save BYOK configuration to localStorage", err);
  }
}

export function buildBYOKHeaders(config?: BYOKConfig): Record<string, string> {
  const current = config || getStoredBYOKConfig();
  const headers: Record<string, string> = {
    "x-provider": current.provider,
  };

  if (current.apiKey?.trim()) {
    headers["x-api-key"] = current.apiKey.trim();
  }
  if (current.modelName?.trim()) {
    headers["x-model-name"] = current.modelName.trim();
  }
  if (current.apiBase?.trim()) {
    headers["x-api-base"] = current.apiBase.trim();
  }

  return headers;
}
