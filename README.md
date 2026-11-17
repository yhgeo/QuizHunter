# 猎答 QuizHunter

识别电脑屏幕窗口 → 离线 OCR 识题 → 本地题库模糊匹配 →（可选）AI 兜底。
Windows 桌面工具，GUI + 命令行双入口。截图与 OCR 全程本地，只有题库未命中且你开启了模型时才联网。

参考 `QuizSniper`（yhgeo/QuizSniper）的核心思路重新实现，独立代码库。

## 功能

- **窗口识别**：枚举所有可见窗口（DWM 可见边界，避开阴影边框偏移），支持多显示器。
- **两种定位**：整窗/全屏、跟随窗口（相对窗口左上角的区域，窗口移动自动跟随）、屏幕绝对坐标。可视化框选，所见即所得。
- **离线 OCR + 三档速度**：RapidOCR 本地推理。
  - `极速 fast`：按亮度只跑 1 个变体，合成题图实测 ~690ms；
  - `均衡 balance`（默认）：最多 2 变体，原图够好即早停，实测 ~780ms；
  - `精准 accurate`：多变体投票，实测 ~1880ms。
  - 已关闭方向分类（屏幕文字恒水平）、限制检测边长。**fast 相对 accurate 提速约 2.7×**，三档命中率相同（6/6）。
- **题库优先**：四段式模糊匹配（完全一致 / 包含 / 子串覆盖 / 编辑相似），毫秒级出答案；结果区「匹配题库」卡片直接显示命中的原题、答案与别名，未命中则列出最接近的候选题目。
- **AI 兜底**：题库未命中时先把 OCR 文本发给 OpenAI 兼容模型，失败再退回整图多模态。
- **交互**：全局热键（默认 F8，可在设置里点「录制按键」直接按下组合键自动录入）、连续识题、自动复制、答题记录与命中率统计。
- **题库管理**：检索、增删改、导入导出，只读题库与用户题库分层。
- **全新界面**：单栏识别主界面（答案大字号动态显示、OCR 文本紧随其下不再留白）；顶栏「题库/日志/记录」按钮弹出独立面板，不占主界面空间；「⚙ 选项」打开独立设置页，内含「参数设置 / 配置说明」两页；所有下拉框已修正深色配色（不再白底盖字）。

## 免安装 exe（推荐给没有 Python 的电脑）

项目根目录执行：

```bash
python build_exe.py            # 生成 dist/QuizHunter/（文件夹，启动快）
python build_exe.py --onefile  # 生成 dist/QuizHunter.exe（单文件，拷贝方便）
```

把整个 `dist/QuizHunter/` 文件夹（或单个 exe）拷到别的 Windows 电脑，**双击即用，无需装 Python 和任何依赖**。内置了 Python 解释器、RapidOCR 及全部 onnx 模型。
首次运行会在 exe 同级目录自动生成 `config.json`、`my_bank.json`、`question_banks/`、`logs/`。
建议 Windows 11 + x64；首次启动 OCR 引擎约 0.5~2 秒。

## 源码运行

Windows + Python 3.10+（推荐官方 CPython，自带 tkinter）。

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py          # 图形界面
python cli.py          # 命令行
```

自检（合成题图跑一遍 OCR + 匹配，不联网不截屏）：

```bash
python tools/selftest.py                 # 默认自检
python tools/selftest.py --benchmark     # 额外输出 fast/balance/accurate 三档耗时对比
```

单元测试：

```bash
python -m unittest discover -s tests -v
```

## 使用（GUI）

1. `python run.py` 启动。
2. 「截图来源」选目标窗口 → 点「框选区域」拖出只含题目和选项的矩形 → 确定。
3. 所有配置（模型、匹配阈值、OCR 档位、热键等）都从顶栏「⚙ 选项」按钮进入设置页修改，保存即生效。
   - 设置页有「参数设置」和「配置说明」两页，后者逐项解释每个配置项的含义与推荐值。
   - 只用题库可跳过模型配置；否则在「参数设置 → 模型」填 API Key / Base URL / 模型（可一键拉取模型列表）。
   - 觉得识题慢就在「参数设置 → OCR 识别」把速度档位切到「极速」。
   - 题库/日志/记录从顶栏对应按钮弹出，关闭只是隐藏，不会丢内容。
4. 看到题目后按 `F8` 或点「识别题目」：题库命中直接显示答案；未命中且有模型自动走 AI。
5. 需要粘贴时点「复制答案」，或在选项里开「识别成功自动复制」。

## 题库格式

放在 `question_banks/*.json`（只读资料）或 `my_bank.json`（界面可写）：

```json
{
  "entries": [
    {
      "question": "以下哪项属于典型的有氧运动方式",
      "answer": "B",
      "aliases": ["以下哪项属于典型有氧运动方式"]
    }
  ]
}
```

- `question`：题干（可带选项，程序自动截出题干候选）
- `answer`：答案文本，原样展示
- `aliases`：可选，OCR 常见误识别版本，能显著提升命中率

## 匹配与阈值

| 模式 | 判定 | 说明 |
|---|---|---|
| exact | 规范化后完全相等 | 直接短路 |
| contains | 一方完整包含另一方，短串长度 ≥ `min_contains_length` | 取最长候选，防短题错配 |
| substring | 较短串被匹配块覆盖比例 ≥ `substring_threshold` | 容忍 OCR 漏字/多字 |
| similarity | 编辑相似度 ≥ `similarity_threshold` | 兜底 |

规范化：`NFKC → 去【答案】等标记 → 只留字母数字汉字`。`SequenceMatcher` 一律 `autojunk=False`，避免长中文题干高频字被当噪声丢弃。过短查询（< `min_query_length`）跳过模糊匹配。

## 目录结构

```
QuizHunter/
├── run.py / cli.py              # GUI / CLI 入口
├── config.example.json          # 配置示例（复制为 config.json 后填写）
├── requirements.txt
├── quizhunter/
│   ├── config.py   log.py       # 配置原子读写 / 日志总线
│   ├── screen.py   ocr.py       # 窗口枚举截图 / RapidOCR 多变体
│   ├── matcher.py  bank.py      # 四段匹配 / 题库管理
│   ├── llm.py      solver.py    # AI 客户端 / 答题流水线
│   ├── hotkey.py                # 全局热键
│   └── ui/                      # Tk 界面（app/theme/tasks/region_picker）
├── question_banks/              # 只读题库
├── tools/selftest.py            # 端到端自检
└── tests/test_core.py           # 单元测试
```

## 说明

仅供个人学习与自测使用，使用前请自行确认目标平台的相关规则。
