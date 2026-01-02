import { defineComponent, html, when } from '../lib/framework.js';
import { appStore, setSetting } from '../stores/app-store.js';

export default defineComponent('settings-panel', {
    stores: { app: appStore },

    data() {
        return { open: false };
    },

    methods: {
        toggle(e) {
            e.stopPropagation();
            this.state.open = !this.state.open;
        },

        handleChange(key) {
            return (e) => {
                e.stopPropagation();
                setSetting(key, e.target.checked);
                window.dispatchEvent(new CustomEvent('settings-changed'));
            };
        },

        handleDropdownClick(e) {
            e.stopPropagation();
        }
    },

    mounted() {
        this._clickHandler = () => {
            if (this.state.open) {
                this.state.open = false;
            }
        };
        document.addEventListener('click', this._clickHandler);
    },

    unmounted() {
        document.removeEventListener('click', this._clickHandler);
    },

    template() {
        return html`
            <div class="settings-panel">
                <button class="settings-toggle" on-click="toggle">
                    Settings
                </button>
                ${when(this.state.open, () => html`
                    <div class="settings-dropdown" on-click="handleDropdownClick">
                        <div class="settings-section">
                            <div class="settings-section-title">Display</div>
                            <label>
                                <input type="checkbox"
                                       checked="${this.stores.app.showTools}"
                                       on-change="${this.handleChange('showTools')}">
                                Show Tool Calls
                            </label>
                            <label>
                                <input type="checkbox"
                                       checked="${this.stores.app.showThinking}"
                                       on-change="${this.handleChange('showThinking')}">
                                Show Thinking Blocks
                            </label>
                        </div>

                        <div class="settings-section">
                            <div class="settings-section-title">Truncation</div>
                            <label>
                                <input type="checkbox"
                                       checked="${this.stores.app.truncateToolCalls}"
                                       on-change="${this.handleChange('truncateToolCalls')}">
                                Truncate Tool Inputs
                            </label>
                            <label>
                                <input type="checkbox"
                                       checked="${this.stores.app.truncateToolResults}"
                                       on-change="${this.handleChange('truncateToolResults')}">
                                Truncate Tool Results
                            </label>
                        </div>

                        <div class="settings-section">
                            <div class="settings-section-title">Exclude Tools</div>
                            <label>
                                <input type="checkbox"
                                       checked="${this.stores.app.excludeEditTools}"
                                       on-change="${this.handleChange('excludeEditTools')}">
                                Hide Edit Commands
                            </label>
                            <label>
                                <input type="checkbox"
                                       checked="${this.stores.app.excludeViewTools}"
                                       on-change="${this.handleChange('excludeViewTools')}">
                                Hide View Commands
                            </label>
                        </div>

                        <div class="settings-section">
                            <div class="settings-section-title">Agent Display</div>
                            <label>
                                <input type="checkbox"
                                       checked="${this.stores.app.showExploreFull}"
                                       on-change="${this.handleChange('showExploreFull')}">
                                Show Explore Agents Full
                            </label>
                            <label>
                                <input type="checkbox"
                                       checked="${this.stores.app.showSubagentsFull}"
                                       on-change="${this.handleChange('showSubagentsFull')}">
                                Show Other Subagents Full
                            </label>
                        </div>
                    </div>
                `)}
            </div>
        `;
    }
});
