# 民族文旅承载协作台

协调乡村讲解、传统表演、工艺体验和药材文化场馆的接待承载与收益确认。

各场所和传承人申报开放时段、承载量与同意讲述或展示的内容；旅行团队提交行程后，服务结合步行时间、无障碍需求、道路与户外条件、人员冲突形成可执行路线。现场迟到、临时改道、取消与离线核销直接改变剩余容量与分账；事件按 `event_id` 幂等，网络恢复后的重复记录不会再次计费；触碰文化禁忌或缺少授权的项目不会出现在游客方案中。

## 快速开始

```bash
python3 service.py --check                       # 基础检查
python3 -m unittest discover -s tests -v        # 契约与行为测试
python3 service.py --demo                       # 用样例走一遍当日流程
python3 service.py --port 8000                  # 启动服务
```

## 当日数据约定（contracts/visit_day.json）

`contracts/visit_day.json` 保存公开的领域样例，用来约定外部数据的名称与层级；样例不含真实个人资料、业务凭据或生产连接信息。`sample` 字段为一天的完整表达：

- `walk_minutes`：地点间步行分钟数（对称）；未列出的点对取 `default_walk_minutes`。
- `mobility_walk_factor`：无障碍团队的步行时间系数（向上取整）。
- `conditions`：`outdoor_ok` 与 `road_closed`，现场条件变化的初始值。
- `resources[]`：资源申报。
  - `capacity` 同一时段可接待的团队数；`capacity_visitors` 同时段人数上限。
  - `open` 开放时段，`slot_minutes` 单场时长；候选开始时间按 5 分钟粒度对齐。
  - `staff` 参与接待的传承人/员工——同一人员不能同时出现在两个团队的排班中。
  - `content` 同意讲述或展示的内容（授权范围）；`taboo` 文化禁忌内容，永不进入游客方案。
  - `accessible`/`outdoor`/`road` 无障碍、户外与道路依赖。
  - `price_cny` 每位游客单价；`share` 分账比例，合计必须为 1。
- `groups[]`：团队申报（`size`、`mobility_support`、`arrive`、`requests[]`）。

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 服务身份 |
| POST | `/day` | 加载当日资源（接受完整契约文档或其 `sample`），重置当日状态 |
| GET | `/day` | 当日资源与现场条件 |
| POST | `/groups` | 团队提交行程，返回可执行路线与被拒绝请求（含原因） |
| GET | `/groups/{id}` | 团队的当前方案与变更通知 |
| POST | `/events` | 现场事件（见下），`event_id` 幂等 |
| GET | `/events` | 已应用事件 |
| GET | `/bookings` | 全部安排 |
| GET | `/capacity` | 各资源剩余容量快照 |
| GET | `/notifications?group={id}` | 游客通知 |
| GET | `/ledger`、`/ledger/{payee}` | 收益明细与分账汇总 |
| GET | `/decisions?kind={accept\|reject\|event}` | 调度决策日志 |

## 现场事件（POST /events）

所有事件必须携带唯一的 `event_id`；重复上报直接确认，不改变状态、不重复计费。

- `checkin` 核销：`booking`、`actual_size`（缺省为申报人数）、`offline` 标记。按实际到场人数结算并分账；同一安排不能核销两次。
- `late` 迟到：`group`、`minutes`。后续未开始的安排整体后移，无法改排的取消并通知。
- `cancel` 取消：`group`，可指定 `booking`。释放剩余容量；未核销不计费，已核销不能取消。
- `condition` 条件变化：`outdoor_ok`、`road_closed.add/remove`。受影响的户外/道路安排取消并释放容量，通知游客。
- `reroute` 临时改道：`group`、`drop[]`（放弃的安排）、`add[]`（新增项目，接在团队当前位置之后）。

## 调度与拒绝原因

每次调度成立或被拒绝都写入决策日志（`GET /decisions`），原因码：

`unknown_resource` 资源不存在 · `taboo_content` 触及文化禁忌 · `content_not_authorized` 未授权内容 · `not_accessible` 无障碍条件不足 · `outdoor_closed` 户外条件不满足 · `road_closed` 道路封闭 · `capacity_full` 团队数或人数已满 · `staff_conflict` 传承人已被排班 · `no_open_slot` 没有可容纳的空档。

## 结算与对账

只有核销才产生账目：金额 = 单价 × 实际到场人数，按资源申报的 `share` 分账（逐项四舍五入到分，末位取差额，各方之和等于总额）。每条账目回链到安排与事件，`GET /ledger/{payee}` 供村民和场馆逐条核对。
