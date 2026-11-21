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
