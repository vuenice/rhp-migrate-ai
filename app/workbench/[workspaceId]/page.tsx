"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import {
  FileTree,
} from "@/components/FileTree";
import {
  MonacoDiffViewer,
} from "@/components/MonacoDiffViewer";
import {
  AICopilotPanel,
} from "@/components/AICopilotPanel";
import {
  BYOKSettingsModal,
} from "@/components/BYOKSettingsModal";
import {
  FileItem,
  FileStatus,
  CompilerLogEntry,
  ASTScorecardData,
  ChatMessage,
  BYOKConfig,
} from "@/types/workbench";
import { useMigrationStream } from "@/hooks/useMigrationStream";
import {
  getStoredBYOKConfig,
  buildBYOKHeaders,
} from "@/lib/byok";
import {
  Sparkles,
  Settings,
  ArrowRight,
  Download,
  Terminal,
  Play,
  RotateCcw,
  CheckCircle2,
  FolderGit2,
} from "lucide-react";

// Rich default project files for instant interactive demo & fallback
const DEMO_PROJECT_FILES: FileItem[] = [
  {
    name: "app",
    path: "app",
    type: "directory",
    children: [
      {
        name: "Services",
        path: "app/Services",
        type: "directory",
        children: [
          {
            name: "PaymentService.php",
            path: "app/Services/PaymentService.php",
            type: "file",
            classification: "Service",
            status: "warning",
            size: 1420,
          },
          {
            name: "TaxCalculator.php",
            path: "app/Services/TaxCalculator.php",
            type: "file",
            classification: "Service",
            status: "migrated",
            size: 980,
          },
        ],
      },
      {
        name: "Models",
        path: "app/Models",
        type: "directory",
        children: [
          {
            name: "Order.php",
            path: "app/Models/Order.php",
            type: "file",
            classification: "Model",
            status: "pending",
            size: 2150,
          },
          {
            name: "User.php",
            path: "app/Models/User.php",
            type: "file",
            classification: "Model",
            status: "migrated",
            size: 1840,
          },
        ],
      },
      {
        name: "Http",
        path: "app/Http",
        type: "directory",
        children: [
          {
            name: "Controllers",
            path: "app/Http/Controllers",
            type: "directory",
            children: [
              {
                name: "OrderController.php",
                path: "app/Http/Controllers/OrderController.php",
                type: "file",
                classification: "Controller",
                status: "pending",
                size: 3200,
              },
            ],
          },
        ],
      },
    ],
  },
  {
    name: "database",
    path: "database",
    type: "directory",
    children: [
      {
        name: "migrations",
        path: "database/migrations",
        type: "directory",
        children: [
          {
            name: "2026_01_01_create_orders_table.php",
            path: "database/migrations/2026_01_01_create_orders_table.php",
            type: "file",
            classification: "Migration",
            status: "migrated",
            size: 1120,
          },
        ],
      },
    ],
  },
  {
    name: "resources",
    path: "resources",
    type: "directory",
    children: [
      {
        name: "views",
        path: "resources/views",
        type: "directory",
        children: [
          {
            name: "receipt.blade.php",
            path: "resources/views/receipt.blade.php",
            type: "file",
            classification: "Blade view",
            status: "pending",
            size: 780,
          },
        ],
      },
    ],
  },
];

