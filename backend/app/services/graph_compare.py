"""图谱比对领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。

新增边覆盖对账（edge coverage reconcile）：
- 基准图登记的边为对账全集，对比图按分区装载，未装载分区只能判「未知」；
- 每条已核对边关联双方实际图代与端点依据，仅「相同」可签收；
- 读取按轮次（round）留痕，中断轮证据过期，重试必须重新真实读取；
- 对账结果驱动图谱报告（report_gen）待办，未知边保留图代引用待补齐。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.edge_reconcile import (
    Edge,
    EdgeCoverageReconciler,
    PartitionedGraph,
)
from app.store import store


def build_demo_graphs() -> tuple[PartitionedGraph, PartitionedGraph]:
    """构造 E6 对账演示场景：6 条基准边分布在 3 个分区。

    - P1/E1、E2，P2/E3、E4，P3/E5、E6；
    - 基准图图代 G-BASE-7，对比图图代 G-CMP-7；
    - 初始对比图只装载 P1、P2，P3（含 E6）尚未装载；
    - 补齐后对比图把 E3 改向，用于暴露“补齐后新出现的差异”。
    """
    baseline_edges = [
        Edge("E1", "T-A", "T-B", "G-BASE-7", "P1"),
        Edge("E2", "T-B", "T-C", "G-BASE-7", "P1"),
        Edge("E3", "T-C", "T-D", "G-BASE-7", "P2"),
        Edge("E4", "T-D", "T-E", "G-BASE-7", "P2"),
        Edge("E5", "T-E", "T-F", "G-BASE-7", "P3"),
        Edge("E6", "T-F", "T-G", "G-BASE-7", "P3"),
    ]
    compare_edges = [
        Edge("E1", "T-A", "T-B", "G-CMP-7", "P1"),
        Edge("E2", "T-B", "T-C", "G-CMP-7", "P1"),
        # 第一轮与基准相同；补齐装载时由测试/接口改写为改向版本。
        Edge("E3", "T-C", "T-D", "G-CMP-7", "P2"),
        Edge("E4", "T-D", "T-E", "G-CMP-7", "P2"),
        Edge("E5", "T-E", "T-F", "G-CMP-7", "P3"),
        # E6 已登记在对比图，但所属 P3 尚未装载 → 查询响应不可读。
        Edge("E6", "T-F", "T-G", "G-CMP-7", "P3"),
    ]
    baseline = PartitionedGraph.from_edges("基准图-G-BASE-7", baseline_edges)
    compare = PartitionedGraph.from_edges("对比图-G-CMP-7", compare_edges)
    # 基准图分区全部可读；对比图仅 P1、P2 装载，P3 未装载。
    for partition in ("P1", "P2", "P3"):
        baseline.load_partition(partition)
    compare.load_partition("P1")
    compare.load_partition("P2")
    return baseline, compare


class ReconciliationSession:
    """一次边覆盖对账会话：持有双方图、轮次证据与最近结论。"""

    def __init__(self, session_id: str, task_no: str) -> None:
        self.session_id = session_id
        self.task_no = task_no
        self.baseline, self.compare = build_demo_graphs()
        self.reconciler = EdgeCoverageReconciler(self.baseline, self.compare)
        self.last_result: dict[str, Any] | None = None
        self.report_id: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_no": self.task_no,
            "round_no": self.reconciler.round_no,
            "baseline": {
                "name": self.baseline.name,
                "loaded_partitions": sorted(self.baseline.loaded_partitions),
                "edges": [edge.to_dict() for edge in self.baseline.edges.values()],
            },
            "compare": {
                "name": self.compare.name,
                "loaded_partitions": sorted(self.compare.loaded_partitions),
                "edges": [edge.to_dict() for edge in self.compare.edges.values()],
            },
            "last_result": self.last_result,
            "report_id": self.report_id,
        }


class ReconciliationRegistry:
    """内存会话仓库（与 store 同生命周期，进程内单例）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, ReconciliationSession] = {}
        self._seq = 0

    def create(self, task_no: str) -> ReconciliationSession:
        self._seq += 1
        session_id = f"RECON-{self._seq:04d}"
        session = ReconciliationSession(session_id, task_no)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> ReconciliationSession | None:
        return self._sessions.get(session_id)

    def require(self, session_id: str) -> ReconciliationSession:
        session = self.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def count(self) -> int:
        return len(self._sessions)


