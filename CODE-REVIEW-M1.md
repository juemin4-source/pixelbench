# 代码审查报告 — PixelBench M1

审查对象：`bench_server.py`、`bench.html`、`asset_tool.py`、`spec.json`、测试脚本
方法：安全专项审查 + 逻辑正确性审查 + 前端专项审查（三路独立），**之后完整重读全部代码做第二轮复核**
结论提交：`eeb330e`（第一轮）、`3fee453`（第二轮）

## 两轮结果

| 轮次 | Blocker | Major | Minor/Nit | 验证 |
|---|---|---|---|---|
| 第一轮（三路专项） | 1 | 11 | 9 | 15 项回归 |
| 第二轮（完整重读复核） | 0 | 3 | 4 | 14 + 8 项 |
| **合计** | **1** | **14** | **13** | **41 项全 PASS** |

**第二轮的价值**：第一轮靠"分配模块给别人看"，第二轮靠"自己从头重读一遍"。第二轮发现的全是第一轮漏掉的**跨模块缺陷**——它们不在任何单个文件的视野里：

- `adopted` 是「个数」，前端拿它当「槽位集合」用（服务端 ↔ 前端契约错配）
- `normalize()` 写 `normalized/`，`pack()` 读根目录（S5 ↔ S6 流水线断裂）
- `normalize()` 左对齐，`foot_y()` 只找中央列带（S5 ↔ S7 互相打架）

教训：**并行分工审查抓不到跨模块缺陷**，必须有一遍单人对全链路的完整重读。


## Blocker

**B1｜采纳按钮提交整个候选数组**（`bench.html` adopt 处理器）

`body: JSON.stringify({ candidate: cand })` 里的 `cand` 是该帧**全部候选的数组**，不是用户点中的那张。服务端 `os.path.join(..., cand)` 收到 list 直接 `TypeError` → 500。这是工作台主流程（点候选=采纳），完全不可用。

根因：`candFile` 参数只写进了 `state.selCand`，POST 时没用它。

修：提交 `candFile`；按钮仅在该候选存在时渲染；点击即 disable 防重复提交。

**为什么之前没发现**：`ui_check.mjs` 只测到"候选出现"就结束了，没点候选。已补真实点击测试。

## Major

| # | 位置 | 问题 | 修法 |
|---|---|---|---|
| M1 | `bench.html` 三处切换点 | `selCand` 从不清空，切动画/切帧后点帧体会带着旧候选值发出，造成 404 或**跨动画错引** | 项目/实体/动画三处重置 + 帧体点击不再复用陈旧值 |
| M2 | `selectFrame` / `addFrame` | `st.frames[state.anim]` 在 `status_data` 为 null 时抛 TypeError，async 处理器无 catch → 静默失败 | 可选链 + 提前 return |
| M3 | `api()` | fetch 无超时，一次挂死请求让轮询**永久停摆**（catch 静默，用户无感知） | `AbortSignal.timeout(8000)` |
| M5–M9 | `bench.html` 共 9 处 | 项目名/实体名/参考图名/候选名/校验报告文本**裸插 innerHTML**；校验报告文本由服务端生成、可能内嵌路径，是最危险注入面 | 新增 `esc()`，全部包住 |
| M10 | `FB()` | 裸拼相对路径，文件名含 `/` 会变子路径、含 `?` 截断 query | 逐段 `encodeURIComponent` |
| M11 | 底部 4 个按钮 | 未选实体时 `EB()` 拼出 `/entities/null` → 404 → 静默失败 | `needEntity()` 守卫 |
| M4 | 服务端 4 处 | 路径穿越：`files/` 静态路由、refs 上传、adopt、参考图路径都是 `os.path.join` 裸拼 | 新增 `safe_join()`，统一校验不逃出 base |
| M5s | `generate` 路由 | `int(seed)`/`int(steps)` 遇非法输入抛 500；`anim` 不校验存在性 | 白名单 + 类型校验，非法返回 400 |
| M6s | `save_tasks` | 非原子写，并发下可产生半截 JSON；下次启动 `json.load` 直接抛异常**让服务起不来** | 临时文件 + `os.replace` + 锁；加载失败降级为空表 |
| M7s | `export` 路由 | 归档内路径未校验 | 显式拒绝 `..`/绝对路径（防 zip-slip） |
| M8s | `spec` 更新 | 动画名不校验，可注入非法目录名 | 正则 `[A-Za-z_][A-Za-z0-9_]*` |

## 第二轮复核新发现（3 Major + 4 Minor）

**R1｜采纳帧号不连续时前端误标**（Major，跨服务端↔前端）

`entity_state` 返回的 `adopted` 是**采纳个数**，前端却用 `i < adopted` 判断"第 i 帧已采纳"。
采纳 0 和 2、跳过 1 时，`adopted=2` → 前端把帧 0、1 都当已采纳 → 渲染不存在的 `idle_001.png` → 404 破图。

修：服务端同时返回 `adopted_slots`（真实帧号列表），前端按集合判定；`max_frame` 同步修正。

**R2｜S5 的产物根本没被 S6 消费**（Major，流水线断裂）

