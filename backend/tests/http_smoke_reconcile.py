"""HTTP 冒烟：对运行中的 uvicorn 服务验证对账路由（仅用标准库 urllib）。

前置：cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000
运行：.venv/bin/python -I tests/http_smoke_reconcile.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/graph-compare/reconcile"


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def assert_true(cond: bool, label: str) -> None:
    if not cond:
        raise AssertionError(f"断言失败：{label}")
    print(f"  ✓ {label}")


def main() -> int:
    print("[1] 建立对账会话（对比图 P3 未装载，含 E6）")
    status, body = call("POST", "/sessions", {"task_no": "GRAP-HTTP-SMOKE"})
    assert_true(status == 200, "会话创建 200")
    sid = body["session_id"]
    assert_true(body["compare"]["loaded_partitions"] == ["P1", "P2"], "对比图仅 P1/P2 装载")

    print("[2] 第 1 轮真实读取 → E5/E6 未知，其余相同")
    call("POST", f"/sessions/{sid}/rounds")
    _, read_body = call("POST", f"/sessions/{sid}/edges/read-all")
    edges = {e["edge_id"]: e for e in read_body["edges"]}
    assert_true(edges["E6"]["verdict"] == "未知", "E6 判定未知")
    assert_true(edges["E6"]["unknown_reason"] == "对比图分区未装载", "E6 原因为分区未装载")
    assert_true(edges["E6"]["compare_revision"] == "G-CMP-7", "未知边保留对比图代引用")
    assert_true(edges["E1"]["verdict"] == "相同" and edges["E1"]["signable"], "E1 相同可签")

    print("[3] 中断后对账 → 禁止零差异、签收窗口关闭")
    call("POST", f"/sessions/{sid}/interrupt")
    _, r1 = call("POST", f"/sessions/{sid}/reconcile", {})
    assert_true(r1["zero_difference"] is False, "中断轮零差异不成立")
    assert_true(r1["signable_edge_ids"] == [], "中断轮无可签收边")
    assert_true(sorted(r1["unknown_edge_ids"]) == ["E5", "E6"], "未知边为 E5/E6")

    print("[4] 中断轮继续读取被拒（400）")
    status, _ = call("POST", f"/sessions/{sid}/edges/read", {"edge_id": "E1"})
    assert_true(status == 400, "中断轮读取返回 400")

    print("[5] 报告签收待办：未知边以图代引用挂账")
    _, sign1 = call("POST", f"/sessions/{sid}/sign-off")
    report1 = sign1["report"]
    assert_true(report1["pending"] is True, "报告保留待办")
    assert_true(report1["待补齐边"] == "E5、E6", "待补齐边为 E5、E6")

    print("[6] 补齐 P3 并把 E3 改向，重试第 2 轮")
    call("POST", f"/sessions/{sid}/partitions/load",
         {"graph_side": "compare", "partition": "P3"})
    status, patched = call("PATCH", f"/sessions/{sid}/compare-edges/E3",
                           {"source": "T-D", "target": "T-C", "revision": "G-CMP-8"})
    assert_true(status == 200 and patched["source"] == "T-D", "E3 改向写入成功")
    _, round_body = call("POST", f"/sessions/{sid}/rounds")
    assert_true(round_body["round_no"] == 2, "进入第 2 轮")
    _, read2 = call("POST", f"/sessions/{sid}/edges/read-all")
    edges2 = {e["edge_id"]: e for e in read2["edges"]}
    assert_true(all(e["read_round"] == 2 for e in edges2.values()), "证据全部来自第 2 轮")
    assert_true(edges2["E6"]["verdict"] == "相同", "E6 补回后相同")
    assert_true(edges2["E3"]["verdict"] == "差异", "E3 改向暴露新差异")

    print("[7] 第 2 轮对账：部分签收 + 差异阻断零差异")
    _, r2 = call("POST", f"/sessions/{sid}/reconcile", {})
    assert_true(r2["zero_difference"] is False, "存在改向差异，非零差异")
    assert_true(sorted(r2["signable_edge_ids"]) == ["E1", "E2", "E4", "E5", "E6"],
                "5 条相同边可签")
    assert_true(r2["different_edge_ids"] == ["E3"], "差异边为 E3")
    assert_true(r2["sign_off"]["stale_rounds_rejected"] == [1], "第 1 轮被记为过期轮次")

    print("[8] 报告按无差异数据签收相同边，差异边挂异常待办")
    _, sign2 = call("POST", f"/sessions/{sid}/sign-off")
    report2 = sign2["report"]
    assert_true(report2["abnormal"] is True, "报告标记异常")
    assert_true(report2["差异边"] == "E3", "报告记录差异边 E3")
    signed = {item["edge_id"]: item for item in report2["签收证据"]}
    assert_true(signed["E6"]["read_round"] == 2, "E6 签收证据关联第 2 轮")
    assert_true(signed["E6"]["baseline_endpoints"] == ["T-F", "T-G"], "E6 含端点依据")

    print("[9] 通用资源路由仍正常（回归检查）")
    req = urllib.request.Request("http://127.0.0.1:8000/api/graph_compare")
    with urllib.request.urlopen(req, timeout=10) as resp:
        page = json.loads(resp.read().decode())
    assert_true(resp.status == 200 and page["total"] >= 3, "通用列表接口未受影响")

    print("\n全部 HTTP 冒烟断言通过。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"\n冒烟失败：{exc}", file=sys.stderr)
        sys.exit(1)
