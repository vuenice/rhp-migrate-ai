"use client";

import React, { useState, useRef, useEffect } from "react";
import dynamic from "next/dynamic";
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
  CheckCircle2,
  RefreshCw,
  AlertCircle,
} from "lucide-react";
import { FileItem, FileStatus } from "@/types/workbench";
import { DiagnosticError, MigrationStep } from "@/hooks/useMigrationStream";

// Dynamically import Monaco DiffEditor to prevent SSR window issues in Next.js
const DiffEditor = dynamic(
  () => import("@monaco-editor/react").then((mod) => mod.DiffEditor),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full w-full items-center justify-center bg-neutral-950 text-neutral-400">
        <div className="flex items-center gap-2 text-xs">
          <Loader2 className="h-4 w-4 animate-spin text-emerald-500" />
          <span>Loading Monaco Diff Editor...</span>
        </div>
      </div>
    ),
  }
);

interface MonacoDiffViewerProps {
  originalCode: string;
  modifiedCode: string;
  activeFile: FileItem | null;
  onChangeModified?: (newCode: string) => void;
  isStreaming?: boolean;
  onMigrateClick?: () => void;
  onVerifyClick?: () => void;
  onMount?: (editor: any, monaco: any) => void;
  onAcceptAndNext?: () => void;
  onRepromptCritic?: () => void;
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
  onVerifyClick,
  onMount,
  onAcceptAndNext,
  onRepromptCritic,
  migrationStep = "idle",
  diagnostics = [],
  isVerified = null,
  hasNextFile = false,
}: MonacoDiffViewerProps) {
  const [copied, setCopied] = useState(false);
  const [renderSideBySide, setRenderSideBySide] = useState(true);
  const diffEditorRef = useRef<any>(null);

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

  return (
    <main className="flex flex-col h-full bg-neutral-950 border-r border-neutral-800 overflow-hidden">
      {/* Editor Header / Breadcrumbs & Action Toolbar */}
      <div className="flex flex-wrap items-center justify-between border-b border-neutral-800 bg-neutral-900/90 px-4 py-2 gap-2 text-xs">
        {/* Left: File breadcrumb & Status */}
        <div className="flex items-center gap-2 min-w-0">
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

          {/* Copy RHP */}
          <button
            type="button"
            onClick={handleCopy}
            title="Copy Migrated RHP code"
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
                <span className="text-[11px]">Copy RHP</span>
              </>
            )}
          </button>

          {/* Revert */}
          <button
            type="button"
            onClick={handleRevert}
            title="Revert RHP changes to original PHP source"
            className="flex items-center gap-1 px-2 py-1 rounded border border-neutral-800 bg-neutral-950 text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200 transition-colors"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>

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
              <span>Re-prompt with Critic</span>
              {diagnostics.length > 0 && (
                <span className="px-1 py-0.2 rounded bg-amber-500/20 text-amber-300 text-[10px] font-mono border border-amber-500/30">
                  {diagnostics.length}
                </span>
              )}
            </button>
          )}

          {/* Accept & Next File button */}
          {onAcceptAndNext && (
            <button
              type="button"
              onClick={onAcceptAndNext}
              disabled={isStreaming}
              title="Accept current RHP code and advance to next pending file"
              className="flex items-center gap-1.5 px-3 py-1 rounded bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 shadow-sm font-medium transition-colors text-[11px]"
            >
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-200" />
              <span>Accept &amp; Next File</span>
              <ArrowRight className="h-3 w-3" />
            </button>
          )}

          {/* AI Migrate Button */}
          {onMigrateClick && (
            <button
              type="button"
              onClick={onMigrateClick}
              disabled={isStreaming}
              className="flex items-center gap-1.5 px-3 py-1 rounded bg-sky-600 text-white hover:bg-sky-500 disabled:opacity-50 shadow-sm font-medium transition-colors text-[11px]"
            >
              {isStreaming ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span className="text-[11px]">Streaming...</span>
                </>
              ) : (
                <>
                  <Sparkles className="h-3.5 w-3.5" />
                  <span className="text-[11px]">Migrate with AI</span>
                </>
              )}
            </button>
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
        <div className="px-4 py-1 text-neutral-400 border-r border-neutral-800 flex items-center justify-between">
          <span className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-rose-400/80" />
            Original PHP 8.x (Read-Only)
          </span>
          <span className="text-[10px] text-neutral-600">Left Pane</span>
        </div>
        <div className="px-4 py-1 text-emerald-400 flex items-center justify-between bg-neutral-900/30">
          <span className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            Generated RHP Strict (Editable & Streamed)
          </span>
          <span className="text-[10px] text-neutral-500">Right Pane</span>
        </div>
      </div>

      {/* Monaco Diff Editor Canvas */}
      <div className="flex-1 w-full h-full relative">
        <DiffEditor
          original={originalCode || "// Select a file to view original PHP source"}
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
    </main>
  );
}