reconciliation_registry = ReconciliationRegistry()


class GraphCompareService(BaseModuleService):
    MODULE = "graph_compare"
    ENTITY = "比对任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["任务编号", "基准图", "对比图", "比对维度", "新增实体", "删除实体", "变更关系", "比对时间"]
    REQUIRED_FIELDS = ["任务编号", "基准图", "对比图"]
    STATUS_ORDER = ["待执行", "比对中", "已出报告", "比对失败"]
    ACTION_RULES = {"启动比对": "比对中", "确认报告": "已出报告", "标记失败": "比对失败"}
    NEGATIVE_ACTIONS = []

    # ===== 边覆盖对账会话 =====
    def create_reconciliation(self, task_no: str = "GRAP-EDGE-DEMO") -> dict[str, Any]:
        return reconciliation_registry.create(task_no).snapshot()

    def get_reconciliation(self, session_id: str) -> dict[str, Any]:
        return reconciliation_registry.require(session_id).snapshot()

    def start_round(self, session_id: str) -> dict[str, Any]:
        session = reconciliation_registry.require(session_id)
        round_no = session.reconciler.start_round()
        return {"session_id": session_id, "round_no": round_no, "stale_evidence_discarded": True}

    def interrupt_round(self, session_id: str) -> dict[str, Any]:
        session = reconciliation_registry.require(session_id)
        round_no = session.reconciler.interrupt_round()
        return {"session_id": session_id, "round_no": round_no, "interrupted": True}

    def load_partition(self, session_id: str, graph_side: str, partition: str) -> dict[str, Any]:
        session = reconciliation_registry.require(session_id)
        graph = self._side_graph(session, graph_side)
        graph.load_partition(partition)
        return {
            "session_id": session_id,
            "graph": graph.name,
            "partition": partition,
            "loaded_partitions": sorted(graph.loaded_partitions),
        }

    def unload_partition(self, session_id: str, graph_side: str, partition: str) -> dict[str, Any]:
        session = reconciliation_registry.require(session_id)
        graph = self._side_graph(session, graph_side)
        graph.unload_partition(partition)
        return {
            "session_id": session_id,
            "graph": graph.name,
            "partition": partition,
            "loaded_partitions": sorted(graph.loaded_partitions),
        }

    def read_edge(self, session_id: str, edge_id: str) -> dict[str, Any]:
        session = reconciliation_registry.require(session_id)
        evidence = session.reconciler.read_edge(edge_id)
        return evidence.to_dict()

    def read_registered_edges(self, session_id: str) -> dict[str, Any]:
        """按基准图登记全集顺序真实读取；未装载分区自然落到「未知」。"""
        session = reconciliation_registry.require(session_id)
        edge_ids = sorted(session.baseline.edges)
        return {
            "session_id": session_id,
            "round_no": session.reconciler.round_no,
            "edges": [
                session.reconciler.read_edge(edge_id).to_dict() for edge_id in edge_ids
            ],
        }

    def update_compare_edge(
        self,
        session_id: str,
        edge_id: str,
        *,
        source: str | None = None,
        target: str | None = None,
        revision: str | None = None,
    ) -> dict[str, Any]:
        """补齐分区时改写对比图边（如 E3 改向），下一轮真实读取才能看到。"""
        session = reconciliation_registry.require(session_id)
        current = session.compare.edges.get(edge_id)
        if current is None:
            raise KeyError(edge_id)
        session.compare.edges[edge_id] = Edge(
            edge_id=edge_id,
            source=source if source is not None else current.source,
            target=target if target is not None else current.target,
            revision=revision if revision is not None else current.revision,
            partition=current.partition,
        )
        return session.compare.edges[edge_id].to_dict()

    def reconcile(self, session_id: str, *, interrupted: bool | None = None) -> dict[str, Any]:
        session = reconciliation_registry.require(session_id)
        result = session.reconciler.reconcile(interrupted=interrupted)
        session.last_result = result.to_dict()
        return session.last_result

    def sign_off_report(self, session_id: str) -> dict[str, Any]:
        """按无差异对账数据驱动图谱报告待办：相同边签收，未知边挂账。"""
        session = reconciliation_registry.require(session_id)
        if session.last_result is None:
            raise RuntimeError("尚无对账结果，先执行 reconcile")
        result = session.last_result
        report = self._push_reconcile_report(session, result)
        session.report_id = report["报告编号"]
        return {"session_id": session_id, "report": report}

    @staticmethod
    def _side_graph(session: ReconciliationSession, graph_side: str) -> PartitionedGraph:
        if graph_side == "baseline":
            return session.baseline
        if graph_side == "compare":
            return session.compare
        raise ValueError(f"graph_side 仅支持 baseline/compare，收到 {graph_side!r}")

    def _push_reconcile_report(self, session: ReconciliationSession, result: dict[str, Any]) -> dict[str, Any]:
        rows = store.rows("report_gen")
        next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        sign_off = result["sign_off"]
        unknown_ids = result["unknown_edge_ids"]
        report: dict[str, Any] = {
            "id": next_id,
            # 有未知/差异时报告不能直接“已生成”签收，保留待办。
            "status": "草稿" if unknown_ids or result["different_edge_ids"] else "已生成",
            "pending": bool(unknown_ids or result["different_edge_ids"]),
            "abnormal": bool(result["different_edge_ids"]),
            "报告编号": f"REPO-EDGE-{next_id:04d}",
            "报告类型": "edge-coverage-reconcile",
            "数据范围": f"{session.baseline.name}-vs-{session.compare.name}",
            "图表配置": "edge-coverage-matrix,sign-off-table",
            "输出格式": "json",
            "生成周期": "on-reconcile-round",
            "分发列表": "",
            "最近生成": "2026-11-22",
            # 签收窗口结构化数据：已核对边的双方图代/端点依据 + 未知挂账引用。
            "对账轮次": result["round_no"],
            "零差异结论": sign_off["zero_difference"],
            "签收结论": sign_off["conclusion"],
            "已签收边": "、".join(result["signable_edge_ids"]),
            "待补齐边": "、".join(unknown_ids),
            "差异边": "、".join(result["different_edge_ids"]),
            "签收证据": sign_off["signed_edges"],
            "待补齐图代引用": sign_off["pending_graph_refs"],
        }
        rows.append(report)
        return report

    # ===== 跨模块数据流实现 =====
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry, message = super().run_action(entry_id, action)
        if entry is None:
            return entry, message
        # 链路 L14: graph_compare → report_gen（比对报告触发报表生成）
        if entry.get("status") == "已出报告":
            report_msg = self._push_to_report_gen(entry)
            message += "；" + report_msg
        # 链路 L15: graph_compare → quality_audit（比对结果触发质量审计）
        if entry.get("status") == "已出报告":
            audit_msg = self._push_to_quality_audit(entry)
            message += "；" + audit_msg
        return entry, message

    def _push_to_report_gen(self, entry: dict[str, Any]) -> str:
        rows = store.rows("report_gen")
        next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        rp_entry: dict[str, Any] = {
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "报告编号": f"REPO-CMP-{next_id:04d}",
            "报告类型": "graph-compare-report",
            "数据范围": f"{entry.get('基准图','')}-vs-{entry.get('对比图','')}",
            "图表配置": "diff-table,chord-diagram",
            "输出格式": "html",
            "生成周期": "on-compare-done",
            "分发列表": "",
            "最近生成": str(entry.get("比对时间", "2026-11-18")),
        }
        rows.append(rp_entry)
        return f"已生成比对报表 {rp_entry['报告编号']}"

    def _push_to_quality_audit(self, entry: dict[str, Any]) -> str:
        rows = store.rows("quality_audit")
        next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        qa_entry: dict[str, Any] = {
            "id": next_id,
            "status": "待审计",
            "pending": True,
            "abnormal": False,
            "报告编号": f"QUAL-CMP-{next_id:04d}",
            "审计范围": f"graph-compare-{entry.get('任务编号','')}",
            "实体准确率": "from-compare-delta",
            "关系完整率": "from-compare-delta",
            "冗余度": "estimated-low",
            "更新时效": "实时",
            "合规结论": "待审计",
            "审计时间": str(entry.get("比对时间", "2026-11-18")),
        }
        rows.append(qa_entry)
        return f"已触发质量审计 {qa_entry['报告编号']}"

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["cross_module_consumers"] = ["report_gen", "quality_audit"]
        base["edge_reconcile_sessions"] = reconciliation_registry.count()
        return base
