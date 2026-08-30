# Repository 约定

Repository 是运行时代码访问 PostgreSQL 的唯一收口层，调用方向固定为：

```text
API / WebSocket / Worker Runtime
                ↓
Service（业务规则、事务和外部副作用）
                ↓
Repository（SQL、ORM 持久化、锁、分页和聚合查询）
                ↓
AsyncSession / PostgreSQL
```

## 接口与返回值

- 使用函数式接口；每个函数显式接收 `AsyncSession`，不保存全局 Session。
- 查询函数未找到时返回 `None`，列表查询返回明确的列表或分页结果。
- 条件更新返回 `bool` 或受影响行数；唯一约束冲突保留为 `IntegrityError`。
- 可以返回 ORM 实体、标量、元组或明确的 dataclass，但不能依赖未显式加载的 lazy relationship。
- 持久化对象的字段赋值放在 Repository command 函数中，Service 只传入业务决策和要变更的值。

## 事务边界

Repository 可以执行 `execute/get/scalar/scalars/add/add_all/delete/merge/flush/refresh`，但禁止调用：

- `commit()`、`rollback()`、`begin()` 或 `begin_nested()`；
- `SessionLocal()` 或直接创建 Session。

Service 可以组合多个 Repository，并负责一次性 `commit()`；异常路径必须 `rollback()` 后再映射业务错误。API、WebSocket handler 和普通 Worker 编排代码不构造 SQL，也不直接调用 Session 持久化方法。

禁止在事务提交前执行 Agent/内部 HTTP/WebSocket、Appium/ADB、报告渲染或长时间文件操作等外部副作用。

## 条件锁查询模板

设备等资源的原子抢占使用条件 `UPDATE`，根据受影响行数判断是否成功：

```python
async def try_lock_device(db: AsyncSession, device_id: int, execution_id: int) -> bool:
    result = await db.execute(
        update(Device)
        .where(Device.id == device_id, Device.status == "idle", Device.locked_by_execution.is_(None))
        .values(status="busy", locked_by_execution=execution_id)
    )
    return result.rowcount == 1
```

Repository 不抛 `HTTPException`，不决定 HTTP 状态码；错误映射由 Service 或 API 边界完成。

## 命名

具体模块按业务资源命名，例如 `auth.py`、`devices.py`、`execution_queue.py`。查询使用 `get_*`、`list_*`，写入使用 `create_*`、`update_*`、`delete_*`、`try_*`；不要引入通用 `BaseRepository`、Repository 类继承或 Unit of Work 包装层。
