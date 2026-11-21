"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { BYOKConfig, CompilerLogEntry } from "@/types/workbench";
import { buildBYOKHeaders } from "@/lib/byok";

export type MigrationStep =
  | "idle"
  | "analyzing"
  | "transpiling"
  | "compiling"
  | "critic_review"
  | "done"
  | "error";

export interface DiagnosticError {
  line: number;
  col: number;
  message: string;
}

export interface UseMigrationStreamOptions {
  filePath?: string;
  originalCode?: string;
  byokConfig?: BYOKConfig;
  endpoint?: string;
  onChunk?: (chunk: string, accumulated: string) => void;
  onStatusChange?: (step: MigrationStep, message?: string) => void;
  onDiagnostic?: (errors: DiagnosticError[]) => void;
  onComplete?: (finalCode: string, verified: boolean) => void;
  onError?: (error: Error) => void;
  onLog?: (level: "info" | "success" | "warn" | "error", message: string) => void;
}

export interface UseMigrationStreamReturn {
  step: MigrationStep;
  isStreaming: boolean;
  code: string;
  diagnostics: DiagnosticError[];
  verified: boolean | null;
  error: string | null;
  startMigration: (customInstruction?: string) => Promise<void>;
  rePromptWithCritic: (criticInstruction?: string) => Promise<void>;
  cancelMigration: () => void;
  setEditorInstance: (editor: any, monaco: any) => void;
  clearDiagnostics: () => void;
  setCode: React.Dispatch<React.SetStateAction<string>>;
}

/**
 * React hook to manage real-time streaming migration loop from /api/migrate/stream
 * and integrate reactive updates & gutter error markers directly into Monaco editor.
 */
