"use client";

import React, { useState, useMemo } from "react";
import {
  Folder,
  FolderOpen,
  FileCode,
  Search,
  ChevronRight,
  ChevronDown,
  RefreshCw,
  SlidersHorizontal,
  FolderTree,
} from "lucide-react";
import { FileItem, FileStatus } from "@/types/workbench";

interface FileTreeProps {
  files: FileItem[];
  selectedFilePath: string | null;
  onSelectFile: (file: FileItem) => void;
  isLoading?: boolean;
  onRefresh?: () => void;
}

// Status Badges with clear icons and styling:
// ⚪ Pending, 🔵 Analyzing, 🟡 Warning, 🟢 Migrated & Compiler Verified
export function StatusBadge({ status }: { status?: FileStatus }) {
  switch (status) {
    case "migrated":
      return (
        <span
          title="🟢 Migrated & Compiler Verified (PASS)"
          className="inline-flex items-center justify-center text-xs"
        >
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.7)]" />
        </span>
      );
    case "warning":
      return (
        <span
          title="🟡 Warning: Needs manual type hints or unsupported constructs"
          className="inline-flex items-center justify-center text-xs"
        >
          <span className="h-2.5 w-2.5 rounded-full bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.7)] animate-pulse" />
        </span>
      );
    case "analyzing":
      return (
        <span
          title="🔵 Analyzing / In AST Check"
          className="inline-flex items-center justify-center text-xs"
        >
          <span className="h-2.5 w-2.5 rounded-full bg-sky-500 shadow-[0_0_8px_rgba(14,165,233,0.7)] animate-ping" />
        </span>
      );
    case "pending":
    default:
      return (
        <span
          title="⚪ Pending Migration"
          className="inline-flex items-center justify-center text-xs"
        >
          <span className="h-2 w-2 rounded-full bg-neutral-500 opacity-60" />
        </span>
      );
  }
}

