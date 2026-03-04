# 🌐 Moodle LLM Bridge

Use your university LMS as a relay to access an LLM — even when the internet is blocked.

## How It Works

```
┌─────────────────────┐          ┌───────────────────┐          ┌──────────────┐
│  YOU (University)   │          │  AGENT (Cloud)     │          │   Groq LLM   │
│                     │  poll    │                    │  query   │  (e.g. Llama│
│  Post blog entry:   │◄────────│  Scans for [LLMQ]  │─────────►│   models)    │
│  [LLMQ] My question │         │  entries every 30s │          │  Returns     │
│                     │  post   │                    │◄─────────│  response    │
│  Read response:     │◄────────│  Posts [LLMR#id]   │          │              │
│  [LLMR#42] Re: ...  │         │  blog entry back   │          │              │
└─────────────────────┘          └───────────────────┘          └──────────────┘
```

## Quick Start (Agent Only)

### 1. Clone & Install

```bash
cd i:\LMS
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your credentials:
#   MOODLE_USERNAME  = your LMS username
#   MOODLE_PASSWORD  = your LMS password
#   GROQ_API_KEY     = your Groq API key
```

### 3. Run the Agent

```bash
python agent.py
```

## Usage

### Posting a Prompt (on your university LMS)

1. Go to **lms.vitc.ac.in** → **Blog** → **Add a new entry**
2. Set the **title** to: `[LLMQ] Your question here`
3. Optionally write a longer prompt in the **body**
4. Save the entry

### Reading the Response

- The agent will pick up your prompt within ~30 seconds
- A new blog entry will appear titled: `[LLMR#<id>] Re: Your question here`
- Open it to read the LLM response

## Markers & Safety

| Marker      | Meaning                        | Example                           |
| ----------- | ------------------------------ | --------------------------------- |
| `[LLMQ]`    | Your prompt to the LLM         | `[LLMQ] Explain recursion`        |
| `[LLMR#42]` | Agent's response to prompt #42 | `[LLMR#42] Re: Explain recursion` |

- The agent **only** processes entries starting with `[LLMQ]`
- It **never** processes `[LLMR#...]` entries → **no infinite loops**
- Processed prompt IDs are saved to `processed_ids.json` to avoid duplicates

## Configuration

| Variable          | Default                  | Description                                   |
| ----------------- | ------------------------ | --------------------------------------------- |
| `MOODLE_URL`      | `https://lms.vitc.ac.in` | Your Moodle LMS URL                           |
| `MOODLE_USERNAME` | —                        | LMS login username                            |
| `MOODLE_PASSWORD` | —                        | LMS login password                            |
| `GROQ_API_KEY`    | —                        | Groq API key                                  |
| `POLL_INTERVAL`   | `30`                     | Seconds between scans                         |
| `PUBLISH_STATE`   | `draft`                  | Blog visibility: `site`, `public`, or `draft` |

## FastAPI HTTP Service

In addition to the background agent, you can run a FastAPI service
that wraps the agent and exposes health/control endpoints.

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the API locally

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open:

- `GET /health` → basic health check
- `GET /status` → agent status and processed prompt count
- `POST /control/poll-once` → trigger a poll cycle immediately
- `POST /control/restart` → restart the background agent thread

This API is suitable for deployment on any platform that can run
Uvicorn/Gunicorn, such as a small VM, Docker container, or managed
container service.

## Cloud Deployment (Agent)

Deploy on any cloud VM (AWS EC2, GCP, Azure, etc.):

```bash
# SSH into your cloud server
ssh user@your-server

# Clone the repo / upload files
# Install Python 3.10+
# Then:
pip install -r requirements.txt
cp .env.example .env
nano .env  # fill in credentials

# Run with nohup so it survives SSH disconnect
nohup python agent.py &

# Or use screen/tmux
screen -S lms-bridge
python agent.py
# Ctrl+A, D to detach
```

## Troubleshooting

- **Login fails**: Check your username/password. Some Moodle instances use SSO — this tool requires direct login.
- **No entries found**: Make sure your blog entries are visible. Check publish state.
- **Agent stops**: Check `agent.log` for errors. The agent auto-reconnects on session expiry.
