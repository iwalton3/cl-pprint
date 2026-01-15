# Web Browser Implementation

Guide for `browse_web.py` and the VDX-based SPA.

## Server Architecture

### Python HTTP Server with JSON API

```python
class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # API routes
        if parsed.path == '/api/transcripts':
            self.send_json_response({'transcripts': get_transcripts()})
        elif parsed.path.startswith('/api/transcript/'):
            session_id = parsed.path.split('/')[-1]
            query = urllib.parse.parse_qs(parsed.query)
            content = format_transcript(session_id, query)
            self.send_json_response({'content': content})
        else:
            # Static file serving
            super().do_GET()

    def send_json_response(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
```

### Auto-Detect Free Port

```python
def find_free_port(start=8080, max_attempts=100):
    for port in range(start, start + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free port in range {start}-{start+max_attempts}")
```

### Static File Directory

`SimpleHTTPRequestHandler` serves from CWD, not script location:

```python
static_dir = os.path.join(os.path.dirname(__file__), 'static')
os.chdir(static_dir)

with socketserver.TCPServer(("", port), RequestHandler) as httpd:
    httpd.serve_forever()
```

## VDX Framework Patterns

See [FRAMEWORK.md](../FRAMEWORK.md) for full framework reference.

### Store Functions (Not Methods)

Export standalone functions, not store methods:

```javascript
// WRONG
const appStore = createStore({
  methods: { isViewed(id) { ... } }
});

// CORRECT
const appStore = createStore({
  viewedItems: new Set(),
  showTools: false
});

export const isViewed = (sessionId) => {
  return appStore.state.viewedItems.has(sessionId);
};

export const setShowTools = (value) => {
  appStore.state.showTools = value;
};
```

### Boolean Attributes

```javascript
// WRONG - causes DOM error
<input .checked="${value}">

// CORRECT
<input checked="${value}">
```

### Hash Router and Anchor Links

Hash router hijacks anchor clicks. Handle in-page navigation manually:

```javascript
// In transcript-viewer.js
handleAnchorClick(event) {
    const href = event.target.getAttribute('href');
    if (href && href.startsWith('#')) {
        event.preventDefault();
        const id = href.slice(1);
        const element = document.getElementById(id);
        if (element) {
            element.scrollIntoView({ behavior: 'smooth' });
        }
    }
}
```

### Marked.js Heading IDs

Configure marked to generate `id` attributes for anchors:

```javascript
marked.use({
  renderer: {
    heading(text, level) {
      const id = text.toLowerCase().replace(/[^\w]+/g, '-');
      return `<h${level} id="${id}">${text}</h${level}>`;
    }
  }
});
```

## Format Options

### Granular Tool Display

8 independent boolean flags for fine control:

| Flag | Default | Purpose |
|------|---------|---------|
| `show_tools` | false | Master switch for tool calls/results |
| `show_thinking` | false | Show thinking blocks |
| `truncate_calls` | true | Truncate tool call inputs |
| `truncate_results` | true | Truncate tool results |
| `exclude_edit_tools` | false | Hide Write/Edit/NotebookEdit |
| `exclude_view_tools` | false | Hide Read/Grep/Glob |
| `show_explore_full` | false | Always show Explore agents in full |
| `show_subagents_full` | false | Always show other subagents in full |

Specific options override general ones:
```python
should_show = (
    options.get('show_tools', False) or
    (is_explore and options.get('show_explore_full', False))
)
```
