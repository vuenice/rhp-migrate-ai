"use client";

import React, { useState, useRef, useEffect } from "react";
import {
  Terminal,
  Send,
  Sparkles,
  ShieldCheck,
  AlertCircle,
  FileCheck2,
  Trash2,
  Copy,
  Check,
  ChevronDown,
  ChevronUp,
  Cpu,
  CornerDownLeft,
  ArrowUpRight,
} from "lucide-react";
import {
  ASTScorecardData,
  CompilerLogEntry,
  ChatMessage,
  FileItem,
} from "@/types/workbench";
import { getStoredBYOKConfig } from "@/lib/byok";

interface AICopilotPanelProps {
  scorecard: ASTScorecardData | null;
  compilerLogs: CompilerLogEntry[];
  chatMessages: ChatMessage[];
  activeFile: FileItem | null;
  isStreaming: boolean;
  onSendMessage: (message: string) => void;
  onClearLogs?: () => void;
  onRunCompilerCheck?: () => void;
  onAcceptAndNext?: () => void;
  onRepromptCritic?: () => void;
  diagnosticsCount?: number;
  migrationStep?: string;
}

const QUICK_PROMPTS = [
  "Convert dynamic arrays to dict<string, int>",
  "Add strict parameter types to all methods",
  "Refactor array_merge to dict spreading",
  "Fix untyped parameters with inferred docblocks",
];

