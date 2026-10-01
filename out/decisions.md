# 调度决策记录（2026-09-16）

记录每一次排班成立、替代、改约、拒绝与现场处置的具体原因，可供管理审计。

## 1. [排班阶段] 《摩苏昆说唱片段》排入 摩苏昆传承人（示例甲）（成立）

- 对象：G-18
- 判定码：`accepted`
- 关联排班：B-001
- 来源：group=G-18, index=0, content=mosukun-frag, resource=storyteller-01, preferred_start=09:00
- 原因：09:00-09:45 按申报成立。

## 2. [排班阶段] 《桦树皮刻画体验》排入 桦树皮工艺坊（成立）

- 对象：G-18
- 判定码：`accepted`
- 关联排班：B-002
- 来源：group=G-18, index=1, content=birchbark-carving, resource=craft-01, preferred_start=10:15
- 原因：10:15-11:15 按申报成立。

## 3. [排班阶段] 《坡地认药采药》替换为《道地药材文化讲解》（替代安排）

- 对象：G-18
- 判定码：`content_substituted`
- 关联排班：B-003
- 来源：group=G-18, index=2, content=herb-walk, resource=herb-02, preferred_start=13:30
- 原因：原项目无法安排：场地“北药辨识坡地”不支持轮椅通行（田埂坡道，轮椅无法进入）。已改约至道地药材文化展馆 13:30 开始的《道地药材文化讲解》。

## 4. [排班阶段] 《民居与岁时民俗体验》排入 民俗体验院落（示例住户）（成立）

- 对象：G-18
- 判定码：`accepted`
- 关联排班：B-004
- 来源：group=G-18, index=3, content=folk-experience, resource=folk-01, preferred_start=15:30
- 原因：15:30-16:30 按申报成立。

## 5. [排班阶段] 拒绝排入《祭祀长歌（内部仪式）》（拒绝）

- 对象：G-18
- 判定码：`taboo`
- 来源：group=G-18, index=4, content=sacred-song, resource=storyteller-01, preferred_start=16:00
- 原因：《祭祀长歌（内部仪式）》仅限氏族内部祭祀场合，族规禁止向旅游团队演唱或录制

## 6. [排班阶段] 《摩苏昆说唱片段》改由 摩苏昆传承人（示例乙） 承接（改约）

- 对象：G-24
- 判定码：`resource_reassigned`
- 关联排班：B-005
- 来源：group=G-24, index=0, content=mosukun-frag, resource=storyteller-01, preferred_start=09:00
- 原因：原申报 摩苏昆传承人（示例甲） 09:00 不可安排：承载量 1 团队 已被占用（G-18 09:00-09:45）。新安排 09:30 开始。

## 7. [排班阶段] 拒绝排入《祭祖叙事片段》（拒绝）

- 对象：G-24
- 判定码：`not_authorized`
- 来源：group=G-24, index=1, content=ritual-fragment, resource=storyteller-01, preferred_start=11:00
- 原因：《祭祖叙事片段》需要单独授权，本年度未签署对外讲述授权

## 8. [排班阶段] 《桦皮船制作体验》排入 桦皮船林间制作点（成立）

- 对象：G-24
- 判定码：`accepted`
- 关联排班：B-006
- 来源：group=G-24, index=2, content=birchbark-boat, resource=craft-02, preferred_start=13:30
- 原因：13:30-14:30 按申报成立。受道路封闭影响，步行路线需绕行

## 9. [排班阶段] 《北药代茶饮品鉴》排入 道地药材文化展馆（成立）

- 对象：G-24
- 判定码：`accepted`
- 关联排班：B-007
- 来源：group=G-24, index=3, content=herb-tasting, resource=herb-01, preferred_start=15:30
- 原因：15:30-16:10 按申报成立。

## 10. [现场阶段] G-18 现场实到 22 人（已执行）

- 对象：G-18
- 判定码：`headcount_updated`
- 来源：event_id=EV-1001, type=headcount
- 原因：申报人数 22，现场核定 22 人，后续容量与分账以实到人数为准。

