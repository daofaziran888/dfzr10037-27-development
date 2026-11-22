"""边覆盖对账领域：基于基准图与对比图的边数据做覆盖校验。

核心规则（对应图谱报告的零差异签收口径）：

1. 基准图登记的每条边是对账全集；对比图按分区（partition）读取，
   尚未装载的分区查不到边，只能给「未知」而不能当成相同或删除。
2. 单条边的结论只接受「同一轮真实读取」拿到的双方证据：
   - 双方端点（含方向）一致 → 相同，可签收，双方实际图代随证据留痕；
   - 双方都读到但端点/方向不一致 → 差异，禁止签收；
   - 任一方分区未装载 → 未知，禁止按零差异签收，保留图代引用待补齐。
3. 轮次（round）代表一次真实读取。比对中断后重试必须开新一轮，
   上一轮的证据全部过期，零差异结论禁止拿旧轮证据拼凑。
4. 整体零差异结论仅当基准边全集都在本轮真实读到且全部相同时成立；
   存在未知分区时只能签收已核对相同的子集。

本模块只依赖标准库，不触碰全局 store，便于用脚本独立验证。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# 单条边对账结论
VERDICT_SAME = "相同"
VERDICT_DIFFERENT = "差异"
VERDICT_UNKNOWN = "未知"
# 未知的具体原因
REASON_COMPARE_NOT_LOADED = "对比图分区未装载"
REASON_BASELINE_NOT_LOADED = "基准图分区未装载"
REASON_BASELINE_MISSING = "基准图未登记该边"


@dataclass(frozen=True)
class Edge:
    """一条有向边：图代（graph_revision）+ 边编号 + 主体/客体端点。"""

    edge_id: str
    source: str
    target: str
    revision: str
    partition: str

    def endpoint_key(self) -> tuple[str, str]:
        return (self.source, self.target)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PartitionedGraph:
    """按分区装载的图数据源。

    `edges` 是该图已登记/已装载的全部边；`loaded_partitions` 是当前真正
    可查询的分区集合。查询未装载分区时抛 PartitionNotLoaded，模拟
    「查询响应中该部分尚未装载」，而不是返回空结果冒充无此边。
    """

    name: str
    edges: dict[str, Edge]
    loaded_partitions: set[str] = field(default_factory=set)

    @classmethod
    def from_edges(cls, name: str, edges: Iterable[Edge]) -> "PartitionedGraph":
        edge_map = {edge.edge_id: edge for edge in edges}
        partitions = {edge.partition for edge in edge_map.values()}
        # 新建图默认所有分区都未装载，显式 load 后才可读，避免“默认读全”。
        return cls(name=name, edges=edge_map, loaded_partitions=set())

    def load_partition(self, partition: str) -> None:
        self.loaded_partitions.add(partition)

    def unload_partition(self, partition: str) -> None:
        self.loaded_partitions.discard(partition)

    def is_partition_loaded(self, partition: str) -> bool:
        return partition in self.loaded_partitions

    def query_edge(self, edge_id: str) -> Edge:
        """真实读取：未装载分区直接拒绝，调用方据此记为未知。"""
        edge = self.edges.get(edge_id)
        if edge is None:
            raise EdgeNotFound(self.name, edge_id)
        if edge.partition not in self.loaded_partitions:
            raise PartitionNotLoaded(self.name, edge.partition, edge_id)
        return edge


class PartitionNotLoaded(Exception):
    """图分区尚未装载，本轮无法提供证据。"""

    def __init__(self, graph_name: str, partition: str, edge_id: str) -> None:
        self.graph_name = graph_name
        self.partition = partition
        self.edge_id = edge_id
        super().__init__(
            f"图 {graph_name} 分区 {partition} 尚未装载，边 {edge_id} 本轮不可读"
        )


class EdgeNotFound(Exception):
    """图中没有登记该边（与“分区未装载”是两种不同的缺失）。"""

    def __init__(self, graph_name: str, edge_id: str) -> None:
        self.graph_name = graph_name
        self.edge_id = edge_id
        super().__init__(f"图 {graph_name} 未登记边 {edge_id}")


@dataclass
class EdgeEvidence:
    """一条边在一次对账中的双方证据与结论。"""

    edge_id: str
    verdict: str
    partition: str
    baseline_revision: str | None
    compare_revision: str | None
    baseline_endpoints: tuple[str, str] | None
    compare_endpoints: tuple[str, str] | None
    read_round: int | None
    unknown_reason: str | None = None
    detail: str = ""
    signable: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["baseline_endpoints"] = (
            list(self.baseline_endpoints) if self.baseline_endpoints else None
        )
        data["compare_endpoints"] = (
            list(self.compare_endpoints) if self.compare_endpoints else None
        )
        return data


@dataclass
class ReconcileResult:
    """一轮覆盖校验的完整对账结果，直接驱动图谱报告待办。"""

    round_no: int
    interrupted: bool
    edges: list[EdgeEvidence]
    zero_difference: bool
    signable_edge_ids: list[str]
    unknown_edge_ids: list[str]
    different_edge_ids: list[str]
    coverage: dict[str, Any]
    sign_off: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_no": self.round_no,
            "interrupted": self.interrupted,
            "zero_difference": self.zero_difference,
            "signable_edge_ids": list(self.signable_edge_ids),
            "unknown_edge_ids": list(self.unknown_edge_ids),
            "different_edge_ids": list(self.different_edge_ids),
            "coverage": dict(self.coverage),
            "sign_off": dict(self.sign_off),
            "edges": [edge.to_dict() for edge in self.edges],
        }


class EdgeCoverageReconciler:
    """边覆盖校验器：基准图边全集 × 对比图分区读取 → 可签收对账数据。"""

    def __init__(self, baseline: PartitionedGraph, compare: PartitionedGraph) -> None:
        self.baseline = baseline
        self.compare = compare
        self._round_no = 0
        # 已中断轮次集合：中断轮的证据禁止被后续签收复用。
        self._interrupted_rounds: set[int] = set()
        # 每轮实际读到的证据快照，round_no -> {edge_id: EdgeEvidence}
        self._round_evidence: dict[int, dict[str, EdgeEvidence]] = {}
        self._last_result: ReconcileResult | None = None

    @property
    def round_no(self) -> int:
        return self._round_no

    def start_round(self) -> int:
        """开启一轮新的真实读取；历史轮证据立即过期，不参与新轮结论。"""
        self._round_no += 1
        self._round_evidence[self._round_no] = {}
        return self._round_no

    def interrupt_round(self) -> int:
        """批次比对中断：当前轮标记为中断，其证据一律不得用于零差异结论。"""
        if self._round_no == 0:
            raise RuntimeError("尚未开启任何读取轮次，无法中断")
        self._interrupted_rounds.add(self._round_no)
        return self._round_no

    def is_round_stale(self, round_no: int) -> bool:
        """旧轮或中断轮的证据对当前签收而言都是过期证据。"""
        return round_no != self._round_no or round_no in self._interrupted_rounds

    def read_edge(self, edge_id: str) -> EdgeEvidence:
        """在当前轮真实读取一条边并给出单条对账结论。"""
        if self._round_no == 0:
            raise RuntimeError("必须先 start_round 开启读取轮次")
        if self._round_no in self._interrupted_rounds:
            raise RuntimeError(
                f"第 {self._round_no} 轮已中断，不能继续读取；请 start_round 重试"
            )

        baseline_edge: Edge | None = None
        compare_edge: Edge | None = None
        # 未装载时保留登记侧的图代引用，未知结论也要能追溯双方实际图代。
        baseline_registered = self.baseline.edges.get(edge_id)
        compare_registered = self.compare.edges.get(edge_id)
        unknown_reason: str | None = None
        partition = ""

        try:
            baseline_edge = self.baseline.query_edge(edge_id)
            partition = baseline_edge.partition
        except PartitionNotLoaded as exc:
            unknown_reason = REASON_BASELINE_NOT_LOADED
            partition = exc.partition
        except EdgeNotFound:
            # 基准图未登记的边不属于对账全集，交由对比侧多余边处理，这里不签收。
            unknown_reason = REASON_BASELINE_MISSING

        try:
            compare_edge = self.compare.query_edge(edge_id)
            if not partition:
                partition = compare_edge.partition
        except PartitionNotLoaded as exc:
            # 基准图已登记 E6，但对比图该分区尚未装载 → 未知（典型 E6 场景）。
            unknown_reason = unknown_reason or REASON_COMPARE_NOT_LOADED
            if not partition:
                partition = exc.partition
        except EdgeNotFound:
            compare_edge = None

        evidence = self._evaluate(
            edge_id=edge_id,
            partition=partition,
            baseline_edge=baseline_edge,
            compare_edge=compare_edge,
            baseline_registered=baseline_registered,
            compare_registered=compare_registered,
            unknown_reason=unknown_reason,
        )
        self._round_evidence[self._round_no][edge_id] = evidence
        return evidence

    def _evaluate(
        self,
        *,
        edge_id: str,
        partition: str,
        baseline_edge: Edge | None,
        compare_edge: Edge | None,
        baseline_registered: Edge | None,
        compare_registered: Edge | None,
        unknown_reason: str | None,
    ) -> EdgeEvidence:
        # 图代引用优先取真实读到的边；未装载时回落到登记图代，保证挂账可追溯。
        base_ref = baseline_edge or baseline_registered
        comp_ref = compare_edge or compare_registered
        base_rev = base_ref.revision if base_ref else None
        comp_rev = comp_ref.revision if comp_ref else None
        base_eps = baseline_edge.endpoint_key() if baseline_edge else None
        comp_eps = compare_edge.endpoint_key() if compare_edge else None

        # 任一方分区未装载 / 基准未登记：未知，保留双方实际图代引用待补齐。
        if unknown_reason is not None or baseline_edge is None:
            reason = unknown_reason or REASON_BASELINE_MISSING
            return EdgeEvidence(
                edge_id=edge_id,
                verdict=VERDICT_UNKNOWN,
                partition=partition,
                baseline_revision=base_rev,
                compare_revision=comp_rev,
                baseline_endpoints=base_eps,
                compare_endpoints=comp_eps,
                read_round=self._round_no,
                unknown_reason=reason,
                signable=False,
                detail=self._unknown_detail(edge_id, reason, base_rev, comp_rev),
            )

        # 双方都在本轮真实读到：关联双方实际图代与端点依据再下结论。
        if compare_edge is None:
            # 对比图已装载该分区但查无此边 → 边被删除，属于差异而非未知。
            return EdgeEvidence(
                edge_id=edge_id,
                verdict=VERDICT_DIFFERENT,
                partition=partition,
                baseline_revision=base_rev,
                compare_revision=comp_rev,
                baseline_endpoints=base_eps,
                compare_endpoints=None,
                read_round=self._round_no,
                unknown_reason=None,
                signable=False,
                detail=f"边 {edge_id} 在对比图已装载分区中缺失（删除）",
            )

        # 「相同」按端点（含方向）判定；双方图代是各自版本标识，作为依据留痕。
        same_endpoints = baseline_edge.endpoint_key() == compare_edge.endpoint_key()
        same_revision = baseline_edge.revision == compare_edge.revision
        if same_endpoints:
            revision_note = (
                f"双方图代均为 {base_rev}"
                if same_revision
                else f"双方图代 {base_rev} / {comp_rev}（版本不同，端点一致）"
            )
            return EdgeEvidence(
                edge_id=edge_id,
                verdict=VERDICT_SAME,
                partition=partition,
                baseline_revision=base_rev,
                compare_revision=comp_rev,
                baseline_endpoints=base_eps,
                compare_endpoints=comp_eps,
                read_round=self._round_no,
                unknown_reason=None,
                signable=True,
                detail=(
                    f"边 {edge_id} 端点 {base_eps[0]}->{base_eps[1]} 一致；"
                    f"{revision_note}"
                ),
            )

        changes = [
            f"端点 {base_eps[0]}->{base_eps[1]} 与 "
            f"{comp_eps[0]}->{comp_eps[1]} 不一致（含改向）"
        ]
        if not same_revision:
            changes.append(f"图代 {base_rev} 与 {comp_rev} 不一致")
        return EdgeEvidence(
            edge_id=edge_id,
            verdict=VERDICT_DIFFERENT,
            partition=partition,
            baseline_revision=base_rev,
            compare_revision=comp_rev,
            baseline_endpoints=base_eps,
            compare_endpoints=comp_eps,
            read_round=self._round_no,
            unknown_reason=None,
            signable=False,
            detail=f"边 {edge_id} " + "；".join(changes),
        )

    @staticmethod
    def _unknown_detail(
        edge_id: str,
        reason: str,
        base_rev: str | None,
        comp_rev: str | None,
    ) -> str:
        return (
            f"边 {edge_id} 暂挂未知（{reason}），"
            f"基准图代={base_rev or '未读到'}，对比图代={comp_rev or '未读到'}，"
            "保留图代引用待分区补齐后重核"
        )

    def reconcile(self, *, interrupted: bool | None = None) -> ReconcileResult:
        """基于本轮真实读取的证据汇总覆盖校验结论。

        中断轮（或显式 interrupted=True）只产出部分对账，禁止零差异签收；
        零差异结论要求基准边全集在本轮全部读到且全部相同。
        """
        if self._round_no == 0:
            raise RuntimeError("尚未开启任何读取轮次，无可对账证据")
        round_evidence = self._round_evidence[self._round_no]
        if interrupted is None:
            interrupted = self._round_no in self._interrupted_rounds
        if interrupted:
            self._interrupted_rounds.add(self._round_no)

        # 基准图登记的边即对账全集，保证没读全的分区不能混进零差异。
        baseline_ids = sorted(self.baseline.edges)
        evidence_list: list[EdgeEvidence] = []
        for edge_id in baseline_ids:
            if edge_id in round_evidence:
                # 中断轮已真实读到的证据保留原结论，但签收窗口在下方统一关闭。
                evidence = round_evidence[edge_id]
            else:
                # 本轮根本没读到的基准边：按未知挂账，不允许默认相同。
                registered = self.baseline.edges[edge_id]
                compare_registered = self.compare.edges.get(edge_id)
                evidence = EdgeEvidence(
                    edge_id=edge_id,
                    verdict=VERDICT_UNKNOWN,
                    partition=registered.partition,
                    baseline_revision=registered.revision,
                    compare_revision=(
                        compare_registered.revision if compare_registered else None
                    ),
                    baseline_endpoints=registered.endpoint_key(),
                    compare_endpoints=None,
                    read_round=self._round_no,
                    unknown_reason=REASON_COMPARE_NOT_LOADED,
                    signable=False,
                    detail=self._unknown_detail(
                        edge_id,
                        REASON_COMPARE_NOT_LOADED,
                        registered.revision,
                        compare_registered.revision if compare_registered else None,
                    ),
                )
            evidence_list.append(evidence)

        same = [e.edge_id for e in evidence_list if e.verdict == VERDICT_SAME]
        unknown = [e.edge_id for e in evidence_list if e.verdict == VERDICT_UNKNOWN]
        different = [e.edge_id for e in evidence_list if e.verdict == VERDICT_DIFFERENT]
        total = len(baseline_ids)
        # 本轮在双方（或至少可下结论的）真实读到的边：未知即未读全，不计覆盖。
        read_count = total - len(unknown)

        # 零差异口径：全集覆盖 + 全部相同 + 轮次未中断。
        zero_difference = (
            not interrupted and not unknown and not different and total > 0
            and len(same) == total
        )
        signable = list(same) if not interrupted else []
        sign_off = self._build_sign_off(
            evidence_list=evidence_list,
            signable=signable,
            unknown=unknown,
            different=different,
            interrupted=interrupted,
            zero_difference=zero_difference,
        )
        coverage = {
            "baseline_registered": total,
            "read_in_round": read_count,
            "same": len(same),
            "unknown": len(unknown),
            "different": len(different),
            "coverage_ratio": round(len(same) / total, 4) if total else 0.0,
            "fully_covered": not unknown and total > 0,
        }
        result = ReconcileResult(
            round_no=self._round_no,
            interrupted=interrupted,
            edges=evidence_list,
            zero_difference=zero_difference,
            signable_edge_ids=signable,
            unknown_edge_ids=unknown,
            different_edge_ids=different,
            coverage=coverage,
            sign_off=sign_off,
        )
        self._last_result = result
        return result

    def _build_sign_off(
        self,
        *,
        evidence_list: list[EdgeEvidence],
        signable: list[str],
        unknown: list[str],
        different: list[str],
        interrupted: bool,
        zero_difference: bool,
    ) -> dict[str, Any]:
        """构造报告签收窗口数据：可签的相同边 + 未知挂账图代引用。"""
        signed_evidence = [
            {
                "edge_id": e.edge_id,
                "baseline_revision": e.baseline_revision,
                "compare_revision": e.compare_revision,
                "baseline_endpoints": list(e.baseline_endpoints)
                if e.baseline_endpoints else None,
                "compare_endpoints": list(e.compare_endpoints)
                if e.compare_endpoints else None,
                "read_round": e.read_round,
                "detail": e.detail,
            }
            for e in evidence_list if e.edge_id in signable
        ]
        pending_refs = [
            {
                "edge_id": e.edge_id,
                "partition": e.partition,
                "baseline_revision": e.baseline_revision,
                "compare_revision": e.compare_revision,
                "unknown_reason": e.unknown_reason,
                "read_round": e.read_round,
            }
            for e in evidence_list if e.edge_id in unknown
        ]
        if zero_difference:
            conclusion = "零差异可签收"
        elif interrupted:
            conclusion = "轮次中断不可签收：已读证据保留备查，重试须重新真实读取"
        elif signable:
            conclusion = "部分签收：仅相同边可签，未知分区补齐前整体不得判定零差异"
        else:
            conclusion = "无可签收边"
        return {
            "conclusion": conclusion,
            "zero_difference": zero_difference,
            "signed_edges": signed_evidence,
            "pending_graph_refs": pending_refs,
            "blocked_differences": list(different),
            "evidence_round": self._round_no,
            "stale_rounds_rejected": sorted(self._interrupted_rounds),
        }

    def last_result(self) -> ReconcileResult | None:
        return self._last_result
