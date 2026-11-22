/** 边覆盖对账接口封装：读取轮次、分区装载、证据读取、对账与报告签收。 */
import { request } from './client'
import type {
  EdgeEvidence,
  ReconcileReport,
  ReconcileResult,
  ReconcileSession,
} from '@/data/edge-reconcile'

const BASE = '/api/graph-compare/reconcile'

async function send<T>(path: string, init?: RequestInit, body?: unknown): Promise<T> {
  const response = await request(path, {
    method: init?.method ?? 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = (await response.json().catch(() => null))?.detail ?? response.statusText
    throw new Error(typeof detail === 'string' ? detail : '对账接口调用失败')
  }
  return (await response.json()) as T
}

export function createReconcileSession(taskNo = 'GRAP-EDGE-DEMO'): Promise<ReconcileSession> {
  return send<ReconcileSession>(`${BASE}/sessions`, { method: 'POST' }, { task_no: taskNo })
}

export function fetchReconcileSession(sessionId: string): Promise<ReconcileSession> {
  return send<ReconcileSession>(`${BASE}/sessions/${sessionId}`, { method: 'GET' })
}

export function startReadRound(sessionId: string): Promise<{ round_no: number; stale_evidence_discarded: boolean }> {
  return send(`${BASE}/sessions/${sessionId}/rounds`, { method: 'POST' }, {})
}

export function interruptReadRound(sessionId: string): Promise<{ round_no: number; interrupted: boolean }> {
  return send(`${BASE}/sessions/${sessionId}/interrupt`, { method: 'POST' }, {})
}

export function loadPartition(
  sessionId: string,
  graphSide: 'baseline' | 'compare',
  partition: string,
): Promise<{ loaded_partitions: string[] }> {
  return send(`${BASE}/sessions/${sessionId}/partitions/load`, { method: 'POST' }, {
    graph_side: graphSide,
    partition,
  })
}

export function readAllEdges(sessionId: string): Promise<{ round_no: number; edges: EdgeEvidence[] }> {
  return send(`${BASE}/sessions/${sessionId}/edges/read-all`, { method: 'POST' }, {})
}

export function redirectCompareEdge(
  sessionId: string,
  edgeId: string,
  endpoints: { source: string; target: string; revision: string },
): Promise<unknown> {
  return send(`${BASE}/sessions/${sessionId}/compare-edges/${edgeId}`, { method: 'PATCH' }, endpoints)
}

export function reconcileSession(
  sessionId: string,
  interrupted: boolean | null = null,
): Promise<ReconcileResult> {
  return send(`${BASE}/sessions/${sessionId}/reconcile`, { method: 'POST' }, { interrupted })
}

export function signOffReport(sessionId: string): Promise<{ session_id: string; report: ReconcileReport }> {
  return send(`${BASE}/sessions/${sessionId}/sign-off`, { method: 'POST' }, {})
}
