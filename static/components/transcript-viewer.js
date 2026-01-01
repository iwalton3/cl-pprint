import { defineComponent, html, when } from '../lib/framework.js';
import { appStore, markViewed, markDownloaded } from '../stores/app-store.js';

export default defineComponent('transcript-viewer', {
    props: {
        params: {},
    },

    stores: { app: appStore },

    data() {
        return {
            markdown: '',
            loading: true,
            error: null,
            title: '',
            sessionId: '',
        };
    },

    mounted() {
        this.loadTranscript();
        this._settingsHandler = () => this.loadTranscript();
        window.addEventListener('settings-changed', this._settingsHandler);
    },

    unmounted() {
        window.removeEventListener('settings-changed', this._settingsHandler);
    },

    propsChanged(prop) {
        if (prop === 'params') {
            this.loadTranscript();
        }
    },

    methods: {
        async loadTranscript() {
            const sessionId = this.props.params?.sessionId;
            if (!sessionId) return;

            this.state.sessionId = sessionId;
            this.state.loading = true;
            this.state.error = null;

            // Mark as viewed
            markViewed(sessionId);

            const showTools = this.stores.app.showTools ? '1' : '0';
            const showThinking = this.stores.app.showThinking ? '1' : '0';
            const url = `/api/transcript/${sessionId}?show_tools=${showTools}&show_thinking=${showThinking}`;

            try {
                const res = await fetch(url);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();

                this.state.markdown = data.markdown;
                this.state.title = data.title;
                this.state.loading = false;

                requestAnimationFrame(() => this.renderMarkdown());
            } catch (e) {
                this.state.error = e.message;
                this.state.loading = false;
            }
        },

        renderMarkdown() {
            const container = this.querySelector('.markdown-content');
            if (!container || !this.state.markdown) return;

            if (typeof marked !== 'undefined') {
                // Custom renderer to add IDs to headings
                const renderer = new marked.Renderer();
                renderer.heading = function(text, level) {
                    // Generate slug from text (remove HTML tags first)
                    const plainText = text.replace(/<[^>]*>/g, '');
                    const slug = plainText.toLowerCase()
                        .replace(/[^\w\s-]/g, '')
                        .replace(/\s+/g, '-')
                        .replace(/-+/g, '-')
                        .trim();
                    return `<h${level} id="${slug}">${text}</h${level}>`;
                };

                marked.setOptions({
                    gfm: true,
                    breaks: false,
                    renderer: renderer,
                });

                container.innerHTML = marked.parse(this.state.markdown);

                if (typeof hljs !== 'undefined') {
                    container.querySelectorAll('pre code').forEach(block => {
                        hljs.highlightElement(block);
                    });
                }

                // Handle anchor links manually to avoid router interference
                container.querySelectorAll('a[href^="#"]').forEach(link => {
                    link.addEventListener('click', (e) => {
                        const href = link.getAttribute('href');
                        // Only handle in-page anchors, not router links
                        if (href && href.startsWith('#') && !href.startsWith('#/')) {
                            e.preventDefault();
                            e.stopPropagation();
                            const targetId = href.slice(1);
                            // Try to find element by ID
                            let target = document.getElementById(targetId);
                            // Also try within container with CSS escaping
                            if (!target) {
                                try {
                                    target = container.querySelector(`[id="${targetId}"]`);
                                } catch (err) {}
                            }
                            if (target) {
                                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                            }
                        }
                    });
                });
            } else {
                container.textContent = this.state.markdown;
            }
        },

        handleBack() {
            window.location.hash = '#/';
        },

        handleDownload() {
            const sessionId = this.state.sessionId;
            if (!sessionId) return;

            markDownloaded(sessionId);

            const showTools = this.stores.app.showTools ? '1' : '0';
            const showThinking = this.stores.app.showThinking ? '1' : '0';
            const url = `/api/download/${sessionId}?show_tools=${showTools}&show_thinking=${showThinking}`;

            window.location.href = url;
        }
    },

    template() {
        if (this.state.loading) {
            return html`<div class="loading">Loading transcript...</div>`;
        }

        if (this.state.error) {
            return html`
                <div class="transcript-viewer">
                    <div class="viewer-header">
                        <button class="back-btn" on-click="handleBack">Back to List</button>
                        <h2>Error</h2>
                    </div>
                    <div class="empty-state">Error loading transcript: ${this.state.error}</div>
                </div>
            `;
        }

        return html`
            <div class="transcript-viewer">
                <div class="viewer-header">
                    <button class="back-btn" on-click="handleBack">Back to List</button>
                    <h2>${this.state.title}</h2>
                    <button class="download-btn viewer-download" on-click="handleDownload">Download</button>
                </div>
                <article class="markdown-content markdown-body">
                </article>
            </div>
        `;
    }
});
