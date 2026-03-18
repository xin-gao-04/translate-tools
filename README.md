# translate-comments

将 C/C++ 源代码中的英文注释翻译为中文，使用本地 Ollama 模型，支持 CLI 和 Electron 图形界面。

---

## 目录

- [功能特性](#功能特性)
- [安装](#安装)
- [CLI 使用指南](#cli-使用指南)
- [GUI 使用指南](#gui-使用指南)
- [开发](#开发)
- [项目结构](#项目结构)

---

## 功能特性

- **文件翻译**：扫描 C/C++ 文件，提取英文注释，逐条翻译为中文；连续 `//` 会合并为同一翻译单元并携带源码上下文
- **严格阻断**：若某个文件存在未翻译注释，流程会停止在该文件，前端禁止直接进入写回阶段
- **流式输出**：大段注释按句子分块翻译，实时显示进度
- **文本翻译**：支持粘贴任意文本直接翻译（大段文本）
- **头文件注释生成**：分析 `.h`/`.hpp`/`.inl`/`.ipp` 中的函数和变量声明，自动生成可配置的标准 Doxygen 注释
- **实现定位与差异预览**：函数可反查 `.cpp`/`.cc`/`.cxx`/`.inl`/`.ipp` 实现片段，并在写入前预览 unified diff
- **多输出模式**：`inplace` 直接覆写、`stdout` 打印、`diff` 显示差异
- **可扩展架构**：通过 `@register([".ext"])` 装饰器添加新语言解析器

---

## 安装

### 前置要求

- Python 3.11+
- [Ollama](https://ollama.ai) 已安装并运行
- 已拉取翻译模型（推荐 `qwen2.5:7b`）

```bash
# 拉取推荐模型
ollama pull qwen2.5:7b
```

### 安装 Python 包

```bash
# 克隆仓库
git clone <repo-url>
cd translate-tool

# 创建虚拟环境（推荐）
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装（可编辑模式）
pip install -e .
```

---

## CLI 使用指南

### 基本用法

```bash
# 翻译单个文件（输出到终端）
translate-comments myfile.cpp

# 翻译整个目录
translate-comments ./src/

# 直接覆写原文件（谨慎使用！）
translate-comments ./src/ --output inplace

# 显示差异（不修改文件）
translate-comments ./src/ --output diff

# 翻译后写回（需先翻译，再应用）
translate-comments ./src/ --output stdout
```

### 常用选项

| 选项 | 短选项 | 默认值 | 说明 |
|------|--------|--------|------|
| `--output` | `-o` | `stdout` | 输出模式：`stdout` / `inplace` / `diff` |
| `--model` | `-m` | `qwen2.5:7b` | Ollama 模型名称 |
| `--host` | | `http://localhost:11434` | Ollama 服务地址 |
| `--ext` | `-e` | 自动（C/C++） | 指定文件扩展名（可多次使用） |
| `--dry-run` | `-n` | `false` | 仅扫描，不翻译 |
| `--verbose` | `-v` | `false` | 显示详细日志 |

### 其他命令

```bash
# 检查 Ollama 连接
translate-comments --check

# 检查特定 host/model
translate-comments --check --host http://192.168.1.100:11434 --model qwen2.5:14b

# 列出所有支持的文件扩展名
translate-comments --list-parsers

# 只翻译 .hpp 文件
translate-comments ./include/ --ext .hpp

# 使用更强的模型
translate-comments ./src/ --model qwen2.5:14b --output inplace

# 翻译并输出到文件
translate-comments ./src/ --output stdout > translations.txt
```

### 示例：翻译单文件预览

```bash
$ translate-comments test_sample.cpp --output stdout

# test_sample.cpp — 5 条英文注释

# L3:  // Initialize the widget
→ // 初始化控件

# L12: /* Compute the bounding box for all visible items */
→ /* 计算所有可见项目的包围盒 */

# L28: /**
#       * @brief Serialize object to JSON format.
#       * @param indent  Number of spaces for indentation.
#       * @return JSON string representation.
#       */
→ /**
   * @brief 将对象序列化为 JSON 格式。
   * @param indent  缩进的空格数。
   * @return JSON 字符串表示。
   */
```

### 启动 API 服务（GUI/前端使用）

```bash
# 默认端口 8765
python -m translate_comments.api

# 指定端口
python -m translate_comments.api --port 9000

# 开发模式（自动重载）
python -m translate_comments.api --reload
```

---

## GUI 使用指南

### 启动（开发模式）

需要两个终端，或使用 `npm run dev`（会自动同时启动）：

```bash
# 方法一：一条命令（推荐）
cd frontend
npm install          # 首次需要
npm run dev          # 同时启动 Vite + Electron + Python 后端

# 方法二：手动启动
# 终端 1 — Python 后端
source .venv/bin/activate
python -m translate_comments.api

# 终端 2 — 前端
cd frontend
npm run dev
```

### 三个功能页面

#### 📁 文件翻译（Files）

1. 拖放文件/文件夹到左侧面板，或点击「文件夹」/「文件」按钮添加
2. 在工具栏设置 Ollama 地址和模型
3. 点击「开始翻译」
4. 点击左侧文件查看翻译进度（双击表格行可展开查看上下文）
5. 若有某条注释翻译失败，表格会标红并阻止写回；需要先解决未翻译项
6. 全部成功后，若非 `inplace` 模式，点击「写回文件」

#### ✏️ 文本翻译（Text）

1. 在左侧文本框粘贴任意英文内容
2. 点击「翻译」，右侧实时显示翻译结果
3. 点击「复制」复制结果

#### 🔧 注释生成（Header）

1. 拖放头文件或整个文件夹，左侧会生成头文件列表
2. 点击左侧文件，在中间面板查看函数和变量声明
3. 勾选需要生成注释的对象（可全选/全取消/仅无注释）
4. 在顶部配置 `@brief` / `@param` / `@return`、作者、日期格式和自定义 tag
5. 点击「生成注释」，AI 会结合声明和已定位到的实现片段生成 Doxygen 注释
6. 点击「Diff 预览」，确认 unified diff 后再点击「写入文件」

---

## 开发

### 项目依赖

```bash
# Python 依赖（pyproject.toml）
pip install -e ".[dev]"

# Node 依赖
cd frontend && npm install
```

### 运行测试

```bash
pytest tests/
```

### 打包发布

```bash
cd frontend

# macOS (.dmg)
npm run electron:build

# Windows (.exe, cross-compile from macOS 需要 Wine)
# 推荐在 Windows 上直接 npm run electron:build
```

### 添加新语言解析器

在 `translate_comments/parsers/` 目录下创建新文件：

```python
from translate_comments.parsers import register
from translate_comments.parsers.base import BaseParser, Comment

@register([".py", ".pyw"])
class PythonParser(BaseParser):
    def extract_comments(self, source: str) -> list[Comment]:
        # 实现注释提取逻辑
        ...
```

---

## 项目结构

```
translate-tool/
├── translate_comments/         # Python 核心包
│   ├── __init__.py
│   ├── api.py                  # FastAPI 后端
│   ├── cli.py                  # Click CLI 入口
│   ├── detector.py             # 英文检测
│   ├── processor.py            # 主处理流程
│   ├── scanner.py              # 文件扫描
│   ├── splitter.py             # 文本分块
│   ├── translator.py           # Ollama 翻译客户端
│   ├── header_parser.py        # C++ 头文件函数解析
│   ├── comment_generator.py    # Doxygen 注释生成
│   └── parsers/
│       ├── __init__.py         # 解析器注册表
│       ├── base.py             # BaseParser 抽象类
│       └── cpp.py              # C/C++ 解析器
│
├── frontend/                   # Electron + React 前端
│   ├── electron/
│   │   ├── main.cjs            # Electron 主进程
│   │   └── preload.cjs         # 预加载脚本
│   └── src/
│       ├── App.tsx             # 应用入口（导航）
│       ├── api.ts              # API 客户端
│       ├── types.ts            # TypeScript 类型
│       ├── components/
│       │   ├── NavTabs.tsx     # 页面导航标签
│       │   ├── Toolbar.tsx     # 顶部工具栏
│       │   ├── FilePanel.tsx   # 文件列表面板
│       │   ├── CommentTable.tsx # 注释对比表格
│       │   └── BottomBar.tsx   # 底部状态栏
│       ├── pages/
│       │   ├── FilePage.tsx    # 文件翻译页
│       │   ├── TextPage.tsx    # 文本翻译页
│       │   └── HeaderPage.tsx  # 头文件注释生成页
│       └── styles/
│           └── app.css         # 全局样式
│
├── pyproject.toml              # Python 包配置
├── requirements.txt            # pip requirements
├── .gitignore
└── README.md
```

---

## API 接口文档

后端 FastAPI 服务运行在 `http://127.0.0.1:8765`：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/scan` | 扫描文件 |
| GET | `/api/check` | 检查 Ollama 连接 |
| POST | `/api/comments` | 获取文件注释（含上下文）|
| POST | `/api/apply` | 写回翻译到文件 |
| POST | `/api/analyze-header` | 解析头文件函数 |
| POST | `/api/apply-comments` | 写入生成的注释 |
| WS | `/ws` | 文件翻译流式事件 |
| WS | `/ws/translate-text` | 文本翻译流式事件 |
| WS | `/ws/generate-comments` | 注释生成流式事件 |
