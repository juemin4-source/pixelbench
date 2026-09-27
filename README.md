# PixelBench

**AI 2D 游戏资产工作台。** 用本地 Qwen-Image 2.1（ComfyUI）把「一个角色的动画」从一句提示词做到可直接进引擎的 sprite sheet + 索引。

> **PixelBench 是独立工具，不隶属于任何单个游戏。** 游戏（如《永蚀 / Ever Eclipse》）只是工作台里项目列表中的一项，工作台本身可服务任意 2D 项目。

## 它解决什么

AI 出图快，但**出一套能用的动画资产**很难，卡在四件事：

| 痛点 | PixelBench 的做法 |
|---|---|
| 单张好看，**帧间不一致** | 参考图锚定：风格圣经 + 基础精灵 + 上一帧一起进 prompt |
| 脚底/身高**飘** | S5 归一化：按脚底锚点对齐到 `ground_y`，缩放到统一身高 |
| 厂商图片**不能直接用** | S6 打包：统一画布 + sheet + `index.json`（引擎按索引切图） |
| 出了错**靠肉眼查** | S7 校验：不透明比、剪影 IoU、脚底漂移，出问题点报告定位到帧 |

## 快速开始

需要 Python 3.11+（只用标准库 + Pillow/numpy 做图像处理）和本地 ComfyUI。

```powershell
python bench_server.py                 # http://127.0.0.1:8321
python bench_server.py                 # 需要 PIXELBENCH_MOCK=1 时先设环境变量

# 无 GPU 也能跑通全流程（合成帧代替出图）
$env:PIXELBENCH_MOCK="1"; python bench_server.py
```

浏览器打开 http://127.0.0.1:8321 ：
1. 建**项目**（如 `ever_eclipse`）
2. 建**实体**（如 `knight`，默认动画 idle/walk/atkA）
3. 上传**参考图**（风格圣经 `style_*.png`、基础精灵 `base_*.png`）
4. 点帧槽 → 写 prompt → 勾参考图 → **生成**（每帧 3 张候选）
5. 点子图**采纳** → 依次 S5 归一化 / S6 打包 / S7 校验 → **导出 zip**

## 产物结构

```
workspaces/<project>/<entity>/
├── spec.json                实体规格（动画列表/fps/备注）
├── refs/                    风格圣经、基础精灵
├── candidates/<anim>/<i>/   候选（最多保留 3 张）
├── <anim>_NNN.png           采纳帧（扁平命名 = 隐式帧序）
├── normalized/              S5 产物
├── sheets/                  S6 产物：<entity>_<anim>.png + <entity>_index.json
└── validation.json          S7 报告
```

导出的 `index.json` 就是引擎接口，每个动画给出 sheet 路径、帧宽高、帧数和隐式顺序。

## 架构

```
浏览器（单页 vanilla JS，零构建）
   │ fetch /api/*  +  1.2s 轮询
   ▼
Python stdlib ThreadingHTTPServer :8321（零第三方 web 依赖）
   ├── /api/projects ...                   项目 / 实体 / 参考图 / 帧槽位
   ├── /api/tasks                          任务进度（落盘 tasks.json，重启不丢）
   ├── normalize | pack | validate         纯 CPU，ComfyUI 挂了也能用
   └── export                              zip 流式下载
   ▼
ComfyUI 127.0.0.1:8188（唯一 GPU 依赖，纯 HTTP：/prompt /history /view /upload/image）
```

**关键取舍**
- 前端零构建：单 HTML + 原生 JS，改完刷新即生效
- 全部状态落盘：workspace 目录就是数据库，刷新页面不丢
- CPU/GPU 分离：ComfyUI 离线时顶部横幅提示，S5–S7 和 UI 照常可用

## 命令行用法（不进 UI）

```powershell
python asset_tool.py normalize <entity_dir>   # S5 脚底锚定 + 统一身高
python asset_tool.py pack <entity_dir> <out>  # S6 sheet + index.json
python asset_tool.py validate <entity_dir>    # S7 校验报告
```

## 规格

`spec.json` 锁定画布与判定阈值（HD 手绘/厚涂档）：

| 项 | 值 |
|---|---|
| 帧画布 | 1024 × 1024 |
| 脚底基准线 | y = 896 |
| 统一身高 | 512 px |
| 校验阈值 | 脚底漂移 ≤ 6px、不透明占比 2%–85%、剪影 IoU ≥ 0.55 |

## 测试

```powershell
$env:PIXELBENCH_MOCK="1"; python bench_server.py   # 另一个终端
python e2e_test.py                                 # 全流程 API E2E（零依赖）
node ui_check.mjs                                  # 浏览器 UI 验证（需 npm i -D playwright-core）
```

## 文档

- [PRD-PixelBench.md](PRD-PixelBench.md) —— 需求、方案、交互、上线计划、Git/PR 约定

## 状态

M1 已交付：项目/实体/参考图/帧槽位/生成/采纳/S5/S6/S7/导出 全链路可用（mock 模式 E2E 与浏览器验证通过）。
真实模型出图质量验证待 GPU 空闲时补。