## 11. [现场阶段] G-24 现场实到 16 人（已执行）

- 对象：G-24
- 判定码：`headcount_updated`
- 来源：event_id=EV-1002, type=headcount
- 原因：申报人数 16，现场核定 16 人，后续容量与分账以实到人数为准。

## 12. [现场阶段] 核销成功：G-18《摩苏昆说唱片段》（已执行）

- 对象：G-18
- 判定码：`checked_in`
- 关联排班：B-001
- 来源：event_id=EV-2001, type=checkin
- 原因：实际参与 22 人，按此人数结算。

## 13. [现场阶段] G-24 迟到 40 分钟（已执行）

- 对象：G-24
- 判定码：`late_arrival`
- 关联排班：B-005
- 来源：event_id=EV-2005, type=late_arrival
- 原因：《摩苏昆说唱片段》开始时间顺延至 10:10，结束时间相应顺延，仍在资源开放时段内。

## 14. [现场阶段] 核销成功：G-24《摩苏昆说唱片段》（已执行）

- 对象：G-24
- 判定码：`checked_in`
- 关联排班：B-005
- 来源：event_id=EV-2006, type=checkin
- 原因：实际参与 16 人，按此人数结算。

## 15. [现场阶段] 核销成功：G-18《桦树皮刻画体验》（已执行）

- 对象：G-18
- 判定码：`checked_in`
- 关联排班：B-002
- 来源：event_id=EV-2002, type=checkin
- 原因：实际参与 22 人，按此人数结算。

## 16. [现场阶段] 桦皮船林间制作点 自 12:40 停演（已执行）

- 对象：全体/资源方
- 判定码：`resource_stopped`
- 来源：event_id=EV-3001, type=stop
- 原因：原因：阵雨延后，林间步道持续湿滑，户外制作点下午场停演。受影响排班：G-24 13:30。相关场次容量即时释放，不计收入，等待改道或取消。

## 17. [现场阶段] G-24 改道至桦树皮工艺坊（已执行）

- 对象：G-24
- 判定码：`rerouted`
- 关联排班：B-R008
- 来源：event_id=EV-3002, type=reroute
- 原因：原《桦皮船制作体验》(桦皮船林间制作点 13:30) 取消；原因：户外停演，改至室内工艺坊同主题体验。新安排《桦树皮刻画体验》13:40 开始，按新项目价格与实际参与人数结算。

## 18. [现场阶段] 核销成功：G-18《道地药材文化讲解》（已执行）

- 对象：G-18
- 判定码：`checked_in`
- 关联排班：B-003
- 来源：event_id=EV-2003, type=checkin
- 原因：实际参与 21 人，按此人数结算。

## 19. [现场阶段] 核销成功：G-24《桦树皮刻画体验》（已执行）

- 对象：G-24
- 判定码：`checked_in`
- 关联排班：B-R008
- 来源：event_id=EV-2007, type=checkin
- 原因：实际参与 16 人，按此人数结算。

## 20. [现场阶段] G-24 取消《北药代茶饮品鉴》（已执行）

- 对象：G-24
- 判定码：`cancelled`
- 关联排班：B-007
- 来源：event_id=EV-3003, type=cancel
- 原因：取消方：group_lead；原因：团队提前返程。容量即时释放给其他团队，该场次不计费、不参与分账。

## 21. [现场阶段] 核销成功：G-18《民居与岁时民俗体验》（已执行）

- 对象：G-18
- 判定码：`checked_in`
- 关联排班：B-004
- 来源：event_id=EV-2004, type=checkin
- 原因：实际参与 20 人，按此人数结算。该核销于 15:50 离线记录，16:20 网络恢复后同步，以实际发生时间入账。

## 22. [现场阶段] 重复事件 EV-2004 已忽略（抑制）

- 对象：G-18
- 判定码：`duplicate_suppressed`
- 来源：event_id=EV-2004, type=checkin
- 原因：事件 id 已处理过（离线终端网络恢复后的重试），不重复占用容量、不重复计费。

## 幂等抑制

- EV-2004：duplicate_event_id（不改变容量、不重复计费）