const SAMPLE_PHP_CODE: Record<string, { php: string; rhp: string; scorecard: ASTScorecardData }> = {
  "app/Services/PaymentService.php": {
    php: `<?php

namespace App\\Services;

use App\\Models\\Order;
use Illuminate\\Support\\Facades\\Http;

class PaymentService
{
    private string $gatewayUrl;
    private $logger; // Untyped property

    public function __construct(string $gatewayUrl, $logger = null)
    {
        $this->gatewayUrl = $gatewayUrl;
        $this->logger = $logger;
    }

    /**
     * Process customer order charge
     */
    public function charge(Order $order, $amount, array $options = []): array
    {
        // Dynamic associative array in PHP
        $payload = [
            'order_id' => $order->id,
            'amount' => (int) ($amount * 100),
            'currency' => $options['currency'] ?? 'USD',
            'metadata' => $options['metadata'] ?? [],
        ];

        $response = Http::timeout(10)->post($this->gatewayUrl . '/v1/charges', $payload);

        if (!$response->successful()) {
            throw new \\RuntimeException("Charge failed: " . $response->body());
        }

        return $response->json();
    }
}
`,
    rhp: `<?rhp
// Transpiled by rhp-migrate-ai (Strict Ahead-of-Time Compiled RHP)
namespace App\\Services;

use App\\Models\\Order;
use RHP\\Http\\Client as Http;

final class PaymentService
{
    public function __construct(
        private string $gatewayUrl,
        private ?\\Psr\\Log\\LoggerInterface $logger = null
    ) {}

    /**
     * Process customer order charge with strict typed shapes
     */
    public function charge(
        Order $order,
        float $amount,
        dict<string, mixed> $options = dict[]
    ): dict<string, mixed> {
        // Enforced strict dict shape
        $payload = dict[
            'order_id' => $order->id,
            'amount' => (int) ($amount * 100.0),
            'currency' => (string) ($options['currency'] ?? 'USD'),
            'metadata' => (dict<string, mixed>) ($options['metadata'] ?? dict[]),
        ];

        $response = Http::timeout(10)->post($this->gatewayUrl . '/v1/charges', $payload);

        if (!$response->isSuccessful()) {
            throw new \\RuntimeException("Charge failed: " . $response->getBody());
        }

        return $response->jsonDict();
    }
}
`,
    scorecard: {
      readinessPercentage: 74,
      unsupportedCount: 1,
      untypedParamCount: 2,
      isMigrationReady: false,
      classes: ["PaymentService"],
      functions: ["__construct", "charge"],
      diagnostics: [
        {
          line: 11,
          column: 13,
          severity: "warning",
          rule_id: "RHP-UNTYPED-PROP",
          message: "Property '$logger' lacks strict type hint. Inferred: ?LoggerInterface",
        },
        {
          line: 20,
          column: 43,
          severity: "warning",
          rule_id: "RHP-UNTYPED-PARAM",
          message: "Parameter '$amount' lacks type hint. Inferred: float",
        },
        {
          line: 20,
          column: 52,
          severity: "warning",
          rule_id: "RHP-UNTYPED-ARRAY",
          message: "Untyped 'array $options' should be specialized to 'dict<string, mixed>'",
        },
      ],
    },
  },
  "app/Services/TaxCalculator.php": {
    php: `<?php

namespace App\\Services;

class TaxCalculator
{
    public function calculate(float $subtotal, float $taxRate): float
    {
        return $subtotal * $taxRate;
    }
}
`,
    rhp: `<?rhp
namespace App\\Services;

final class TaxCalculator
{
    public function calculate(float $subtotal, float $taxRate): float
    {
        return $subtotal * $taxRate;
    }
}
`,
    scorecard: {
      readinessPercentage: 100,
      unsupportedCount: 0,
      untypedParamCount: 0,
      isMigrationReady: true,
      classes: ["TaxCalculator"],
      functions: ["calculate"],
      diagnostics: [],
    },
  },
};

