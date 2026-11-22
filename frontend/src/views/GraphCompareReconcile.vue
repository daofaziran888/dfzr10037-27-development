<template>
  <section class="page" data-module="graph_compare">
    <header class="page-head">
      <div>
        <h2>图谱比对 · 边覆盖对账</h2>
        <p class="page-desc">
          基准图边全集 × 对比图分区真实读取：未装载分区只判未知，零差异结论只接受同一轮真实读取的证据。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="new-session" type="button" @click="openSession">
          新建对账会话
        </button>
      </div>
    </header>

    <template v-if="session">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">当前读取轮次</span>
          <strong class="stat-value">第 {{ session.round_no }} 轮</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">对比图已装载分区</span>
          <strong class="stat-value">{{ session.compare.loaded_partitions.join('、') || '无' }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">零差异结论</span>
          <strong class="stat-value" :data-testid="'zero-diff'">
            {{ result ? (result.zero_difference ? '可签收' : '不成立') : '未对账' }}
          </strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">可签收 / 未知 / 差异</span>
          <strong class="stat-value">
            {{ result ? result.signable_edge_ids.length : 0 }} /
            {{ result ? result.unknown_edge_ids.length : 0 }} /
            {{ result ? result.different_edge_ids.length : 0 }}
          </strong>
        </article>
      </div>

      <div class="filter-bar">
        <button class="btn primary" data-testid="start-round" type="button" @click="onStartRound">
          开启新一轮真实读取
        </button>
        <button class="btn" type="button" @click="onReadAll">读取基准登记全集</button>
        <button class="btn" type="button" @click="onInterrupt">标记批次中断</button>
        <button class="btn" type="button" @click="onLoadP3">补齐装载对比图 P3</button>
        <button class="btn" type="button" @click="onRedirectE3">把 E3 改向（G-CMP-8）</button>
        <button class="btn" type="button" @click="onReconcile">执行覆盖对账</button>
        <button class="btn primary" type="button" @click="onSignOff">驱动报告签收</button>
      </div>

      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

      <h3>边证据（关联双方图代与端点依据）</h3>
      <table class="data-table" data-testid="evidence-table">
        <thead>
          <tr>
            <th>边编号</th>
            <th>分区</th>
            <th>基准图代 / 端点</th>
            <th>对比图代 / 端点</th>
            <th>轮次</th>
            <th>结论</th>
            <th>未知原因 / 依据说明</th>
            <th>可签收</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="edge in evidenceRows" :key="edge.edge_id" :data-testid="`edge-${edge.edge_id}`">
            <td>{{ edge.edge_id }}</td>
            <td>{{ edge.partition }}</td>
            <td>
              {{ edge.baseline_revision ?? '—' }}<br />
              {{ formatEndpoints(edge.baseline_endpoints) }}
            </td>
            <td>
              {{ edge.compare_revision ?? '未读到' }}<br />
              {{ formatEndpoints(edge.compare_endpoints) }}
            </td>
            <td>{{ edge.read_round ?? '—' }}</td>
            <td :data-verdict="edge.verdict">{{ edge.verdict }}</td>
            <td>{{ edge.unknown_reason ?? edge.detail }}</td>
            <td>{{ edge.signable ? '是' : '否' }}</td>
          </tr>
        </tbody>
      </table>

      <template v-if="result">
        <h3>覆盖校验结论</h3>
        <div class="stat-card" data-testid="sign-off-panel">
          <p><strong>签收口径：</strong>{{ result.sign_off.conclusion }}</p>
          <p>
            证据轮次：第 {{ result.sign_off.evidence_round }} 轮；
            拒绝复用的过期轮次：{{ result.sign_off.stale_rounds_rejected.join('、') || '无' }}
          </p>
          <p>覆盖率：{{ (result.coverage.coverage_ratio * 100).toFixed(1) }}%（{{ result.coverage.read_in_round }}/{{ result.coverage.baseline_registered }}）</p>
          <p>可签收边：{{ result.signable_edge_ids.join('、') || '无' }}</p>
          <p>未知挂账边：{{ result.unknown_edge_ids.join('、') || '无' }}</p>
          <p>差异阻断边：{{ result.different_edge_ids.join('、') || '无' }}</p>
        </div>
      </template>

      <template v-if="report">
        <h3>图谱报告待办（report_gen）</h3>
        <table class="data-table" data-testid="report-table">
          <thead>
            <tr><th>报告编号</th><th>状态</th><th>已签收边</th><th>待补齐边（图代引用）</th><th>差异边</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>{{ report.报告编号 }}</td>
              <td>{{ report.status }}{{ report.abnormal ? '（异常）' : '' }}</td>
              <td>{{ report.已签收边 || '无' }}</td>
              <td>
                <span v-for="ref in report.待补齐图代引用" :key="ref.edge_id" class="legend-item">
                  {{ ref.edge_id }}[{{ ref.partition }}｜基准 {{ ref.baseline_revision }} / 对比 {{ ref.compare_revision }}]
                </span>
                <span v-if="!report.待补齐图代引用.length">无</span>
              </td>
              <td>{{ report.差异边 || '无' }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </template>

    <p v-else class="page-desc">点击「新建对账会话」开始：初始场景对比图 P3（含 E5、E6）尚未装载。</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import {
  createReconcileSession,
  interruptReadRound,
  loadPartition,
  readAllEdges,
  reconcileSession,
  redirectCompareEdge,
  signOffReport,
  startReadRound,
} from '@/api/edge-reconcile'
import type { EdgeEvidence, ReconcileReport, ReconcileResult, ReconcileSession } from '@/data/edge-reconcile'

const session = ref<ReconcileSession | null>(null)
const result = ref<ReconcileResult | null>(null)
const report = ref<ReconcileReport | null>(null)
const evidence = ref<EdgeEvidence[]>([])
const errorMessage = ref('')

const evidenceRows = computed(() => evidence.value)

function formatEndpoints(endpoints: [string, string] | null): string {
  return endpoints ? `${endpoints[0]} → ${endpoints[1]}` : '—'
}

async function guard(label: string, task: () => Promise<void>) {
  errorMessage.value = ''
  try {
    await task()
  } catch (error) {
    errorMessage.value = `${label}失败：${error instanceof Error ? error.message : '未知错误'}`
  }
}

async function openSession() {
  await guard('新建会话', async () => {
    session.value = await createReconcileSession('GRAP-EDGE-DEMO')
    result.value = null
    report.value = null
    evidence.value = []
  })
}

async function onStartRound() {
  if (!session.value) return
  await guard('开启轮次', async () => {
    const round = await startReadRound(session.value!.session_id)
    session.value!.round_no = round.round_no
  })
}

async function onReadAll() {
  if (!session.value) return
  await guard('读取边全集', async () => {
    const payload = await readAllEdges(session.value!.session_id)
    evidence.value = payload.edges
  })
}

async function onInterrupt() {
  if (!session.value) return
  await guard('中断', async () => {
    await interruptReadRound(session.value!.session_id)
  })
}

async function onLoadP3() {
  if (!session.value) return
  await guard('装载 P3', async () => {
    const loaded = await loadPartition(session.value!.session_id, 'compare', 'P3')
    session.value!.compare.loaded_partitions = loaded.loaded_partitions
  })
}

async function onRedirectE3() {
  if (!session.value) return
  await guard('E3 改向', async () => {
    await redirectCompareEdge(session.value!.session_id, 'E3', {
      source: 'T-D',
      target: 'T-C',
      revision: 'G-CMP-8',
    })
  })
}

async function onReconcile() {
  if (!session.value) return
  await guard('对账', async () => {
    result.value = await reconcileSession(session.value!.session_id)
    evidence.value = result.value.edges
    session.value!.last_result = result.value
  })
}

async function onSignOff() {
  if (!session.value) return
  await guard('报告签收', async () => {
    const outcome = await signOffReport(session.value!.session_id)
    report.value = outcome.report
    session.value!.report_id = outcome.report.报告编号
  })
}
</script>

<style scoped>
h3 {
  font-size: 15px;
  margin: 18px 0 8px;
}
.page-actions {
  display: flex;
  gap: 8px;
}
.data-table td[data-verdict='相同'] {
  color: #15803d;
  font-weight: 600;
}
.data-table td[data-verdict='差异'] {
  color: #b42318;
  font-weight: 600;
}
.data-table td[data-verdict='未知'] {
  color: #b45309;
  font-weight: 600;
}
.legend-item {
  display: inline-block;
  margin-right: 10px;
  font-size: 12px;
  color: #b45309;
}
</style>
