"use client";

import React, { useMemo, useState } from "react";
import {
  FolderGit2,
  Sparkles,
  Play,
  Layers,
  FileCode2,
  CheckCircle2,
  AlertTriangle,
  Clock,
  ArrowRight,
  ShieldCheck,
  FolderPlus,
  RefreshCw,
  Search,
  Zap,
  Code,
  FileText,
  Boxes,
  FileSpreadsheet,
  HardDrive,
} from "lucide-react";

import { FileItem, FileStatus } from "@/types/workbench";

interface ProjectOverviewProps {
  workspaceId: string;
  files: FileItem[];
  onSelectFile: (file: FileItem) => void;
  onStartMigration: (preferredFile?: FileItem) => void;
  onBatchMigrate: () => void;
  isBatchMigrating: boolean;
  batchProgress?: { current: number; total: number; currentFile: string };
  onRefreshTree: () => void;
  onNewProject: () => void;
  hasManifest?: boolean | null;
  onInitManifest?: () => Promise<boolean | void>;
  isInitializingManifest?: boolean;
}

export function ProjectOverview({
  workspaceId,
  files,
  onSelectFile,
  onStartMigration,
  onBatchMigrate,
  isBatchMigrating,
  batchProgress,
  onRefreshTree,
  onNewProject,
  hasManifest,
  onInitManifest,
  isInitializingManifest,
}: ProjectOverviewProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [filterCategory, setFilterCategory] = useState<string>("all");

  // Flatten all files
  const allFiles = useMemo(() => {
    const list: FileItem[] = [];
    const traverse = (items: FileItem[]) => {
      for (const item of items) {
        if (item.type === "file") {
          list.push(item);
        }
        if (item.children) {
          traverse(item.children);
        }
      }
    };
    traverse(files);
    return list;
  }, [files]);

  // PHP / code files eligible for migration
  const phpFiles = useMemo(() => {
    return allFiles.filter(
      (f) =>
        f.name.endsWith(".php") ||
        f.name.endsWith(".phtml") ||
        f.name.includes(".blade.php")
    );
  }, [allFiles]);

  // Statistics
  const stats = useMemo(() => {
    const totalFiles = allFiles.length;
    const totalPhp = phpFiles.length;
    const migrated = phpFiles.filter((f) => f.status === "migrated").length;
    const warnings = phpFiles.filter((f) => f.status === "warning").length;
    const pending = totalPhp - migrated - warnings;
    const progressPercent = totalPhp > 0 ? Math.round((migrated / totalPhp) * 100) : 0;

    // Categories
    const categories: Record<string, number> = {};
    for (const f of phpFiles) {
      const cat = f.classification || "General PHP";
      categories[cat] = (categories[cat] || 0) + 1;
    }

    return {
      totalFiles,
      totalPhp,
      migrated,
      warnings,
      pending,
      progressPercent,
      categories,
    };
  }, [allFiles, phpFiles]);


  // Filtered files for the table
  const filteredFiles = useMemo(() => {
    return phpFiles.filter((f) => {
      const matchesSearch =
        searchQuery === "" ||
        f.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        f.path.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesCat =
        filterCategory === "all" ||
        (f.classification || "General PHP") === filterCategory;

      return matchesSearch && matchesCat;
    });
  }, [phpFiles, searchQuery, filterCategory]);

  return (
    <div className="flex-1 flex flex-col h-full bg-neutral-950 overflow-y-auto">
      {/* 1. Header Banner */}
      <div className="border-b border-neutral-800 bg-gradient-to-b from-neutral-900/90 to-neutral-950 px-8 py-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 text-xs font-mono font-medium">
                Project Overview
              </span>
              {hasManifest !== undefined && hasManifest !== null && (
                hasManifest ? (
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 text-[11px] font-mono">
                    <CheckCircle2 className="h-3 w-3" />
                    rhp.toml active
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-500/10 text-amber-300 border border-amber-500/30 text-[11px] font-mono">
                    <AlertTriangle className="h-3 w-3 text-amber-400" />
                    rhp.toml missing
                  </span>
                )
              )}
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-neutral-100 flex items-center gap-2.5">
              <span>Migrate PHP to RHP</span>
              {stats.progressPercent === 100 && (
                <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 text-xs font-medium font-sans">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Fully Migrated
                </span>
              )}
            </h1>
            <p className="text-xs text-neutral-400 max-w-xl leading-relaxed">
              Analyze AST readiness, resolve strict type shapes, transpile PHP to compiled RHP, and verify output with ahead-of-time compiler checks.
            </p>
          </div>

          {/* Action CTAs */}
          <div className="flex items-center gap-3 shrink-0">
            {/* Primary Migrate Project Button */}
            <button
              type="button"
              onClick={onBatchMigrate}
              disabled={isBatchMigrating || stats.pending === 0}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-medium text-xs shadow-[0_0_20px_rgba(16,185,129,0.25)] hover:shadow-[0_0_28px_rgba(16,185,129,0.4)] transition-all transform hover:-translate-y-0.5 active:translate-y-0 disabled:opacity-50"
              title="Runs 'rhp migrate .' recursively across the entire project directory"
            >
              {isBatchMigrating ? (
                <>
                  <RefreshCw className="h-4 w-4 animate-spin text-emerald-200" />
                  <span>Migrating Project ({batchProgress?.current || 0}/{batchProgress?.total || stats.totalPhp})...</span>
                </>
              ) : (
                <>
                  <Zap className="h-4 w-4 text-emerald-200" />
                  <span>Migrate Project</span>
                  <ArrowRight className="h-3.5 w-3.5 text-emerald-200" />
                </>
              )}
            </button>
          </div>
        </div>


        {/* Missing rhp.toml Scaffolding Alert */}
        {hasManifest === false && (
          <div className="mt-4 p-3.5 rounded-xl border border-amber-500/30 bg-amber-950/20 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5 text-xs text-amber-200">
              <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0" />
              <span>
                <strong>Project manifest (<code>rhp.toml</code>) is missing:</strong> Single-file migration (<code className="font-mono text-amber-300">rhp migrate --file=...</code>) strictly requires an <code className="font-mono">rhp.toml</code> manifest at the project root to map PSR-4 namespaces. Whole-project migration runs directly.
              </span>
            </div>
            {onInitManifest && (
              <button
                type="button"
                onClick={() => onInitManifest()}
                disabled={isInitializingManifest}
                className="px-3.5 py-1.5 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 text-amber-200 font-medium text-xs transition-colors shrink-0 flex items-center gap-1.5 disabled:opacity-50"
              >
                {isInitializingManifest ? (
                  <>
                    <RefreshCw className="h-3.5 w-3.5 animate-spin text-amber-300" />
                    <span>Initializing...</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="h-3.5 w-3.5 text-amber-300" />
                    <span>Initialize rhp.toml</span>
                  </>
                )}
              </button>
            )}
          </div>
        )}

        {/* Batch Migration Progress Banner (if active) */}
        {isBatchMigrating && batchProgress && (
          <div className="mt-4 p-3 rounded-lg border border-sky-500/30 bg-sky-950/20 space-y-2">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-sky-300 flex items-center gap-1.5">
                <RefreshCw className="h-3.5 w-3.5 animate-spin text-sky-400" />
                Batch Migrating: <span className="text-white font-semibold truncate max-w-sm">{batchProgress.currentFile}</span>
              </span>
              <span className="text-neutral-400">
                File {batchProgress.current} of {batchProgress.total} ({Math.round((batchProgress.current / batchProgress.total) * 100)}%)
              </span>
            </div>
            <div className="h-2 w-full bg-neutral-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-sky-500 to-emerald-500 transition-all duration-300"
                style={{ width: `${(batchProgress.current / batchProgress.total) * 100}%` }}
              />
            </div>
          </div>
        )}
      </div>

      {/* 2. Key Metrics Bar */}
      <div className="px-8 py-5 border-b border-neutral-800/80 bg-neutral-900/30">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {/* Card 1: Total Project Files */}
          <div className="p-3.5 rounded-xl border border-neutral-800 bg-neutral-900/60">
            <div className="flex items-center justify-between text-xs text-neutral-400 mb-1">
              <span>Total Workspace Files</span>
              <Boxes className="h-4 w-4 text-neutral-500" />
            </div>
            <div className="text-2xl font-bold text-neutral-100 font-mono">
              {stats.totalFiles}
            </div>
            <div className="text-[11px] text-neutral-500 mt-1">
              {stats.totalPhp} PHP / template files
            </div>
          </div>

          {/* Card 2: Migrated Files */}
          <div className="p-3.5 rounded-xl border border-neutral-800 bg-neutral-900/60">
            <div className="flex items-center justify-between text-xs text-emerald-400 mb-1">
              <span>Migrated &amp; Verified</span>
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
            </div>
            <div className="text-2xl font-bold text-emerald-400 font-mono">
              {stats.migrated}
              <span className="text-neutral-500 text-sm font-normal"> / {stats.totalPhp}</span>
            </div>
            <div className="text-[11px] text-neutral-400 mt-1">
              {stats.progressPercent}% completion rate
            </div>
          </div>

          {/* Card 3: Pending Files */}
          <div className="p-3.5 rounded-xl border border-neutral-800 bg-neutral-900/60">
            <div className="flex items-center justify-between text-xs text-neutral-400 mb-1">
              <span>Pending Migration</span>
              <Clock className="h-4 w-4 text-neutral-500" />
            </div>
            <div className="text-2xl font-bold text-neutral-200 font-mono">
              {stats.pending}
            </div>
            <div className="text-[11px] text-neutral-500 mt-1">
              Awaiting AOT transpilation
            </div>
          </div>

          {/* Card 4: Diagnostics / Warnings */}
          <div className="p-3.5 rounded-xl border border-neutral-800 bg-neutral-900/60">
            <div className="flex items-center justify-between text-xs text-amber-400 mb-1">
              <span>Requires Attention</span>
              <AlertTriangle className="h-4 w-4 text-amber-400" />
            </div>
            <div className="text-2xl font-bold text-amber-400 font-mono">
              {stats.warnings}
            </div>
            <div className="text-[11px] text-neutral-500 mt-1">
              Manual hints / critic review
            </div>
          </div>
        </div>

        {/* Progress Bar */}
        <div className="mt-4 space-y-1.5">
          <div className="flex items-center justify-between text-xs text-neutral-400">
            <span className="font-medium text-neutral-300">Overall Migration Progress</span>
            <span className="font-mono text-emerald-400">{stats.progressPercent}%</span>
          </div>
          <div className="h-2 w-full bg-neutral-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-emerald-500 to-teal-400 transition-all duration-500 rounded-full"
              style={{ width: `${stats.progressPercent}%` }}
            />
          </div>
        </div>
      </div>

      {/* 3. Guide & Workflow Steps */}
      <div className="px-8 py-5 border-b border-neutral-800/80 bg-neutral-950">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-3">
          How to Migrate Your Project
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
          <div className="p-3 rounded-lg border border-neutral-800 bg-neutral-900/40 space-y-1">
            <div className="flex items-center gap-2 text-neutral-200 font-medium">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-400 text-[11px] font-bold">1</span>
              <span>Select File or Click Start</span>
            </div>
            <p className="text-neutral-400 text-[11px] leading-relaxed pl-7">
              Pick any PHP file from the table below or click &quot;Start Migration&quot; to open the first pending component.
            </p>
          </div>

          <div className="p-3 rounded-lg border border-neutral-800 bg-neutral-900/40 space-y-1">
            <div className="flex items-center gap-2 text-neutral-200 font-medium">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-400 text-[11px] font-bold">2</span>
              <span>Transpile &amp; Compiler Verify</span>
            </div>
            <p className="text-neutral-400 text-[11px] leading-relaxed pl-7">
              AI applies strict prefix types, converts arrays to dicts, and runs compiler checks to ensure strict type safety.
            </p>
          </div>

          <div className="p-3 rounded-lg border border-neutral-800 bg-neutral-900/40 space-y-1">
            <div className="flex items-center gap-2 text-neutral-200 font-medium">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500/20 text-emerald-400 text-[11px] font-bold">3</span>
              <span>Review Diff &amp; Accept Next</span>
            </div>
            <p className="text-neutral-400 text-[11px] leading-relaxed pl-7">
              Inspect side-by-side Monaco diffs, re-prompt with the Critic loop if needed, then advance to the next file.
            </p>
          </div>
        </div>
      </div>

      {/* 4. Files List Table */}
      <div className="flex-1 px-8 py-5 flex flex-col min-h-0">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div>
            <h2 className="text-sm font-semibold text-neutral-200">
              Project Components &amp; Files ({phpFiles.length})
            </h2>
            <p className="text-[11px] text-neutral-500">
              Click any file to inspect code and begin migration
            </p>
          </div>

          <div className="flex items-center gap-2.5">
            {/* Category Filter */}
            <div className="flex items-center gap-1 bg-neutral-900 p-0.5 rounded-lg border border-neutral-800 text-[11px]">
              <button
                type="button"
                onClick={() => setFilterCategory("all")}
                className={`px-2.5 py-1 rounded-md transition-colors ${
                  filterCategory === "all"
                    ? "bg-neutral-800 text-white font-medium"
                    : "text-neutral-400 hover:text-neutral-200"
                }`}
              >
                All ({phpFiles.length})
              </button>
              {Object.entries(stats.categories).map(([cat, count]) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setFilterCategory(cat)}
                  className={`px-2.5 py-1 rounded-md transition-colors ${
                    filterCategory === cat
                      ? "bg-neutral-800 text-white font-medium"
                      : "text-neutral-400 hover:text-neutral-200"
                  }`}
                >
                  {cat} ({count})
                </button>
              ))}
            </div>

            {/* Search Input */}
            <div className="relative">
              <Search className="h-3.5 w-3.5 text-neutral-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Filter files..."
                className="pl-8 pr-3 py-1 text-xs rounded-lg bg-neutral-900 border border-neutral-800 text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-emerald-500 w-44"
              />
            </div>
          </div>
        </div>

        {/* Files Table */}
        <div className="flex-1 rounded-xl border border-neutral-800 bg-neutral-900/40 overflow-hidden flex flex-col">
          <div className="overflow-x-auto overflow-y-auto flex-1">
            <table className="w-full text-left text-xs text-neutral-300">
              <thead className="bg-neutral-900/90 text-[11px] uppercase tracking-wider text-neutral-400 border-b border-neutral-800 sticky top-0 z-10">
                <tr>
                  <th className="py-2.5 px-4 font-medium">File Path</th>
                  <th className="py-2.5 px-4 font-medium">Type</th>
                  <th className="py-2.5 px-4 font-medium">Size</th>
                  <th className="py-2.5 px-4 font-medium">Status</th>
                  <th className="py-2.5 px-4 font-medium text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-800/60 font-mono">
                {filteredFiles.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-neutral-500 font-sans">
                      No matching files found.
                    </td>
                  </tr>
                ) : (
                  filteredFiles.map((file) => {
                    const isPending = file.status !== "migrated" && file.status !== "warning";
                    const isMigrated = file.status === "migrated";
                    const isWarning = file.status === "warning";

                    return (
                      <tr
                        key={file.path}
                        onClick={() => onSelectFile(file)}
                        className="hover:bg-neutral-800/40 cursor-pointer transition-colors group"
                      >
                        <td className="py-2.5 px-4 font-mono text-neutral-200 group-hover:text-emerald-400 transition-colors">
                          <div className="flex items-center gap-2">
                            <FileCode2 className="h-3.5 w-3.5 text-sky-400 shrink-0" />
                            <span className="truncate max-w-md">{file.path}</span>
                          </div>
                        </td>
                        <td className="py-2.5 px-4">
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-sans uppercase font-medium bg-neutral-800 text-neutral-300 border border-neutral-700/60">
                            {file.classification || "PHP"}
                          </span>
                        </td>
                        <td className="py-2.5 px-4 text-neutral-500 text-[11px]">
                          {file.size ? `${(file.size / 1024).toFixed(1)} KB` : "—"}
                        </td>
                        <td className="py-2.5 px-4">
                          {isMigrated && (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-950/60 text-emerald-400 border border-emerald-800/60 text-[10px] font-sans">
                              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                              Migrated (Verified)
                            </span>
                          )}
                          {isWarning && (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-amber-950/60 text-amber-300 border border-amber-800/60 text-[10px] font-sans">
                              <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                              Needs Review
                            </span>
                          )}
                          {isPending && (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-neutral-800/60 text-neutral-400 border border-neutral-700/60 text-[10px] font-sans">
                              <span className="h-1.5 w-1.5 rounded-full bg-neutral-500" />
                              Pending
                            </span>
                          )}
                        </td>
                        <td className="py-2.5 px-4 text-right">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (isMigrated) {
                                onSelectFile(file);
                              } else {
                                onStartMigration(file);
                              }
                            }}
                            className={`px-3 py-1 rounded-md text-xs font-sans font-medium transition-colors ${
                              isMigrated
                                ? "bg-neutral-800 hover:bg-neutral-700 text-neutral-200"
                                : "bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm"
                            }`}
                            title={isMigrated ? "Inspect side-by-side diff" : `Migrate ${file.name} (rhp migrate --file)`}
                          >
                            {isMigrated ? "View Diff" : "Migrate File"}
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Table Footer with Summary and Download Status CSV */}
          <div className="px-5 py-3 bg-neutral-900/80 border-t border-neutral-800 flex items-center justify-between text-xs text-neutral-400 shrink-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[11px] text-neutral-500">
                Showing {filteredFiles.length} of {phpFiles.length} files
              </span>
              {stats.migrated > 0 && (
                <span className="text-[11px] text-emerald-400 font-mono">
                  ({stats.migrated} migrated, {stats.warnings} review)
                </span>
              )}
            </div>
            <a
              href={`/api/workspace/${workspaceId}/csv?download=true`}
              download={`migration_status_${workspaceId}.csv`}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg border border-sky-800/60 bg-sky-950/40 text-sky-300 hover:bg-sky-900/50 hover:text-sky-200 font-medium text-xs transition-colors shrink-0 shadow-sm"
              title="Download migration_status.csv containing all project file information: which file needs review, which is migration complete"
            >
              <FileSpreadsheet className="h-3.5 w-3.5 text-sky-400" />
              <span>Download Status CSV</span>
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
