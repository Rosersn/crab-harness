# Crab

[English](./README.md) | 中文

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

Crab 是一个多租户 AI Agent SaaS 平台，编排**子 Agent**、**记忆**和**沙箱**来完成各种任务 —— 由**可扩展技能**驱动。

基于三层架构（Harness → Platform → App），Crab 开箱即用地提供 JWT 认证、用户级 MCP/技能/记忆隔离、E2B 云端沙箱，以及 PostgreSQL + Redis 持久化。

---

## 目录

- [架构](#架构)
- [快速开始](#快速开始)
  - [配置](#配置)
  - [运行应用](#运行应用)
- [核心功能](#核心功能)
- [嵌入式 Python 客户端](#嵌入式-python-客户端)
- [文档](#文档)
- [许可证](#许可证)

## 架构

```
crab-harness/
├── backend/
│   ├── packages/
│   │   ├── harness/crab/          # Agent 框架（租户无关）
│   │   └── platform/crab_platform/ # 多租户编排层
│   ├── app/gateway/                # FastAPI Gateway API（端口 8001）
│   └── tests/
├── frontend/                       # Next.js 前端（端口 3000）
├── docker/                         # Docker Compose 配置
├── skills/                         # Agent 技能（公共 + 自定义）
└── config.yaml                     # 主配置文件
```

**三层架构**：

| 层 | 包名 | Import 前缀 | 职责 |
|----|------|-------------|------|
| **Harness** | `crab-harness` | `crab.*` | 租户无关的 Agent 框架：编排、工具、沙箱、模型、MCP、技能、配置 |
| **Platform** | `crab-platform` | `crab_platform.*` | 多租户编排：认证、数据库、Redis、请求上下文、E2B 沙箱、BOS 存储 |
| **App** | — | `app.*` | FastAPI Gateway API，JWT 认证，LangGraph SDK 兼容流式端点 |

**依赖方向**：App → Platform → Harness（CI 强制保障）

**基础设施**：
- **Gateway**（端口 8001）：FastAPI + 嵌入式 LangGraph Agent 运行时
- **Frontend**（端口 3000）：Next.js 前端
- **Nginx**（端口 2026）：统一反向代理
- **PostgreSQL**：用户、线程、消息、记忆、MCP/技能配置
- **Redis**：线程锁、限流、会话管理
- **BOS**（百度对象存储）：文件上传和技能存档
- **E2B**：云端沙箱执行（VM 级隔离）

## 快速开始

### 前置要求

- Python 3.12+
- Node.js 22+
- Docker（用于 PostgreSQL 和 Redis）

### 配置

1. 复制示例配置：
   ```bash
   cp config.example.yaml config.yaml
   ```

2. 设置环境变量（在项目根目录创建 `.env`）：
   ```bash
   # 必需：至少一个 LLM API Key
   OPENAI_API_KEY=your-key-here

   # 平台层（多租户）
   CRAB_DATABASE_URL=postgresql+asyncpg://crab:crab@localhost:5432/crab
   CRAB_REDIS_URL=redis://localhost:6379/0
   CRAB_JWT_SECRET=your-secret-key

   # 可选：E2B 云端沙箱
   E2B_API_KEY=your-e2b-key
   ```

3. 在 `config.yaml` 中配置模型

### 运行应用

#### 方式一：Docker（推荐）

```bash
make check     # 检查系统要求
make install   # 安装所有依赖
make dev       # 启动所有服务
```

应用将在 `http://localhost:2026` 可用。

#### 方式二：仅后端

```bash
cd backend
make infra     # 启动 PostgreSQL + Redis
make install   # 安装 Python 依赖
make dev       # 启动 Gateway API（端口 8001）
```

## 核心功能

- **多租户隔离**：JWT 认证，用户级数据隔离，PostgreSQL 持久化
- **技能系统**：公共技能 + 用户自定义技能（BOS 存储）
- **子 Agent**：内置通用和 Bash 专家 Agent，并发执行
- **沙箱**：E2B 云端 VM 隔离，虚拟路径系统，自动暂停/恢复
- **长期记忆**：LLM 驱动的事实提取，用户级 PG 存储
- **MCP 集成**：多服务器支持，用户级配置，OAuth 管理
- **文件上传**：BOS 存储，自动文档转换（PDF/Office）

## 嵌入式 Python 客户端

```python
from crab.client import CrabClient

client = CrabClient()
response = client.chat("你好", thread_id="my-thread")
```

## 文档

详细文档见 `backend/docs/` 目录。

## 许可证

[MIT License](./LICENSE)
