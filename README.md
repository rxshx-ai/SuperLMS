# 🌐 Moodle LLM Bridge

Use your university LMS as a relay to access an LLM — even when the internet is blocked.

## How It Works

```
┌─────────────────────┐          ┌───────────────────┐          ┌──────────────┐
│  YOU (University)   │          │  AGENT (Cloud)     │          │  Google      │
│                     │  poll    │                    │  query   │  Gemini API  │
│  Post blog entry:   │◄────────│  Scans for [LLMQ]  │─────────►│              │
│  [LLMQ] My question │         │  entries every 30s │          │  Returns     │
│                     │  post   │                    │◄─────────│  response    │
│  Read response:     │◄────────│  Posts [LLMR#id]   │          │              │
│  [LLMR#42] Re: ...  │         │  blog entry back   │          │              │
└─────────────────────┘          └───────────────────┘          └──────────────┘
```

## Quick Start

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
#   GEMINI_API_KEY   = your Google Gemini API key
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
- Open it to read the Gemini response

## Markers & Safety

| Marker | Meaning | Example |
|--------|---------|---------|
| `[LLMQ]` | Your prompt to the LLM | `[LLMQ] Explain recursion` |
| `[LLMR#42]` | Agent's response to prompt #42 | `[LLMR#42] Re: Explain recursion` |

- The agent **only** processes entries starting with `[LLMQ]`
- It **never** processes `[LLMR#...]` entries → **no infinite loops**
- Processed prompt IDs are saved to `processed_ids.json` to avoid duplicates

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MOODLE_URL` | `https://lms.vitc.ac.in` | Your Moodle LMS URL |
| `MOODLE_USERNAME` | — | LMS login username |
| `MOODLE_PASSWORD` | — | LMS login password |
| `GEMINI_API_KEY` | — | Google Gemini API key |
| `POLL_INTERVAL` | `30` | Seconds between scans |
| `PUBLISH_STATE` | `site` | Blog visibility: `site`, `public`, or `draft` |

## Cloud Deployment

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
