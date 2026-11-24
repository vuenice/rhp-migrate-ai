"use client";

import React, { useState, useEffect } from "react";
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
  FolderOpen,
  Clock,
  HardDrive,
  FileSpreadsheet,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";
import { NewProjectModal } from "@/components/NewProjectModal";
import { BYOKSettingsModal } from "@/components/BYOKSettingsModal";
import { getStoredBYOKConfig, isBYOKConfigured, DEFAULT_BYOK_CONFIG } from "@/lib/byok";
import { BYOKConfig } from "@/types/workbench";

interface LastSession {
  workspace_id?: string | null;
  workspace_path?: string | null;
  project_name?: string | null;
  last_opened_at?: string | null;
  file_count?: number;
}

interface RecentProject {
  workspace_id: string;
  project_name: string;
  workspace_path: string;
  created_at?: number | null;
  last_opened_at?: number | null;
  file_count: number;
  completed_count: number;
  needs_review_count: number;
}

export default function HomePage() {
  const router = useRouter();
  const [isMounted, setIsMounted] = useState(false);
  const [isNewProjectOpen, setIsNewProjectOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [byokConfig, setByokConfig] = useState<BYOKConfig>(DEFAULT_BYOK_CONFIG);

  // Software directory and last session state
  const [lastSession, setLastSession] = useState<LastSession | null>(null);
  const [recentProjects, setRecentProjects] = useState<RecentProject[]>([]);
  const [softwareInfo, setSoftwareInfo] = useState<{ software_dir: string; projects_dir: string } | null>(null);
  const [isLoadingSession, setIsLoadingSession] = useState(true);

  useEffect(() => {
    setIsMounted(true);
    setByokConfig(getStoredBYOKConfig());

    // Fetch software directory and last opened project
    const loadSessionAndSoftwareData = async () => {
      try {
        const [sessRes, infoRes, listRes] = await Promise.all([
          fetch("/api/workspace/last-opened"),
          fetch("/api/workspace/software-info"),
          fetch("/api/workspace/list"),
        ]);

        if (sessRes.ok) {
          const sessData = await sessRes.json();
          if (sessData.workspace_id) {
            setLastSession(sessData);
          }
        }

        if (infoRes.ok) {
          const infoData = await infoRes.json();
          setSoftwareInfo(infoData);
        }

        if (listRes.ok) {
          const listData = await listRes.json();
          if (Array.isArray(listData.projects)) {
            setRecentProjects(listData.projects);
          }
        }
      } catch (err) {
        console.warn("Could not load software session info:", err);
      } finally {
        setIsLoadingSession(false);
      }
    };

    loadSessionAndSoftwareData();
  }, []);

  const isConfigured = isMounted && isBYOKConfigured(byokConfig);

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
              RHP <span className="text-emerald-400">Migrate</span>
            </span>
          </div>

          <span className="h-4 w-[1px] bg-neutral-800" />

          {/* Software storage directory badge */}
          {softwareInfo && (
            <div
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-neutral-950 border border-neutral-800 text-xs"
              title={`Software Data Directory: ${softwareInfo.software_dir}\nAll projects & CSV status files are kept here persistently.`}
            >
              <HardDrive className="h-3.5 w-3.5 text-neutral-500" />
              <span className="text-neutral-500 font-mono text-[11px]">storage:</span>
              <span className="text-neutral-400 font-mono text-[11px] truncate max-w-[200px]">
                {softwareInfo.projects_dir}
              </span>
            </div>
          )}
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
            <span
              suppressHydrationWarning
              className={`px-1.5 py-0.2 rounded text-[10px] font-mono border ${
                isConfigured
                  ? "bg-neutral-900 text-emerald-400 border-neutral-700"
                  : "bg-amber-950/40 text-amber-400 border-amber-700/50"
              }`}
            >
              {isConfigured ? byokConfig.provider : "Setup Required"}
            </span>
          </button>
        </div>
      </header>

      {/* 2. Main View */}
      <main className="flex-1 flex flex-col items-center justify-center p-6 relative overflow-y-auto">
        {/* Subtle background glow */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[550px] h-[380px] bg-emerald-500/5 rounded-full blur-3xl pointer-events-none" />

        <div className="relative z-10 flex flex-col items-center text-center max-w-xl space-y-6">
          {/* Glowing Center Icon */}
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-neutral-900 border border-neutral-800 shadow-[0_0_30px_rgba(16,185,129,0.15)] text-emerald-400">
            <FolderPlus className="h-8 w-8 text-emerald-400" />
          </div>

          {/* Titles */}
          <div className="space-y-2">
            <h1 className="text-2xl font-bold tracking-tight text-neutral-100">
              RHP Migration Workbench
            </h1>
            <p className="text-sm text-neutral-400 leading-relaxed max-w-md">
              Persistent workspaces, AST readiness analysis, and AI transpilation to strictly-typed ahead-of-time compiled RHP.
            </p>
          </div>

          {/* Resume Last Opened Project Card (If exists) */}
          {lastSession && lastSession.workspace_id && (
            <div className="w-full text-left p-4 rounded-xl border border-emerald-600/30 bg-emerald-950/20 backdrop-blur-sm space-y-3 shadow-lg shadow-emerald-950/20">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="flex h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                  <span className="text-xs font-semibold text-emerald-300 uppercase tracking-wider">
                    Last Opened Project
                  </span>
                </div>
                {lastSession.last_opened_at && (
                  <div className="flex items-center gap-1 text-[11px] text-neutral-400 font-mono">
                    <Clock className="h-3 w-3" />
                    <span>{new Date(lastSession.last_opened_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-neutral-100">
                    {lastSession.project_name || "Active Project"}
                  </h3>
                  <div className="flex items-center gap-3 text-xs text-neutral-400 mt-0.5">
                    {lastSession.file_count !== undefined && lastSession.file_count > 0 && (
                      <span>{lastSession.file_count} files</span>
                    )}
                    <span className="flex items-center gap-1 text-emerald-400 text-[11px]">
                      <FileSpreadsheet className="h-3 w-3" />
                      <span>CSV status ready</span>
                    </span>
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => router.push(`/workbench/${lastSession.workspace_id}`)}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs shadow-md shadow-emerald-950/50 transition-all hover:scale-105 active:scale-100"
                >
                  <FolderOpen className="h-3.5 w-3.5" />
                  <span>Resume Project</span>
                  <ArrowRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}

          {/* Action Buttons: Start New Project or Open Demo */}
          <div className="flex flex-col items-center gap-3 pt-1 w-full">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => setIsNewProjectOpen(true)}
                className="group flex items-center gap-2.5 px-6 py-3 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-medium text-sm shadow-[0_0_24px_rgba(16,185,129,0.25)] hover:shadow-[0_0_32px_rgba(16,185,129,0.45)] transition-all transform hover:-translate-y-0.5 active:translate-y-0"
              >
                <FolderPlus className="h-4 w-4 text-emerald-200 group-hover:scale-110 transition-transform" />
                <span>Start New Project</span>
                <ArrowRight className="h-4 w-4 text-emerald-200 group-hover:translate-x-1 transition-transform" />
              </button>

              <button
                type="button"
                onClick={() => router.push("/workbench/demo")}
                className="flex items-center gap-2 px-4 py-3 rounded-xl border border-neutral-700 bg-neutral-800/80 hover:bg-neutral-700 text-neutral-200 text-sm font-medium transition-colors"
              >
                <Zap className="h-4 w-4 text-emerald-400" />
                <span>Explore Demo Project</span>
              </button>
            </div>
          </div>

          {/* Recent Saved Projects in Software Directory (if more than 1) */}
          {recentProjects.length > 0 && (
            <div className="w-full text-left space-y-2 pt-2">
              <div className="flex items-center justify-between text-xs text-neutral-400">
                <span className="font-medium text-neutral-300">Saved Projects in Software Directory</span>
                <span className="text-[11px] font-mono text-neutral-500">{recentProjects.length} total</span>
              </div>
              <div className="grid grid-cols-1 gap-2 max-h-40 overflow-y-auto pr-1">
                {recentProjects.slice(0, 3).map((proj) => (
                  <div
                    key={proj.workspace_id}
                    onClick={() => router.push(`/workbench/${proj.workspace_id}`)}
                    className="flex items-center justify-between p-2.5 rounded-lg border border-neutral-800 bg-neutral-900/60 hover:bg-neutral-800/80 hover:border-neutral-700 transition-all cursor-pointer group"
                  >
                    <div className="flex items-center gap-2.5">
                      <FolderGit2 className="h-4 w-4 text-neutral-400 group-hover:text-emerald-400 transition-colors" />
                      <div>
                        <div className="text-xs font-medium text-neutral-200 group-hover:text-white transition-colors">
                          {proj.project_name}
                        </div>
                        <div className="flex items-center gap-2 text-[10px] text-neutral-500 font-mono">
                          <span>{proj.file_count} files</span>
                          <span>•</span>
                          <span className="text-emerald-400">{proj.completed_count} completed</span>
                          {proj.needs_review_count > 0 && (
                            <>
                              <span>•</span>
                              <span className="text-amber-400">{proj.needs_review_count} review</span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                    <ArrowRight className="h-3.5 w-3.5 text-neutral-600 group-hover:text-emerald-400 group-hover:translate-x-0.5 transition-all" />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Feature Highlights Grid */}
          <div className="grid grid-cols-3 gap-3 pt-4 w-full text-left">
            <div className="p-3.5 rounded-lg border border-neutral-800/80 bg-neutral-900/50 space-y-1.5">
              <div className="flex items-center gap-1.5 text-neutral-200 text-xs font-semibold">
                <HardDrive className="h-3.5 w-3.5 text-emerald-400" />
                <span>Persistent Storage</span>
              </div>
              <p className="text-[11px] text-neutral-400 leading-normal">
                Saved like VSCode & Antigravity in persistent software directories with migration state.
              </p>
            </div>

            <div className="p-3.5 rounded-lg border border-neutral-800/80 bg-neutral-900/50 space-y-1.5">
              <div className="flex items-center gap-1.5 text-neutral-200 text-xs font-semibold">
                <FileSpreadsheet className="h-3.5 w-3.5 text-sky-400" />
                <span>Status CSV File</span>
              </div>
              <p className="text-[11px] text-neutral-400 leading-normal">
                Auto-generated CSV tracking which files need review and which migrations are complete.
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
