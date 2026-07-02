# Reliable AI Agent: Multi-Step Task Execution Under Uncertainty

![Build](https://img.shields.io/badge/Build-Passing-brightgreen)
![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![React](https://img.shields.io/badge/React-18-61DAFB.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Docker Compose](https://img.shields.io/badge/Deploy-Docker%20Compose-2496ED?logo=docker&logoColor=white)

Reliable AI Agent is an enterprise-grade orchestration platform designed to plan, execute, validate, and self-correct complex multi-step AI tasks. Built around a robust Directed Acyclic Graph (DAG) state machine, the system treats AI generation as an auditable, resilient runtime. 

Unlike simple prompt-and-response interfaces, this platform guarantees execution reliability by employing parallel task execution, specialized multi-agent roles, circuit breakers, and automatic failover using an open-source model stack.

## Key Differentiators

*   **Parallel Execution via DAG:** Intelligently schedules and executes independent tasks concurrently, drastically reducing overall latency for complex workflows.
*   **Specialized Multi-Agent Routing:** Dynamically delegates steps to specialized agent roles (Research, Code, Analysis, Writing), each optimized with specific system prompts and models.
*   **Open-Source First:** Fully configured to leverage leading open-source models (Llama 3.1, Qwen 2.5, Mistral 7B) through a scalable fallback chain.
*   **Self-Healing Architecture:** Built-in validation nodes assess output quality, triggering automated reflection and retry loops for subpar results.
*   **Resiliency Patterns:** Implements production-grade circuit breakers, exponential backoffs, and deterministic state checkpointing via Redis.
*   **Real-time Observability:** A React/Vite dashboard provides deep telemetry, visualizing the execution DAG, live logs, and trace waterfalls.

## System Architecture

```text
			+--------------------------------------+
			|         React + Vite Frontend        |
			| TaskInput | ExecutionDAG | Timeline  |
			+------------------+-------------------+
					   |
			      HTTP + WebSocket APIs
					   |
			+------------------v-------------------+
			|            FastAPI Backend           |
			|  /tasks | /execute | /traces | /ws   |
			+------------------+-------------------+
					   |
			 +-----------------v------------------+
			 |         LangGraph Orchestrator     |
			 | Planner -> Parallel Executor -> Valid|
			 |              |          |          |
			 |              +-> Reflector -> Final|
			 +-----------------+------------------+
					   |
		+--------------------------+---------------------------+
		|                          |                           |
	+-------v-------+          +-------v--------+          +-------v-------+
	| Redis         |          | LLM Providers  |          | Tool Layer    |
	| checkpoints   |          | Llama/Qwen     |          | Web/API/Code  |
	| pub-sub trace |          | fallback chain |          | execution     |
	+---------------+          +----------------+          +---------------+
```

## Project Structure

```text
├── backend/
│   ├── agent/                 # Core LangGraph logic, nodes, and DAG orchestration
│   │   ├── multi_agent/       # Routing and specialized agent definitions
│   │   ├── parallel/          # DAG validation and concurrent step execution
│   │   ├── reliability/       # Circuit breakers, fallbacks, and Chaos Mode
│   │   └── nodes/             # Individual state machine nodes (Planner, Executor, etc.)
│   ├── routes/                # FastAPI endpoint handlers
│   ├── services/              # Integrations (LLM, Redis, Tracing)
│   └── main.py                # FastAPI application bootstrap
├── frontend/
│   ├── src/
│   │   ├── components/        # React UI components (ToastStack, TraceWaterfall, etc.)
│   │   └── hooks/             # State management and WebSocket listeners
│   └── vite.config.js
├── docker-compose.yml         # Container orchestration with resource limits
└── README.md
```

## Quick Start

1.  **Clone the repository.**
2.  **Configure environment:**
    ```bash
    cp .env.example .env
    # Add your API keys (HuggingFace, Tavily) to the .env file
    ```
3.  **Launch the stack:**
    ```bash
    docker compose up --build -d
    ```
4.  **Access the Dashboard:** Open `http://localhost:5173` in your browser.

## Tech Stack

*   **Backend:** Python 3.11, FastAPI, Uvicorn, LangGraph, Pydantic
*   **Infrastructure:** Redis (State & Pub/Sub), Docker Compose
*   **Frontend:** React 18, Vite, Tailwind CSS, Framer Motion
*   **AI Integration:** HuggingFace OpenRouter (Llama, Qwen, Mistral), Tavily Search API

## Demo Scenarios

*   **Happy Path:** Quantum computing research synthesis utilizing parallel execution for independent data gathering steps.
*   **Failure Recovery:** Multi-city weather comparison with "Chaos Mode" enabled, demonstrating circuit breakers and graceful failover.
*   **Reflection & Polish:** Generating an implementation plan for an algorithm, with quality gating enforcing rigorous standards.

## API Documentation

*   **Interactive Docs:** Available locally at `http://localhost:8000/docs`
*   **Key Endpoints:**

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service and Redis health check |
| `POST` | `/tasks` | Create and plan a new task |
| `GET` | `/tasks/{task_id}` | Retrieve full task state and checkpoints |
| `POST` | `/tasks/{task_id}/execute` | Start asynchronous DAG execution |
| `GET` | `/traces/{task_id}` | Retrieve execution trace timeline |
| `POST` | `/config` | Update runtime settings (parallelism, agents) |
| `WS` | `/ws/{task_id}` | Stream live task events via WebSocket |

## Contributing

We welcome contributions to improve the reliability and capabilities of the platform.
1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.
