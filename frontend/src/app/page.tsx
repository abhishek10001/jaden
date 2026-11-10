'use client';

import { useState, useRef, useEffect, useCallback } from 'react';

/* ─────────── Types ─────────── */
type OperationMode = 'scratch' | 'existing';

interface NodeUpdate {
  node: string;
  qa_passed?: boolean;
  retry_count?: number;
  pr_url?: string;
  live_url?: string;
  error_preview?: string;
}

interface AgentActivity {
  agent: string;
  status: 'active' | 'done' | 'error';
  action: string;
}

interface ArchitectPlanData {
  plan: string;
  filesToRead: string[];
  fileTree?: string;
}

interface GeneratedFile {
  path: string;
  content: string;
  lines: number;
}

interface VisualValidation {
  passed: boolean;
  visual_score?: number;
  observations?: string[];
  issues_detected?: string[];
  feedback_for_coder?: string;
}

interface ApiTestResult {
  endpoint: string;
  method: string;
  status: number;
  latencyMs: number;
  responseBody?: unknown;
  error?: string;
  passed: boolean;
}

interface BrowserMetrics {
  status?: number;
  title?: string;
  targetRoute?: string;
  bodyLength?: number;
  consoleErrors?: string[];
  screenshotTaken?: boolean;
}

interface QAResult {
  passed: boolean;
  logs: string;
  exitCode?: number;
  retryCount?: number;
  qaType?: 'frontend' | 'backend' | 'fullstack';
  targetRoute?: string;
  buildPassed?: boolean;
  browserPassed?: boolean;
  visualPassed?: boolean;
  screenshot?: string;
  browserMetrics?: BrowserMetrics;
  apiTestResults?: ApiTestResult[];
  visualValidation?: VisualValidation;
}

interface ReleaseData {
  prNumber?: number;
  prUrl?: string;
  previewUrl?: string;
  productionMerged?: boolean;
  liveUrl?: string;
  branchName?: string;
}

interface ApprovalRequiredData {
  runId: string;
  prNumber?: number;
  prUrl?: string;
  previewUrl?: string;
  branchName?: string;
  visualScore?: number;
  targetRoute?: string;
  filesChanged?: string[];
  qaType?: string;
  screenshot?: string;
}

interface LogEntry {
  id: string | number;
  timestamp: string;
  type: 'status' | 'node_update' | 'error' | 'done' | 'info';
  node?: string;
  message: string;
  raw?: unknown;
}

/* ─────────── Constants ─────────── */
const PIPELINE_STEPS = [
  { key: 'sandbox_init', label: 'Sandbox', icon: '01', description: 'Docker provisioning' },
  { key: 'architect', label: 'Architect', icon: '02', description: 'Component planning' },
  { key: 'coder', label: 'Coder', icon: '03', description: 'Source generation' },
  { key: 'qa_tester', label: 'QA', icon: '04', description: 'Multi-gate verification' },
  { key: 'preview_release', label: 'Preview', icon: '05', description: 'Draft PR & preview' },
  { key: 'human_approval', label: 'Gate', icon: '06', description: 'Human sign-off' },
  { key: 'production_release', label: 'Release', icon: '07', description: 'Production deploy' },
];

const NODE_LABELS: Record<string, string> = {
  architect: 'ARCHITECT',
  coder: 'CODER',
  qa_tester: 'QA_TESTER',
  preview_release: 'PREVIEW_RELEASE',
  human_approval: 'HUMAN_APPROVAL',
  production_release: 'PRODUCTION_RELEASE',
  release: 'RELEASE',
  sandbox: 'SANDBOX',
};

/* ─────────── Helpers ─────────── */
function getTime(): string {
  return new Date().toLocaleTimeString('en-US', { hour12: false });
}

function useClockTime() {
  const [time, setTime] = useState<string>('--:--:--');
  useEffect(() => {
    setTime(getTime());
    const interval = setInterval(() => setTime(getTime()), 1000);
    return () => clearInterval(interval);
  }, []);
  return time;
}

