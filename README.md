# Crab

English | [中文](./README_zh.md)

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

Crab is a multi-tenant AI Agent SaaS platform that orchestrates **sub-agents**, **memory**, and **sandboxes** to do almost anything — powered by **extensible skills**.

Built on a three-layer architecture (Harness → Platform → App), Crab provides JWT authentication, per-user MCP/Skills/Memory isolation, E2B cloud sandboxes, and PostgreSQL + Redis persistence out of the box.

---

## Table of Contents

- [Architecture](#architecture)
- [Quick Start](#quick-start)
  - [Configuration](#configuration)
  - [Running the Application](#running-the-application)
- [Core Features](#core-features)
  - [Multi-Tenant Isolation](#multi-tenant-isolation)
  - [Skills & Tools](#skills--tools)
  - [Sub-Agents](#sub-agents)
  - [Sandbox & File System](#sandbox--file-system)
  - [Long-Term Memory](#long-term-memory)
  - [MCP Integration](#mcp-integration)
- [Embedded Python Client](#embedded-python-client)
- [Documentation](#documentation)
- [License](#license)

## Architecture

```
crab-harness/
├── backend/
│   ├── packages/
│   │   ├── harness/crab/          # Agent framework (tenant-agnostic)
│   │   └── platform/crab_platform/ # Multi-tenant orchestration layer
│   ├── app/gateway/                # FastAPI Gateway API (port 8001)
│   └── tests/
├── frontend/                       # Next.js web interface (port 3000)
├── docker/                         # Docker Compose configs
├── skills/                         # Agent skills (public + custom)
└── config.yaml                     # Main application configuration
```

**Three-Layer Architecture**:

| Layer | Package | Import Prefix | Responsibility |
|-------|---------|---------------|----------------|
| **Harness** | `crab-harness` | `crab.*` | Tenant-agnostic agent framework: orchestration, tools, sandbox, models, MCP, skills, config |
| **Platform** | `crab-platform` | `crab_platform.*` | Multi-tenant orchestration: auth, DB, Redis, request context, E2B sandbox, BOS storage |
| **App** | — | `app.*` | FastAPI Gateway API with JWT auth and LangGraph SDK-compatible streaming |

**Dependency direction**: App → Platform → Harness (strictly enforced by CI)

**Infrastructure**:
- **Gateway** (port 8001): FastAPI with embedded LangGraph agent runtime
- **Frontend** (port 3000): Next.js web interface
- **Nginx** (port 2026): Unified reverse proxy
- **PostgreSQL**: Users, threads, messages, runs, memory, MCP/skill configs
- **Redis**: Thread locks, rate limiting, session management
- **BOS** (Baidu Object Storage): File uploads and skill archives
- **E2B**: Cloud sandbox execution (VM-level isolation)

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 22+
- Docker (for PostgreSQL & Redis)

### Configuration

1. Copy the example config:
   ```bash
   cp config.example.yaml config.yaml
   ```

2. Set up environment variables (create `.env` in project root):
   ```bash
   # Required: At least one LLM API key
   OPENAI_API_KEY=your-key-here

   # Platform layer (multi-tenant)
   CRAB_DATABASE_URL=postgresql+asyncpg://crab:crab@localhost:5432/crab
   CRAB_REDIS_URL=redis://localhost:6379/0
   CRAB_JWT_SECRET=your-secret-key

   # Optional: E2B cloud sandbox
   E2B_API_KEY=your-e2b-key

   # Optional: BOS storage
   CRAB_STORAGE_BACKEND=local  # or "bos" for cloud storage
   ```

3. Configure models in `config.yaml`:
   ```yaml
   models:
     - name: gpt-4o
       use: langchain_openai:ChatOpenAI
       api_key: $OPENAI_API_KEY
       model: gpt-4o
       supports_thinking: false
       supports_vision: true
   ```

### Running the Application

#### Option 1: Docker (Recommended)

```bash
make check     # Check system requirements
make install   # Install all dependencies
make dev       # Start all services (Gateway + Frontend + Nginx + Infra)
```

The application will be available at `http://localhost:2026`.

#### Option 2: Backend Only

```bash
cd backend
make infra     # Start PostgreSQL + Redis
make install   # Install Python dependencies
make dev       # Start Gateway API (port 8001)
```

### Advanced

#### Sandbox Mode

Configure the sandbox provider in `config.yaml`:

```yaml
sandbox:
  # Local execution (default, no isolation)
  use: crab.sandbox.local:LocalSandboxProvider

  # Docker-based isolation
  # use: crab.community.aio_sandbox:AioSandboxProvider

  # E2B cloud VM (requires E2B_API_KEY)
  # use: crab_platform.sandbox:E2BSandboxProvider
```

#### MCP Server

Configure MCP servers in `extensions_config.json`:

```json
{
  "mcpServers": {
    "my-server": {
      "enabled": true,
      "type": "http",
      "url": "https://my-mcp-server.example.com/mcp"
    }
  }
}
```

#### LangSmith Tracing

```bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY=your-key
export LANGSMITH_PROJECT=crab-harness
```

## Core Features

### Multi-Tenant Isolation

- JWT authentication with pluggable auth providers
- Per-user data isolation (threads, memory, MCP configs, skills, uploads)
- `RequestContext` flows through the entire request lifecycle
- PostgreSQL-backed persistence for all user data

### Skills & Tools

- **Public skills**: Shared across all users, loaded from `skills/public/`
- **Custom skills**: Per-user, uploaded via `.skill` archives, stored in BOS
- **Tool groups**: Configurable sets of tools per agent invocation
- **Community tools**: DuckDuckGo search, Tavily, Jina AI, Firecrawl

### Sub-Agents

- Built-in `general-purpose` and `bash` specialist agents
- Concurrent execution with configurable limits (default: 3)
- 15-minute timeout with automatic cleanup

### Sandbox & File System

- **Virtual path system**: Agent sees `/mnt/user-data/{workspace,uploads,outputs}`
- **E2B cloud sandbox**: VM-level isolation, auto-pause/resume, PG-backed lifecycle
- **File uploads**: BOS storage with automatic PDF/Office document conversion
- **Docker sandbox**: Alternative container-based isolation

### Long-Term Memory

- LLM-powered fact extraction and context summarization
- Per-user memory stored in PostgreSQL
- Debounced background updates (30s default)
- Automatic injection into agent system prompt

### MCP Integration

- Multi-server support (HTTP, SSE, stdio transports)
- OAuth token management
- Per-user MCP configurations stored in PostgreSQL
- Runtime updates via Gateway API

## Embedded Python Client

```python
from crab.client import CrabClient

client = CrabClient()

# Synchronous chat
response = client.chat("What is the capital of France?", thread_id="my-thread")

# Streaming
for event in client.stream("Write a Python script", thread_id="my-thread"):
    print(event)
```

See `backend/tests/test_client.py` for comprehensive usage examples.

## Documentation

See `backend/docs/` for detailed documentation:

- [ARCHITECTURE.md](backend/docs/ARCHITECTURE.md) — Architecture details
- [CONFIGURATION.md](backend/docs/CONFIGURATION.md) — Configuration options
- [API.md](backend/docs/API.md) — API reference
- [SETUP.md](backend/docs/SETUP.md) — Setup guide
- [FILE_UPLOAD.md](backend/docs/FILE_UPLOAD.md) — File upload feature
- [GUARDRAILS.md](backend/docs/GUARDRAILS.md) — Tool guardrails
- [MCP_SERVER.md](backend/docs/MCP_SERVER.md) — MCP server setup

## License

[MIT License](./LICENSE)
