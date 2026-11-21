export type LLMProvider = "gemini" | "openai" | "anthropic" | "deepseek" | "ollama" | "mock";

export interface BYOKConfig {
  provider: LLMProvider;
  apiKey: string;
  modelName: string;
  apiBase?: string;
  temperature?: number;
}

export type FileStatus = "pending" | "analyzing" | "warning" | "migrated";

export interface FileItem {
  name: string;
  path: string;
  type: "file" | "directory";
  size?: number;
  classification?: string; // Controller, Model, Service, Migration, Blade view
  status?: FileStatus;
  warningDetails?: string;
  children?: FileItem[];
}

export interface CompilerLogEntry {
  id: string;
  timestamp: string;
  level: "info" | "success" | "warn" | "error";
  message: string;
}

export interface ASTDiagnostic {
  line: number;
  column: number;
  severity: "error" | "warning" | "info";
  rule_id?: string;
  message: string;
  code_snippet?: string;
}

export interface ASTScorecardData {
  readinessPercentage: number;
  unsupportedCount: number;
  untypedParamCount: number;
  isMigrationReady: boolean;
  classes?: string[];
  functions?: string[];
  diagnostics?: ASTDiagnostic[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  stage?: string;
  stats?: {
    deterministicEdits?: number;
    todosMarked?: number;
    tokensUsed?: number;
  };
}

export interface RuleExample {
  before: string;
  after: string;
  explanation: string;
}

export interface ActiveRuleItem {
  id: string;
  title: string;
  category: string;
  strategy: string;
  php_pattern: string;
  rhp_replacement?: string | null;
  unsupported: boolean;
  doc_url?: string | null;
  llm_prompt_guidance?: string | null;
  tags: string[];
  examples: RuleExample[];
}

export interface ActiveRulesResponse {
  file_path?: string | null;
  detected_tags: string[];
  rules: ActiveRuleItem[];
  heuristics: string[];
  rules_count: number;
  heuristics_count: number;
  system_prompt_preview?: string | null;
}

export interface SaveLearningFixRequest {
  title: string;
  context_symptom?: string;
  root_cause?: string;
  working_solution?: string;
  verification?: string;
  key_heuristic?: string;
  content?: string;
}

