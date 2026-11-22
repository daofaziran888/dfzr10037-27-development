"""边覆盖对账端到端验证（标准库 unittest，不引入新依赖）。

场景严格对应需求：
1. 基准图登记 E1..E6，对比图 P3 分区未装载 → E5、E6 两条边未知（E6 即题面边）；
2. 第一轮真实读取后中断：只有 P1/P2 的 4 条相同边可签，整体禁止零差异；
3. 重试开新一轮，拒绝上一轮证据，重新真实读取；
4. 补齐 P3：E6 读回为相同，E3 在对比图被改向 → 新出现一条差异；
5. 报告按无差异对账数据签收相同边，未知边保留图代引用挂账。

HTTP 层的真实请求冒烟由 tests/http_smoke_reconcile.py 对运行中的服务验证。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.services import SERVICES  # noqa: E402
from app.services.edge_reconcile import (  # noqa: E402
    EdgeCoverageReconciler,
    PartitionNotLoaded,
)
from app.services.graph_compare import build_demo_graphs  # noqa: E402

service = SERVICES["graph_compare"]


class EdgeReconcileScenarioTest(unittest.TestCase):
    """服务层全链路：未装载、中断、重试、分区补齐、报告签收。"""

    def setUp(self) -> None:
        snapshot = service.create_reconciliation("GRAP-EDGE-E2E")
        self.session_id = snapshot["session_id"]
        self.assertEqual(snapshot["compare"]["loaded_partitions"], ["P1", "P2"])
        # 基准图 6 条边全部登记，P3 上是 E5/E6。
        self.assertEqual(
            sorted(edge["edge_id"] for edge in snapshot["baseline"]["edges"]),
            ["E1", "E2", "E3", "E4", "E5", "E6"],
        )

    def _start_round_and_read(self) -> dict:
        service.start_round(self.session_id)
        payload = service.read_registered_edges(self.session_id)
        self.assertEqual(payload["round_no"], 1)
        return {edge["edge_id"]: edge for edge in payload["edges"]}

    def test_e6_unknown_when_partition_not_loaded(self) -> None:
        """E6 在基准图已登记、对比图查询响应属于未装载部分 → 未知而非相同。"""
        edges = self._start_round_and_read()
        e6 = edges["E6"]
        self.assertEqual(e6["verdict"], "未知")
        self.assertFalse(e6["signable"])
        self.assertEqual(e6["unknown_reason"], "对比图分区未装载")
        # 未知边仍保留双方图代引用，供后续补齐重核。
        self.assertEqual(e6["baseline_revision"], "G-BASE-7")
        self.assertEqual(e6["compare_revision"], "G-CMP-7")
        self.assertEqual(e6["read_round"], 1)
        # 未装载分区的直接查询必须抛错，不能默默返回空冒充无此边。
        _, compare = build_demo_graphs()
        with self.assertRaises(PartitionNotLoaded):
            compare.query_edge("E6")

    def test_interrupted_round_only_partial_signoff_no_zero_difference(self) -> None:
        """最初可签收证据：仅 4 条相同边；E5/E6 未知，整体不得零差异。"""
        edges = self._start_round_and_read()
        same = [eid for eid, edge in edges.items() if edge["verdict"] == "相同"]
        self.assertEqual(sorted(same), ["E1", "E2", "E3", "E4"])
        unknown = [eid for eid, edge in edges.items() if edge["verdict"] == "未知"]
        self.assertEqual(sorted(unknown), ["E5", "E6"])

        # 批次比对中断。
        service.interrupt_round(self.session_id)
        result = service.reconcile(self.session_id)
        self.assertFalse(result["zero_difference"])
        self.assertTrue(result["interrupted"])
        # 已读的 4 条相同边证据保留原结论，但中断轮签收窗口关闭：一条都不能签。
        retained = {edge["edge_id"]: edge["verdict"] for edge in result["edges"]}
        for edge_id in ("E1", "E2", "E3", "E4"):
            self.assertEqual(retained[edge_id], "相同")
        self.assertEqual(result["signable_edge_ids"], [])
        self.assertEqual(sorted(result["unknown_edge_ids"]), ["E5", "E6"])
        self.assertFalse(result["coverage"]["fully_covered"])
        self.assertIn("中断", result["sign_off"]["conclusion"])
        self.assertEqual(result["sign_off"]["signed_edges"], [])

        # 报告待办：没有可签收边，E5/E6 以图代引用挂账。
        outcome = service.sign_off_report(self.session_id)
        report = outcome["report"]
        self.assertEqual(report["status"], "草稿")
        self.assertTrue(report["pending"])
        self.assertEqual(report["待补齐边"], "E5、E6")
        self.assertEqual(report["已签收边"], "")
        pending_refs = {item["edge_id"] for item in report["待补齐图代引用"]}
        self.assertEqual(pending_refs, {"E5", "E6"})
        self.assertTrue(
            all(item["baseline_revision"] == "G-BASE-7"
                for item in report["待补齐图代引用"])
        )

    def test_retry_round_rejects_stale_evidence_and_backfill_reveals_diff(self) -> None:
        """重试：新一轮真实读取；补齐后 E6 相同可签、E3 改向成为新差异。"""
        # 第一轮读全登记边后中断，制造“上一次的完整图证据”。
        self._start_round_and_read()
        service.interrupt_round(self.session_id)
        first_result = service.reconcile(self.session_id)
        self.assertEqual(first_result["round_no"], 1)

        # 中断轮禁止继续读取，必须重试。
        with self.assertRaises(RuntimeError):
            service.read_edge(self.session_id, "E1")

        # 补齐分区：装载对比图 P3，并把 E3 改向（T-C->T-D 改为 T-D->T-C）。
        loaded = service.load_partition(self.session_id, "compare", "P3")
        self.assertEqual(sorted(loaded["loaded_partitions"]), ["P1", "P2", "P3"])
        patched = service.update_compare_edge(
            self.session_id, "E3",
            source="T-D", target="T-C", revision="G-CMP-8",
        )
        self.assertEqual(patched["source"], "T-D")

        # 重试开新一轮：旧轮证据过期。
        round_resp = service.start_round(self.session_id)
        self.assertEqual(round_resp["round_no"], 2)
        self.assertTrue(round_resp["stale_evidence_discarded"])

        payload = service.read_registered_edges(self.session_id)
        edges = {edge["edge_id"]: edge for edge in payload["edges"]}
        # 所有证据必须来自第 2 轮真实读取，不能引用第 1 轮。
        self.assertTrue(all(edge["read_round"] == 2 for edge in edges.values()))

        # E6 补回为相同并关联双方图代与端点依据。
        self.assertEqual(edges["E6"]["verdict"], "相同")
        self.assertTrue(edges["E6"]["signable"])
        self.assertEqual(edges["E6"]["baseline_revision"], "G-BASE-7")
        self.assertEqual(edges["E6"]["compare_revision"], "G-CMP-7")
        self.assertEqual(edges["E6"]["baseline_endpoints"], ["T-F", "T-G"])
        # E3 改向 → 补齐后新出现的差异，不可签收。
        self.assertEqual(edges["E3"]["verdict"], "差异")
        self.assertFalse(edges["E3"]["signable"])
        self.assertIn("改向", edges["E3"]["detail"])

        result = service.reconcile(self.session_id)
        self.assertFalse(result["zero_difference"])  # 有改向差异，仍非零差异
        self.assertEqual(sorted(result["signable_edge_ids"]),
                         ["E1", "E2", "E4", "E5", "E6"])
        self.assertEqual(result["unknown_edge_ids"], [])
        self.assertEqual(result["different_edge_ids"], ["E3"])
        self.assertTrue(result["coverage"]["fully_covered"])
        # 旧中断轮被记录为拒绝复用的过期轮次。
        self.assertEqual(result["sign_off"]["stale_rounds_rejected"], [1])
        self.assertEqual(result["sign_off"]["evidence_round"], 2)

        report = service.sign_off_report(self.session_id)["report"]
        # 相同边按无差异对账数据签收；差异边阻止报告完成，待办保留。
        self.assertEqual(report["status"], "草稿")
        self.assertTrue(report["abnormal"])
        self.assertEqual(
            sorted(report["已签收边"].split("、")),
            ["E1", "E2", "E4", "E5", "E6"],
        )
        self.assertEqual(report["差异边"], "E3")
        self.assertEqual(report["待补齐边"], "")
        signed = {item["edge_id"]: item for item in report["签收证据"]}
        self.assertEqual(signed["E6"]["read_round"], 2)
        self.assertEqual(signed["E6"]["compare_endpoints"], ["T-F", "T-G"])


class EdgeReconcileDomainTest(unittest.TestCase):
    """纯领域引擎：零差异正例、未装载分区防护、旧轮证据隔离。"""

    def test_full_coverage_all_same_allows_zero_difference(self) -> None:
        baseline, compare = build_demo_graphs()
        compare.load_partition("P3")  # 全部分区装载且数据一致
        reconciler = EdgeCoverageReconciler(baseline, compare)
        reconciler.start_round()
        for edge_id in sorted(baseline.edges):
            evidence = reconciler.read_edge(edge_id)
            self.assertEqual(evidence.verdict, "相同", edge_id)
            self.assertTrue(evidence.signable)
        result = reconciler.reconcile()
        self.assertTrue(result.zero_difference)
        self.assertEqual(len(result.signable_edge_ids), 6)
        self.assertEqual(result.coverage["coverage_ratio"], 1.0)
        self.assertEqual(result.sign_off["conclusion"], "零差异可签收")

    def test_interrupted_round_keeps_read_evidence_but_closes_signoff(self) -> None:
        """中断轮已读的相同边证据保留可追溯，但签收窗口关闭、零差异不成立。"""
        baseline, compare = build_demo_graphs()
        reconciler = EdgeCoverageReconciler(baseline, compare)
        reconciler.start_round()
        for edge_id in ("E1", "E2"):
            reconciler.read_edge(edge_id)
        reconciler.interrupt_round()
        result = reconciler.reconcile()
        self.assertFalse(result.zero_difference)
        self.assertTrue(result.interrupted)
        # 已读边的「相同」结论和证据保留，未读边落未知。
        verdicts = {edge.edge_id: edge.verdict for edge in result.edges}
        self.assertEqual(verdicts["E1"], "相同")
        self.assertEqual(verdicts["E2"], "相同")
        self.assertEqual(verdicts["E6"], "未知")
        # 但一条都不允许签收。
        self.assertEqual(result.signable_edge_ids, [])
        self.assertEqual(sorted(result.unknown_edge_ids),
                         ["E3", "E4", "E5", "E6"])
        self.assertEqual(result.sign_off["signed_edges"], [])

    def test_stale_round_evidence_cannot_be_stitched_into_zero_diff(self) -> None:
        baseline, compare = build_demo_graphs()
        reconciler = EdgeCoverageReconciler(baseline, compare)

        # 第 1 轮：P1/P2 可读，读到 4 条相同后中断。
        reconciler.start_round()
        for edge_id in ("E1", "E2", "E3", "E4"):
            reconciler.read_edge(edge_id)
        reconciler.interrupt_round()
        stale = reconciler.reconcile()
        self.assertFalse(stale.zero_difference)
        self.assertTrue(stale.interrupted)

        # 第 2 轮重试：即使补齐 P3，也必须重新读 E1..E6，旧证据不在新轮里。
        compare.load_partition("P3")
        reconciler.start_round()
        # 故意一条都不读直接对账：全集应落未知，绝不能拿第 1 轮结果凑零差异。
        retry = reconciler.reconcile()
        self.assertFalse(retry.zero_difference)
        self.assertEqual(sorted(retry.unknown_edge_ids),
                         ["E1", "E2", "E3", "E4", "E5", "E6"])
        self.assertEqual(retry.signable_edge_ids, [])
        self.assertTrue(reconciler.is_round_stale(1))
        self.assertFalse(reconciler.is_round_stale(2))

    def test_loaded_partition_missing_edge_is_difference_not_unknown(self) -> None:
        baseline, compare = build_demo_graphs()
        # 对比图 P3 装载但删掉 E6：分区已读，缺失属于删除差异而非未知。
        compare.load_partition("P3")
        del compare.edges["E6"]
        reconciler = EdgeCoverageReconciler(baseline, compare)
        reconciler.start_round()
        evidence = reconciler.read_edge("E6")
        self.assertEqual(evidence.verdict, "差异")
        self.assertFalse(evidence.signable)
        self.assertIsNone(evidence.compare_revision)


if __name__ == "__main__":
    unittest.main(verbosity=2)
