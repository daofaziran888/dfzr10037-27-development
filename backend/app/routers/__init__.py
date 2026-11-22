"""业务路由汇总：模块共用资源路由 + 边覆盖对账专用路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import edge_reconcile, resource

# 固定前缀的对账路由先注册，避免被通用资源路由 /api/{module} 动态段抢先匹配。
ROUTERS = [edge_reconcile, resource]
