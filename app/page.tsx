"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Sparkles,
  Settings,
  FolderPlus,
  FolderGit2,
  ArrowRight,
  FileCode2,
  ShieldCheck,
  Zap,
} from "lucide-react";
import { NewProjectModal } from "@/components/NewProjectModal";
import { BYOKSettingsModal } from "@/components/BYOKSettingsModal";
import { getStoredBYOKConfig } from "@/lib/byok";
import { BYOKConfig } from "@/types/workbench";

export default function HomePage() {
  const router = useRouter();
  const [isNewProjectOpen, setIsNewProjectOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [byokConfig, setByokConfig] = useState<BYOKConfig>(getStoredBYOKConfig());

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-neutral-950 text-neutral-100 font-sans">
      {/* 1. Global Navigation Topbar */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-neutral-800 bg-neutral-900/90 z-20">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 shadow-[0_0_12px_rgba(16,185,129,0.2)]">
              <Sparkles className="h-4 w-4" />
            </div>
            <span className="font-bold text-sm tracking-tight text-neutral-100">
              rhp-migrate<span className="text-emerald-400">-ai</span>
            </span>
          </div>

          <span className="h-4 w-[1px] bg-neutral-800" />

          {/* Workspace status badge */}
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-neutral-950 border border-neutral-800 text-xs">
            <FolderGit2 className="h-3.5 w-3.5 text-neutral-500" />
            <span className="text-neutral-500 font-mono text-[11px]">workspace:</span>
            <span className="text-neutral-400 font-mono text-[11px]">none</span>
          </div>
        </div>

        {/* Global Action Controls */}
        <div className="flex items-center gap-2.5">
          {/* Topbar Start New Project Trigger */}
          <button
            type="button"
            onClick={() => setIsNewProjectOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-emerald-600/40 bg-emerald-950/30 text-emerald-300 hover:bg-emerald-900/40 hover:text-emerald-200 transition-colors text-xs font-medium"
          >
            <FolderPlus className="h-3.5 w-3.5 text-emerald-400" />
            <span>New Project</span>
          </button>

          {/* BYOK Settings Trigger */}
          <button
            type="button"
            onClick={() => setIsSettingsOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-neutral-700 bg-neutral-800 text-neutral-200 hover:bg-neutral-700 hover:text-white transition-colors text-xs font-medium"
          >
            <Settings className="h-3.5 w-3.5 text-neutral-400" />
            <span>AI Settings (BYOK)</span>
            <span className="px-1.5 py-0.2 rounded bg-neutral-900 text-[10px] text-emerald-400 border border-neutral-700 font-mono">
              {byokConfig.provider}
            </span>
          </button>
        </div>
      </header>

      {/* 2. Empty State View with Center Action Button */}
      <main className="flex-1 flex flex-col items-center justify-center p-6 relative overflow-y-auto">
        {/* Subtle background glow */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[350px] bg-emerald-500/5 rounded-full blur-3xl pointer-events-none" />

        <div className="relative z-10 flex flex-col items-center text-center max-w-lg space-y-6">
          {/* Glowing Center Icon */}
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-neutral-900 border border-neutral-800 shadow-[0_0_30px_rgba(16,185,129,0.15)] text-emerald-400">
            <FolderPlus className="h-8 w-8 text-emerald-400" />
          </div>

          {/* Empty State Titles */}
          <div className="space-y-2">
            <h1 className="text-2xl font-bold tracking-tight text-neutral-100">
              RHP Migration Workbench
            </h1>
            <p className="text-sm text-neutral-400 leading-relaxed max-w-md">
              No project open. Start a new project to inspect AST readiness, resolve strict type shapes, and transpile PHP to compiled RHP.
            </p>
          </div>

          {/* Button in Center of Screen to Start New Project */}
          <div className="flex flex-col items-center gap-3 pt-2">
            <button
              type="button"
              onClick={() => setIsNewProjectOpen(true)}
              className="group flex items-center gap-2.5 px-6 py-3 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-medium text-sm shadow-[0_0_24px_rgba(16,185,129,0.25)] hover:shadow-[0_0_32px_rgba(16,185,129,0.45)] transition-all transform hover:-translate-y-0.5 active:translate-y-0"
            >
              <FolderPlus className="h-4 w-4 text-emerald-200 group-hover:scale-110 transition-transform" />
              <span>Start New Project</span>
              <ArrowRight className="h-4 w-4 text-emerald-200 group-hover:translate-x-1 transition-transform" />
            </button>

            {/* Quick Demo Option */}
            <div className="flex items-center gap-1.5 text-xs text-neutral-500">
              <span>Want to explore first?</span>
              <button
                type="button"
                onClick={() => router.push("/workbench/demo")}
                className="text-emerald-400 hover:text-emerald-300 underline font-medium transition-colors"
              >
                Open Demo Project
              </button>
            </div>
          </div>

          {/* Feature Highlights Grid */}
          <div className="grid grid-cols-3 gap-3 pt-6 w-full text-left">
            <div className="p-3.5 rounded-lg border border-neutral-800/80 bg-neutral-900/50 space-y-1.5">
              <div className="flex items-center gap-1.5 text-neutral-200 text-xs font-semibold">
                <FolderGit2 className="h-3.5 w-3.5 text-emerald-400" />
                <span>Safe Ingestion</span>
              </div>
              <p className="text-[11px] text-neutral-400 leading-normal">
                Upload ZIP or shallow-clone GitHub repositories into isolated sandboxes.
              </p>
            </div>

            <div className="p-3.5 rounded-lg border border-neutral-800/80 bg-neutral-900/50 space-y-1.5">
              <div className="flex items-center gap-1.5 text-neutral-200 text-xs font-semibold">
                <FileCode2 className="h-3.5 w-3.5 text-sky-400" />
                <span>Monaco AST Diff</span>
              </div>
              <p className="text-[11px] text-neutral-400 leading-normal">
                Side-by-side Monaco diff with real-time streaming and syntax gutter markers.
              </p>
            </div>

            <div className="p-3.5 rounded-lg border border-neutral-800/80 bg-neutral-900/50 space-y-1.5">
              <div className="flex items-center gap-1.5 text-neutral-200 text-xs font-semibold">
                <ShieldCheck className="h-3.5 w-3.5 text-purple-400" />
                <span>Compiler Verify</span>
              </div>
              <p className="text-[11px] text-neutral-400 leading-normal">
                Integrated <span className="font-mono text-neutral-300">rhp check</span> verification with Critic auto-repair loop.
              </p>
            </div>
          </div>
        </div>
      </main>

      {/* 3. Start New Project Modal */}
      <NewProjectModal
        isOpen={isNewProjectOpen}
        onClose={() => setIsNewProjectOpen(false)}
      />

      {/* 4. BYOK Settings Modal */}
      <BYOKSettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        onConfigSaved={(newCfg) => setByokConfig(newCfg)}
      />
    </div>
  );
}
