# 民族文旅承载协作台

协调新生鄂伦春族乡的民俗体验、摩苏昆说唱、桦树皮工艺与道地药材文化场馆的
**申报—排班—现场—核销—分账**全流程。

## 解决的问题

- **同一传承人被两个团队同时排班**：资源方按团队/人两种口径申报承载量，
  排班时逐时段校验人员与容量冲突，冲突自动改约并留痕。
- **停演/取消消息触达**：停演、改道、取消、迟到、替代、禁忌移除、离线同步
  都生成面向团队的变更通知。
- **按实际参与者结算**：只承认带稳定事件 id 的现场核销，按核销人数 ×
  内容单价计费，再按资源方申报的份额整数分配，每行恒等配平。
- **离线与重复**：离线核销以实际发生时间入账，网络恢复后重试的同一事件
  id 只生效一次，不重复占容、不重复计费。
- **禁忌与授权**：标记禁忌或需要授权而未授权、以及资源方未申报同意展示的
  内容，在规划阶段即从游客方案中剔除并说明原因，不做静默安排。
- **步行/无障碍/道路与户外条件**：基于地点步行图最短路核算转场时间，
  按团队无障碍需求过滤路线，结合开放时段、道路封闭与天气（现场停演事件）
  形成可执行路线。

## 数据契约

`contracts/visit_day.json` 保存一天资源的原始表达：

- `locations` / `edges`：地点与步行边（含是否支持轮椅、是否林间步道）；
- `contents`：可讲述/展示的内容目录，含时长、单价（整数分）、
  `taboo_for_visitors`、`requires_authorization`、`outdoor_only`、
  声明式 `alternatives`；
- `resources`：传承人/场所申报的地点、开放时段、承载量
  （`team`/`person`）与授权内容清单；
- `groups` / `itinerary_requests`：团队情况（含 `mobility_support`）
  与行程申报；
- `conditions`：申报时天气与道路封闭；
- `payees` / `split_rules`：收款方登记与各资源分账份额（合计须为 1.0）；
- `operations`：当天现场日志（核定人数、迟到、核销、停演、改道、取消；
  核销可带 `offline` 块；同 id 重复即幂等重试）。

## 运行方式

```bash
python3 service.py --check          # 服务身份 + 数据契约自检
python3 service.py --run-day        # 回放当天日志，生成交付物到 out/
python3 service.py                 # 启动 HTTP 服务（默认 8000 端口）
```

`--run-day` 在 `out/` 写出：

- `notices.md` —— 给游客/领队的清楚变更通知；
- `settlement.md` —— 给村民与场馆可核对的逐笔收益与分账；
- `decisions.md` —— 给管理者的每次调度成立/替代/改约/拒绝的具体原因；
- `schedule.md` —— 最终执行排班；
- `day_result.json` —— 全部结果的机器可读版本。

## HTTP 接口

启动后可用例如 `curl localhost:8000/plan` 查询：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 项目标识 |
| GET | `/plan` | 初始排班、规划决策与规划阶段通知 |
| GET | `/schedule` | 经现场事件修正后的最终排班 |
| GET | `/notices` | 全部游客变更通知 |
| GET | `/settlement` | 逐笔收入、分账与合计（JSON） |
| GET | `/decisions` | 排班与现场的全部决策原因 |
| GET | `/day_result` | 完整结果 |
| POST | `/events` | 上报一条或多条现场事件（按 id 幂等） |

POST `/events` 示例（可传单个事件对象或数组）：

```json
{
  "id": "EV-9001",
  "at": "14:00",
  "type": "checkin",
  "group": "G-24",
  "resource": "herb-01",
  "content": "herb-tasting",
  "scheduled_start": "15:30",
  "actual": 15
}
```

## 架构与测试

`coordination/` 包内部分层：

- `model.py`：时间/金额约定、步行图最短路（Dijkstra）、加载校验；
- `planner.py`：授权与禁忌过滤、冲突/容量判定、同内容改约与内容替代、
  步行/无障碍/封闭/天气约束；
- `engine.py`：按时间回放操作日志，迟到顺延、停演释放容量、改道重排、
  取消释放、离线核销、按 id 幂等去重；
- `settlement.py`：只对有效核销按实际人数与申报份额整数分配；
- `reports.py`：三类交付物与 JSON 结果。

事件日志是唯一事实源，每次请求由日志确定性重放，因此并发上报与重试
可由 id 直接幂等。

执行 `python3 -m unittest discover -s tests -v` 可核对全部契约。

样本中的人物、团体、收款方均为示例数据，不含真实个人资料、业务凭据
或生产连接信息。