`normalize()` 输出到 `<entity>/normalized/`，而 `pack()` 用 `frames_of(entity_dir)` 读**实体根目录**。
→ 归一化（裁剪、对齐脚底、居中）全部白做，打包出来的图集用的是未归一化的原始帧。

修：`pack()` 优先使用 `normalized/`，并在 index 里写明 `source_dir` 便于排查。

**R3｜`normalize()` 左对齐，`foot_y()` 只找中央列带**（Major，S5 与 S7 互相打架）

`normalize()` 把裁剪结果贴在 `x=0`（左对齐）。若角色原本靠画布左缘，归一化后内容全在左 20%，
而 `foot_y()` 只看中央 30–70% 列带 → 返回 `None` → `validate()` 对**完全正常的帧**误报
`no central foot anchor`。附加后果：多帧播放时角色横向漂移，而 S7 的 silhouette IoU 检查不出来。

修：改为水平居中；`normalize()` 返回真实的 `foot_y`（原来返回的是粘贴偏移）；`foot_y()` 加整幅图回退。

**R4｜失败任务永久卡在 running**（Minor）

`mock_run` / `track_task` 线程无异常保护：任何异常（如目录被外部删除）都让任务永远停在 `running`，
前端一直显示"生成中"，用户无从知晓。

修：统一 `try/except` 落盘为 `failed` + `error`。**这一条是第一轮"任务失败无落盘"缺口的实际修复。**

**R5｜归档帧号正则位数不一致**（Minor）：normalize 路由用 `\d{3}`，`frames_of()` 用 `\d{2,4}` → 统一。

**R6–R7｜favicon 404、末尾缺"追加下一帧"空槽**（Nit）：采纳完最后一帧后没有可点的槽位，加回末尾空槽。

## 修复过程中新发现并修掉的问题


**M12｜typing 守卫过宽导致轮询永久停摆**（我按 M3 建议改动时引入的回归，浏览器实测抓到）

把守卫改成"焦点在 `#fparam` 内即暂停"后，点一次「生成」按钮→按钮获得焦点→被判定为"正在输入"→轮询**永远不再重渲染**，候选生成了也不显示。

修：守卫需同时满足"在 `#fparam` 内"**且**"是 INPUT/TEXTAREA/SELECT"；再加"任务结束后 4 秒内强制刷新"兜底（避免 `hasActive` 刚翻转那一轮漏掉结果）。

教训：静态审查给出的"更严格"建议可能引入功能回归 —— 所以每条修复都必须过真实浏览器验证，不能只靠单测。

## 未修的 Minor / Nit（记录在案）

- 缩略图 `?t=Date.now()` 每轮都变，有活跃任务时整批重下（量小，暂留）
- 点候选即采纳、无确认（有意设计，建议后续加 300ms 可撤销 toast）
- `selectProject` / `selectEntity(null)` 面板复位代码重复两份，可抽 `resetPanels()`
- boot 阶段连拉 3 次 `/api/projects`
- 服务端已有能力的**脚底漂移检查**（`foot_anchor_tolerance_px=6`）、**统一身高 512**、**任务自动找回**仍未实现（M1 已知缺口，README 已如实标注）

## 验证证据

```
test_review_fixes.py    15/15 PASS   路径穿越×3、类型校验×4、采纳链路×3、S5/S6/S7、原子写
test_review_fixes2.py   14/14 PASS   不连续采纳、pack 走 normalized、居中、底边对齐、foot_y 回退
e2e_test.py             PASS         原全流程无回归
ui_smoke.mjs            8/8 PASS     真实浏览器点击：生成→点候选采纳→S5→S6→S7，零 console 错误
                        ─────────
                        41 项全 PASS
```

## 未修的 Minor / Nit（记录在案）

- 缩略图 `?t=Date.now()` 每轮都变，有活跃任务时整批重下（量小，暂留）
- 点候选即采纳、无确认（有意设计，建议后续加 300ms 可撤销 toast）
- `selectProject` / `selectEntity(null)` 面板复位代码重复两份，可抽 `resetPanels()`
- boot 阶段连拉 3 次 `/api/projects`
- **脚底漂移检查**（`foot_anchor_tolerance_px=6`）仍只在 spec 里、没有代码——现在 `foot_y()` 已是可信输入，实现它只差十来行
- **统一身高 512**、**任务自动找回**（重启后 `recovered` 无人管）仍未实现

注意：R4 修好后，"任务失败"不再是静默黑洞；但**重启后 `recovered` 任务仍无人重新拉取** `/history`，这是独立缺口。

## 建议下一步

1. 实现 S7 脚底漂移检查（`foot_y()` 已修好，阈值早已在 spec 里）
2. 重启恢复：`recovered` 任务重新轮询 ComfyUI `/history`，找不到再判 failed
3. 真实模型出图后验证帧间一致性 —— 这才是 S5–S7 阈值是否合理的唯一判据
4. **测试隔离**：`ui_smoke.mjs` 与 `e2e_test.py` 会互相干扰（前者依赖磁盘状态、后者重置 `workspaces`）。
   建议各自使用独立项目前缀，并在套件入口清空服务端任务表

