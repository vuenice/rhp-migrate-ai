"use client";

import React, { useState, useRef, useEffect } from "react";
import { Editor, DiffEditor } from "@monaco-editor/react";
import {
  FileCode2,
  Copy,
  Check,
  RotateCcw,
  Play,
  Columns2,
  Rows2,
  ShieldCheck,
  AlertTriangle,
  Loader2,
  Sparkles,
  ArrowRight,
  ArrowLeft,
  CheckCircle2,
  RefreshCw,
  AlertCircle,
  Zap,
  Square,
} from "lucide-react";
import { FileItem, FileStatus, ASTScorecardData } from "@/types/workbench";
import { DiagnosticError, MigrationStep } from "@/hooks/useMigrationStream";

interface MonacoDiffViewerProps {
  originalCode: string;
  modifiedCode: string;
  activeFile: FileItem | null;
  onChangeModified?: (newCode: string) => void;
  isStreaming?: boolean;
  onMigrateClick?: () => void;
  onStopStreaming?: () => void;
  onVerifyClick?: () => void;
  onMount?: (editor: any, monaco: any) => void;
  onAcceptAndNext?: () => void;
  onRepromptCritic?: () => void;
  onBackToOverview?: () => void;
  hasMigrated?: boolean;
  scorecard?: ASTScorecardData | null;
  migrationStep?: MigrationStep;
  diagnostics?: DiagnosticError[];
  isVerified?: boolean | null;
  hasNextFile?: boolean;
}