export function AICopilotPanel({
  scorecard,
  compilerLogs,
  chatMessages,
  activeFile,
  isStreaming,
  onSendMessage,
  onClearLogs,
  onRunCompilerCheck,
  onAcceptAndNext,
  onRepromptCritic,
  diagnosticsCount = 0,
  migrationStep = "idle",
}: AICopilotPanelProps) {
  const [inputText, setInputText] = useState("");
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [copiedLogs, setCopiedLogs] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  const byok = getStoredBYOKConfig();

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [compilerLogs]);

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!inputText.trim() || isStreaming) return;
    onSendMessage(inputText.trim());
    setInputText("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleCopyLogs = async () => {
    if (compilerLogs.length === 0) return;
    const text = compilerLogs
      .map((l) => `[${l.timestamp}] [${l.level.toUpperCase()}] ${l.message}`)
      .join("\n");
    await navigator.clipboard.writeText(text);
    setCopiedLogs(true);
    setTimeout(() => setCopiedLogs(false), 2000);
  };

  // Determine readiness score styling
  const readiness = scorecard?.readinessPercentage ?? 82;
  const readinessColor =
    readiness >= 80
      ? "text-emerald-400 border-emerald-500/30 bg-emerald-950/20"
      : readiness >= 50
      ? "text-amber-400 border-amber-500/30 bg-amber-950/20"
      : "text-rose-400 border-rose-500/30 bg-rose-950/20";

  return (
    <section className="flex flex-col h-full bg-neutral-900 text-neutral-200 select-none overflow-hidden">
      {/* Top Header */}
      <div className="flex items-center justify-between px-3.5 py-3 border-b border-neutral-800 bg-neutral-950/40">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-emerald-400" />
          <h2 className="text-xs font-semibold uppercase tracking-wider text-neutral-300">
            AI Copilot & Compiler Loop
          </h2>
        </div>
        <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-neutral-800 border border-neutral-700/60 text-[10px] text-neutral-300 font-mono">
          <Cpu className="h-3 w-3 text-emerald-400" />
          <span className="truncate max-w-[100px]">{byok.modelName}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3.5">
        {/* 1. AST Readiness Scorecard */}
        <div className="rounded-lg border border-neutral-800 bg-neutral-950/60 p-3 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-wider text-neutral-400 flex items-center gap-1.5">
              <FileCheck2 className="h-3.5 w-3.5 text-emerald-400" />
              AST Readiness Scorecard
            </span>
            <span
              className={`px-2 py-0.5 text-xs font-mono font-semibold rounded-md border ${readinessColor}`}
            >
              {readiness.toFixed(0)}% Type Safe
            </span>
          </div>

          {/* Progress bar */}
          <div className="w-full bg-neutral-800 rounded-full h-1.5 overflow-hidden">
            <div
              className={`h-full transition-all duration-500 ${
                readiness >= 80
                  ? "bg-emerald-500"
                  : readiness >= 50
                  ? "bg-amber-400"
                  : "bg-rose-500"
              }`}
              style={{ width: `${Math.min(100, Math.max(0, readiness))}%` }}
            />
          </div>

          {/* Metrics Pills */}
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div className="rounded border border-neutral-800/80 bg-neutral-900/80 px-2 py-1.5">
              <div className="text-neutral-500 text-[10px]">Unsupported PHP</div>
              <div className="font-semibold text-neutral-200 mt-0.5 flex items-center gap-1">
                <span>{scorecard?.unsupportedCount ?? 0}</span>
                <span className="text-[10px] font-normal text-neutral-400">
                  constructs
                </span>
              </div>
            </div>

            <div className="rounded border border-neutral-800/80 bg-neutral-900/80 px-2 py-1.5">
              <div className="text-neutral-500 text-[10px]">Untyped Parameters</div>
              <div className="font-semibold text-neutral-200 mt-0.5 flex items-center gap-1">
                <span>{scorecard?.untypedParamCount ?? 0}</span>
                <span className="text-[10px] font-normal text-neutral-400">
                  missing hints
                </span>
              </div>
            </div>
          </div>

          {/* Diagnostics toggle if issues exist */}
          {scorecard?.diagnostics && scorecard.diagnostics.length > 0 && (
            <div className="pt-1">
              <button
                type="button"
                onClick={() => setShowDiagnostics(!showDiagnostics)}
                className="flex items-center justify-between w-full text-[10px] text-amber-400 hover:text-amber-300 font-mono"
              >
                <span>
                  {scorecard.diagnostics.length} diagnostic warning(s)
                </span>
                {showDiagnostics ? (
                  <ChevronUp className="h-3 w-3" />
                ) : (
                  <ChevronDown className="h-3 w-3" />
                )}
              </button>

              {showDiagnostics && (
                <div className="mt-2 space-y-1.5 max-h-36 overflow-y-auto pr-1">
                  {scorecard.diagnostics.map((diag, idx) => (
                    <div
                      key={idx}
                      className="p-1.5 rounded bg-neutral-900 border border-neutral-800 text-[10px] text-neutral-300"
                    >
                      <div className="flex items-center justify-between text-neutral-500">
                        <span>
                          Line {diag.line}:{diag.column}
                        </span>
                        <span className="text-amber-400 uppercase font-mono">
                          {diag.severity}
                        </span>
                      </div>
                      <p className="mt-0.5 text-neutral-200">{diag.message}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 2. Real-time Compiler Check Logs (`rhp check` output) */}
        <div className="rounded-lg border border-neutral-800 bg-neutral-950/80 overflow-hidden space-y-1">
          {/* Header */}
          <div className="flex items-center justify-between px-3 py-2 border-b border-neutral-800/80 bg-neutral-950 text-xs">
            <span className="font-mono text-neutral-400 flex items-center gap-1.5 text-[11px]">
              <Terminal className="h-3.5 w-3.5 text-sky-400" />
              rhp check verification logs
            </span>
            <div className="flex items-center gap-1">
              {onRunCompilerCheck && (
                <button
                  type="button"
                  onClick={onRunCompilerCheck}
                  title="Run rhp check"
                  className="p-1 text-neutral-400 hover:text-emerald-400 hover:bg-neutral-800 rounded transition-colors"
                >
                  <ShieldCheck className="h-3 w-3" />
                </button>
              )}
              <button
                type="button"
                onClick={handleCopyLogs}
                title="Copy compiler output"
                className="p-1 text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 rounded transition-colors"
              >
                {copiedLogs ? (
                  <Check className="h-3 w-3 text-emerald-400" />
                ) : (
                  <Copy className="h-3 w-3" />
                )}
              </button>
              {onClearLogs && (
                <button
                  type="button"
                  onClick={onClearLogs}
                  title="Clear logs"
                  className="p-1 text-neutral-400 hover:text-rose-400 hover:bg-neutral-800 rounded transition-colors"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              )}
            </div>
          </div>

          {/* Terminal Console Box */}
          <div className="p-2.5 font-mono text-[10.5px] leading-relaxed max-h-48 overflow-y-auto space-y-1 bg-black/50">
            {compilerLogs.length === 0 ? (
              <div className="text-neutral-600 italic">
                No active compiler check running. Click &apos;rhp check&apos; or trigger AI migration.
              </div>
            ) : (
              compilerLogs.map((log) => {
                const colorClass =
                  log.level === "success"
                    ? "text-emerald-400"
                    : log.level === "warn"
                    ? "text-amber-400"
                    : log.level === "error"
                    ? "text-rose-400"
                    : "text-neutral-300";

                return (
                  <div key={log.id} className="flex items-start gap-1.5">
                    <span className="text-neutral-600 shrink-0">
                      [{log.timestamp}]
                    </span>
                    <span className={`break-words ${colorClass}`}>
                      {log.message}
                    </span>
                  </div>
                );
              })
            )}
            <div ref={logsEndRef} />
          </div>
        </div>

        {/* 3. AI Copilot Chat Feed */}
        <div className="rounded-lg border border-neutral-800 bg-neutral-950/60 flex flex-col h-64 overflow-hidden">
          <div className="px-3 py-2 border-b border-neutral-800/80 bg-neutral-950 flex items-center justify-between text-xs">
            <span className="font-medium text-neutral-300 text-[11px] flex items-center gap-1.5">
              <Sparkles className="h-3.5 w-3.5 text-emerald-400" />
              Copilot Interaction Loop
            </span>
            <span className="text-[10px] text-neutral-500 font-mono">
              AST & Compiler Aware
            </span>
          </div>

          {/* Message Stream */}
          <div className="flex-1 overflow-y-auto p-2.5 space-y-2 text-xs">
            {chatMessages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center text-neutral-500 px-4 space-y-1">
                <Sparkles className="h-5 w-5 text-neutral-600 mb-1" />
                <p className="text-[11px]">Direct the AI to refine RHP code types.</p>
                <p className="text-[10px] text-neutral-600">
                  e.g. &quot;Convert dynamic arrays to dict&lt;string, int&gt;&quot;
                </p>
              </div>
            ) : (
              chatMessages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex flex-col ${
                    msg.role === "user" ? "items-end" : "items-start"
                  }`}
                >
                  <div
                    className={`max-w-[90%] rounded-lg px-3 py-2 ${
                      msg.role === "user"
                        ? "bg-emerald-600 text-white"
                        : "bg-neutral-800/80 text-neutral-200 border border-neutral-700/60"
                    }`}
                  >
                    <p className="text-xs whitespace-pre-wrap">{msg.content}</p>

                    {msg.stats && (
                      <div className="mt-1.5 pt-1.5 border-t border-neutral-700/50 flex items-center gap-2 text-[10px] text-neutral-400 font-mono">
                        {msg.stats.deterministicEdits !== undefined && (
                          <span>⚡ {msg.stats.deterministicEdits} AST edits</span>
                        )}
                        {msg.stats.todosMarked !== undefined && (
                          <span>📌 {msg.stats.todosMarked} TODOs</span>
                        )}
                      </div>
                    )}
                  </div>
                  <span className="text-[9px] text-neutral-500 mt-0.5 px-1 font-mono">
                    {msg.timestamp}
                  </span>
                </div>
              ))
            )}
            <div ref={chatEndRef} />
          </div>
        </div>
      </div>

      {/* Interactive Critic & Workflow Action Buttons */}
      {(onRepromptCritic || onAcceptAndNext) && (
        <div className="px-3 pt-2 pb-1 border-t border-neutral-800 bg-neutral-950 flex items-center gap-2">
          {onRepromptCritic && (
            <button
              type="button"
              onClick={onRepromptCritic}
              disabled={isStreaming}
              title="Re-prompt Critic loop with compiler diagnostics for automated repair"
              className="flex-1 flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-amber-600/40 bg-amber-950/40 text-amber-300 hover:bg-amber-900/50 hover:text-amber-100 disabled:opacity-50 transition-colors font-medium text-[11px] shadow-sm"
            >
              <Sparkles className="h-3 w-3 text-amber-400" />
              <span>Re-prompt with Critic</span>
              {diagnosticsCount > 0 && (
                <span className="px-1 py-0.2 rounded bg-amber-500/20 text-[10px] font-mono border border-amber-500/30">
                  {diagnosticsCount}
                </span>
              )}
            </button>
          )}

          {onAcceptAndNext && (
            <button
              type="button"
              onClick={onAcceptAndNext}
              disabled={isStreaming}
              title="Accept current RHP code and advance to next pending file"
              className="flex-1 flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50 transition-colors font-medium text-[11px] shadow-sm"
            >
              <Check className="h-3.5 w-3.5 text-emerald-200" />
              <span>Accept &amp; Next File</span>
            </button>
          )}
        </div>
      )}

      {/* Quick Prompts Chips */}
      <div className="px-3 pt-2 pb-1 border-t border-neutral-800 bg-neutral-950/70">
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 no-scrollbar text-[10px]">
          {QUICK_PROMPTS.map((prompt, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => onSendMessage(prompt)}
              disabled={isStreaming}
              className="shrink-0 px-2 py-1 rounded bg-neutral-800/80 hover:bg-neutral-700 text-neutral-300 hover:text-neutral-100 border border-neutral-700/60 flex items-center gap-1 transition-colors disabled:opacity-50"
            >
              <ArrowUpRight className="h-2.5 w-2.5 text-emerald-400" />
              <span className="truncate max-w-[150px]">{prompt}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Chat Input Box */}
      <div className="p-3 bg-neutral-950 border-t border-neutral-800">
        <form onSubmit={handleSubmit} className="relative">
          <textarea
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming}
            rows={2}
            placeholder={
              activeFile
                ? `Instruct AI on ${activeFile.name} (e.g. "Convert dynamic arrays to dict<string, int>")...`
                : "Select a file first to instruct the AI..."
            }
            className="w-full resize-none rounded-lg border border-neutral-800 bg-neutral-900/90 px-3 py-2 pr-10 text-xs text-neutral-100 placeholder-neutral-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 disabled:opacity-50"
          />

          <button
            type="submit"
            disabled={!inputText.trim() || isStreaming || !activeFile}
            className="absolute right-2 bottom-3 p-1.5 rounded-md bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-30 disabled:hover:bg-emerald-600 transition-colors"
            title="Send (Cmd/Ctrl + Enter)"
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </form>
        <div className="flex items-center justify-between text-[10px] text-neutral-500 mt-1 px-1">
          <span>Press Cmd/Ctrl + Enter to send</span>
          <span className="flex items-center gap-1">
            <CornerDownLeft className="h-2.5 w-2.5" />
            RHP Loop
          </span>
        </div>
      </div>
    </section>
  );
}
