import { defineComponent, html, when, each } from '../lib/framework.js';
import { appStore, markViewed, markDownloaded, isViewed } from '../stores/app-store.js';

export default defineComponent('transcript-list', {
    stores: { app: appStore },

    data() {
        return {
            transcripts: [],
            loading: true,
            error: null,
            searchTerm: '',
            projectFilter: '',
        };
    },

    mounted() {
        this.loadTranscripts();
    },

    methods: {
        async loadTranscripts() {
            try {
                const res = await fetch('/api/transcripts');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                this.state.transcripts = data.transcripts;
                this.state.loading = false;
            } catch (e) {
                this.state.error = e.message;
                this.state.loading = false;
            }
        },

        getFilteredTranscripts() {
            let list = this.state.transcripts;

            if (this.state.searchTerm) {
                const term = this.state.searchTerm.toLowerCase();
                list = list.filter(t =>
                    (t.title && t.title.toLowerCase().includes(term)) ||
                    (t.description && t.description.toLowerCase().includes(term)) ||
                    (t.project && t.project.toLowerCase().includes(term)) ||
                    (t.first_prompt && t.first_prompt.toLowerCase().includes(term))
                );
            }

            if (this.state.projectFilter) {
                list = list.filter(t => t.project === this.state.projectFilter);
            }

            return list;
        },

        getUniqueProjects() {
            const projects = new Set();
            for (const t of this.state.transcripts) {
                if (t.project) projects.add(t.project);
            }
            return Array.from(projects).sort();
        },

        handleSearch(e) {
            this.state.searchTerm = e.target.value;
        },

        handleProjectFilter(e) {
            this.state.projectFilter = e.target.value;
        },

        handleItemClick(t) {
            markViewed(t.session_id);
        },

        handleDownload(t, e) {
            e.preventDefault();
            e.stopPropagation();

            markDownloaded(t.session_id);

            const showTools = this.stores.app.showTools ? '1' : '0';
            const showThinking = this.stores.app.showThinking ? '1' : '0';
            const url = `/api/download/${t.session_id}?show_tools=${showTools}&show_thinking=${showThinking}`;

            window.location.href = url;
        },

        getItemClass(t) {
            return isViewed(t.session_id) ? 'transcript-item viewed' : 'transcript-item';
        }
    },

    template() {
        if (this.state.loading) {
            return html`<div class="loading">Loading transcripts...</div>`;
        }

        if (this.state.error) {
            return html`<div class="empty-state">Error: ${this.state.error}</div>`;
        }

        const transcripts = this.getFilteredTranscripts();
        const projects = this.getUniqueProjects();

        return html`
            <div class="transcript-list">
                <div class="filters">
                    <input type="search"
                           placeholder="Search transcripts..."
                           value="${this.state.searchTerm}"
                           on-input="${this.handleSearch}">
                    <select value="${this.state.projectFilter}"
                            on-change="${this.handleProjectFilter}">
                        <option value="">All Projects</option>
                        ${each(projects, p => html`
                            <option value="${p}">${p}</option>
                        `)}
                    </select>
                </div>

                ${when(transcripts.length === 0, () => html`
                    <div class="empty-state">
                        ${this.state.searchTerm || this.state.projectFilter
                            ? 'No transcripts match your filters'
                            : 'No transcripts found'}
                    </div>
                `, () => html`
                    <div class="list">
                        ${each(transcripts, t => html`
                            <a href="#/view/${t.session_id}/"
                               class="${this.getItemClass(t)}"
                               on-click="${() => this.handleItemClick(t)}">
                                <div class="item-header">
                                    <span class="title">${t.title || t.session_id.slice(0, 8)}</span>
                                    <span class="date">${t.date_str}</span>
                                </div>
                                <div class="item-meta">
                                    <span class="project">${t.project}</span>
                                    <span class="stats">${t.message_count} msgs / ${t.size_str}${t.duration_str ? ` / ${t.duration_str}` : ''}</span>
                                </div>
                                <div class="description">${t.description || ''}</div>
                                <button class="download-btn"
                                        on-click="${e => this.handleDownload(t, e)}">
                                    Download
                                </button>
                            </a>
                        `, t => t.session_id)}
                    </div>
                `)}
            </div>
        `;
    }
});
