"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
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
  BookOpen,
  Plus,
  Brain,
  Tag,
  ExternalLink,
  Layers,
  Lightbulb,
  CheckCircle2,
  X,
  Code2,
  Bookmark,
} from "lucide-react";
import {
  ASTScorecardData,
  CompilerLogEntry,
  ChatMessage,
  FileItem,
  ActiveRuleItem,
  ActiveRulesResponse,
  SaveLearningFixRequest,
} from "@/types/workbench";
import { getStoredBYOKConfig } from "@/lib/byok";

interface AICopilotPanelProps {
  scorecard: ASTScorecardData | null;
  compilerLogs: CompilerLogEntry[];
  chatMessages: ChatMessage[];
  activeFile: FileItem | null;
  activeCode?: string;
  workspaceId?: string;
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

// Fallback rules & heuristics when backend is offline or loading
const FALLBACK_RULES_DATA: ActiveRulesResponse = {
  detected_tags: [
    "has_superglobals",
    "has_dynamic_arrays",
    "has_prefix_types",
    "has_strict_equality",
  ],
  rules: [
    {
      id: "RHP-HTTP-002",
      title: "Replace superglobals with Request facade",
      category: "HTTP_FACADE",
      strategy: "DETERMINISTIC",
      php_pattern: "$_GET['k'], $_POST['k'], $_REQUEST['k']",
      rhp_replacement: "Request::query('k'), Request::body('k'), Request::input('k')",
      unsupported: false,
      doc_url: "https://rhp.dev/docs/facades/request",
      llm_prompt_guidance: "Replace direct access to superglobals $_GET, $_POST with typed methods on the Request facade.",
      tags: ["has_superglobals"],
      examples: [
        {
          before: "$q = $_GET['q'];",
          after: "$q = Request::query('q');",
          explanation: "Superglobals replaced by typed Request facade",
        },
      ],
    },
    {
      id: "RHP-SYN-007",
      title: "Use prefix type syntax for variable declarations",
      category: "TYPE_SYSTEM",
      strategy: "DETERMINISTIC",
      php_pattern: "$var: Type = value; or Type $var = value;",
      rhp_replacement: "Type $var = value;",
      unsupported: false,
      doc_url: "https://rhp.dev/docs/syntax/variables",
      llm_prompt_guidance: "RHP uses C/Java-style prefix type syntax ('Type $var = expr;'), NOT postfix colon type syntax ('$var: Type').",
      tags: ["has_dynamic_arrays", "has_prefix_types", "has_typed_vars"],
      examples: [
        {
          before: "$name: string = 'Alice';",
          after: "string $name = 'Alice';",
          explanation: "Prefix type syntax required by RHP parser",
        },
      ],
    },
    {
      id: "RHP-SYN-003",
      title: "Replace strict equality with standard equality",
      category: "SYNTAX",
      strategy: "DETERMINISTIC",
      php_pattern: "$a === $b or $a !== $b",
      rhp_replacement: "$a == $b or $a != $b",
      unsupported: false,
      doc_url: "https://rhp.dev/docs/syntax/operators",
      llm_prompt_guidance: "Equality operators == and != are strict by default in RHP.",
      tags: ["has_strict_equality"],
      examples: [
        {
          before: "if ($user->status === 'active') { return true; }",
          after: "if ($user->status == 'active') { return true; }",
          explanation: "Standard equality is strict in RHP",
        },
      ],
    },
  ],
  heuristics: [
    "**RHP Type System Disambiguation Rules**: Treat compiler errors from rhp check as ground truth; never bypass with mixed unless strictly unavoidable.",
    "**RHP Prefix-Typed Variable Declaration Syntax**: Always use prefix typing (Type name = expr;) for typed variable declarations in RHP.",
    "**Deterministic Mistake Auditing**: Never assume deterministic migration is flawless; validate output syntax first and audit mistakes before proceeding to LLM or commit steps.",
    "**Dual-Loop Learning**: Log working fixes immediately into learnings.md; graduate high-value rules into context.md / rules.yaml.",
  ],
  rules_count: 3,
  heuristics_count: 4,
};

export function AICopilotPanel({
  scorecard,
  compilerLogs,
  chatMessages,
  activeFile,
  activeCode,
  workspaceId,
  isStreaming,
  onSendMessage,
  onClearLogs,
  onRunCompilerCheck,
  onAcceptAndNext,
  onRepromptCritic,
  diagnosticsCount = 0,
  migrationStep = "idle",
}: AICopilotPanelProps) {
  // Navigation tabs: Copilot & Loop vs Learned Rules
  const [activeTab, setActiveTab] = useState<"copilot" | "rules">("copilot");
  const [rulesSubTab, setRulesSubTab] = useState<"rules" | "heuristics">("rules");

  // Rules and Learnings state
  const [activeRulesData, setActiveRulesData] = useState<ActiveRulesResponse | null>(null);
  const [isLoadingRules, setIsLoadingRules] = useState<boolean>(false);
  const [expandedRuleId, setExpandedRuleId] = useState<string | null>(null);

  // Save Fix to Learnings modal state
  const [isSaveModalOpen, setIsSaveModalOpen] = useState(false);
  const [fixTitle, setFixTitle] = useState("");
  const [fixContext, setFixContext] = useState("");
  const [fixRootCause, setFixRootCause] = useState("");
  const [fixWorkingSolution, setFixWorkingSolution] = useState("");
  const [fixKeyHeuristic, setFixKeyHeuristic] = useState("");
  const [fixVerification, setFixVerification] = useState("");
  const [isSavingFix, setIsSavingFix] = useState(false);
  const [saveBannerMsg, setSaveBannerMsg] = useState<string | null>(null);

  // Copilot Chat state
  const [inputText, setInputText] = useState("");
  const [showDiagnostics, setShowDiagnostics] = useState(false);
  const [copiedLogs, setCopiedLogs] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  const byok = getStoredBYOKConfig();

  // Scroll triggers
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [compilerLogs]);

