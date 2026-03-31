/**
 * About Crab markdown content. Inlined to avoid raw-loader dependency
 * (Turbopack cannot resolve raw-loader for .md imports).
 */
export const aboutMarkdown = `# About Crab

**Crab** is a multi-tenant AI Agent SaaS platform that orchestrates **sub-agents**, **memory**, and **sandboxes** to do almost anything — powered by **extensible skills**.

---

## Core Features

* **Skills & Tools**: Built-in and extensible skills and tools for a wide range of tasks.
* **Sub-Agents**: Concurrent sub-agent delegation for complex multi-step tasks.
* **Sandbox & File System**: Safely execute code and manipulate files in isolated cloud sandboxes.
* **Context Engineering**: Isolated sub-agent context, summarization to keep the context window sharp.
* **Long-Term Memory**: Persistent per-user memory with automatic fact extraction.

---

## Architecture

Built on a three-layer architecture (Harness / Platform / App) with:
- **PostgreSQL** for persistent storage
- **Redis** for thread locks and rate limiting
- **E2B** for cloud sandbox execution (VM-level isolation)

---

## License

Crab is distributed under the **MIT License**.
`;
