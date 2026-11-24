"use client";

import React, { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  X,
  Upload,
  FolderGit2,
  Sparkles,
  Loader2,
  AlertCircle,
  FileArchive,
  ArrowRight,
  CheckCircle2,
  Github,
} from "lucide-react";

interface NewProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProjectCreated?: (workspaceId: string) => void;
}

type TabType = "upload" | "clone" | "demo";

export function NewProjectModal({
  isOpen,
  onClose,
  onProjectCreated,
}: NewProjectModalProps) {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<TabType>("upload");

  // Upload State
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Clone State
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [isCloning, setIsCloning] = useState(false);
  const [cloneError, setCloneError] = useState<string | null>(null);

  if (!isOpen) return null;

  // Handle Drag & Drop
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      if (file.name.endsWith(".zip")) {
        setSelectedFile(file);
        setUploadError(null);
      } else {
        setUploadError("Please provide a .zip archive of your PHP project.");
      }
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const file = e.target.files[0];
      if (file.name.endsWith(".zip")) {
        setSelectedFile(file);
        setUploadError(null);
      } else {
        setUploadError("Please provide a .zip archive of your PHP project.");
      }
    }
  };

  // Submit ZIP Upload
  const handleUploadZip = async () => {
    if (!selectedFile) {
      setUploadError("Please select a .zip archive to upload.");
      return;
    }

    setIsUploading(true);
    setUploadError(null);

    try {
      console.info(`[Workspace] Uploading ZIP archive: "${selectedFile.name}" (${(selectedFile.size / 1024).toFixed(1)} KB)...`);
      const formData = new FormData();
      formData.append("file", selectedFile);

      const res = await fetch("/api/workspace/upload-zip", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        const detailMsg =
          typeof errorData.detail === "string"
            ? errorData.detail
            : Array.isArray(errorData.detail)
            ? errorData.detail.map((d: any) => d.msg || JSON.stringify(d)).join(", ")
            : errorData.message || errorData.error;
        console.warn(`[Workspace] ZIP upload responded with status ${res.status}:`, errorData);
        throw new Error(
          detailMsg ||
            (res.status === 500
              ? "Backend server error (HTTP 500). Please ensure FastAPI backend is running at http://localhost:8000."
              : `Upload failed with status code ${res.status}`)
        );
      }

      const data = await res.json();
      const workspaceId = data.workspace_id;
      console.info(`[Workspace] ZIP unpacked successfully. Workspace ID: "${workspaceId}", total files: ${data.file_count}`);

      onClose();
      if (onProjectCreated) {
        onProjectCreated(workspaceId);
      }
      router.push(`/workbench/${workspaceId}?setup=true`);
    } catch (err: any) {
      console.error("[Workspace] Failed to upload ZIP archive:", err);
      setUploadError(
        err.message ||
          "Failed to connect to backend server. Make sure the backend is running at http://localhost:8000."
      );
    } finally {
      setIsUploading(false);
    }
  };

  // Submit GitHub Clone
  const handleCloneRepo = async () => {
    if (!repoUrl.trim()) {
      setCloneError("Please enter a valid GitHub repository URL.");
      return;
    }

    setIsCloning(true);
    setCloneError(null);

    try {
      const payload: {
        repo_url: string;
        branch?: string;
        github_token?: string;
      } = {
        repo_url: repoUrl.trim(),
      };
      if (branch.trim()) payload.branch = branch.trim();
      if (githubToken.trim()) payload.github_token = githubToken.trim();

      console.info(`[Workspace] Initiating GitHub clone: repo="${payload.repo_url}", branch="${payload.branch || 'default'}", token=${payload.github_token ? 'provided' : 'none'}...`);

      const res = await fetch("/api/workspace/clone-github", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        const detailMsg =
          typeof errorData.detail === "string"
            ? errorData.detail
            : Array.isArray(errorData.detail)
            ? errorData.detail.map((d: any) => d.msg || JSON.stringify(d)).join(", ")
            : errorData.message || errorData.error;
        console.warn(`[Workspace] GitHub clone responded with status ${res.status}:`, errorData);
        throw new Error(
          detailMsg ||
            (res.status === 500
              ? "Backend server error (HTTP 500). Please ensure FastAPI backend is running at http://localhost:8000."
              : `Clone failed with status code ${res.status}`)
        );
      }

      const data = await res.json();
      const workspaceId = data.workspace_id;
      console.info(`[Workspace] GitHub clone succeeded. Workspace ID: "${workspaceId}", total files: ${data.file_count}`);

      onClose();
      if (onProjectCreated) {
        onProjectCreated(workspaceId);
      }
      router.push(`/workbench/${workspaceId}?setup=true`);
    } catch (err: any) {
      console.error("[Workspace] Failed to clone GitHub repo:", err);
      setCloneError(
        err.message ||
          "Failed to clone repository. Check the repository URL and connectivity."
      );
    } finally {
      setIsCloning(false);
    }
  };

  // Load Demo Project
  const handleLoadDemo = () => {
    onClose();
    if (onProjectCreated) {
      onProjectCreated("demo");
    }
    router.push("/workbench/demo?setup=true");
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-xl rounded-xl border border-neutral-800 bg-neutral-900 shadow-2xl overflow-hidden flex flex-col">
        {/* Modal Header */}
        <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-4 bg-neutral-950/60">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
              <FolderGit2 className="h-4 w-4" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-neutral-100">
                Start New Project
              </h2>
              <p className="text-xs text-neutral-400">
                Import PHP codebase into an isolated migration workspace
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-800 hover:text-neutral-100 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="flex border-b border-neutral-800 bg-neutral-950/30 px-5 pt-2">
          <button
            type="button"
            onClick={() => setActiveTab("upload")}
            className={`flex items-center gap-2 border-b-2 px-3 py-2.5 text-xs font-medium transition-colors ${
              activeTab === "upload"
                ? "border-emerald-500 text-emerald-400"
                : "border-transparent text-neutral-400 hover:text-neutral-200"
            }`}
          >
            <Upload className="h-3.5 w-3.5" />
            <span>Upload ZIP Archive</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("clone")}
            className={`flex items-center gap-2 border-b-2 px-3 py-2.5 text-xs font-medium transition-colors ${
              activeTab === "clone"
                ? "border-emerald-500 text-emerald-400"
                : "border-transparent text-neutral-400 hover:text-neutral-200"
            }`}
          >
            <Github className="h-3.5 w-3.5" />
            <span>Clone from GitHub</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("demo")}
            className={`flex items-center gap-2 border-b-2 px-3 py-2.5 text-xs font-medium transition-colors ${
              activeTab === "demo"
                ? "border-emerald-500 text-emerald-400"
                : "border-transparent text-neutral-400 hover:text-neutral-200"
            }`}
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span>Demo Project</span>
          </button>
        </div>

        {/* Tab Content */}
        <div className="p-5 flex-1 space-y-4">
          {/* TAB 1: UPLOAD ZIP */}
          {activeTab === "upload" && (
            <div className="space-y-4">
              <div
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                className={`flex flex-col items-center justify-center p-6 border-2 border-dashed rounded-lg cursor-pointer transition-colors ${
                  isDragging
                    ? "border-emerald-500 bg-emerald-500/10"
                    : "border-neutral-700 bg-neutral-950/40 hover:border-neutral-600 hover:bg-neutral-800/40"
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".zip"
                  onChange={handleFileChange}
                  className="hidden"
                />
                <FileArchive className="h-10 w-10 text-emerald-400/80 mb-2" />
                <p className="text-xs font-medium text-neutral-200 text-center">
                  Drag and drop your project <span className="text-emerald-400 font-mono">.zip</span> here, or click to browse
                </p>
                <p className="text-[11px] text-neutral-500 mt-1">
                  Supports Laravel, Symfony, WordPress, or custom PHP codebases
                </p>
              </div>

              {/* Selected File Details */}
              {selectedFile && (
                <div className="flex items-center justify-between p-3 rounded-lg border border-neutral-800 bg-neutral-950/80 text-xs">
                  <div className="flex items-center gap-2.5 truncate">
                    <FileArchive className="h-4 w-4 text-emerald-400 shrink-0" />
                    <span className="text-neutral-200 font-mono truncate">
                      {selectedFile.name}
                    </span>
                    <span className="text-neutral-500 text-[11px]">
                      ({formatFileSize(selectedFile.size)})
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectedFile(null);
                    }}
                    className="p-1 text-neutral-400 hover:text-neutral-200"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}

              {/* Error Message */}
              {uploadError && (
                <div className="flex items-start gap-2 p-3 rounded-lg bg-rose-950/40 border border-rose-800 text-rose-300 text-xs">
                  <AlertCircle className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
                  <span>{uploadError}</span>
                </div>
              )}

              {/* Upload Button */}
              <button
                type="button"
                onClick={handleUploadZip}
                disabled={!selectedFile || isUploading}
                className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium text-xs shadow-sm transition-colors"
              >
                {isUploading ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    <span>Unpacking &amp; Creating Workspace...</span>
                  </>
                ) : (
                  <>
                    <Upload className="h-4 w-4" />
                    <span>Upload &amp; Start Migration</span>
                  </>
                )}
              </button>
            </div>
          )}

          {/* TAB 2: CLONE GITHUB */}
          {activeTab === "clone" && (
            <div className="space-y-3.5">
              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1">
                  GitHub Repository URL <span className="text-rose-400">*</span>
                </label>
                <input
                  type="text"
                  placeholder="https://github.com/laravel/laravel"
                  value={repoUrl}
                  onChange={(e) => setRepoUrl(e.target.value)}
                  className="w-full rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-xs text-neutral-200 placeholder-neutral-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-neutral-300 mb-1">
                    Branch (Optional)
                  </label>
                  <input
                    type="text"
                    placeholder="main"
                    value={branch}
                    onChange={(e) => setBranch(e.target.value)}
                    className="w-full rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-xs text-neutral-200 placeholder-neutral-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-neutral-300 mb-1">
                    GitHub Token (Private Repos)
                  </label>
                  <input
                    type="password"
                    placeholder="ghp_..."
                    value={githubToken}
                    onChange={(e) => setGithubToken(e.target.value)}
                    className="w-full rounded-lg border border-neutral-700 bg-neutral-950 px-3 py-2 text-xs text-neutral-200 placeholder-neutral-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 font-mono"
                  />
                </div>
              </div>

              {/* Error Message */}
              {cloneError && (
                <div className="flex items-start gap-2 p-3 rounded-lg bg-rose-950/40 border border-rose-800 text-rose-300 text-xs">
                  <AlertCircle className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
                  <span>{cloneError}</span>
                </div>
              )}

              {/* Clone Button */}
              <button
                type="button"
                onClick={handleCloneRepo}
                disabled={!repoUrl.trim() || isCloning}
                className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium text-xs shadow-sm transition-colors mt-2"
              >
                {isCloning ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    <span>Cloning Repository (Shallow)...</span>
                  </>
                ) : (
                  <>
                    <Github className="h-4 w-4" />
                    <span>Clone &amp; Start Migration</span>
                  </>
                )}
              </button>
            </div>
          )}

          {/* TAB 3: DEMO PROJECT */}
          {activeTab === "demo" && (
            <div className="space-y-4 py-2">
              <div className="p-4 rounded-lg border border-neutral-800 bg-neutral-950/60 text-xs space-y-2">
                <div className="flex items-center gap-2 text-emerald-400 font-semibold">
                  <Sparkles className="h-4 w-4" />
                  <span>Pre-Packaged PHP Demo Project</span>
                </div>
                <p className="text-neutral-300 leading-relaxed">
                  Want to explore RHP migration right away? The demo project includes sample PHP files with dynamic types, untyped array shapes, database migrations, and blade templates ready for transpilation.
                </p>
                <div className="pt-2 grid grid-cols-2 gap-2 text-[11px] text-neutral-400">
                  <div className="flex items-center gap-1.5">
                    <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                    <span>PaymentService (74% AST)</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                    <span>TaxCalculator (100% AST)</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                    <span>OrderController &amp; Model</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                    <span>Migrations &amp; Views</span>
                  </div>
                </div>
              </div>

              <button
                type="button"
                onClick={handleLoadDemo}
                className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs shadow-sm transition-colors"
              >
                <Sparkles className="h-4 w-4" />
                <span>Open Demo Project</span>
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="flex items-center justify-end border-t border-neutral-800 px-5 py-3 bg-neutral-950/40">
          <button
            type="button"
            onClick={onClose}
            className="px-3.5 py-1.5 rounded-lg border border-neutral-700 bg-neutral-800 text-neutral-300 hover:bg-neutral-700 hover:text-white transition-colors text-xs font-medium"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