function FileTreeNodeItem({
  item,
  selectedFilePath,
  onSelectFile,
  expandedPaths,
  toggleExpand,
  depth = 0,
}: {
  item: FileItem;
  selectedFilePath: string | null;
  onSelectFile: (file: FileItem) => void;
  expandedPaths: Set<string>;
  toggleExpand: (path: string) => void;
  depth?: number;
}) {
  const isDirectory = item.type === "directory";
  const isExpanded = expandedPaths.has(item.path);
  const isSelected = selectedFilePath === item.path;

  const handleClick = () => {
    if (isDirectory) {
      toggleExpand(item.path);
    } else {
      onSelectFile(item);
    }
  };

  return (
    <div className="select-none text-xs">
      <div
        onClick={handleClick}
        style={{ paddingLeft: `${depth * 14 + 10}px` }}
        className={`group flex items-center justify-between py-1.5 pr-2.5 cursor-pointer rounded-md transition-colors ${
          isSelected
            ? "bg-emerald-950/40 text-emerald-200 border-l-2 border-emerald-500 font-medium"
            : "text-neutral-300 hover:bg-neutral-800/60 hover:text-neutral-100"
        }`}
      >
        <div className="flex items-center gap-2 truncate">
          {isDirectory ? (
            <>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  toggleExpand(item.path);
                }}
                className="text-neutral-500 group-hover:text-neutral-300"
              >
                {isExpanded ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
              </button>
              {isExpanded ? (
                <FolderOpen className="h-3.5 w-3.5 text-amber-400 shrink-0" />
              ) : (
                <Folder className="h-3.5 w-3.5 text-amber-500/80 shrink-0" />
              )}
            </>
          ) : (
            <>
              <span className="w-3.5 shrink-0" />
              <FileCode className="h-3.5 w-3.5 text-sky-400 shrink-0" />
            </>
          )}

          <span className="truncate">{item.name}</span>
        </div>

        {/* Right tags & status */}
        <div className="flex items-center gap-1.5 shrink-0 ml-2">
          {item.classification && !isDirectory && (
            <span className="px-1 py-0.2 text-[9px] rounded font-mono uppercase tracking-tight bg-neutral-800 text-neutral-400 group-hover:text-neutral-300 border border-neutral-700/60">
              {item.classification.replace(" view", "")}
            </span>
          )}
          {!isDirectory && <StatusBadge status={item.status} />}
        </div>
      </div>

      {isDirectory && isExpanded && item.children && (
        <div>
          {item.children.map((child) => (
            <FileTreeNodeItem
              key={child.path}
              item={child}
              selectedFilePath={selectedFilePath}
              onSelectFile={onSelectFile}
              expandedPaths={expandedPaths}
              toggleExpand={toggleExpand}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function FileTree({
  files,
  selectedFilePath,
  onSelectFile,
  isLoading,
  onRefresh,
}: FileTreeProps) {
  const [searchTerm, setSearchTerm] = useState("");
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(() => {
    // Expand root directories by default
    const set = new Set<string>();
    files.forEach((f) => {
      if (f.type === "directory") set.add(f.path);
    });
    return set;
  });

  const toggleExpand = (path: string) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const expandAll = () => {
    const all = new Set<string>();
    const collect = (items: FileItem[]) => {
      for (const it of items) {
        if (it.type === "directory") {
          all.add(it.path);
          if (it.children) collect(it.children);
        }
      }
    };
    collect(files);
    setExpandedPaths(all);
  };

  const collapseAll = () => {
    setExpandedPaths(new Set());
  };

  // Compute status summary counts
  const counts = useMemo(() => {
    let pending = 0;
    let analyzing = 0;
    let warning = 0;
    let migrated = 0;

    const traverse = (items: FileItem[]) => {
      for (const it of items) {
        if (it.type === "file") {
          if (it.status === "migrated") migrated++;
          else if (it.status === "warning") warning++;
          else if (it.status === "analyzing") analyzing++;
          else pending++;
        }
        if (it.children) traverse(it.children);
      }
    };
    traverse(files);
    return { pending, analyzing, warning, migrated, total: pending + analyzing + warning + migrated };
  }, [files]);

  // Filter items by search query
  const filteredFiles = useMemo(() => {
    if (!searchTerm.trim()) return files;
    const term = searchTerm.toLowerCase();

    const filterNode = (node: FileItem): FileItem | null => {
      if (node.type === "file") {
        if (
          node.name.toLowerCase().includes(term) ||
          node.path.toLowerCase().includes(term)
        ) {
          return node;
        }
        return null;
      }

      if (node.children) {
        const matchingChildren = node.children
          .map(filterNode)
          .filter((n): n is FileItem => n !== null);

        if (
          matchingChildren.length > 0 ||
          node.name.toLowerCase().includes(term)
        ) {
          return {
            ...node,
            children: matchingChildren,
          };
        }
      }
      return null;
    };

    return files.map(filterNode).filter((n): n is FileItem => n !== null);
  }, [files, searchTerm]);

  return (
    <aside className="flex flex-col h-full bg-neutral-900 border-r border-neutral-800 select-none">
      {/* Explorer Header */}
      <div className="flex items-center justify-between px-3.5 py-3 border-b border-neutral-800 bg-neutral-950/40">
        <div className="flex items-center gap-2">
          <FolderTree className="h-4 w-4 text-emerald-400" />
          <span className="text-xs font-semibold uppercase tracking-wider text-neutral-300">
            Explorer
          </span>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-neutral-800 text-neutral-400 font-mono">
            {counts.total}
          </span>
        </div>

        <div className="flex items-center gap-1">
          {onRefresh && (
            <button
              onClick={onRefresh}
              title="Refresh tree"
              className="p-1 rounded text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 transition-colors"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`}
              />
            </button>
          )}
          <button
            onClick={expandAll}
            title="Expand all folders"
            className="p-1 rounded text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 transition-colors text-[10px]"
          >
            +
          </button>
          <button
            onClick={collapseAll}
            title="Collapse all folders"
            className="p-1 rounded text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 transition-colors text-[10px]"
          >
            -
          </button>
        </div>
      </div>

      {/* Status Legend & Quick Metrics */}
      <div className="grid grid-cols-4 gap-1 px-3 py-2 bg-neutral-950/60 border-b border-neutral-800/80 text-[10px] text-neutral-400 font-mono">
        <div className="flex items-center gap-1" title="⚪ Pending files">
          <span className="h-2 w-2 rounded-full bg-neutral-500" />
          <span>{counts.pending}</span>
        </div>
        <div className="flex items-center gap-1" title="🔵 Analyzing files">
          <span className="h-2 w-2 rounded-full bg-sky-500" />
          <span>{counts.analyzing}</span>
        </div>
        <div className="flex items-center gap-1" title="🟡 Needs type hints / warnings">
          <span className="h-2 w-2 rounded-full bg-amber-400" />
          <span>{counts.warning}</span>
        </div>
        <div className="flex items-center gap-1" title="🟢 Migrated & Verified PASS">
          <span className="h-2 w-2 rounded-full bg-emerald-500" />
          <span>{counts.migrated}</span>
        </div>
      </div>

      {/* Filter / Search input */}
      <div className="p-2 border-b border-neutral-800/80 bg-neutral-900">
        <div className="relative">
          <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-neutral-500" />
          <input
            type="text"
            placeholder="Filter files (e.g. Service)..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full rounded-md border border-neutral-800 bg-neutral-950/80 pl-8 pr-3 py-1.5 text-xs text-neutral-200 placeholder-neutral-600 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
          />
        </div>
      </div>

      {/* File List Tree */}
      <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
        {filteredFiles.length === 0 ? (
          <div className="p-4 text-center text-xs text-neutral-500">
            {searchTerm ? "No matching files found" : "Workspace is empty"}
          </div>
        ) : (
          filteredFiles.map((file) => (
            <FileTreeNodeItem
              key={file.path}
              item={file}
              selectedFilePath={selectedFilePath}
              onSelectFile={onSelectFile}
              expandedPaths={expandedPaths}
              toggleExpand={toggleExpand}
            />
          ))
        )}
      </div>

      {/* Footer Legend */}
      <div className="px-3 py-2 border-t border-neutral-800 bg-neutral-950/40 text-[10px] text-neutral-500 flex items-center justify-between">
        <span>RHP Strict Engine</span>
        <span className="text-emerald-500/80 font-mono">v0.1.0</span>
      </div>
    </aside>
  );
}