export function useMigrationStream(
  options: UseMigrationStreamOptions = {}
): UseMigrationStreamReturn {
  const {
    filePath = "input.php",
    originalCode = "",
    byokConfig,
    endpoint = "/api/migrate/stream",
    onChunk,
    onStatusChange,
    onDiagnostic,
    onComplete,
    onError,
    onLog,
  } = options;

  const [step, setStep] = useState<MigrationStep>("idle");
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [code, setCode] = useState<string>("");
  const [diagnostics, setDiagnostics] = useState<DiagnosticError[]>([]);
  const [verified, setVerified] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  // References to Monaco editor and monaco library instance
  const editorRef = useRef<any>(null);
  const monacoRef = useRef<any>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const codeAccumulatorRef = useRef<string>("");

  // Store options in refs so callbacks stay stable
  const optionsRef = useRef(options);
  optionsRef.current = options;

  /**
   * Register Monaco editor and monaco instances
   */
  const setEditorInstance = useCallback((editor: any, monaco: any) => {
    editorRef.current = editor;
    monacoRef.current = monaco;

    // Enable glyph margin on the modified editor for clear error marker display
    try {
      const targetEditor =
        typeof editor?.getModifiedEditor === "function"
          ? editor.getModifiedEditor()
          : editor;
      if (targetEditor?.updateOptions) {
        targetEditor.updateOptions({
          glyphMargin: true,
          lightbulb: { enabled: true },
        });
      }
    } catch {
      // ignore
    }
  }, []);

  /**
   * Apply or update error markers in the Monaco editor gutter and code lines
   */
  const applyMonacoMarkers = useCallback((errors: DiagnosticError[]) => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco) return;

    try {
      const targetEditor =
        typeof editor.getModifiedEditor === "function"
          ? editor.getModifiedEditor()
          : editor;
      const model = targetEditor?.getModel();
      if (!model) return;

      const markers = errors.map((err) => {
        const line = Math.max(1, err.line || 1);
        const col = Math.max(1, err.col || 1);
        let endCol = col + 20;
        try {
          const lineContent = model.getLineContent(line) || "";
          endCol = Math.max(col + 1, lineContent.length + 1);
        } catch {
          // fallback
        }

        const severity = monaco.MarkerSeverity
          ? monaco.MarkerSeverity.Error
          : 8;

        return {
          startLineNumber: line,
          startColumn: col,
          endLineNumber: line,
          endColumn: endCol,
          message: err.message,
          severity,
        };
      });

      monaco.editor.setModelMarkers(model, "rhp-critic", markers);
    } catch (err) {
      console.warn("Failed to apply Monaco editor markers:", err);
    }
  }, []);

  /**
   * Clear error markers from Monaco editor
   */
  const clearDiagnostics = useCallback(() => {
    setDiagnostics([]);
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco) return;

    try {
      const targetEditor =
        typeof editor.getModifiedEditor === "function"
          ? editor.getModifiedEditor()
          : editor;
      const model = targetEditor?.getModel();
      if (model) {
        monaco.editor.setModelMarkers(model, "rhp-critic", []);
      }
    } catch {
      // ignore
    }
  }, []);

  /**
   * Cancel any active streaming migration
   */
  const cancelMigration = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  /**
   * Execute migration stream against /api/migrate/stream
   */
  const startMigration = useCallback(
    async (customInstruction?: string) => {
      cancelMigration();

      const controller = new AbortController();
      abortControllerRef.current = controller;

      setIsStreaming(true);
      setError(null);
      setVerified(null);
      clearDiagnostics();
      codeAccumulatorRef.current = "";
      setCode("");

      // Helper log
      const log = (level: "info" | "success" | "warn" | "error", msg: string) => {
        optionsRef.current.onLog?.(level, msg);
      };

      try {
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          ...(byokConfig ? buildBYOKHeaders(byokConfig) : {}),
        };

        const payload = {
          file_path: filePath,
          code: originalCode,
          user_instruction: customInstruction,
          php_version: "8.2",
          run_critic: true,
          max_retries: 2,
        };

        log("info", `Initiating migration stream for ${filePath}...`);

        const response = await fetch(endpoint, {
          method: "POST",
          headers,
          body: JSON.stringify(payload),
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`Server returned status ${response.status}: ${response.statusText}`);
        }

        if (!response.body) {
          throw new Error("ReadableStream not supported by response body.");
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split(/\r?\n\r?\n/);
          buffer = blocks.pop() || "";

          for (const block of blocks) {
            if (!block.trim()) continue;

            let eventType = "message";
            let dataStr = "";

            for (const line of block.split(/\r?\n/)) {
              if (line.startsWith("event:")) {
                eventType = line.replace(/^event:\s*/, "").trim();
              } else if (line.startsWith("data:")) {
                const chunk = line.replace(/^data:\s*/, "");
                dataStr = dataStr ? `${dataStr}\n${chunk}` : chunk;
              }
            }

            if (!dataStr) continue;

            try {
              const parsed = JSON.parse(dataStr);

              // 1. event: status
              if (eventType === "status") {
                const nextStep = parsed.step as MigrationStep;
                setStep(nextStep);
                optionsRef.current.onStatusChange?.(nextStep, parsed.message);
                if (parsed.message) {
                  log("info", `[${nextStep}] ${parsed.message}`);
                }
              }

              // 2. event: code_chunk
              else if (eventType === "code_chunk") {
                const chunk = parsed.chunk ?? "";
                codeAccumulatorRef.current += chunk;
                const accumulated = codeAccumulatorRef.current;
                setCode(accumulated);

                // Update Monaco right-side editor directly for real-time responsiveness
                try {
                  const editor = editorRef.current;
                  const targetEditor =
                    typeof editor?.getModifiedEditor === "function"
                      ? editor.getModifiedEditor()
                      : editor;
                  const model = targetEditor?.getModel();
                  if (model) {
                    model.setValue(accumulated);
                  }
                } catch {
                  // ignore
                }

                optionsRef.current.onChunk?.(chunk, accumulated);
              }

              // 3. event: diagnostic
              else if (eventType === "diagnostic") {
                const rawErrors = parsed.errors || [];
                const formattedErrors: DiagnosticError[] = rawErrors.map((e: any) => ({
                  line: Number(e.line || 1),
                  col: Number(e.col ?? e.column ?? 1),
                  message: String(e.message || "Compiler diagnostic defect"),
                }));

                setDiagnostics(formattedErrors);
                applyMonacoMarkers(formattedErrors);
                optionsRef.current.onDiagnostic?.(formattedErrors);

                if (formattedErrors.length > 0) {
                  log(
                    "warn",
                    `rhp check: ${formattedErrors.length} diagnostic error(s) flagged on lines: ${formattedErrors
                      .map((e) => e.line)
                      .join(", ")}`
                  );
                } else {
                  log("success", "rhp check: 0 errors detected. Syntax & types verified.");
                }
              }

              // 4. event: complete
              else if (eventType === "complete") {
                const finalCode = parsed.final_code ?? codeAccumulatorRef.current;
                const isVerified = Boolean(parsed.verified);
                codeAccumulatorRef.current = finalCode;
                setCode(finalCode);
                setVerified(isVerified);

                // Update Monaco editor with final code
                try {
                  const editor = editorRef.current;
                  const targetEditor =
                    typeof editor?.getModifiedEditor === "function"
                      ? editor.getModifiedEditor()
                      : editor;
                  const model = targetEditor?.getModel();
                  if (model) {
                    model.setValue(finalCode);
                  }
                } catch {
                  // ignore
                }

                optionsRef.current.onComplete?.(finalCode, isVerified);
                log(
                  isVerified ? "success" : "warn",
                  `Migration loop finished. Verified: ${isVerified ? "PASS" : "FAIL"}`
                );
              }

              // Fallback / legacy events compatibility
              else if (eventType === "final_code" && parsed.data?.final_code) {
                const finalCode = parsed.data.final_code;
                setCode(finalCode);
                codeAccumulatorRef.current = finalCode;
              } else if (eventType === "done") {
                setStep("done");
              } else if (eventType === "error") {
                throw new Error(parsed.message || "Backend error during migration stream");
              }
            } catch (jsonErr: any) {
              // Ignore single malformed line parse errors
              if (eventType === "error") {
                throw jsonErr;
              }
            }
          }
        }

        setStep("done");
      } catch (err: any) {
        if (err.name === "AbortError") {
          log("info", "Migration stream cancelled by user.");
          return;
        }

        const msg = err.message || "Failed to stream migration from server.";
        setError(msg);
        setStep("error");
        log("error", `Migration stream error: ${msg}`);
        optionsRef.current.onError?.(err);
      } finally {
        setIsStreaming(false);
        abortControllerRef.current = null;
      }
    },
    [
      endpoint,
      filePath,
      originalCode,
      byokConfig,
      cancelMigration,
      clearDiagnostics,
      applyMonacoMarkers,
    ]
  );

  /**
   * Re-prompt with Critic loop passing compiler diagnostics and instructions
   */
  const rePromptWithCritic = useCallback(
    async (criticInstruction?: string) => {
      const errorSummary =
        diagnostics.length > 0
          ? `Fix compiler diagnostics: ${diagnostics.map((d) => `L${d.line}:${d.col} ${d.message}`).join("; ")}`
          : "Review and refine types, dict shapes, and constructor property promotion.";

      const prompt = criticInstruction
        ? `${criticInstruction}. Context: ${errorSummary}`
        : errorSummary;

      await startMigration(prompt);
    },
    [diagnostics, startMigration]
  );

  // Clean up on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  return {
    step,
    isStreaming,
    code,
    diagnostics,
    verified,
    error,
    startMigration,
    rePromptWithCritic,
    cancelMigration,
    setEditorInstance,
    clearDiagnostics,
    setCode,
  };
}