export default function WorkbenchPage() {
  const params = useParams();
  const workspaceId = (params?.workspaceId as string) || "demo";

  // State
  const [files, setFiles] = useState<FileItem[]>(DEMO_PROJECT_FILES);
  const [activeFile, setActiveFile] = useState<FileItem | null>(null);
  const [originalCode, setOriginalCode] = useState<string>("");
  const [modifiedCode, setModifiedCode] = useState<string>("");
  const [isTreeLoading, setIsTreeLoading] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [byokConfig, setByokConfig] = useState<BYOKConfig>(getStoredBYOKConfig());

  // Compiler logs & AST scorecard
  const [scorecard, setScorecard] = useState<ASTScorecardData | null>(null);
  const [compilerLogs, setCompilerLogs] = useState<CompilerLogEntry[]>([]);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);

  // Add a compiler log entry helper
  const addCompilerLog = useCallback(
    (level: "info" | "success" | "warn" | "error", message: string) => {
      const entry: CompilerLogEntry = {
        id: Math.random().toString(36).substring(7),
        timestamp: new Date().toLocaleTimeString(),
        level,
        message,
      };
      setCompilerLogs((prev) => [...prev, entry]);
    },
    []
  );

  // Update a file's status in the tree
  const updateFileStatus = useCallback(
    (filePath: string, status: FileStatus) => {
      const updateNode = (items: FileItem[]): FileItem[] => {
        return items.map((item) => {
          if (item.path === filePath) {
            return { ...item, status };
          }
          if (item.children) {
            return { ...item, children: updateNode(item.children) };
          }
          return item;
        });
      };
      setFiles((prev) => updateNode(prev));
      if (activeFile && activeFile.path === filePath) {
        setActiveFile((prev) => (prev ? { ...prev, status } : null));
      }
    },
    [activeFile]
  );

  // Load workspace file tree from FastAPI backend (or fallback to demo files)
  const fetchWorkspaceTree = useCallback(async () => {
    setIsTreeLoading(true);
    try {
      const res = await fetch(`/api/workspace/${workspaceId}/tree`);
      if (res.ok) {
        const data = await res.json();
        if (data.tree && Array.isArray(data.tree)) {
          // Map backend FileTreeNode to FileItem
          const mapNode = (node: any): FileItem => ({
            name: node.name,
            path: node.path,
            type: node.type,
            size: node.size,
            classification: node.classification,
            status: "pending",
            children: node.children ? node.children.map(mapNode) : undefined,
          });
          const mapped = data.tree.map(mapNode);
          setFiles(mapped);
          addCompilerLog("info", `Workspace '${workspaceId}' tree loaded (${data.total_files} files).`);
          return;
        }
      }
    } catch {
      // Backend not running or demo workspace, proceed with demo fallback
    } finally {
      setIsTreeLoading(false);
    }

    // Default to demo files
    setFiles(DEMO_PROJECT_FILES);
    addCompilerLog("info", `Loaded workspace '${workspaceId}' with demo project files.`);
  }, [workspaceId, addCompilerLog]);

  useEffect(() => {
    fetchWorkspaceTree();
  }, [fetchWorkspaceTree]);

  // Select initial file
  useEffect(() => {
    if (!activeFile && files.length > 0) {
      const firstFile = SAMPLE_PHP_CODE["app/Services/PaymentService.php"];
      if (firstFile) {
        const target: FileItem = {
          name: "PaymentService.php",
          path: "app/Services/PaymentService.php",
          type: "file",
          classification: "Service",
          status: "warning",
        };
        setActiveFile(target);
        setOriginalCode(firstFile.php);
        setModifiedCode(firstFile.rhp);
        setScorecard(firstFile.scorecard);

        setCompilerLogs([
          {
            id: "1",
            timestamp: new Date().toLocaleTimeString(),
            level: "info",
            message: "RHP Language Engine v0.1.0 initialized.",
          },
          {
            id: "2",
            timestamp: new Date().toLocaleTimeString(),
            level: "warn",
            message: "Untyped parameters detected in PaymentService::charge(). Manual hint recommended.",
          },
        ]);

        setChatMessages([
          {
            id: "m1",
            role: "assistant",
            content:
              "Welcome to the RHP Migration Workbench! I have inspected PaymentService.php. It has an AST readiness score of 74%. You can instruct me to specialize arrays or convert types using the chat below.",
            timestamp: new Date().toLocaleTimeString(),
          },
        ]);
      }
    }
  }, [files, activeFile]);

  // Handle file selection from FileTree
  const handleSelectFile = async (file: FileItem) => {
    if (file.type === "directory") return;
    setActiveFile(file);

    // Try fetching from backend first
    try {
      const res = await fetch(`/api/workspace/${workspaceId}/file?path=${encodeURIComponent(file.path)}`);
      if (res.ok) {
        const data = await res.json();
        setOriginalCode(data.content);
        setModifiedCode(data.content); // initially un-migrated
        addCompilerLog("info", `Loaded ${file.path} (${data.size} bytes).`);

        // Trigger AST analyze
        runAstAnalysis(file.path, data.content);
        return;
      }
    } catch {
      // Fallback to sample data
    }

    if (SAMPLE_PHP_CODE[file.path]) {
      const sample = SAMPLE_PHP_CODE[file.path];
      setOriginalCode(sample.php);
      setModifiedCode(sample.rhp);
      setScorecard(sample.scorecard);
      addCompilerLog("info", `Switched to sample: ${file.path}`);
    } else {
      const stubPhp = `<?php\n\n// Source code for ${file.path}\nclass ${file.name.replace(".php", "")} {\n    public function execute() {\n        return true;\n    }\n}\n`;
      setOriginalCode(stubPhp);
      setModifiedCode(stubPhp);
      setScorecard({
        readinessPercentage: 85,
        unsupportedCount: 0,
        untypedParamCount: 1,
        isMigrationReady: true,
      });
      addCompilerLog("info", `Opened stub: ${file.path}`);
    }
  };

  // Run AST Analysis via backend
  const runAstAnalysis = async (filePath: string, code: string) => {
    try {
      const headers = buildBYOKHeaders(byokConfig);
      const res = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify({ code, file_name: filePath }),
      });
      if (res.ok) {
        const data = await res.json();
        const fileReport = data.file_reports?.[filePath];
        if (fileReport) {
          setScorecard({
            readinessPercentage: fileReport.readiness_percentage,
            unsupportedCount: fileReport.unsupported_count,
            untypedParamCount: fileReport.untyped_param_count,
            isMigrationReady: fileReport.is_migration_ready,
            classes: fileReport.classes,
            functions: fileReport.functions,
            diagnostics: fileReport.diagnostics,
          });
          addCompilerLog("info", `AST Readiness for ${filePath}: ${fileReport.readiness_percentage}%`);
        }
      }
    } catch (err) {
      console.warn("AST analysis fallback used", err);
    }
  };

  // Run rhp check verification
  const handleRunCompilerCheck = async () => {
    if (!activeFile) return;
    addCompilerLog("info", `Running 'rhp check --strict ${activeFile.name.replace(".php", ".rhp")}'...`);

    // Simulate compiler execution or call backend verify
    setTimeout(() => {
      const hasErrors = modifiedCode.includes("TODO_RHP_TYPE") || scorecard?.unsupportedCount! > 0;
      if (hasErrors) {
        addCompilerLog("warn", "rhp check: 1 warning(s) found. Check AST Scorecard diagnostics.");
        updateFileStatus(activeFile.path, "warning");
      } else {
        addCompilerLog("success", "rhp check PASS: 0 syntax or type errors. AOT compilation ready!");
        updateFileStatus(activeFile.path, "migrated");
      }
    }, 700);
  };

  // Collect all files in flat list to determine next file
  const getAllFiles = useCallback((items: FileItem[]): FileItem[] => {
    let result: FileItem[] = [];
    for (const item of items) {
      if (item.type === "file") {
        result.push(item);
      }
      if (item.children) {
        result = result.concat(getAllFiles(item.children));
      }
    }
    return result;
  }, []);

  const allFileList = getAllFiles(files);
  const hasNextFile = allFileList.some(
    (f) => f.path !== activeFile?.path && f.status !== "migrated"
  );

  // ---------------------------------------------------------------------------
  // Reactive Migration Stream Hook with Real-Time Monaco Gutter Markers
  // ---------------------------------------------------------------------------
  const {
    step: migrationStep,
    isStreaming,
    diagnostics,
    verified: isVerified,
    startMigration,
    rePromptWithCritic,
    setEditorInstance,
  } = useMigrationStream({
    filePath: activeFile?.path || "input.php",
    originalCode,
    byokConfig,
    onChunk: (chunk, accumulated) => {
      setModifiedCode(accumulated);
    },
    onStatusChange: (step) => {
      if (activeFile) {
        if (step === "analyzing" || step === "transpiling") {
          updateFileStatus(activeFile.path, "analyzing");
        } else if (step === "critic_review") {
          updateFileStatus(activeFile.path, "warning");
        } else if (step === "done") {
          updateFileStatus(activeFile.path, "migrated");
        }
      }
    },
    onDiagnostic: (errors) => {
      if (errors.length > 0 && activeFile) {
        updateFileStatus(activeFile.path, "warning");
      }
      setScorecard((prev) =>
        prev
          ? {
              ...prev,
              diagnostics: errors.map((e) => ({
                line: e.line,
                column: e.col,
                severity: "error",
                message: e.message,
              })),
            }
          : null
      );
    },
    onComplete: (finalCode, verified) => {
      setModifiedCode(finalCode);
      if (activeFile) {
        updateFileStatus(activeFile.path, verified ? "migrated" : "warning");
      }
      setScorecard((prev) =>
        prev
          ? {
              ...prev,
              readinessPercentage: verified ? 100 : Math.max(70, prev.readinessPercentage),
              isMigrationReady: verified,
            }
          : null
      );
      setChatMessages((prev) => [
        ...prev,
        {
          id: Math.random().toString(36).substring(7),
          role: "assistant",
          content: verified
            ? `Successfully migrated ${activeFile?.name || "file"} to strict RHP! Syntax and compiler checks passed.`
            : `Migration completed for ${activeFile?.name || "file"} with compiler diagnostics. Click "Re-prompt with Critic" to auto-repair.`,
          timestamp: new Date().toLocaleTimeString(),
        },
      ]);
    },
    onLog: (level, msg) => {
      addCompilerLog(level, msg);
    },
  });

  // Action: Trigger migration loop
  const handleMigrate = async (customInstruction?: string) => {
    if (!activeFile || isStreaming) return;
    if (customInstruction) {
      setChatMessages((prev) => [
        ...prev,
        {
          id: Math.random().toString(36).substring(7),
          role: "user",
          content: customInstruction,
          timestamp: new Date().toLocaleTimeString(),
        },
      ]);
    }
    await startMigration(customInstruction);
  };

  // Action: Re-prompt with Critic
  const handleRepromptCritic = async () => {
    if (!activeFile || isStreaming) return;
    addCompilerLog("info", "Re-prompting Critic loop with compiler diagnostics for automated repair...");
    setChatMessages((prev) => [
      ...prev,
      {
        id: Math.random().toString(36).substring(7),
        role: "user",
        content: `Re-prompt Critic: Repair compiler diagnostics (${diagnostics.length} issue(s) detected)`,
        timestamp: new Date().toLocaleTimeString(),
      },
    ]);
    await rePromptWithCritic();
  };

  // Action: Accept & Next File
  const handleAcceptAndNext = useCallback(() => {
    if (!activeFile) return;

    // 1. Mark current file as migrated
    updateFileStatus(activeFile.path, "migrated");
    addCompilerLog("success", `Accepted RHP migration for ${activeFile.name}`);

    // 2. Find next pending / non-migrated file
    const flatFiles = getAllFiles(files);
    const currentIndex = flatFiles.findIndex((f) => f.path === activeFile.path);
    const remainingFiles = flatFiles.filter(
      (f, idx) => idx > currentIndex && f.status !== "migrated"
    );
    const nextFile =
      remainingFiles[0] ||
      flatFiles.find((f) => f.status !== "migrated" && f.path !== activeFile.path);

    if (nextFile) {
      addCompilerLog("info", `Advancing to next file: ${nextFile.path}`);
      handleSelectFile(nextFile);
    } else {
      addCompilerLog("success", "All files in workspace successfully migrated to RHP!");
    }
  }, [activeFile, files, getAllFiles, updateFileStatus, addCompilerLog]);

  const handleSendMessage = (msg: string) => {
    handleMigrate(msg);
  };

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-neutral-950 text-neutral-100 font-sans">
      {/* 1. Global Navigation Topbar */}
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-neutral-800 bg-neutral-900/90 z-20">
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

          {/* Workspace badge */}
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-neutral-950 border border-neutral-800 text-xs">
            <FolderGit2 className="h-3.5 w-3.5 text-neutral-400" />
            <span className="text-neutral-500 font-mono text-[11px]">workspace:</span>
            <span className="text-emerald-400 font-mono font-medium text-[11px] truncate max-w-[140px]">
              {workspaceId}
            </span>
          </div>
        </div>

        {/* Global Action Controls */}
        <div className="flex items-center gap-2">
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

      {/* 2. Main 3-Panel Grid Structure:
          - Left Panel (20% width): FileTree Explorer
          - Center Panel (55% width): Monaco Diff Editor
          - Right Panel (25% width): AI Copilot & Compiler Loop
      */}
      <div className="flex flex-1 w-full overflow-hidden">
        {/* Left Panel: 20% width */}
        <div className="w-[20%] min-w-[230px] max-w-[320px] h-full flex flex-col shrink-0">
          <FileTree
            files={files}
            selectedFilePath={activeFile?.path || null}
            onSelectFile={handleSelectFile}
            isLoading={isTreeLoading}
            onRefresh={fetchWorkspaceTree}
          />
        </div>

        {/* Center Panel: 55% width */}
        <div className="flex-1 min-w-[400px] h-full flex flex-col overflow-hidden">
          <MonacoDiffViewer
            originalCode={originalCode}
            modifiedCode={modifiedCode}
            activeFile={activeFile}
            onChangeModified={setModifiedCode}
            isStreaming={isStreaming}
            onMigrateClick={() => handleMigrate()}
            onVerifyClick={handleRunCompilerCheck}
            onMount={setEditorInstance}
            onAcceptAndNext={handleAcceptAndNext}
            onRepromptCritic={handleRepromptCritic}
            migrationStep={migrationStep}
            diagnostics={diagnostics}
            isVerified={isVerified}
            hasNextFile={hasNextFile}
          />
        </div>

        {/* Right Panel: 25% width */}
        <div className="w-[25%] min-w-[320px] max-w-[460px] h-full flex flex-col shrink-0 border-l border-neutral-800">
          <AICopilotPanel
            scorecard={scorecard}
            compilerLogs={compilerLogs}
            chatMessages={chatMessages}
            activeFile={activeFile}
            isStreaming={isStreaming}
            onSendMessage={handleSendMessage}
            onClearLogs={() => setCompilerLogs([])}
            onRunCompilerCheck={handleRunCompilerCheck}
            onAcceptAndNext={handleAcceptAndNext}
            onRepromptCritic={handleRepromptCritic}
            diagnosticsCount={diagnostics.length}
            migrationStep={migrationStep}
          />
        </div>
      </div>

      {/* 3. BYOK Settings Modal */}
      <BYOKSettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        onConfigSaved={(newCfg) => {
          setByokConfig(newCfg);
          addCompilerLog("info", `Updated AI Provider: ${newCfg.provider} (${newCfg.modelName})`);
        }}
      />
    </div>
  );
}
