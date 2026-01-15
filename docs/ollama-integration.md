# Ollama Integration Guide

This document covers using Ollama for transcript summarization.

## Configuration

Set Ollama settings in `config.json`:

```json
{
  "ollama": {
    "model": "qwen3:30b-a3b-thinking-2507-q4_K_M",
    "url": "http://localhost:11434/api/generate"
  }
}
```

## Critical API Parameters

### Disable Thinking Mode

Thinking models will loop forever without this:

```python
response = requests.post(
    ollama_url,
    json={
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "think": False,  # CRITICAL - disables thinking traces
        "format": "json"
    }
)
```

### Force JSON Output

Prevent preamble contamination (e.g., "Here is a summary:"):

```python
prompt = f"""Summarize what the user wanted. Return JSON:
{{"summary": "detailed description", "filename": "short-kebab-case-name"}}

Messages:
{messages_text}

JSON:"""

response = requests.post(
    ollama_url,
    json={
        "format": "json",  # Forces structured output
        ...
    }
)
```

## Context Extraction Strategy

Don't send entire transcripts. Extract meaningful user messages:

```python
def extract_relevant_messages(entries):
    """Extract messages most likely to capture user intent."""
    messages = []

    # Always include first user message
    if first_message:
        messages.append(first_message)

    # Include pre-plan messages (before ExitPlanMode)
    messages.extend(pre_plan_messages)

    # Include substantial messages (>250 chars)
    messages.extend([m for m in user_messages if len(m) > 250])

    return messages
```

## Dual-Purpose Generation

Generate summary and filename in single API call:

```python
prompt = """Return JSON:
{"summary": "detailed description", "filename": "short-kebab-case-name"}"""

result = json.loads(response["response"])
summary = result["summary"]
filename = result["filename"]  # Ready for filesystem use
```

## Caching

Cache summaries to avoid repeated API calls:

```python
CACHE_PATH = Path("~/.claude/transcript_summaries.json").expanduser()

def load_cache():
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}

def save_cache(cache):
    CACHE_PATH.write_text(json.dumps(cache, indent=2))

# Skip already-processed sessions
if session_id in cache:
    continue
```

## Error Handling

Handle common Ollama issues:

```python
try:
    response = requests.post(ollama_url, json=payload, timeout=120)
    response.raise_for_status()
except requests.exceptions.Timeout:
    print(f"Ollama timeout for {session_id}")
except requests.exceptions.ConnectionError:
    print("Ollama not running. Start with: ollama serve")
except json.JSONDecodeError:
    print(f"Invalid JSON response: {response.text[:200]}")
```
