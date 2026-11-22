/** 边覆盖对账类型：与后端 app/services/edge_reconcile.py 的返回结构对齐。 */

export type EdgeVerdict = '相同' | '差异' | '未知'

export type EdgeEvidence = {
  edge_id: string
  verdict: EdgeVerdict
  partition: string
  baseline_revision: string | null
  compare_revision: string | null
  baseline_endpoints: [string, string] | null
  compare_endpoints: [string, string] | null
  read_round: number | null
  unknown_reason: string | null
  detail: string
  signable: boolean
}

export type GraphSnapshot = {
  name: string
  loaded_partitions: string[]
  edges: {
    edge_id: string
    source: string
    target: string
    revision: string
    partition: string
  }[]
}

export type ReconcileSession = {
  session_id: string
  task_no: string
  round_no: number
  baseline: GraphSnapshot
  compare: GraphSnapshot
  last_result: ReconcileResult | null
  report_id: string | null
}

export type SignedEvidence = {
  edge_id: string
  baseline_revision: string | null
  compare_revision: string | null
  baseline_endpoints: [string, string] | null
  compare_endpoints: [string, string] | null
  read_round: number | null
  detail: string
}

export type PendingGraphRef = {
  edge_id: string
  partition: string
  baseline_revision: string | null
  compare_revision: string | null
  unknown_reason: string | null
  read_round: number | null
}

export type ReconcileResult = {
  round_no: number
  interrupted: boolean
  zero_difference: boolean
  signable_edge_ids: string[]
  unknown_edge_ids: string[]
  different_edge_ids: string[]
  coverage: {
    baseline_registered: number
    read_in_round: number
    same: number
    unknown: number
    different: number
    coverage_ratio: number
    fully_covered: boolean
  }
  sign_off: {
    conclusion: string
    zero_difference: boolean
    signed_edges: SignedEvidence[]
    pending_graph_refs: PendingGraphRef[]
    blocked_differences: string[]
    evidence_round: number
    stale_rounds_rejected: number[]
  }
  edges: EdgeEvidence[]
}

export type ReconcileReport = {
  报告编号: string
  status: string
  pending: boolean
  abnormal: boolean
  对账轮次: number
  零差异结论: boolean
  签收结论: string
  已签收边: string
  待补齐边: string
  差异边: string
  签收证据: SignedEvidence[]
  待补齐图代引用: PendingGraphRef[]
}
