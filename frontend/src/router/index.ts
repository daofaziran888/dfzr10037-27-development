import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'
import GraphCompareReconcile from '@/views/GraphCompareReconcile.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 图谱比对承载边覆盖对账领域逻辑，使用专用工作台；其余模块共用通用视图。
    {
      path: '/graph_compare',
      name: 'graph-compare-reconcile',
      component: GraphCompareReconcile,
    },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