  // Fetch active AST rules for the currently open file
  const fetchActiveRules = useCallback(async () => {
    setIsLoadingRules(true);
    try {
      const res = await fetch("/api/rules/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          file_path: activeFile?.path || null,
          code: activeCode || null,
          workspace_id: workspaceId || null,
        }),
      });

      if (res.ok) {
        const data: ActiveRulesResponse = await res.json();
        setActiveRulesData(data);
      } else {
        // Fallback to client data if backend route unavailable
        setActiveRulesData(FALLBACK_RULES_DATA);
      }
    } catch {
      setActiveRulesData(FALLBACK_RULES_DATA);
    } finally {
      setIsLoadingRules(false);
    }
  }, [activeFile?.path, activeCode, workspaceId]);

  // Refetch rules when file or code changes, or when rules tab is opened
  useEffect(() => {
    fetchActiveRules();
  }, [fetchActiveRules]);

  // Pre-fill Save Fix Modal with current context
  const handleOpenSaveModal = () => {
    const fileName = activeFile?.name || "current_file.php";
    const latestError = compilerLogs.filter((l) => l.level === "error").slice(-1)[0]?.message;
    const firstDiag = scorecard?.diagnostics?.[0]?.message;

    setFixTitle(`Fix for ${fileName}: strict typing and AST alignment`);
    setFixContext(
      latestError || firstDiag || `Type mismatch or compiler diagnostic flagged in ${fileName}`
    );
    setFixRootCause(
      "Dynamic PHP polymorphic structures and missing parameter/return types incompatible with strict RHP ahead-of-time compilation."
    );
    setFixWorkingSolution(
      "Apply prefix type declarations ('Type $var = ...') and replace PHP superglobals with typed Request facade methods."
    );
    setFixKeyHeuristic(
      "Always declare explicit prefix types ('Type $name') and verify with 'rhp check' before committing."
    );
    setFixVerification("Verified with rhp check and compiler verification loop.");
    setIsSaveModalOpen(true);
  };

  // Submit fix to learnings.md
  const handleSaveFix = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fixTitle.trim()) return;

    setIsSavingFix(true);
    try {
      const payload: SaveLearningFixRequest = {
        title: fixTitle.trim(),
        context_symptom: fixContext.trim(),
        root_cause: fixRootCause.trim(),
        working_solution: fixWorkingSolution.trim(),
        key_heuristic: fixKeyHeuristic.trim(),
        verification: fixVerification.trim(),
      };

      const res = await fetch("/api/rules/learnings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        setSaveBannerMsg(`Successfully appended fix "${fixTitle}" to learnings.md!`);
        setIsSaveModalOpen(false);
        // Refresh active rules & heuristics
        await fetchActiveRules();
        setTimeout(() => setSaveBannerMsg(null), 4000);
      } else {
        alert("Failed to append learning entry. Please check server logs.");
      }
    } catch (err) {
      alert(`Error saving to learnings.md: ${err}`);
    } finally {
      setIsSavingFix(false);
    }
  };

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

  const readiness = scorecard?.readinessPercentage ?? 82;
  const readinessColor =
    readiness >= 80
      ? "text-emerald-400 border-emerald-500/30 bg-emerald-950/20"
      : readiness >= 50
      ? "text-amber-400 border-amber-500/30 bg-amber-950/20"
      : "text-rose-400 border-rose-500/30 bg-rose-950/20";

  const rulesData = activeRulesData || FALLBACK_RULES_DATA;

  return (
    <section className="flex flex-col h-full bg-neutral-900 text-neutral-200 select-none overflow-hidden relative">
      {/* Top Header */}
      <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-neutral-800 bg-neutral-950/60">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-emerald-400" />
          <h2 className="text-xs font-semibold uppercase tracking-wider text-neutral-300">
            AI Copilot &amp; Rules
          </h2>
        </div>
        <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-neutral-800 border border-neutral-700/60 text-[10px] text-neutral-300 font-mono">
          <Cpu className="h-3 w-3 text-emerald-400" />
          <span className="truncate max-w-[90px]">{byok.modelName}</span>
        </div>
      </div>

      {/* Navigation Tab Switcher: Copilot & Loop vs Learned Rules */}
      <div className="flex items-center border-b border-neutral-800 bg-neutral-950/80 px-2 py-1 gap-1 text-xs">
        <button
          type="button"
          onClick={() => setActiveTab("copilot")}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-medium text-xs transition-colors ${
            activeTab === "copilot"
              ? "bg-neutral-800 text-emerald-400 border border-neutral-700/70 shadow-sm"
              : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/40"
          }`}
        >
          <Sparkles className="h-3.5 w-3.5" />
          <span>Copilot &amp; Loop</span>
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("rules")}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md font-medium text-xs transition-colors ${
            activeTab === "rules"
              ? "bg-neutral-800 text-emerald-400 border border-neutral-700/70 shadow-sm"
              : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/40"
          }`}
        >
          <BookOpen className="h-3.5 w-3.5" />
          <span>Learned Rules</span>
          <span className="px-1.5 py-0.2 rounded-full text-[10px] font-mono bg-emerald-950/60 text-emerald-400 border border-emerald-800/50">
            {rulesData.rules_count}
          </span>
        </button>
      </div>

      {/* Save Success Notification Banner */}
      {saveBannerMsg && (
        <div className="mx-3 mt-2 p-2 rounded-lg bg-emerald-950/60 border border-emerald-500/40 text-emerald-300 text-xs flex items-center justify-between animate-fadeIn">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-400 shrink-0" />
            <span>{saveBannerMsg}</span>
          </div>
          <button
            type="button"
            onClick={() => setSaveBannerMsg(null)}
            className="text-emerald-400 hover:text-emerald-200"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* ===================================================================
          TAB 1: COPILOT & COMPILER LOOP
      =================================================================== */}
      {activeTab === "copilot" && (
        <div className="flex-1 flex flex-col overflow-hidden">
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
                    <span>{scorecard.diagnostics.length} diagnostic warning(s)</span>
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
              <div className="p-2.5 font-mono text-[10.5px] leading-relaxed max-h-40 overflow-y-auto space-y-1 bg-black/50">
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
            <div className="rounded-lg border border-neutral-800 bg-neutral-950/60 flex flex-col h-56 overflow-hidden">
              <div className="px-3 py-2 border-b border-neutral-800/80 bg-neutral-950 flex items-center justify-between text-xs">
                <span className="font-medium text-neutral-300 text-[11px] flex items-center gap-1.5">
                  <Sparkles className="h-3.5 w-3.5 text-emerald-400" />
                  Copilot Interaction Loop
                </span>
                <span className="text-[10px] text-neutral-500 font-mono">
                  AST &amp; Compiler Aware
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

          {/* Action Buttons: Critic & Advance */}
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
        </div>
      )}

      {/* ===================================================================
          TAB 2: LEARNED RULES & PERSISTENT WORKING MEMORY INSPECTOR
      =================================================================== */}
      {activeTab === "rules" && (
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Top Inspector Bar with "+ Save Current Fix to Learnings" */}
          <div className="p-3 border-b border-neutral-800 bg-neutral-950/90 flex items-center justify-between gap-2">
            <div>
              <div className="text-[11px] font-semibold text-neutral-200 flex items-center gap-1.5">
                <Brain className="h-3.5 w-3.5 text-emerald-400" />
                Working Memory &amp; Rules
              </div>
              <div className="text-[10px] text-neutral-400 truncate max-w-[200px]">
                {activeFile ? `File: ${activeFile.name}` : "Workspace rules"}
              </div>
            </div>

            <button
              type="button"
              onClick={handleOpenSaveModal}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium shadow-sm transition-colors shrink-0"
              title="Save current file fix into learnings.md working memory"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>Save Fix to Learnings</span>
            </button>
          </div>

          {/* Sub-Tabs: Active Rules vs Heuristics */}
          <div className="flex items-center border-b border-neutral-800 bg-neutral-950 px-3 py-1.5 gap-2 text-xs">
            <button
              type="button"
              onClick={() => setRulesSubTab("rules")}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors ${
                rulesSubTab === "rules"
                  ? "bg-neutral-800 text-emerald-400"
                  : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              <Layers className="h-3 w-3" />
              <span>Targeted Rules ({rulesData.rules.length})</span>
            </button>

            <button
              type="button"
              onClick={() => setRulesSubTab("heuristics")}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors ${
                rulesSubTab === "heuristics"
                  ? "bg-neutral-800 text-emerald-400"
                  : "text-neutral-400 hover:text-neutral-200"
              }`}
            >
              <Lightbulb className="h-3 w-3 text-amber-400" />
              <span>Active Heuristics ({rulesData.heuristics.length})</span>
            </button>
          </div>

          {/* Sub-Tab Content Area */}
          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {/* SUB-TAB 1: TARGETED RULES */}
            {rulesSubTab === "rules" && (
              <div className="space-y-3">
                {/* Detected AST Tags Bar */}
                <div className="p-2.5 rounded-lg border border-neutral-800 bg-neutral-950/60 space-y-1.5">
                  <div className="text-[10px] font-medium uppercase tracking-wider text-neutral-400 flex items-center gap-1">
                    <Tag className="h-3 w-3 text-emerald-400" />
                    Detected AST Construct Tags:
                  </div>

                  {rulesData.detected_tags.length === 0 ? (
                    <div className="text-[11px] text-neutral-500 italic">
                      No special AST constructs detected for this file.
                    </div>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {rulesData.detected_tags.map((tag) => (
                        <span
                          key={tag}
                          className="px-2 py-0.5 rounded-full text-[10px] font-mono bg-neutral-800 border border-neutral-700 text-neutral-300"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Rules List */}
                <div className="space-y-2">
                  <div className="text-[10px] uppercase font-medium tracking-wider text-neutral-400">
                    Active Rules Applied to Current File:
                  </div>

                  {rulesData.rules.length === 0 ? (
                    <div className="p-4 rounded-lg border border-neutral-800 bg-neutral-950/40 text-center text-neutral-500 text-xs">
                      No targeted rules matched for this file.
                    </div>
                  ) : (
                    rulesData.rules.map((rule) => {
                      const isExpanded = expandedRuleId === rule.id;
                      const isUnsupported = rule.unsupported;

                      return (
                        <div
                          key={rule.id}
                          className="rounded-lg border border-neutral-800 bg-neutral-950/70 overflow-hidden text-xs transition-colors hover:border-neutral-700"
                        >
                          {/* Rule Header */}
                          <div
                            onClick={() =>
                              setExpandedRuleId(isExpanded ? null : rule.id)
                            }
                            className="p-2.5 flex items-start justify-between cursor-pointer gap-2"
                          >
                            <div className="space-y-1">
                              <div className="flex items-center gap-1.5">
                                <span className="font-mono font-semibold text-emerald-400 text-[11px]">
                                  {rule.id}
                                </span>
                                <span className="px-1.5 py-0.2 rounded text-[9px] font-mono bg-neutral-800 border border-neutral-700 text-neutral-300 uppercase">
                                  {rule.category}
                                </span>
                                <span
                                  className={`px-1.5 py-0.2 rounded text-[9px] font-mono uppercase ${
                                    isUnsupported
                                      ? "bg-rose-950 text-rose-300 border border-rose-800"
                                      : "bg-sky-950 text-sky-300 border border-sky-800"
                                  }`}
                                >
                                  {rule.strategy}
                                </span>
                              </div>
                              <div className="font-medium text-neutral-200 text-xs">
                                {rule.title}
                              </div>
                            </div>

                            <button
                              type="button"
                              className="text-neutral-500 hover:text-neutral-300 p-0.5 mt-0.5"
                            >
                              {isExpanded ? (
                                <ChevronUp className="h-3.5 w-3.5" />
                              ) : (
                                <ChevronDown className="h-3.5 w-3.5" />
                              )}
                            </button>
                          </div>

                          {/* Collapsible Details */}
                          {isExpanded && (
                            <div className="px-2.5 pb-2.5 pt-1 border-t border-neutral-800/80 space-y-2 text-[11px]">
                              {/* Pattern & Replacement */}
                              <div className="space-y-1">
                                <div className="text-neutral-400 text-[10px]">
                                  PHP Pattern:
                                </div>
                                <div className="p-1.5 rounded bg-neutral-900 border border-neutral-800 font-mono text-[10px] text-neutral-300 break-all">
                                  {rule.php_pattern}
                                </div>
                              </div>

                              {rule.rhp_replacement && (
                                <div className="space-y-1">
                                  <div className="text-neutral-400 text-[10px]">
                                    RHP Replacement:
                                  </div>
                                  <div className="p-1.5 rounded bg-emerald-950/30 border border-emerald-800/40 font-mono text-[10px] text-emerald-300 break-all">
                                    {rule.rhp_replacement}
                                  </div>
                                </div>
                              )}

                              {rule.llm_prompt_guidance && (
                                <div className="text-neutral-400 text-[10.5px] italic bg-neutral-900/60 p-1.5 rounded border border-neutral-800/60">
                                  {rule.llm_prompt_guidance}
                                </div>
                              )}

                              {/* Example before / after */}
                              {rule.examples && rule.examples.length > 0 && (
                                <div className="space-y-1 pt-1">
                                  <div className="text-neutral-400 text-[10px] flex items-center gap-1">
                                    <Code2 className="h-3 w-3 text-emerald-400" />
                                    Example ({rule.examples[0].explanation}):
                                  </div>
                                  <div className="grid grid-cols-2 gap-1.5 font-mono text-[9.5px]">
                                    <div className="p-1.5 rounded bg-neutral-900 border border-neutral-800 text-rose-300 overflow-x-auto whitespace-pre">
                                      <span className="text-neutral-500">// BEFORE:{"\n"}</span>
                                      {rule.examples[0].before.trim()}
                                    </div>
                                    <div className="p-1.5 rounded bg-neutral-900 border border-neutral-800 text-emerald-300 overflow-x-auto whitespace-pre">
                                      <span className="text-neutral-500">// AFTER:{"\n"}</span>
                                      {rule.examples[0].after.trim()}
                                    </div>
                                  </div>
                                </div>
                              )}

                              {rule.doc_url && (
                                <div className="pt-1">
                                  <a
                                    href={rule.doc_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="text-sky-400 hover:text-sky-300 flex items-center gap-1 text-[10px]"
                                  >
                                    <span>RHP Reference Documentation</span>
                                    <ExternalLink className="h-2.5 w-2.5" />
                                  </a>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            )}

            {/* SUB-TAB 2: HEURISTICS FROM LEARNINGS.MD */}
            {rulesSubTab === "heuristics" && (
              <div className="space-y-2">
                <div className="text-[10px] uppercase font-medium tracking-wider text-neutral-400 flex items-center justify-between">
                  <span>System Memory (learnings.md):</span>
                  <span className="text-emerald-400 font-mono text-[10px]">
                    Auto-injected into LLM
                  </span>
                </div>

                {rulesData.heuristics.length === 0 ? (
                  <div className="p-4 rounded-lg border border-neutral-800 bg-neutral-950/40 text-center text-neutral-500 text-xs">
                    No active heuristics loaded.
                  </div>
                ) : (
                  rulesData.heuristics.map((heuristic, idx) => (
                    <div
                      key={idx}
                      className="p-2.5 rounded-lg border border-neutral-800 bg-neutral-950/70 text-xs space-y-1 hover:border-neutral-700 transition-colors"
                    >
                      <div className="flex items-start gap-2">
                        <Bookmark className="h-3.5 w-3.5 text-amber-400 shrink-0 mt-0.5" />
                        <div className="text-neutral-200 leading-relaxed text-[11px]">
                          {heuristic}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ===================================================================
          SAVE FIX TO LEARNINGS MODAL DIALOG
      =================================================================== */}
      {isSaveModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-neutral-900 border border-neutral-800 rounded-xl max-w-lg w-full p-4 shadow-2xl space-y-3 animate-fadeIn text-xs">
            <div className="flex items-center justify-between border-b border-neutral-800 pb-2.5">
              <div className="flex items-center gap-2">
                <Brain className="h-4 w-4 text-emerald-400" />
                <h3 className="font-semibold text-neutral-100 text-sm">
                  Save Fix to Persistent Working Memory
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setIsSaveModalOpen(false)}
                className="text-neutral-400 hover:text-neutral-200"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <p className="text-[11px] text-neutral-400">
              Appends a structured entry to <code className="text-emerald-400">learnings.md</code>.
              The AI engine will automatically inject this heuristic into future migration prompts.
            </p>

            <form onSubmit={handleSaveFix} className="space-y-2.5">
              <div>
                <label className="block text-[10px] font-medium text-neutral-300 uppercase mb-1">
                  Title:
                </label>
                <input
                  type="text"
                  value={fixTitle}
                  onChange={(e) => setFixTitle(e.target.value)}
                  required
                  placeholder="e.g. Strict return types on Controller actions"
                  className="w-full rounded-md border border-neutral-800 bg-neutral-950 px-2.5 py-1.5 text-xs text-neutral-100 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-[10px] font-medium text-neutral-300 uppercase mb-1">
                  Context / Symptom:
                </label>
                <textarea
                  value={fixContext}
                  onChange={(e) => setFixContext(e.target.value)}
                  rows={2}
                  placeholder="What failed or needed resolution? (Error message, compiler check)"
                  className="w-full resize-none rounded-md border border-neutral-800 bg-neutral-950 px-2.5 py-1.5 text-xs text-neutral-100 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] font-medium text-neutral-300 uppercase mb-1">
                    Root Cause:
                  </label>
                  <textarea
                    value={fixRootCause}
                    onChange={(e) => setFixRootCause(e.target.value)}
                    rows={2}
                    placeholder="Why did the previous approach fail?"
                    className="w-full resize-none rounded-md border border-neutral-800 bg-neutral-950 px-2.5 py-1.5 text-xs text-neutral-100 focus:border-emerald-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="block text-[10px] font-medium text-neutral-300 uppercase mb-1">
                    Verification:
                  </label>
                  <textarea
                    value={fixVerification}
                    onChange={(e) => setFixVerification(e.target.value)}
                    rows={2}
                    placeholder="e.g. rhp check passes with 0 errors"
                    className="w-full resize-none rounded-md border border-neutral-800 bg-neutral-950 px-2.5 py-1.5 text-xs text-neutral-100 focus:border-emerald-500 focus:outline-none"
                  />
                </div>
              </div>

              <div>
                <label className="block text-[10px] font-medium text-neutral-300 uppercase mb-1">
                  Working Solution:
                </label>
                <textarea
                  value={fixWorkingSolution}
                  onChange={(e) => setFixWorkingSolution(e.target.value)}
                  rows={2}
                  placeholder="What exact pattern or configuration resolved the issue?"
                  className="w-full resize-none rounded-md border border-neutral-800 bg-neutral-950 px-2.5 py-1.5 text-xs text-neutral-100 focus:border-emerald-500 focus:outline-none font-mono"
                />
              </div>

              <div>
                <label className="block text-[10px] font-medium text-emerald-400 uppercase mb-1 flex items-center gap-1">
                  <Lightbulb className="h-3 w-3" />
                  Key Heuristic (Actionable Prompt Rule):
                </label>
                <input
                  type="text"
                  value={fixKeyHeuristic}
                  onChange={(e) => setFixKeyHeuristic(e.target.value)}
                  placeholder="1-2 sentence rule-of-thumb for future operations"
                  className="w-full rounded-md border border-emerald-500/50 bg-neutral-950 px-2.5 py-1.5 text-xs text-emerald-200 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-neutral-800">
                <button
                  type="button"
                  onClick={() => setIsSaveModalOpen(false)}
                  disabled={isSavingFix}
                  className="px-3 py-1.5 rounded-lg border border-neutral-700 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 text-xs transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSavingFix || !fixTitle.trim()}
                  className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium shadow-sm transition-colors flex items-center gap-1.5 disabled:opacity-50"
                >
                  {isSavingFix ? (
                    <span>Saving...</span>
                  ) : (
                    <>
                      <Check className="h-3.5 w-3.5" />
                      <span>Save to learnings.md</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}
