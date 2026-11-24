"use client";

import React from "react";
import {
  Sparkles,
  AlertTriangle,
  ArrowRight,
  X,
  Layers,
  RefreshCw,
} from "lucide-react";
import { FileItem } from "@/types/workbench";

interface RhpManifestModalProps {
  isOpen: boolean;
  onClose: () => void;
  workspaceId: string;
  targetFile?: FileItem | null;
  onInitAndContinue: () => Promise<void>;
  onMigrateProjectInstead: () => void;
  isInitializing: boolean;
}

export function RhpManifestModal({
  isOpen,
  onClose,
  workspaceId,
  targetFile,
  onInitAndContinue,
  onMigrateProjectInstead,
  isInitializing,
}: RhpManifestModalProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-xl rounded-2xl border border-neutral-800 bg-neutral-900 shadow-2xl p-6 space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-amber-500/10 text-amber-400 border border-amber-500/30 shadow-[0_0_16px_rgba(245,158,11,0.2)]">
              <AlertTriangle className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-neutral-100 flex items-center gap-2">
                Project Manifest (<code className="text-emerald-400 font-mono text-xs bg-neutral-800 px-1.5 py-0.5 rounded">rhp.toml</code>) Required
              </h2>
              <p className="text-xs text-neutral-400 mt-0.5">
                Single-file migration requires project context for strict typing
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-lg text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Target File Badge (if specified) */}
        {targetFile && (
          <div className="p-3 rounded-lg border border-neutral-800 bg-neutral-950/60 flex items-center justify-between text-xs">
            <span className="text-neutral-400">Target file to migrate:</span>
            <span className="font-mono text-emerald-400 bg-emerald-950/40 px-2 py-0.5 rounded border border-emerald-900/50 truncate max-w-xs">
              {targetFile.path}
            </span>
          </div>
        )}

        {/* Explanation Context */}
        <div className="text-xs text-neutral-300 leading-relaxed space-y-2">
          <p>
            The RHP compiler strictly requires an <strong className="text-neutral-100"><code>rhp.toml</code></strong> manifest at your project root when running in single-file mode (<code className="text-sky-300 font-mono">rhp migrate --file=&quot;...&quot;</code>).
          </p>
          <p className="text-neutral-400">
            This manifest provides the compiler with your package name, PSR-4 autoload root, and dependencies so ahead-of-time type inference remains fully consistent. Whole-project mode (<code className="text-neutral-200 font-mono">rhp migrate .</code>) can run directly on the entire workspace.
          </p>
        </div>

        {/* Comparison Options Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
          {/* Option A: Auto-generate rhp.toml */}
          <div className="p-4 rounded-xl border border-emerald-500/40 bg-emerald-950/10 space-y-2.5 relative">
            <div className="flex items-center gap-2 text-emerald-300 font-semibold">
              <Sparkles className="h-4 w-4 text-emerald-400" />
              <span>Recommended</span>
            </div>
            <p className="text-neutral-300 text-[11px] leading-relaxed">
              Auto-generate <code className="text-emerald-400">rhp.toml</code> from your <code className="text-neutral-200">composer.json</code> PSR-4 mappings, then immediately continue migrating this file.
            </p>
          </div>

          {/* Option B: Project-wide migration */}
          <div className="p-4 rounded-xl border border-neutral-800 bg-neutral-950/40 space-y-2.5">
            <div className="flex items-center gap-2 text-neutral-300 font-semibold">
              <Layers className="h-4 w-4 text-sky-400" />
              <span>Whole-Project Mode</span>
            </div>
            <p className="text-neutral-400 text-[11px] leading-relaxed">
              Run <code className="text-sky-300 font-mono">rhp migrate .</code> across all files in the project. Project mode processes all modules simultaneously.
            </p>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex flex-col sm:flex-row items-center justify-end gap-2.5 pt-2 border-t border-neutral-800">
          <button
            type="button"
            onClick={onClose}
            className="w-full sm:w-auto px-4 py-2 rounded-xl border border-neutral-800 bg-neutral-900 text-neutral-300 hover:bg-neutral-800 text-xs font-medium transition-colors"
          >
            Cancel
          </button>

          <button
            type="button"
            onClick={onMigrateProjectInstead}
            className="w-full sm:w-auto px-4 py-2 rounded-xl border border-sky-500/30 bg-sky-950/30 text-sky-300 hover:bg-sky-900/40 text-xs font-medium transition-colors flex items-center justify-center gap-1.5"
          >
            <Layers className="h-3.5 w-3.5 text-sky-400" />
            <span>Migrate Entire Project Instead</span>
          </button>

          <button
            type="button"
            onClick={onInitAndContinue}
            disabled={isInitializing}
            className="w-full sm:w-auto px-4 py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-medium text-xs shadow-[0_0_16px_rgba(16,185,129,0.25)] transition-all flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {isInitializing ? (
              <>
                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                <span>Creating rhp.toml...</span>
              </>
            ) : (
              <>
                <Sparkles className="h-3.5 w-3.5 text-emerald-200" />
                <span>Auto-Generate rhp.toml &amp; Continue</span>
                <ArrowRight className="h-3.5 w-3.5 text-emerald-200" />
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
