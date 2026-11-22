"""边覆盖对账路由：围绕 graph_compare 的读取轮次、分区装载和报告签收。

路径统一挂在 /api/graph-compare/reconcile 之下，避免和通用资源路由
/api/{module} 的动态段冲突（固定路径优先注册）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import SERVICES
from app.services.graph_compare import GraphCompareService

router = APIRouter(prefix="/api/graph-compare/reconcile", tags=["边覆盖对账"])

# 复用服务注册表中的单例，保证对账会话与通用资源接口看到的是同一份领域状态。
service: GraphCompareService = SERVICES["graph_compare"]  # type: ignore[assignment]


class CreateSessionPayload(BaseModel):
    task_no: str = "GRAP-EDGE-DEMO"


class PartitionPayload(BaseModel):
    graph_side: str = Field(description="baseline 或 compare")
    partition: str


class ReadEdgePayload(BaseModel):
    edge_id: str


class ReconcilePayload(BaseModel):
    interrupted: bool | None = None


class CompareEdgePayload(BaseModel):
    source: str | None = None
    target: str | None = None
    revision: str | None = None


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/sessions")
def create_session(payload: CreateSessionPayload) -> dict[str, Any]:
    """建立对账会话：基准图全分区可读，对比图 P3（含 E6）未装载。"""
    return service.create_reconciliation(payload.task_no)


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    try:
        return service.get_reconciliation(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"对账会话 {session_id} 不存在")


@router.post("/sessions/{session_id}/rounds")
def start_round(session_id: str) -> dict[str, Any]:
    """开启新一轮真实读取；旧轮证据立即过期，禁止拿来凑零差异。"""
    try:
        return service.start_round(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"对账会话 {session_id} 不存在")


@router.post("/sessions/{session_id}/interrupt")
def interrupt_round(session_id: str) -> dict[str, Any]:
    """批次比对中断：当前轮证据全部标记过期。"""
    try:
        return service.interrupt_round(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"对账会话 {session_id} 不存在")
    except RuntimeError as exc:
        raise _fail(exc)


@router.post("/sessions/{session_id}/partitions/load")
def load_partition(session_id: str, payload: PartitionPayload) -> dict[str, Any]:
    try:
        return service.load_partition(session_id, payload.graph_side, payload.partition)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"对账会话 {session_id} 不存在")
    except ValueError as exc:
        raise _fail(exc)


@router.post("/sessions/{session_id}/partitions/unload")
def unload_partition(session_id: str, payload: PartitionPayload) -> dict[str, Any]:
    try:
        return service.unload_partition(session_id, payload.graph_side, payload.partition)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"对账会话 {session_id} 不存在")
    except ValueError as exc:
        raise _fail(exc)


@router.post("/sessions/{session_id}/edges/read")
def read_edge(session_id: str, payload: ReadEdgePayload) -> dict[str, Any]:
    """单条边真实读取，返回带双方图代、端点依据和轮次号的证据。"""
    try:
        return service.read_edge(session_id, payload.edge_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"对账会话 {session_id} 不存在")
    except RuntimeError as exc:
        raise _fail(exc)


@router.post("/sessions/{session_id}/edges/read-all")
def read_all_edges(session_id: str) -> dict[str, Any]:
    """按基准图登记全集顺序读取；未装载分区的边（如 E6）落为未知。"""
    try:
        return service.read_registered_edges(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"对账会话 {session_id} 不存在")
    except RuntimeError as exc:
        raise _fail(exc)


@router.patch("/sessions/{session_id}/compare-edges/{edge_id}")
def update_compare_edge(session_id: str, edge_id: str, payload: CompareEdgePayload) -> dict[str, Any]:
    """补齐分区时改写对比图边（例如把 E3 改向）。"""
    try:
        return service.update_compare_edge(
            session_id,
            edge_id,
            source=payload.source,
            target=payload.target,
            revision=payload.revision,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"边 {edge_id} 不在对比图登记中")


@router.post("/sessions/{session_id}/reconcile")
def reconcile(session_id: str, payload: ReconcilePayload) -> dict[str, Any]:
    """汇总本轮覆盖校验：零差异结论、可签边、未知挂账和差异清单。"""
    try:
        return service.reconcile(session_id, interrupted=payload.interrupted)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"对账会话 {session_id} 不存在")
    except RuntimeError as exc:
        raise _fail(exc)


@router.post("/sessions/{session_id}/sign-off")
def sign_off_report(session_id: str) -> dict[str, Any]:
    """驱动图谱报告待办：相同边按无差异数据签收，未知边保留图代引用。"""
    try:
        return service.sign_off_report(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"对账会话 {session_id} 不存在")
    except RuntimeError as exc:
        raise _fail(exc)