export function MonacoDiffViewer({
  originalCode,
  modifiedCode,
  activeFile,
  onChangeModified,
  isStreaming = false,
  onMigrateClick,
  onStopStreaming,
  onVerifyClick,
  onMount,
  onAcceptAndNext,
  onRepromptCritic,
  onBackToOverview,
  hasMigrated,
  scorecard,
  migrationStep = "idle",
  diagnostics = [],
  isVerified = null,
  hasNextFile = false,
}: MonacoDiffViewerProps) {
  const [copied, setCopied] = useState(false);
  const [renderSideBySide, setRenderSideBySide] = useState(true);
  const [isMounted, setIsMounted] = useState(false);
  const [showRawPreMigrationEditor, setShowRawPreMigrationEditor] = useState(false);
  const diffEditorRef = useRef<any>(null);

  useEffect(() => {
    setIsMounted(true);
  }, []);

  // Handle editor mount with editor & monaco instances
  const handleEditorDidMount = (editor: any, monaco: any) => {
    diffEditorRef.current = editor;

    if (onMount) {
      onMount(editor, monaco);
    }

    // Get modified editor instance to configure gutter margin & listen for typing
    const modifiedEditor = editor.getModifiedEditor();
    if (modifiedEditor) {
      try {
        modifiedEditor.updateOptions({
          glyphMargin: true,
          lightbulb: { enabled: true },
        });
      } catch {
        // ignore
      }

      modifiedEditor.onDidChangeModelContent(() => {
        const val = modifiedEditor.getValue();
        if (onChangeModified) {
          onChangeModified(val);
        }
      });
    }
  };

  const handleCopy = async () => {
    if (!modifiedCode) return;
    try {
      await navigator.clipboard.writeText(modifiedCode);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Failed to copy RHP code", err);
    }
  };

  const handleRevert = () => {
    if (!originalCode) return;
    if (onChangeModified) {
      onChangeModified(originalCode);
    }
  };

  if (!activeFile) {
    return (
      <main className="flex flex-col h-full items-center justify-center bg-neutral-950 p-6 text-center select-none">
        <div className="max-w-md space-y-4">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-neutral-900 border border-neutral-800 text-emerald-400 shadow-xl">
            <Columns2 className="h-7 w-7" />
          </div>
          <div className="space-y-1.5">
            <h3 className="text-base font-semibold text-neutral-200">
              No File Selected for Migration
            </h3>
            <p className="text-xs text-neutral-400 leading-relaxed">
              Select a PHP file from the explorer on the left to inspect its
              AST readiness, view the Monaco side-by-side diff, and run the RHP compiler loop.
            </p>
          </div>
        </div>
      </main>
    );
  }

  const rhpFileName = activeFile.name.replace(/\.php$/i, ".rhp");

  const isFileMigrated = Boolean(
    hasMigrated ||
    activeFile.status === "migrated" ||
    activeFile.status === "warning" ||
    migrationStep === "done"
  );

  return (
    <main className="flex flex-col h-full bg-neutral-950 border-r border-neutral-800 overflow-hidden">
      {/* Editor Header / Breadcrumbs & Action Toolbar */}
      <div className="flex flex-wrap items-center justify-between border-b border-neutral-800 bg-neutral-900/90 px-4 py-2 gap-2 text-xs">
        {/* Left: Overview back button, File breadcrumb & Status */}
        <div className="flex items-center gap-2 min-w-0">
          {onBackToOverview && (
            <button
              type="button"
              onClick={onBackToOverview}
              title="Return to Project Overview"
              className="flex items-center gap-1 px-2.5 py-1 rounded-md border border-neutral-800 bg-neutral-950 text-neutral-400 hover:bg-neutral-800 hover:text-white transition-colors shrink-0 text-[11px]"
            >
              <ArrowLeft className="h-3.5 w-3.5 text-neutral-400" />
              <span>Overview</span>
            </button>
          )}

          <div className="flex items-center gap-1.5 font-mono text-neutral-300 truncate">
            <FileCode2 className="h-4 w-4 text-sky-400 shrink-0" />
            <span className="text-neutral-400 truncate">{activeFile.path}</span>
            <span className="text-neutral-600">➔</span>
            <span className="text-emerald-400 font-semibold truncate">{rhpFileName}</span>
          </div>

          {activeFile.status === "migrated" && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-950/60 text-emerald-400 border border-emerald-800/80 text-[10px] font-mono">
              <ShieldCheck className="h-3 w-3" />
              Verified PASS
            </span>
          )}

          {activeFile.status === "warning" && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-950/60 text-amber-300 border border-amber-800/80 text-[10px] font-mono">
              <AlertTriangle className="h-3 w-3" />
              Manual Hint Required
            </span>
          )}

          {isStreaming && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-sky-950/60 text-sky-300 border border-sky-800/80 text-[10px] font-mono animate-pulse">
              <Loader2 className="h-3 w-3 animate-spin" />
              Streaming RHP...
            </span>
          )}

          {!isFileMigrated && !isStreaming && (
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-neutral-800/80 text-neutral-400 border border-neutral-700/80 text-[10px] font-mono">
              <span className="h-1.5 w-1.5 rounded-full bg-neutral-500" />
              Pending Migration
            </span>
          )}
        </div>

        {/* Right Toolbar Actions */}
        <div className="flex items-center gap-1.5 shrink-0">
          {/* Toggle Side-by-Side vs Inline */}
          <button
            type="button"
            onClick={() => setRenderSideBySide(!renderSideBySide)}
            title={renderSideBySide ? "Switch to Inline Diff" : "Switch to Side-by-Side Diff"}
            className="flex items-center gap-1 px-2.5 py-1 rounded border border-neutral-800 bg-neutral-950 text-neutral-300 hover:bg-neutral-800 hover:text-neutral-100 transition-colors"
          >
            {renderSideBySide ? (
              <>
                <Rows2 className="h-3.5 w-3.5" />
                <span className="text-[11px]">Inline</span>
              </>
            ) : (
              <>
                <Columns2 className="h-3.5 w-3.5" />
                <span className="text-[11px]">Side-by-Side</span>
              </>
            )}
          </button>

          {/* Copy RHP (or code) */}
          <button
            type="button"
            onClick={handleCopy}
            title={isFileMigrated ? "Copy Migrated RHP code" : "Copy code"}
            className="flex items-center gap-1 px-2.5 py-1 rounded border border-neutral-800 bg-neutral-950 text-neutral-300 hover:bg-neutral-800 hover:text-neutral-100 transition-colors"
          >
            {copied ? (
              <>
                <Check className="h-3.5 w-3.5 text-emerald-400" />
                <span className="text-[11px] text-emerald-400">Copied</span>
              </>
            ) : (
              <>
                <Copy className="h-3.5 w-3.5" />
                <span className="text-[11px]">{isFileMigrated ? "Copy RHP" : "Copy"}</span>
              </>
            )}
          </button>

          {/* Revert (after migration) */}
          {isFileMigrated && (
            <button
              type="button"
              onClick={handleRevert}
              title="Revert RHP changes to original PHP source"
              className="flex items-center gap-1 px-2 py-1 rounded border border-neutral-800 bg-neutral-950 text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200 transition-colors"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          )}

          {/* POST-MIGRATION ACTIONS */}
          {isFileMigrated && (
            <>
              {/* Verify / Check button */}
              {onVerifyClick && (
                <button
                  type="button"
                  onClick={onVerifyClick}
                  disabled={isStreaming}
                  title="Run rhp check syntax and type verification"
                  className="flex items-center gap-1 px-2.5 py-1 rounded border border-neutral-700 bg-neutral-800 text-neutral-200 hover:bg-neutral-700 disabled:opacity-50 transition-colors"
                >
                  <ShieldCheck className="h-3.5 w-3.5 text-sky-400" />
                  <span className="text-[11px]">rhp check</span>
                </button>
              )}

              {/* Re-prompt with Critic button */}
              {onRepromptCritic && (
                <button
                  type="button"
                  onClick={onRepromptCritic}
                  disabled={isStreaming}
                  title="Re-prompt Critic loop with compiler diagnostics for automated repair"
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded border border-amber-600/40 bg-amber-950/40 text-amber-300 hover:bg-amber-900/50 hover:text-amber-100 disabled:opacity-50 transition-colors font-medium text-[11px] shadow-sm"
                >
                  <RefreshCw
                    className={`h-3 w-3 ${
                      isStreaming && migrationStep === "critic_review" ? "animate-spin" : ""
                    }`}
                  />
                  <span>Re-prompt Critic</span>
                  {diagnostics.length > 0 && (
                    <span className="px-1 py-0.2 rounded bg-amber-500/20 text-amber-300 text-[10px] font-mono border border-amber-500/30">
                      {diagnostics.length}
                    </span>
                  )}
                </button>
              )}

              {/* Stop button while streaming in post-migration / critic state */}
              {isStreaming && onStopStreaming && (
                <button
                  type="button"
                  onClick={onStopStreaming}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-rose-300 hover:text-rose-100 text-[11px] font-medium transition-colors cursor-pointer"
                  title="Stop migration stream"
                >
                  <Square className="h-3 w-3 fill-rose-400 text-rose-400" />
                  <span>Stop</span>
                </button>
              )}

              {/* Accept & Next File button (Primary CTA after migration) */}
              {onAcceptAndNext && (
                <button
                  type="button"
                  onClick={onAcceptAndNext}
                  disabled={isStreaming}
                  title="Accept current RHP code and advance to next pending file"
                  className="flex items-center gap-1.5 px-3.5 py-1 rounded-lg bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 shadow-[0_0_12px_rgba(16,185,129,0.3)] font-medium transition-colors text-[11px]"
                >
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-200" />
                  <span>Accept &amp; Next File</span>
                  <ArrowRight className="h-3 w-3" />
                </button>
              )}
            </>
          )}

          {/* PRE-MIGRATION PRIMARY CTA */}
          {!isFileMigrated && onMigrateClick && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onMigrateClick}
                disabled={isStreaming}
                className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-medium shadow-[0_0_16px_rgba(16,185,129,0.3)] hover:shadow-[0_0_24px_rgba(16,185,129,0.5)] disabled:opacity-50 transition-all text-xs"
              >
                {isStreaming ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span>Streaming RHP...</span>
                  </>
                ) : (
                  <>
                    <Zap className="h-3.5 w-3.5 text-emerald-200" />
                    <span>Start Migration with AI</span>
                  </>
                )}
              </button>

              {/* Stop button while streaming in pre-migration state */}
              {isStreaming && onStopStreaming && (
                <button
                  type="button"
                  onClick={onStopStreaming}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-rose-300 hover:text-rose-100 font-medium text-xs transition-colors cursor-pointer"
                  title="Stop streaming migration"
                >
                  <Square className="h-3 w-3 fill-rose-400 text-rose-400" />
                  <span>Stop</span>
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Reactive State Machine Indicator Banner */}
      {migrationStep !== "idle" && (
        <div className="flex items-center justify-between px-4 py-1.5 bg-neutral-900 border-b border-neutral-800 text-xs">
          <div className="flex items-center gap-3">
            <span className="text-[10px] uppercase tracking-wider font-semibold text-neutral-400">
              Migration State:
            </span>
            <div className="flex items-center gap-1.5 font-mono text-[11px]">
              {/* 1. analyzing */}
              <span
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border ${
                  migrationStep === "analyzing"
                    ? "bg-sky-950/80 text-sky-300 border-sky-600 animate-pulse"
                    : migrationStep === "transpiling" ||
                      migrationStep === "compiling" ||
                      migrationStep === "critic_review" ||
                      migrationStep === "done"
                    ? "bg-emerald-950/40 text-emerald-400 border-emerald-800/60"
                    : "text-neutral-500 border-neutral-800"
                }`}
              >
                1. AST Analysis
              </span>
              <span className="text-neutral-600">➔</span>

              {/* 2. transpiling */}
              <span
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border ${
                  migrationStep === "transpiling"
                    ? "bg-sky-950/80 text-sky-300 border-sky-600 animate-pulse"
                    : migrationStep === "compiling" ||
                      migrationStep === "critic_review" ||
                      migrationStep === "done"
                    ? "bg-emerald-950/40 text-emerald-400 border-emerald-800/60"
                    : "text-neutral-500 border-neutral-800"
                }`}
              >
                2. Transpiling RHP
              </span>
              <span className="text-neutral-600">➔</span>

              {/* 3. compiling */}
              <span
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border ${
                  migrationStep === "compiling"
                    ? "bg-sky-950/80 text-sky-300 border-sky-600 animate-pulse"
                    : migrationStep === "critic_review" || migrationStep === "done"
                    ? diagnostics.length > 0
                      ? "bg-amber-950/40 text-amber-300 border-amber-800/60"
                      : "bg-emerald-950/40 text-emerald-400 border-emerald-800/60"
                    : "text-neutral-500 border-neutral-800"
                }`}
              >
                3. Compiler Verify
              </span>
              <span className="text-neutral-600">➔</span>

              {/* 4. critic_review / done */}
              <span
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full border ${
                  migrationStep === "critic_review"
                    ? "bg-purple-950/80 text-purple-300 border-purple-600 animate-pulse"
                    : migrationStep === "done"
                    ? isVerified
                      ? "bg-emerald-950/80 text-emerald-400 border-emerald-500"
                      : "bg-amber-950/80 text-amber-300 border-amber-500"
                    : "text-neutral-500 border-neutral-800"
                }`}
              >
                4. Critic Loop &amp; Done
              </span>
            </div>
          </div>

          {/* Diagnostic gutter marker status pill */}
          {diagnostics.length > 0 && (
            <div className="flex items-center gap-1 text-[11px] font-mono text-amber-400 bg-amber-950/60 px-2.5 py-0.5 rounded border border-amber-800/80">
              <AlertCircle className="h-3 w-3 text-amber-400 shrink-0" />
              <span>
                {diagnostics.length} gutter error marker{diagnostics.length > 1 ? "s" : ""} flagged
              </span>
            </div>
          )}
        </div>
      )}

      {/* Pane Labels */}
      <div className="grid grid-cols-2 bg-neutral-950 border-b border-neutral-800/80 text-[11px] font-mono select-none">
        <div className="px-4 py-1.5 text-neutral-400 border-r border-neutral-800 flex items-center justify-between">
          <span className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-rose-400/80" />
            Original PHP 8.x (Source Code)
          </span>
          <span className="text-[10px] text-neutral-600">Left Pane</span>
        </div>
        <div
          className={`px-4 py-1.5 flex items-center justify-between ${
            isFileMigrated
              ? "text-emerald-400 bg-neutral-900/40"
              : isStreaming
              ? "text-sky-400 bg-sky-950/20"
              : "text-neutral-400 bg-neutral-900/20"
          }`}
        >
          <span className="flex items-center gap-1.5">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                isFileMigrated
                  ? "bg-emerald-400"
                  : isStreaming
                  ? "bg-sky-400 animate-ping"
                  : "bg-neutral-500"
              }`}
            />
            {isFileMigrated
              ? "Generated RHP Strict (Editable & Streamed)"
              : isStreaming
              ? "Streaming RHP Strict Transpilation..."
              : "RHP Strict Target (Pending Generation)"}
          </span>
          <span className="text-[10px] text-neutral-500">Right Pane</span>
        </div>
      </div>

      {/* Monaco Diff Editor Canvas or Pre-Migration Screen */}
      <div className="flex-1 w-full h-full relative">
        {!activeFile ? (
          <div className="flex h-full w-full flex-col items-center justify-center p-8 text-center space-y-3 bg-neutral-950 text-neutral-400 select-none">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-neutral-900 border border-neutral-800 text-neutral-500">
              <FileCode2 className="h-6 w-6" />
            </div>
            <div className="space-y-1">
              <h3 className="text-sm font-medium text-neutral-200">No File Selected</h3>
              <p className="text-xs text-neutral-500 max-w-xs">
                Select a file from the explorer on the left or project overview to inspect its code, view AST readiness, and generate strict RHP.
              </p>
            </div>
          </div>
        ) : !isMounted ? (
          <div className="flex h-full w-full items-center justify-center bg-neutral-950 text-neutral-400">
            <div className="flex items-center gap-2 text-xs">
              <Loader2 className="h-4 w-4 animate-spin text-emerald-500" />
              <span>Loading Monaco Diff Editor...</span>
            </div>
          </div>
        ) : !isFileMigrated && !isStreaming && !showRawPreMigrationEditor ? (
          /* PRE-MIGRATION SPLIT VIEW: Left is Source Code, Right is Pre-Migration Readiness Card */
          <div className="flex w-full h-full">
            {/* Left: Original Code Viewer */}
            <div className="w-1/2 h-full border-r border-neutral-800">
              <Editor
                value={originalCode || "// Original PHP source"}
                language="php"
                theme="vs-dark"
                options={{
                  readOnly: true,
                  minimap: { enabled: false },
                  automaticLayout: true,
                  fontSize: 13,
                  lineHeight: 20,
                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                  wordWrap: "on",
                }}
              />
            </div>

            {/* Right: Pre-Migration State Card */}
            <div className="w-1/2 h-full flex flex-col items-center justify-center p-8 text-center bg-neutral-950/80 space-y-5 select-none overflow-y-auto">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-tr from-emerald-950 to-neutral-900 border border-emerald-500/30 text-emerald-400 shadow-[0_0_24px_rgba(16,185,129,0.2)]">
                <Sparkles className="h-7 w-7 text-emerald-400" />
              </div>

              <div className="space-y-2 max-w-sm">
                <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-neutral-900 border border-neutral-700 text-neutral-300 text-[11px] font-mono">
                  <span className="h-2 w-2 rounded-full bg-amber-400" />
                  <span>Pending Strict AOT Compilation</span>
                </div>
                <h3 className="text-base font-bold text-neutral-100">
                  Ready for Strict RHP Transpilation
                </h3>
                <p className="text-xs text-neutral-400 leading-relaxed">
                  This file has not been transpiled to RHP yet. Start migration to infer strict types, convert dynamic arrays to typed dicts, and verify with ahead-of-time compiler checks.
                </p>
              </div>

              {/* AST Readiness preview */}
              {scorecard && (
                <div className="flex items-center gap-3 px-3.5 py-2 rounded-xl bg-neutral-900 border border-neutral-800 text-xs font-mono">
                  <span className="text-neutral-400">AST Readiness:</span>
                  <span className="text-emerald-400 font-bold">{scorecard.readinessPercentage}%</span>
                  {scorecard.untypedParamCount > 0 && (
                    <span className="text-amber-400">
                      ({scorecard.untypedParamCount} untyped param{scorecard.untypedParamCount > 1 ? "s" : ""})
                    </span>
                  )}
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex flex-col items-center gap-2.5 pt-2">
                {onMigrateClick && (
                  <button
                    type="button"
                    onClick={onMigrateClick}
                    className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-medium text-xs shadow-[0_0_20px_rgba(16,185,129,0.3)] hover:shadow-[0_0_28px_rgba(16,185,129,0.5)] transition-all transform hover:-translate-y-0.5 active:translate-y-0"
                  >
                    <Zap className="h-4 w-4 text-emerald-200" />
                    <span>Start Migration with AI</span>
                    <ArrowRight className="h-3.5 w-3.5 text-emerald-200" />
                  </button>
                )}

                <button
                  type="button"
                  onClick={() => setShowRawPreMigrationEditor(true)}
                  className="text-[11px] text-neutral-500 hover:text-neutral-300 underline transition-colors"
                >
                  Or open in raw diff editor
                </button>
              </div>
            </div>
          </div>
        ) : (
          /* MIGRATED OR STREAMING OR RAW DIFF EDITOR */
          <div className="w-full h-full relative">
            {!isFileMigrated && showRawPreMigrationEditor && (
              <div className="absolute top-2 right-4 z-10 flex items-center gap-2 px-2.5 py-1 rounded-md bg-neutral-900/90 border border-neutral-700 text-[11px] text-neutral-300 shadow-md">
                <span>Pre-Migration Draft Mode</span>
                <button
                  type="button"
                  onClick={() => setShowRawPreMigrationEditor(false)}
                  className="text-emerald-400 hover:underline"
                >
                  Back to Card
                </button>
              </div>
            )}
            <DiffEditor
              original={originalCode || "// Original PHP source"}
              modified={modifiedCode || "// RHP code will be generated or can be typed here"}
              language="php"
              theme="vs-dark"
              onMount={handleEditorDidMount}
              options={{
                readOnly: false,
                originalEditable: false,
                renderSideBySide: renderSideBySide,
                automaticLayout: true,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                fontSize: 13,
                lineHeight: 20,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                renderWhitespace: "selection",
                diffWordWrap: "on",
                folding: true,
                wordWrap: "on",
              }}
            />
          </div>
        )}
      </div>
    </main>
  );
}