/* ─────────── Main Page ─────────── */
export default function AgentCommandCenter() {
  const [prompt, setPrompt] = useState('');
  const [repoName, setRepoName] = useState('');
  const [mode, setMode] = useState<OperationMode>('scratch');
  const [isRunning, setIsRunning] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [activeStep, setActiveStep] = useState(-1);
  const [completedSteps, setCompletedSteps] = useState<Set<string>>(new Set());
  const [failedStep, setFailedStep] = useState<string | null>(null);
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [liveUrl, setLiveUrl] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [isDone, setIsDone] = useState(false);
  const [isHalted, setIsHalted] = useState(false);
  const [isHalting, setIsHalting] = useState(false);

  /* ─── Rich Agent Observability State ─── */
  const [activeTab, setActiveTab] = useState<'activity' | 'blueprint' | 'code' | 'qa' | 'gitops' | 'logs'>('activity');
  const [currentActivity, setCurrentActivity] = useState<AgentActivity | null>(null);
  const [architectPlan, setArchitectPlan] = useState<ArchitectPlanData | null>(null);
  const [generatedFiles, setGeneratedFiles] = useState<GeneratedFile[]>([]);
  const [selectedFilePath, setSelectedFilePath] = useState<string>('');
  const [qaResult, setQaResult] = useState<QAResult | null>(null);
  const [releaseData, setReleaseData] = useState<ReleaseData | null>(null);
  const [copied, setCopied] = useState(false);
  const [isScreenshotExpanded, setIsScreenshotExpanded] = useState(false);

  /* ─── Human-in-the-Loop Approval State ─── */
  const [runId, setRunId] = useState<string>('');
  const [previewUrl, setPreviewUrl] = useState<string>('');
  const [approvalData, setApprovalData] = useState<ApprovalRequiredData | null>(null);
  const [isAwaitingApproval, setIsAwaitingApproval] = useState<boolean>(false);
  const [reviewerFeedback, setReviewerFeedback] = useState<string>('');
  const [isSubmittingApproval, setIsSubmittingApproval] = useState<boolean>(false);

  /* ─── Run Persistence & History State ─── */
  const [runsList, setRunsList] = useState<Array<Record<string, unknown>>>([]);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);

  const logContainerRef = useRef<HTMLDivElement>(null);
  const logIdRef = useRef(0);
  const clockTime = useClockTime();

  const addLog = useCallback((type: LogEntry['type'], message: string, node?: string, raw?: unknown) => {
    const entry: LogEntry = {
      id: `${Date.now()}-${logIdRef.current++}`,
      timestamp: getTime(),
      type,
      node,
      message,
      raw,
    };
    setLogs((prev) => [...prev, entry]);
  }, []);

  /* Auto-scroll logs */
  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs]);

  /* ─── SSE Stream Handler ─── */
  const startRun = useCallback(async (overrideMode?: OperationMode) => {
    if (!prompt.trim() || !repoName.trim()) return;

    const activeMode = overrideMode || mode;
    if (overrideMode && overrideMode !== mode) {
      setMode(overrideMode);
    }

    setIsRunning(true);
    setIsHalted(false);
    setIsHalting(false);
    setLogs([]);
    setActiveStep(0);
    setCompletedSteps(new Set());
    setFailedStep(null);
    setPrUrl(null);
    setLiveUrl(null);
    setRetryCount(0);
    setIsDone(false);

    // Reset rich agent states
    setCurrentActivity({ agent: 'sandbox', status: 'active', action: 'Provisioning isolated container...' });
    setArchitectPlan(null);
    setGeneratedFiles([]);
    setSelectedFilePath('');
    setQaResult(null);
    setReleaseData(null);
    setRunId('');
    setPreviewUrl('');
    setApprovalData(null);
    setIsAwaitingApproval(false);
    setReviewerFeedback('');
    setIsSubmittingApproval(false);
    setActiveTab('activity');

    addLog('info', `Initializing ${activeMode === 'scratch' ? 'greenfield' : 'update'} pipeline // target: ${repoName}`);

    try {
      const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const res = await fetch(`${backendUrl}/api/runs/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, mode: activeMode, repo_name: repoName }),
      });

      if (!res.ok || !res.body) {
        addLog('error', `HTTP ${res.status} // Connection to backend failed`);
        setIsRunning(false);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEventType = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          if (trimmed.startsWith('event:')) {
            currentEventType = trimmed.slice(6).trim();
          } else if (trimmed.startsWith('data:')) {
            const rawData = trimmed.slice(5).trim();
            const eventToDispatch = currentEventType || 'message';
            currentEventType = '';
            try {
              const data = JSON.parse(rawData);
              handleSSEEvent(eventToDispatch, data);
            } catch {
              /* skip malformed JSON */
            }
          }
        }
      }
    } catch (err) {
      addLog('error', `Connection error // ${err instanceof Error ? err.message : 'Unknown'}`);
    } finally {
      setIsRunning(false);
    }
  }, [prompt, repoName, mode, addLog]);

  /* ─── Emergency Halt Execution ─── */
  const haltRun = useCallback(async () => {
    const targetId = runId || (typeof window !== 'undefined' ? localStorage.getItem('jaden_active_run_id') : '') || '';
    setIsHalting(true);
    addLog('error', '[OPERATOR] Emergency halt dispatched. Aborting agents and container...');
    try {
      const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      if (!targetId) {
        setIsRunning(false);
        setIsHalting(false);
        setIsHalted(true);
        return;
      }
      const res = await fetch(`${backendUrl}/api/runs/${targetId}/halt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (res.ok) {
        setIsHalted(true);
        setIsRunning(false);
        addLog('error', '[HALTED] Pipeline execution terminated by operator.');
      }
    } catch (err) {
      addLog('error', `Halt dispatch failed // ${err instanceof Error ? err.message : 'Unknown'}`);
    } finally {
      setIsHalting(false);
    }
  }, [runId, addLog]);

  /* ─── SSE Event Dispatcher ─── */
  const handleSSEEvent = useCallback((event: string, data: Record<string, unknown>) => {
    switch (event) {
      case 'agent_activity': {
        const activity = data as unknown as AgentActivity;
        setCurrentActivity(activity);
        addLog('status', `[${activity.agent.toUpperCase()}] ${activity.action}`);
        if (activity.agent === 'sandbox' && activity.action.includes('branch agent/')) {
          const match = activity.action.match(/branch (agent\/[^\s.]+)/);
          if (match) {
            setReleaseData((prev) => ({
              ...prev,
              branchName: match[1],
            }));
          }
        }
        break;
      }
      case 'architect_plan': {
        const planData = {
          plan: data.plan as string,
          filesToRead: (data.files_to_read as string[]) || (data.filesToRead as string[]) || [],
          fileTree: data.file_tree as string,
        };
        setArchitectPlan(planData);
        addLog('info', `Architect blueprint created (${planData.filesToRead.length} files targeted)`);
        break;
      }
      case 'file_generated': {
        const newFile = data as unknown as GeneratedFile;
        setGeneratedFiles((prev) => {
          const idx = prev.findIndex((f) => f.path === newFile.path);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = newFile;
            return next;
          }
          return [...prev, newFile];
        });
        setSelectedFilePath((prev) => prev || newFile.path);
        addLog('node_update', `[CODER] Generated ${newFile.path} (${newFile.lines} lines)`);
        break;
      }
      case 'coder_complete': {
        const summary = (data.commit_summary as string) || '';
        const count = data.file_count as number;
        addLog('node_update', `[CODER] Completed ${count} files: ${summary}`);
        break;
      }
      case 'qa_screenshot': {
        const raw = data as Record<string, unknown>;
        const screenshot = (raw.screenshot as string) || '';
        const metrics = (raw.metrics as BrowserMetrics) || {};
        const visual = (raw.visual_validation as VisualValidation) || {};
        const qaType = (raw.qa_type as QAResult['qaType']) || 'frontend';
        const targetRoute = (raw.target_route as string) || '/';
        const apiTestResults = (raw.api_test_results as ApiTestResult[]) || [];

        setQaResult((prev) => {
          const base: QAResult = prev || { passed: true, logs: '' };
          return {
            ...base,
            qaType,
            targetRoute,
            screenshot: screenshot || base.screenshot,
            browserPassed: Boolean(raw.browser_passed ?? true),
            browserMetrics: metrics,
            apiTestResults: apiTestResults.length > 0 ? apiTestResults : base.apiTestResults,
            visualValidation: visual,
            visualPassed: visual.passed ?? true,
          };
        });
        const targetInfo = qaType === 'backend' ? `${apiTestResults.length} API endpoints verified` : `Route ${targetRoute} captured`;
        addLog('node_update', `[QA_TESTER] ${qaType.toUpperCase()} verification telemetry ready (${targetInfo})`);
        break;
      }
      case 'qa_result': {
        const raw = data as Record<string, unknown>;
        const qaType = (raw.qa_type as QAResult['qaType']) || 'frontend';
        const targetRoute = (raw.target_route as string) || '/';
        const apiTestResults = (raw.api_test_results as ApiTestResult[]) || [];
        const result: QAResult = {
          passed: Boolean(raw.passed),
          logs: (raw.logs as string) || '',
          exitCode: (raw.exit_code as number) ?? (raw.exitCode as number),
          retryCount: (raw.retry_count as number) ?? (raw.retryCount as number),
          qaType,
          targetRoute,
          buildPassed: (raw.build_passed as boolean) ?? Boolean(raw.passed),
          browserPassed: (raw.browser_passed as boolean) ?? false,
          visualPassed: (raw.visual_passed as boolean) ?? true,
          browserMetrics: raw.browser_metrics as BrowserMetrics,
          apiTestResults,
          visualValidation: raw.visual_validation as VisualValidation,
        };
        setQaResult((prev) => ({
          ...prev,
          ...result,
          screenshot: prev?.screenshot || (raw.screenshot as string) || '',
        }));
        if (result.passed) {
          const desc = qaType === 'backend' ? 'API endpoints validated' : qaType === 'fullstack' ? 'API and UI dual-gate passed' : 'Full-page UI validated';
          addLog('node_update', `[QA_TESTER] Multi-Gate QA verification PASSED (${desc})`);
        } else {
          addLog('error', `[QA_TESTER] QA verification FAILED (Exit code ${result.exitCode || 1})`);
        }
        break;
      }
      case 'preview_ready': {
        const raw = data as Record<string, unknown>;
        const pUrl = (raw.preview_url as string) || (raw.previewUrl as string) || '';
        const pr = (raw.pr_url as string) || (raw.prUrl as string) || '';
        if (pUrl) setPreviewUrl(pUrl);
        if (pr) setPrUrl(pr);
        setReleaseData((prev) => ({
          ...prev,
          previewUrl: pUrl || prev?.previewUrl,
          prUrl: pr || prev?.prUrl,
          prNumber: (raw.pr_number as number) ?? (raw.prNumber as number) ?? prev?.prNumber,
        }));
        setCompletedSteps((prev) => new Set(prev).add('preview_release'));
        setActiveStep(5);
        addLog('node_update', `[PREVIEW] Ephemeral preview and Pull Request ready: ${pUrl || pr}`);
        break;
      }
      case 'approval_required': {
        const raw = data as Record<string, unknown>;
        const rawScreenshot = (raw.screenshot as string) || '';
        const approvalPayload: ApprovalRequiredData = {
          runId: (raw.run_id as string) || (raw.runId as string) || '',
          prNumber: (raw.pr_number as number) ?? (raw.prNumber as number),
          prUrl: (raw.pr_url as string) || (raw.prUrl as string) || '',
          previewUrl: (raw.preview_url as string) || (raw.previewUrl as string) || '',
          branchName: (raw.branch_name as string) || (raw.branchName as string) || '',
          visualScore: (raw.visual_score as number) ?? (raw.visualScore as number),
          targetRoute: (raw.target_route as string) || (raw.targetRoute as string) || '/',
          filesChanged: (raw.files_changed as string[]) || (raw.filesChanged as string[]) || [],
          qaType: (raw.qa_type as string) || 'frontend',
          screenshot: rawScreenshot,
        };
        setApprovalData(approvalPayload);
        setIsAwaitingApproval(true);
        setCompletedSteps((prev) => new Set(prev).add('preview_release'));
        setActiveStep(5);
        setActiveTab('gitops');
        if (rawScreenshot) {
          setQaResult((prev) => (prev ? { ...prev, screenshot: rawScreenshot } : { passed: true, logs: '', screenshot: rawScreenshot }));
        }
        if (approvalPayload.runId) setRunId(approvalPayload.runId);
        if (approvalPayload.previewUrl) setPreviewUrl(approvalPayload.previewUrl);
        if (approvalPayload.prUrl) setPrUrl(approvalPayload.prUrl);
        addLog('status', `[HUMAN_APPROVAL] Production release hold! Human review & sign-off required.`);
        break;
      }
      case 'release_complete': {
        const raw = data as Record<string, unknown>;
        setIsAwaitingApproval(false);
        const rel: ReleaseData = {
          prNumber: (raw.pr_number as number) ?? (raw.prNumber as number),
          prUrl: (raw.pr_url as string) ?? (raw.prUrl as string),
          previewUrl: (raw.preview_url as string) ?? (raw.previewUrl as string) ?? previewUrl,
          productionMerged: Boolean(raw.production_merged ?? raw.productionMerged),
          liveUrl: (raw.live_url as string) ?? (raw.liveUrl as string),
          branchName: (raw.branch_name as string) ?? (raw.branchName as string),
        };
        setReleaseData((prev) => ({
          ...prev,
          ...rel,
        }));
        setCompletedSteps((prev) => new Set(prev).add('human_approval').add('production_release'));
        setActiveStep(6);
        setIsDone(true);
        if (rel.prUrl) setPrUrl(rel.prUrl);
        if (rel.liveUrl) setLiveUrl(rel.liveUrl);
        addLog('done', `[RELEASE] PR #${rel.prNumber || ''} merged to main! Production live: ${rel.liveUrl}`);
        break;
      }
      case 'status': {
        const step = data.step as string;
        const message = data.message as string;
        addLog('status', message);
        const rId = (data.run_id as string) || (data.runId as string);
        if (rId) {
          setRunId(rId);
          try {
            localStorage.setItem('jaden_active_run_id', rId);
          } catch { }
        }
        const branch = (data.branch_name as string) || (data.branchName as string);
        if (branch) {
          setReleaseData((prev) => ({
            ...prev,
            branchName: branch,
          }));
        }
        if (step === 'sandbox_init') {
          setActiveStep(0);
          setCompletedSteps((prev) => new Set(prev).add('sandbox_init'));
        }
        break;
      }
      case 'node_update': {
        const update = data as unknown as NodeUpdate;
        const nodeLabel = NODE_LABELS[update.node] || update.node;
        const stepIndex = PIPELINE_STEPS.findIndex((s) => s.key === update.node);

        if (stepIndex >= 0) setActiveStep(stepIndex);

        setCompletedSteps((prev) => {
          const next = new Set(prev);
          next.add('sandbox_init');
          for (let i = 0; i < stepIndex; i++) {
            next.add(PIPELINE_STEPS[i].key);
          }
          return next;
        });

        if (update.qa_passed === true) {
          setCompletedSteps((prev) => new Set(prev).add('qa_tester'));
        } else if (update.qa_passed === false) {
          const rc = update.retry_count || 0;
          setRetryCount(rc);
          if (rc >= 4) {
            setFailedStep('qa_tester');
          }
        } else if (update.pr_url) {
          setPrUrl(update.pr_url);
          if (update.live_url) setLiveUrl(update.live_url);
          setCompletedSteps((prev) => new Set(prev).add('release'));
        }
        break;
      }
      case 'error':
        addLog('error', `ERR // ${data.error}`);
        setCurrentActivity({ agent: 'system', status: 'error', action: String(data.error) });
        break;
      case 'done': {
        const runStatus = (data.status as string) || 'completed';
        if (runStatus === 'halted') {
          addLog('error', 'PIPELINE HALTED // Terminated by operator');
          setIsHalted(true);
        } else if (runStatus === 'failed') {
          addLog('error', 'PIPELINE FAILED // Quality gates could not be satisfied after retries');
          setFailedStep('qa_tester');
        } else {
          addLog('done', 'PIPELINE COMPLETE // All agents finished');
        }
        setIsDone(true);
        setIsRunning(false);
        setIsHalting(false);
        break;
      }
    }
  }, [addLog]);

  /* ─── Reconnect Stream after Browser Refresh ─── */
  const reconnectStream = useCallback(async (targetRunId: string) => {
    try {
      setIsRunning(true);
      const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const res = await fetch(`${backendUrl}/api/runs/${targetRunId}/stream`);
      if (!res.ok || !res.body) {
        setIsRunning(false);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEventType = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          if (trimmed.startsWith('event:')) {
            currentEventType = trimmed.slice(6).trim();
          } else if (trimmed.startsWith('data:')) {
            const rawData = trimmed.slice(5).trim();
            const eventToDispatch = currentEventType || 'message';
            currentEventType = '';
            try {
              const data = JSON.parse(rawData);
              handleSSEEvent(eventToDispatch, data);
            } catch { }
          }
        }
      }
    } catch (err) {
      console.warn('Stream reconnect warning:', err);
    } finally {
      setIsRunning(false);
    }
  }, [handleSSEEvent]);

  /* ─── Rehydrate Run from MongoDB / Database ─── */
  const loadRunById = useCallback(async (targetRunId: string) => {
    try {
      const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const res = await fetch(`${backendUrl}/api/runs/${targetRunId}`);
      if (!res.ok) return;
      const run = await res.json();

      setRunId(run.run_id);
      try {
        localStorage.setItem('jaden_active_run_id', run.run_id);
      } catch { }

      if (run.prompt) setPrompt(run.prompt);
      if (run.repo_name) {
        setRepoName(run.repo_name.includes('/') ? run.repo_name.split('/')[1] : run.repo_name);
      }
      if (run.mode) setMode(run.mode);
      if (run.pr_url) setPrUrl(run.pr_url);
      if (run.live_url) setLiveUrl(run.live_url);
      if (run.architect_plan) setArchitectPlan(run.architect_plan);
      if (run.files) {
        setGeneratedFiles(run.files);
        if (run.files.length > 0) setSelectedFilePath(run.files[0].path);
      }
      if (run.qa_result) setQaResult(run.qa_result);
      if (run.approval_data) setApprovalData(run.approval_data);
      if (run.preview_url) setPreviewUrl(run.preview_url);

      const isAwaiting = run.status === 'awaiting_approval';
      const isComplete = run.status === 'completed';
      const isHaltedStatus = run.status === 'halted';

      setIsAwaitingApproval(isAwaiting);
      setIsDone(isComplete || isHaltedStatus);
      setIsHalted(isHaltedStatus);
      setIsRunning(run.status === 'running');

      // Hydrate step indicators
      const completed = new Set<string>();
      if (run.checkpoints?.architect || run.architect_plan) {
        completed.add('sandbox_init').add('architect');
      }
      if (run.checkpoints?.coder || (run.files && run.files.length > 0)) {
        completed.add('coder');
      }
      if (run.checkpoints?.qa_tester || run.qa_result) {
        completed.add('qa_tester');
      }
      if (run.checkpoints?.preview_release || run.preview_url || run.pr_url) {
        completed.add('preview_release');
      }
      if (isComplete) {
        completed.add('human_approval').add('production_release');
        setActiveStep(6);
      } else if (isAwaiting) {
        setActiveStep(5);
        setActiveTab('gitops');
      }
      setCompletedSteps(completed);

      // Hydrate events/logs
      const evRes = await fetch(`${backendUrl}/api/runs/${targetRunId}/events`);
      if (evRes.ok) {
        const evData = await evRes.json();
        const evLogs: LogEntry[] = (evData.events || []).map((ev: { event: string; timestamp: number; data: Record<string, unknown> }, idx: number) => ({
          id: `hist-${ev.timestamp || Date.now()}-${idx}`,
          timestamp: new Date(ev.timestamp * 1000).toLocaleTimeString('en-US', { hour12: false }),
          type: ev.event === 'error' ? 'error' : ev.event === 'done' ? 'done' : ev.event === 'node_update' ? 'node_update' : 'status',
          node: (ev.data?.node as string) || undefined,
          message: (ev.data?.action as string) || (ev.data?.message as string) || (ev.data?.error as string) || `[${ev.event}]`,
          raw: ev.data
        }));
        logIdRef.current = evLogs.length + 10;
        setLogs(evLogs);
      }

      // If run is still actively executing, re-attach to the live SSE stream!
      if (run.status === 'running') {
        reconnectStream(targetRunId);
      }
    } catch (err) {
      console.warn('Failed to rehydrate run:', err);
    }
  }, [reconnectStream]);

  /* ─── Fetch Historical Runs from MongoDB ─── */
  const fetchRunHistory = useCallback(async () => {
    try {
      const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const res = await fetch(`${backendUrl}/api/runs`);
      if (res.ok) {
        const data = await res.json();
        setRunsList(data.runs || []);
      }
    } catch { }
  }, []);

  /* ─── Start Fresh New Mission ─── */
  const startNewMission = useCallback(() => {
    try {
      localStorage.removeItem('jaden_active_run_id');
    } catch { }
    setRunId('');
    setPrompt('');
    setRepoName('');
    setIsRunning(false);
    setIsDone(false);
    setIsHalted(false);
    setIsHalting(false);
    setLogs([]);
    setActiveStep(-1);
    setCompletedSteps(new Set());
    setPrUrl(null);
    setLiveUrl(null);
    setArchitectPlan(null);
    setGeneratedFiles([]);
    setSelectedFilePath('');
    setQaResult(null);
    setReleaseData(null);
    setApprovalData(null);
    setIsAwaitingApproval(false);
    setCurrentActivity(null);
    setActiveTab('activity');
  }, []);

  /* ─── Rehydrate on Initial Mount ─── */
  useEffect(() => {
    fetchRunHistory();
    try {
      const storedRunId = localStorage.getItem('jaden_active_run_id');
      if (storedRunId) {
        loadRunById(storedRunId);
      }
    } catch { }
  }, [fetchRunHistory, loadRunById]);


  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const selectedFile = generatedFiles.find((f) => f.path === selectedFilePath) || generatedFiles[0];

  /* ─── Human-in-the-Loop Decision Submission ─── */
  const handleApprovalDecision = async (approved: boolean) => {
    const targetRunId = runId || approvalData?.runId;
    if (!targetRunId) return;

    setIsSubmittingApproval(true);
    try {
      const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const res = await fetch(`${backendUrl}/api/runs/${targetRunId}/approval`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved, feedback: reviewerFeedback }),
      });

      if (res.ok) {
        if (approved) {
          addLog('status', '[HUMAN_APPROVAL] Production release approved! Proceeding with automated squash-merge...');
          setActiveStep(6);
          setCompletedSteps((prev) => new Set(prev).add('human_approval'));
        } else {
          addLog('status', `[HUMAN_APPROVAL] Fixation / changes requested: "${reviewerFeedback}". Re-routing to Coder for repair...`);
          setActiveStep(2);
          setCompletedSteps((prev) => {
            const next = new Set(prev);
            next.delete('qa_tester');
            next.delete('preview_release');
            next.delete('human_approval');
            return next;
          });
        }
        setIsAwaitingApproval(false);
        setReviewerFeedback('');
      } else {
        addLog('error', `Failed to submit approval: HTTP ${res.status}`);
      }
    } catch (err) {
      addLog('error', `Approval submission error: ${err instanceof Error ? err.message : 'Unknown'}`);
    } finally {
      setIsSubmittingApproval(false);
    }
  };

  /* ─── Derived state ─── */
  const isFailed = failedStep !== null && !isRunning;
  const statusText = isHalted
    ? 'HALTED'
    : isFailed
    ? 'FAILED'
    : isDone
    ? 'COMPLETE'
    : isRunning
    ? isHalting
      ? 'HALTING...'
      : 'EXECUTING'
    : 'STANDBY';
  const statusDotClass = isHalted
    ? 'hud-dot-halted'
    : isFailed
    ? 'hud-dot-halted'
    : isDone
    ? 'hud-dot-done'
    : isRunning
    ? 'hud-dot-active'
    : 'hud-dot-idle';
  const statusTextColor = isHalted || isFailed
    ? '#ff6b6b'
    : isDone
    ? '#5cb85c'
    : isRunning
    ? '#b89a4a'
    : '#666';

  return (
    <div className="hud-root">
      {/* Background effects */}
      <div className="hud-grid-bg" />
      <div className="hud-scan-overlay" />
      <div className="hud-bracket-tl" />
      <div className="hud-bracket-tr" />
      <div className="hud-bracket-bl" />
      <div className="hud-bracket-br" />

      {/* ─── Top Bar ─── */}
      <div className="hud-topbar">
        <div className="hud-logo">
          <div className="hud-logo-mark">J</div>
          <span className="hud-logo-text">Jaden</span>
          <div className="hud-logo-divider" />
          <span className="hud-logo-sub">Multi-Agent SDLC Operations</span>
        </div>
        <div className="hud-topbar-right">
          <button
            type="button"
            className="hud-copy-btn"
            onClick={() => {
              setIsHistoryOpen(!isHistoryOpen);
              fetchRunHistory();
            }}
            style={{ fontSize: '10px', padding: '4px 10px', borderColor: '#444' }}
            title="Inspect historical missions stored in MongoDB"
          >
            📋 RUN HISTORY [{runsList.length}]
          </button>
          {runId && (
            <button
              type="button"
              className="hud-copy-btn"
              onClick={startNewMission}
              style={{ fontSize: '10px', padding: '4px 10px', borderColor: '#5a9ab5', color: '#5a9ab5' }}
              title="Reset dashboard and start a new mission"
            >
              + NEW MISSION
            </button>
          )}
          <div className="hud-status-indicator">
            <div className={`hud-dot ${statusDotClass}`} />
            <span style={{ color: statusTextColor }}>
              {statusText}
            </span>
          </div>
          <span className="hud-time" suppressHydrationWarning>{clockTime}</span>
        </div>
      </div>

      {/* ─── Main Content ─── */}
      <div className="hud-main">
        {/* ─── Left Panel: Mission Controls & Pipeline Status ─── */}
        <div className="hud-left">
          <div className="hud-section-header">Mission Parameters</div>

          <div className="hud-form">
            {/* Mode */}
            <div>
              <label className="hud-label">Operation Mode</label>
              <div className="hud-mode-toggle">
                <button
                  id="mode-scratch"
                  className={`hud-mode-btn ${mode === 'scratch' ? 'active' : ''}`}
                  onClick={() => setMode('scratch')}
                  disabled={isRunning}
                >
                  New Build
                </button>
                <button
                  id="mode-existing"
                  className={`hud-mode-btn ${mode === 'existing' ? 'active' : ''}`}
                  onClick={() => setMode('existing')}
                  disabled={isRunning}
                >
                  Update
                </button>
              </div>
            </div>

            {/* Target Repo */}
            <div>
              <label className="hud-label">Target Repository</label>
              <input
                id="input-repo-name"
                type="text"
                className="hud-input"
                placeholder={mode === 'scratch' ? 'project-name' : 'user/repository'}
                value={repoName}
                onChange={(e) => setRepoName(e.target.value)}
                disabled={isRunning}
              />
            </div>

            {/* Directive Prompt */}
            <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
              <label className="hud-label">Directive Prompt</label>
              <textarea
                id="input-prompt"
                className="hud-input hud-textarea"
                placeholder="Describe what the autonomous agents should build or modify..."
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                disabled={isRunning}
              />
            </div>

            {/* Primary Action Button (Launch / Halt) */}
            {!isRunning ? (
              <button
                id="btn-launch"
                onClick={() => startRun()}
                disabled={!prompt.trim() || !repoName.trim()}
                className="hud-deploy-btn"
              >
                [&gt;&gt;] Launch Autonomous Pipeline
              </button>
            ) : (
              <button
                id="btn-halt"
                type="button"
                onClick={haltRun}
                disabled={isHalting}
                className="hud-halt-btn hud-halt-btn-main"
                title="Immediately abort running pipeline and kill Docker sandbox"
              >
                {isHalting ? (
                  <>
                    <span className="hud-spinner" style={{ width: 12, height: 12, borderTopColor: '#ff6b6b' }} />
                    HALTING PIPELINE...
                  </>
                ) : (
                  <>■ HALT PIPELINE</>
                )}
              </button>
            )}
          </div>

          {/* ─── Pipeline Progress Gates ─── */}
          <div className="hud-pipeline">
            <div className="hud-section-header">SDLC Gate Progression</div>
            {PIPELINE_STEPS.map((step) => {
              const isComplete = completedSteps.has(step.key);
              const isActive = PIPELINE_STEPS[activeStep]?.key === step.key && isRunning;
              const isFailed = failedStep === step.key || (isHalted && PIPELINE_STEPS[activeStep]?.key === step.key);

              const markerClass = isComplete ? 'complete' : isFailed ? 'failed' : isActive ? 'active' : 'waiting';
              const nameClass = isComplete ? 'complete' : isFailed ? 'failed' : isActive ? 'active' : '';

              return (
                <div key={step.key} className="hud-step">
                  <div className={`hud-step-marker ${markerClass}`}>
                    {isComplete ? '✓' : isFailed ? '✗' : isActive ? '▸' : step.icon}
                  </div>
                  <div className="hud-step-info">
                    <div className={`hud-step-name ${nameClass}`}>
                      {step.label}
                    </div>
                    <div className="hud-step-desc">{isFailed && isHalted ? 'Halted by operator' : step.description}</div>
                  </div>
                  {step.key === 'qa_tester' && retryCount > 0 && !isComplete && (
                    <span className="hud-step-badge">
                      Self-Heal {retryCount}/4
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* ─── Right Panel: Interactive Agent Workspace ─── */}
        <div className="hud-right">
          {/* Recovery Banner on Failure/Interruption */}
          {(failedStep || currentActivity?.status === 'error') && (
            <div className="hud-recovery-banner">
              <div className="hud-recovery-info">
                <span className="hud-recovery-title">[PIPELINE INTERRUPTED]</span>
                <span className="hud-recovery-msg">
                  Tasks up to this step are preserved on GitHub. Click to continue without restarting from scratch.
                </span>
              </div>
              <button
                className="hud-recovery-btn"
                disabled={isRunning}
                onClick={() => {
                  startRun('existing');
                }}
              >
                [&gt;&gt;] RESUME IN UPDATE MODE
              </button>
            </div>
          )}

          {/* Human Approval Gate Banner */}
          {isAwaitingApproval && approvalData && (
            <div className="hud-approval-gate">
              <div className="hud-approval-header">
                <div className="hud-approval-title-box">
                  <span className="hud-approval-tag">HUMAN APPROVAL REQUIRED</span>
                  <span className="hud-approval-subtitle">
                    Automated build verified. Review ephemeral preview and sign-off before production merge.
                  </span>
                </div>
                <div className="hud-approval-metrics">
                  {approvalData.visualScore !== undefined && (
                    <span className="hud-approval-chip">
                      VISUAL SCORE: <strong>{approvalData.visualScore}/10</strong>
                    </span>
                  )}
                  {approvalData.targetRoute && (
                    <span className="hud-approval-chip">
                      ROUTE: <strong>{approvalData.targetRoute}</strong>
                    </span>
                  )}
                  {approvalData.branchName && (
                    <span className="hud-approval-chip">
                      BRANCH: <strong>{approvalData.branchName}</strong>
                    </span>
                  )}
                </div>
              </div>

              <div className="hud-approval-body">
                {/* ── In-App Sandbox Render Preview Card ── */}
                {(approvalData.screenshot || qaResult?.screenshot) && (
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '14px',
                    padding: '8px 12px',
                    background: 'rgba(0,0,0,0.45)',
                    border: '1px solid rgba(184, 154, 74, 0.35)',
                    borderRadius: '2px',
                    position: 'relative'
                  }}>
                    <div
                      style={{
                        width: '120px',
                        height: '72px',
                        overflow: 'hidden',
                        position: 'relative',
                        cursor: 'pointer',
                        border: '1px solid #444',
                        background: '#090909',
                        flexShrink: 0
                      }}
                      onClick={() => setIsScreenshotExpanded(true)}
                      title="Click to view full resolution in-app screenshot"
                    >
                      <img
                        src={`data:image/png;base64,${approvalData.screenshot || qaResult?.screenshot}`}
                        alt="Sandbox Render Preview"
                        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                      />
                      <div style={{
                        position: 'absolute',
                        inset: 0,
                        background: 'rgba(0,0,0,0.3)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '9px',
                        color: '#fff',
                        fontWeight: 600,
                        letterSpacing: '1px'
                      }}>
                        EXPAND 🔍
                      </div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ color: '#5cb85c', fontSize: '11px', fontWeight: 700, letterSpacing: '1px' }}>
                          ● IN-APP LIVE PREVIEW READY
                        </span>
                        <span style={{ color: '#888', fontSize: '10px' }}>
                          Route: <strong style={{ color: '#ccc' }}>http://localhost:3000{approvalData.targetRoute || '/'}</strong>
                        </span>
                      </div>
                      <span style={{ fontSize: '11px', color: '#aaa' }}>
                        Build validated inside Docker sandbox. Review the rendered output, inspect code diffs, or submit revision feedback below.
                      </span>
                    </div>
                    <button
                      type="button"
                      className="hud-copy-btn"
                      onClick={() => setIsScreenshotExpanded(true)}
                      style={{ flexShrink: 0 }}
                    >
                      FULLSCREEN PREVIEW [🔍]
                    </button>
                  </div>
                )}

                <div className="hud-approval-links">
                  {approvalData.previewUrl && approvalData.previewUrl.startsWith('http') && (
                    <a
                      href={approvalData.previewUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hud-approval-link"
                    >
                      <span>🔍</span>
                      <span>EXTERNAL LIVE PREVIEW ↗</span>
                    </a>
                  )}
                  {approvalData.prUrl && (
                    <a
                      href={approvalData.prUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hud-approval-link"
                    >
                      <span>🐙</span>
                      <span>DRAFT PR #{approvalData.prNumber || ''} ↗</span>
                    </a>
                  )}
                  {approvalData.filesChanged && approvalData.filesChanged.length > 0 && (
                    <button
                      type="button"
                      className="hud-step-badge"
                      style={{ color: '#5a9ab5', borderColor: '#5a9ab5', background: 'transparent', cursor: 'pointer' }}
                      onClick={() => setActiveTab('code')}
                    >
                      Inspect {approvalData.filesChanged.length} Modified Files →
                    </button>
                  )}
                </div>

                <div className="hud-approval-input-row">
                  <input
                    type="text"
                    className="hud-approval-input"
                    placeholder="Describe fixes or modifications for Coder agent (e.g. 'Add backspace button', 'Make buttons round')..."
                    value={reviewerFeedback}
                    onChange={(e) => setReviewerFeedback(e.target.value)}
                    disabled={isSubmittingApproval}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && reviewerFeedback.trim()) {
                        handleApprovalDecision(false);
                      }
                    }}
                  />
                  <div className="hud-approval-actions">
                    <button
                      type="button"
                      className="hud-approval-btn-approve"
                      disabled={isSubmittingApproval}
                      onClick={() => handleApprovalDecision(true)}
                    >
                      {isSubmittingApproval ? 'Processing Merge...' : '✓ Approve & Deploy to Prod'}
                    </button>
                    <button
                      type="button"
                      className="hud-approval-btn-reject"
                      disabled={isSubmittingApproval}
                      onClick={() => handleApprovalDecision(false)}
                      title="Route back to Coder agent with feedback for repair"
                    >
                      ✕ Request Fixation
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* 1. Live Agent Mind & Activity Banner */}
          <div className={`hud-activity-banner ${isRunning ? 'active' : ''}`}>
            <div className="hud-activity-left">
              <span className={`hud-activity-badge ${currentActivity?.agent || ''}`}>
                <span className="hud-spinner" style={{ display: isRunning ? 'inline-block' : 'none', width: 8, height: 8 }} />
                [{currentActivity ? currentActivity.agent.toUpperCase() : 'STANDBY'}]
              </span>
              <span className="hud-activity-action">
                {currentActivity?.action || 'Awaiting mission parameters. Agents standing by.'}
              </span>
            </div>
            {isRunning && <div className="hud-activity-scanbar" />}
          </div>

          {/* 2. Workspace Navigation Tabs */}
          <div className="hud-workspace-tabs">
            <button
              className={`hud-tab-btn ${activeTab === 'activity' ? 'active' : ''}`}
              onClick={() => setActiveTab('activity')}
            >
              <span>// Activity Feed</span>
              <span className="hud-tab-badge">{logs.length}</span>
            </button>

            <button
              className={`hud-tab-btn ${activeTab === 'blueprint' ? 'active' : ''}`}
              onClick={() => setActiveTab('blueprint')}
            >
              <span>// Architect Plan</span>
              {architectPlan && <span className="hud-tab-badge highlight">READY</span>}
            </button>

            <button
              className={`hud-tab-btn ${activeTab === 'code' ? 'active' : ''}`}
              onClick={() => setActiveTab('code')}
            >
              <span>// Code Explorer</span>
              <span className={`hud-tab-badge ${generatedFiles.length > 0 ? 'highlight' : ''}`}>
                {generatedFiles.length} {generatedFiles.length === 1 ? 'FILE' : 'FILES'}
              </span>
            </button>

            <button
              className={`hud-tab-btn ${activeTab === 'qa' ? 'active' : ''}`}
              onClick={() => setActiveTab('qa')}
            >
              <span>// QA Test Lab</span>
              {qaResult && (
                <span className={`hud-tab-badge ${qaResult.passed ? 'green' : 'amber'}`}>
                  {qaResult.passed ? 'PASS' : 'RETRY'}
                </span>
              )}
            </button>

            <button
              className={`hud-tab-btn ${activeTab === 'gitops' ? 'active' : ''}`}
              onClick={() => setActiveTab('gitops')}
            >
              <span>// GitOps & Release</span>
              {(releaseData?.liveUrl || liveUrl) && <span className="hud-tab-badge green">LIVE</span>}
            </button>
          </div>

          {/* 3. Workspace Tab Content */}
          <div className="hud-tab-content">
            {/* ─── TAB 1: Real-time Activity Feed & System Console ─── */}
            {activeTab === 'activity' && (
              <div ref={logContainerRef} className="hud-console-body">
                {logs.length === 0 ? (
                  <div className="hud-empty">
                    <div className="hud-empty-icon">// STANDBY</div>
                    <div className="hud-empty-text">
                      No active telemetry stream<br />
                      Launch pipeline to inspect live agent collaboration
                    </div>
                  </div>
                ) : (
                  logs.map((log, idx) => {
                    const prefix =
                      log.type === 'error'
                        ? '!'
                        : log.type === 'done'
                          ? 'OK'
                          : log.type === 'status'
                            ? '~'
                            : log.type === 'info'
                              ? '#'
                              : '>';
                    const msgClass = `hud-log-${log.type === 'node_update' ? 'node' : log.type}`;
                    return (
                      <div key={`log-${log.id}-${idx}`} className="hud-log-entry">
                        <span className="hud-log-time">{log.timestamp}</span>
                        <span
                          className="hud-log-prefix"
                          style={{
                            color: log.type === 'error' ? '#c04040' : log.type === 'done' ? '#5cb85c' : '#5a9ab5',
                          }}
                        >
                          {prefix}
                        </span>
                        <span className={`hud-log-msg ${msgClass}`}>{log.message}</span>
                      </div>
                    );
                  })
                )}
              </div>
            )}

            {/* ─── TAB 2: Architect Blueprint & System Strategy ─── */}
            {activeTab === 'blueprint' && (
              <div className="hud-blueprint-container">
                {!architectPlan ? (
                  <div className="hud-empty">
                    <div className="hud-empty-icon">// ARCHITECT</div>
                    <div className="hud-empty-text">
                      {isRunning
                        ? 'Architect Agent (Gemini 3.8 Flash) is analyzing codebase and formulating plan...'
                        : 'Architect Plan will display here once the pipeline is triggered.'}
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="hud-blueprint-meta">
                      <div className="hud-meta-item">
                        <span className="hud-meta-label">Inspected Files</span>
                        <div>
                          {architectPlan.filesToRead.map((f, idx) => (
                            <span key={`inspected-${f}-${idx}`} className="hud-tag-pill">{f}</span>
                          ))}
                        </div>
                      </div>
                    </div>

                    <div className="hud-markdown-box">
                      {architectPlan.plan}
                    </div>
                  </>
                )}
              </div>
            )}

            {/* ─── TAB 3: Code Explorer & File Inspector ─── */}
            {activeTab === 'code' && (
              <div className="hud-code-explorer">
                {generatedFiles.length === 0 ? (
                  <div className="hud-empty" style={{ gridColumn: '1 / -1' }}>
                    <div className="hud-empty-icon">// CODE</div>
                    <div className="hud-empty-text">
                      {isRunning
                        ? 'Coder Agent is generating components and styles inside the Docker sandbox...'
                        : 'Generated source code and components will appear here in real time.'}
                    </div>
                  </div>
                ) : (
                  <>
                    {/* File Sidebar */}
                    <div className="hud-code-sidebar">
                      {generatedFiles.map((file) => (
                        <div
                          key={file.path}
                          className={`hud-file-item ${file.path === selectedFile?.path ? 'selected' : ''}`}
                          onClick={() => setSelectedFilePath(file.path)}
                        >
                          <span>{file.path}</span>
                          <span className="hud-file-lines">{file.lines}L</span>
                        </div>
                      ))}
                    </div>

                    {/* Code Pane */}
                    <div className="hud-code-pane">
                      <div className="hud-code-header">
                        <span className="hud-code-path">{selectedFile?.path}</span>
                        <button
                          className="hud-copy-btn"
                          onClick={() => copyToClipboard(selectedFile?.content || '')}
                        >
                          {copied ? 'COPIED' : 'COPY CODE'}
                        </button>
                      </div>
                      <pre className="hud-code-content">
                        {selectedFile?.content}
                      </pre>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* ─── TAB 4: QA Verification Lab ─── */}
            {activeTab === 'qa' && (
              <div className="hud-qa-container">
                {/* Mode & Target Route Status Banner */}
                <div className="hud-qa-mode-banner">
                  <div className="hud-qa-mode-left">
                    <span className="hud-meta-label">Verification Mode</span>
                    <span className={`hud-qa-mode-badge ${qaResult?.qaType || 'frontend'}`}>
                      {(qaResult?.qaType || 'FRONTEND').toUpperCase()} VERIFICATION
                    </span>
                  </div>
                  <div className="hud-qa-mode-right">
                    <span className="hud-meta-label">Target Scope</span>
                    <span className="hud-tag-pill">
                      {qaResult?.qaType === 'backend'
                        ? `${qaResult?.apiTestResults?.length || 0} API ENDPOINTS`
                        : `ROUTE: http://localhost:3000${qaResult?.targetRoute || '/'}`}
                    </span>
                  </div>
                </div>

                {/* 4 Multi-Gate Cards */}
                <div className="hud-qa-gates-grid">
                  <div className={`hud-qa-gate-card ${qaResult?.buildPassed ? 'passed' : qaResult ? 'failed' : ''}`}>
                    <span className="hud-gate-num">GATE 01</span>
                    <span className="hud-gate-name">TypeScript Static Validation</span>
                    <span className={`hud-gate-status ${qaResult?.buildPassed ? 'pass' : qaResult ? 'fail' : 'wait'}`}>
                      {qaResult?.buildPassed ? 'PASSED' : qaResult ? 'FAILED' : 'STANDBY'}
                    </span>
                  </div>

                  <div className={`hud-qa-gate-card ${qaResult?.buildPassed ? 'passed' : qaResult ? 'failed' : ''}`}>
                    <span className="hud-gate-num">GATE 02</span>
                    <span className="hud-gate-name">Next.js Production Build</span>
                    <span className={`hud-gate-status ${qaResult?.buildPassed ? 'pass' : qaResult ? 'fail' : 'wait'}`}>
                      {qaResult?.buildPassed ? 'PASSED' : qaResult ? 'RETRYING' : 'STANDBY'}
                    </span>
                  </div>

                  <div className={`hud-qa-gate-card ${qaResult?.browserPassed ? 'passed' : qaResult?.buildPassed ? 'testing' : ''}`}>
                    <span className="hud-gate-num">GATE 03</span>
                    <span className="hud-gate-name">
                      {qaResult?.qaType === 'backend'
                        ? 'Backend API Probes'
                        : `Playwright Full-Page (${qaResult?.targetRoute || '/'})`}
                    </span>
                    <span className={`hud-gate-status ${qaResult?.browserPassed ? 'pass' : qaResult?.buildPassed ? 'wait' : 'wait'}`}>
                      {qaResult?.browserPassed
                        ? qaResult?.qaType === 'backend'
                          ? `PASSED (${qaResult.apiTestResults?.length || 0} ENDPOINTS)`
                          : `PASSED (HTTP 200)`
                        : qaResult?.buildPassed
                          ? 'PROBING...'
                          : 'STANDBY'}
                    </span>
                  </div>

                  <div className={`hud-qa-gate-card ${qaResult?.visualValidation?.passed || qaResult?.visualPassed || (qaResult?.qaType === 'backend' && qaResult?.browserPassed) ? 'passed' : qaResult?.browserPassed ? 'testing' : ''}`}>
                    <span className="hud-gate-num">GATE 04</span>
                    <span className="hud-gate-name">
                      {qaResult?.qaType === 'backend'
                        ? 'API Payload & Status Contract'
                        : 'Vision AI Full-Page Validation'}
                    </span>
                    <span className={`hud-gate-status ${qaResult?.visualValidation?.passed || qaResult?.visualPassed || (qaResult?.qaType === 'backend' && qaResult?.browserPassed) ? 'pass' : qaResult?.browserPassed ? 'wait' : 'wait'}`}>
                      {qaResult?.qaType === 'backend' && qaResult?.browserPassed
                        ? 'VALIDATED (2xx)'
                        : qaResult?.visualValidation?.passed || qaResult?.visualPassed
                          ? `VERIFIED (${qaResult?.visualValidation?.visual_score ?? 10}/10)`
                          : qaResult?.browserPassed
                            ? 'INSPECTING...'
                            : 'STANDBY'}
                    </span>
                  </div>
                </div>

                {/* ─── Backend / API Functional Execution Results ─── */}
                {qaResult?.apiTestResults && qaResult.apiTestResults.length > 0 && (
                  <div className="hud-api-console">
                    <div className="hud-section-header">// Backend API Endpoint Probes & Response Payloads</div>
                    <div className="hud-api-grid">
                      {qaResult.apiTestResults.map((api, idx) => (
                        <div key={`api-card-${idx}-${api.endpoint}`} className={`hud-api-card ${api.passed ? 'passed' : 'failed'}`}>
                          <div className="hud-api-card-header">
                            <span className={`hud-api-method ${api.method.toLowerCase()}`}>{api.method}</span>
                            <span className="hud-api-endpoint">http://localhost:3000{api.endpoint}</span>
                            <span className={`hud-api-status ${api.passed ? 'pass' : 'fail'}`}>
                              HTTP {api.status || 'ERR'} {api.passed ? 'OK' : 'FAILED'}
                            </span>
                            <span className="hud-api-latency">{api.latencyMs}ms</span>
                          </div>
                          <div className="hud-api-body">
                            <div className="hud-api-body-label">Response Payload:</div>
                            <pre className="hud-api-json">
                              {typeof api.responseBody === 'object'
                                ? JSON.stringify(api.responseBody, null, 2)
                                : String(api.responseBody || api.error || 'Empty body')}
                            </pre>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* ─── Frontend UI: Live Headless Browser Full-Page Preview ─── */}
                {qaResult?.qaType !== 'backend' && (
                  <div className="hud-browser-mockup">
                    <div className="hud-browser-toolbar">
                      <div className="hud-browser-dots">
                        <span className="hud-browser-dot red" />
                        <span className="hud-browser-dot yellow" />
                        <span className="hud-browser-dot green" />
                      </div>
                      <div className="hud-browser-address">
                        <span className="hud-browser-lock">🔒</span>
                        <span>http://localhost:3000{qaResult?.targetRoute || ''}</span>
                        {qaResult?.browserMetrics?.status && (
                          <span className="hud-browser-status-badge">
                            HTTP {qaResult.browserMetrics.status} OK
                          </span>
                        )}
                      </div>
                      <div className="hud-browser-actions">
                        <span className="hud-browser-mode-tag">FULL-PAGE SCROLL</span>
                        {qaResult?.screenshot && (
                          <button
                            type="button"
                            className="hud-copy-btn"
                            onClick={() => setIsScreenshotExpanded(true)}
                          >
                            EXPAND FULL RES
                          </button>
                        )}
                      </div>
                    </div>

                    <div className="hud-browser-viewport scrollable">
                      {qaResult?.screenshot ? (
                        <div
                          className="hud-browser-img-wrapper"
                          onClick={() => setIsScreenshotExpanded(true)}
                          title="Click to view full resolution screenshot"
                        >
                          <img
                            src={`data:image/png;base64,${qaResult.screenshot}`}
                            alt="Playwright Headless Browser Full-Page Screenshot"
                            className="hud-browser-img fullpage"
                          />
                          <div className="hud-browser-img-overlay">
                            <span>FULL-PAGE CAPTURE — CLICK TO EXPAND FULL RESOLUTION</span>
                          </div>
                        </div>
                      ) : (
                        <div className="hud-browser-empty">
                          <div className="hud-empty-icon">// PLAYWRIGHT_BROWSER</div>
                          <div className="hud-empty-text">
                            {isRunning
                              ? `Headless Chromium will launch upon production build to capture full-page screenshot of '${qaResult?.targetRoute || '/'}'...`
                              : 'Browser full-page screenshot validation preview will appear here during QA testing.'}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* ─── Visual Validation Intelligence Panel ─── */}
                {qaResult?.visualValidation && qaResult?.qaType !== 'backend' && (
                  <div className="hud-qa-visual-card">
                    <div className="hud-qa-visual-header">
                      <div className="hud-qa-visual-title">
                        <span>// Multimodal Vision Quality Assessment</span>
                        <span className="hud-tag-pill">
                          FIDELITY SCORE: {qaResult.visualValidation.visual_score ?? 10}/10
                        </span>
                      </div>
                      <span className="hud-gate-status pass">VALIDATED</span>
                    </div>

                    {qaResult.visualValidation.observations && qaResult.visualValidation.observations.length > 0 && (
                      <div className="hud-qa-observations">
                        <div className="hud-meta-label">Visual Elements Verified on Route {qaResult.targetRoute || '/'}</div>
                        <ul>
                          {qaResult.visualValidation.observations.map((obs, i) => (
                            <li key={`obs-${i}`}>{obs}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {qaResult.visualValidation.issues_detected && qaResult.visualValidation.issues_detected.length > 0 ? (
                      <div className="hud-qa-issues">
                        <div className="hud-meta-label">Visual Warnings</div>
                        <ul>
                          {qaResult.visualValidation.issues_detected.map((iss, i) => (
                            <li key={`iss-${i}`}>{iss}</li>
                          ))}
                        </ul>
                      </div>
                    ) : (
                      <div className="hud-qa-clean-badge">
                        ✅ Zero layout defects or broken styles detected. Full-page content verified against user prompt.
                      </div>
                    )}
                  </div>
                )}

                {/* Console Logs */}
                <div className="hud-section-header">Sandbox Build, API & Playwright Runner Logs</div>
                <pre className="hud-qa-log-box">
                  {qaResult?.logs || (isRunning ? 'Waiting for QA Tester agent to trigger build, API, and browser verification in Docker...' : 'No build logs recorded.')}
                </pre>
              </div>
            )}

            {/* ─── TAB 5: GitOps & Release ─── */}
            {activeTab === 'gitops' && (
              <div className="hud-gitops-container">
                <div className="hud-gitops-card">
                  <div className="hud-gitops-row">
                    <span className="hud-gitops-label">Target Repository</span>
                    <span className="hud-gitops-val">{repoName || 'Not configured'}</span>
                  </div>
                  <div className="hud-gitops-row">
                    <span className="hud-gitops-label">Feature Branch</span>
                    <span className="hud-gitops-val">{releaseData?.branchName || 'Auto-generated on launch'}</span>
                  </div>
                  <div className="hud-gitops-row">
                    <span className="hud-gitops-label">GitHub Pull Request</span>
                    <span className="hud-gitops-val">
                      {prUrl ? (
                        <a href={prUrl} target="_blank" rel="noopener noreferrer" style={{ color: '#5a9ab5' }}>
                          {prUrl} -&gt;
                        </a>
                      ) : (
                        'Pending build verification'
                      )}
                    </span>
                  </div>
                  <div className="hud-gitops-row">
                    <span className="hud-gitops-label">Ephemeral Preview URL</span>
                    <span className="hud-gitops-val">
                      {previewUrl || releaseData?.previewUrl ? (
                        <a href={previewUrl || releaseData?.previewUrl} target="_blank" rel="noopener noreferrer" style={{ color: '#b89a4a', fontWeight: 'bold' }}>
                          {previewUrl || releaseData?.previewUrl} -&gt;
                        </a>
                      ) : (
                        'Generated upon QA validation'
                      )}
                    </span>
                  </div>
                  <div className="hud-gitops-row">
                    <span className="hud-gitops-label">Human Approval Gate</span>
                    <span
                      className="hud-gitops-val"
                      style={{
                        color:
                          releaseData?.productionMerged
                            ? '#5cb85c'
                            : isAwaitingApproval
                              ? '#b89a4a'
                              : '#777',
                      }}
                    >
                      {releaseData?.productionMerged
                        ? 'APPROVED & RELEASED'
                        : isAwaitingApproval
                          ? 'HOLD // AWAITING HUMAN DECISION'
                          : qaResult?.passed
                            ? 'Pending review gate'
                            : 'Awaiting QA pass'}
                    </span>
                  </div>
                  <div className="hud-gitops-row">
                    <span className="hud-gitops-label">Main Branch Merge</span>
                    <span
                      className="hud-gitops-val"
                      style={{
                        color:
                          releaseData?.productionMerged || (isDone && liveUrl)
                            ? '#5cb85c'
                            : qaResult?.passed
                              ? '#5a9ab5'
                              : '#777',
                      }}
                    >
                      {releaseData?.productionMerged || (isDone && liveUrl)
                        ? 'SQUASH MERGED'
                        : qaResult?.passed
                          ? 'Awaiting GitOps release merge...'
                          : 'Awaiting QA pass'}
                    </span>
                  </div>
                  <div className="hud-gitops-row">
                    <span className="hud-gitops-label">Live Production URL</span>
                    <span className="hud-gitops-val">
                      {liveUrl ? (
                        <a href={liveUrl} target="_blank" rel="noopener noreferrer" style={{ color: '#5cb85c', fontWeight: 'bold' }}>
                          {liveUrl} -&gt;
                        </a>
                      ) : (
                        'Pending release deployment'
                      )}
                    </span>
                  </div>
                </div>

                {/* ─── In-App Visual Preview & GitOps Decision Station ─── */}
                {(approvalData?.screenshot || qaResult?.screenshot) && (
                  <div className="hud-gitops-card" style={{ marginTop: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ color: '#5a9ab5', fontFamily: 'var(--font-display)', fontSize: '11px', letterSpacing: '1px', fontWeight: 700 }}>
                          // IN-APP PREVIEW // DOCKER SANDBOX RENDER
                        </span>
                        <span className="hud-step-badge" style={{ color: '#5cb85c', borderColor: '#5cb85c' }}>
                          HTTP 200 OK
                        </span>
                      </div>
                      <button
                        type="button"
                        className="hud-copy-btn"
                        onClick={() => setIsScreenshotExpanded(true)}
                      >
                        EXPAND FULL RESOLUTION [🔍]
                      </button>
                    </div>

                    <div
                      style={{
                        position: 'relative',
                        maxHeight: '340px',
                        overflowY: 'auto',
                        border: '1px solid #333',
                        background: '#0d0d0d',
                        borderRadius: '2px',
                        cursor: 'pointer'
                      }}
                      onClick={() => setIsScreenshotExpanded(true)}
                      title="Click to expand fullscreen"
                    >
                      <img
                        src={`data:image/png;base64,${approvalData?.screenshot || qaResult?.screenshot}`}
                        alt="Sandbox Render Preview"
                        style={{ width: '100%', display: 'block' }}
                      />
                    </div>

                    {isAwaitingApproval && (
                      <div style={{ marginTop: '14px', paddingTop: '14px', borderTop: '1px solid #222' }}>
                        <div style={{ color: '#b89a4a', fontFamily: 'var(--font-display)', fontSize: '11px', fontWeight: 700, letterSpacing: '1px', marginBottom: '8px' }}>
                          HUMAN SIGN-OFF // APPROVE DEPLOYMENT OR REQUEST FIXATION
                        </div>
                        <div className="hud-approval-input-row">
                          <input
                            type="text"
                            className="hud-approval-input"
                            placeholder="Need changes? Enter feedback for Coder agent (e.g. 'Add backspace button', 'Make it responsive')..."
                            value={reviewerFeedback}
                            onChange={(e) => setReviewerFeedback(e.target.value)}
                            disabled={isSubmittingApproval}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' && reviewerFeedback.trim()) {
                                handleApprovalDecision(false);
                              }
                            }}
                          />
                          <div className="hud-approval-actions">
                            <button
                              type="button"
                              className="hud-approval-btn-approve"
                              disabled={isSubmittingApproval}
                              onClick={() => handleApprovalDecision(true)}
                            >
                              {isSubmittingApproval ? 'Processing Merge...' : '✓ Approve & Merge'}
                            </button>
                            <button
                              type="button"
                              className="hud-approval-btn-reject"
                              disabled={isSubmittingApproval}
                              onClick={() => handleApprovalDecision(false)}
                            >
                              ✕ Request Fixation
                            </button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* 4. Bottom Results Bar */}
          {isDone && (prUrl || liveUrl) && (
            <div className="hud-results">
              {prUrl && (
                <a
                  id="link-pr"
                  href={prUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hud-result-link"
                >
                  <span className="hud-result-label hud-result-label-pr">GitHub PR →</span>
                  <span className="hud-result-url">{prUrl}</span>
                </a>
              )}
              {liveUrl && (
                <a
                  id="link-live"
                  href={liveUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hud-result-link"
                >
                  <span className="hud-result-label hud-result-label-live">Live Deployment →</span>
                  <span className="hud-result-url">{liveUrl}</span>
                </a>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ─── Bottom Bar ─── */}
      <div className="hud-bottombar">
        <div className="hud-bottombar-left">
          <span>JADEN v1.0</span>
          <span style={{ color: '#333' }}>|</span>
          <span>AUTONOMOUS SDLC PLATFORM</span>
        </div>
        <span suppressHydrationWarning>SYS {clockTime}</span>
      </div>

      {/* ─── Playwright Screenshot Full-Resolution Modal ─── */}
      {isScreenshotExpanded && qaResult?.screenshot && (
        <div className="hud-modal-backdrop" onClick={() => setIsScreenshotExpanded(false)}>
          <div className="hud-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="hud-modal-header">
              <div className="hud-modal-title">
                <span className="hud-browser-lock">🔒</span>
                <span>// PLAYWRIGHT HEADLESS BROWSER FULLSCREEN INSPECTOR (1280x800)</span>
              </div>
              <button
                type="button"
                className="hud-copy-btn"
                onClick={() => setIsScreenshotExpanded(false)}
              >
                CLOSE [ESC]
              </button>
            </div>
            <div className="hud-modal-body">
              <img
                src={`data:image/png;base64,${qaResult.screenshot}`}
                alt="Enlarged Playwright Browser Screenshot"
                className="hud-modal-img"
              />
            </div>
          </div>
        </div>
      )}

      {/* ─── Run History / Mission Archive Modal (MongoDB Persistence) ─── */}
      {isHistoryOpen && (
        <div className="hud-modal-backdrop" onClick={() => setIsHistoryOpen(false)}>
          <div className="hud-modal-card" style={{ maxWidth: '780px', width: '92%' }} onClick={(e) => e.stopPropagation()}>
            <div className="hud-modal-header">
              <div className="hud-modal-title">
                <span className="hud-browser-lock">🗄️</span>
                <span>// MISSION HISTORY & CHECKPOINT ARCHIVE (MONGODB)</span>
              </div>
              <button
                type="button"
                className="hud-copy-btn"
                onClick={() => setIsHistoryOpen(false)}
              >
                CLOSE [ESC]
              </button>
            </div>
            <div className="hud-modal-body" style={{ maxHeight: '480px', overflowY: 'auto', padding: '16px' }}>
              {runsList.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '30px', color: '#666', fontFamily: 'var(--font-mono)' }}>
                  No mission records discovered in MongoDB archive.
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {runsList.map((r, i) => {
                    const isSelected = r.run_id === runId;
                    const rStatus = (r.status as string) || 'completed';
                    const statusColor = rStatus === 'completed' ? '#5cb85c' : rStatus === 'awaiting_approval' ? '#b89a4a' : rStatus === 'running' ? '#5a9ab5' : '#d9534f';
                    const timeStr = r.updated_at ? new Date((r.updated_at as number) * 1000).toLocaleString() : 'Unknown';

                    return (
                      <div
                        key={r.run_id ? `run-item-${String(r.run_id)}` : `run-idx-${i}`}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          padding: '12px 14px',
                          background: isSelected ? 'rgba(90, 154, 181, 0.12)' : 'rgba(255, 255, 255, 0.02)',
                          border: `1px solid ${isSelected ? '#5a9ab5' : '#222'}`,
                          borderRadius: '2px',
                          gap: '12px'
                        }}
                      >
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', flex: 1, minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', fontWeight: 700, color: '#fff' }}>
                              {String(r.run_id)}
                            </span>
                            <span style={{
                              fontFamily: 'var(--font-mono)',
                              fontSize: '9px',
                              padding: '2px 6px',
                              borderRadius: '2px',
                              background: 'rgba(0,0,0,0.5)',
                              border: `1px solid ${statusColor}`,
                              color: statusColor,
                              textTransform: 'uppercase',
                              letterSpacing: '1px'
                            }}>
                              {rStatus}
                            </span>
                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: '#666' }}>
                              {timeStr}
                            </span>
                          </div>
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: '#aaa', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            Target: <strong style={{ color: '#ccc' }}>{String(r.repo_name || 'project')}</strong> // {String(r.prompt || '')}
                          </span>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                          {isSelected ? (
                            <span style={{ color: '#5a9ab5', fontSize: '10px', fontWeight: 700, letterSpacing: '1px' }}>
                              ACTIVE
                            </span>
                          ) : (
                            <button
                              type="button"
                              className="hud-copy-btn"
                              onClick={() => {
                                loadRunById(String(r.run_id));
                                setIsHistoryOpen(false);
                              }}
                              style={{ borderColor: '#5a9ab5', color: '#5a9ab5' }}
                            >
                              LOAD MISSION →
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
