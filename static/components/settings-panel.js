import { defineComponent, html, when } from '../lib/framework.js';
import { appStore, setShowTools, setShowThinking } from '../stores/app-store.js';

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

        handleToolsChange(e) {
            e.stopPropagation();
            setShowTools(e.target.checked);
            window.dispatchEvent(new CustomEvent('settings-changed'));
        },

        handleThinkingChange(e) {
            e.stopPropagation();
            setShowThinking(e.target.checked);
            window.dispatchEvent(new CustomEvent('settings-changed'));
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
                        <label>
                            <input type="checkbox"
                                   .checked="${this.stores.app.showTools}"
                                   on-change="${this.handleToolsChange}">
                            Show Tool Calls
                        </label>
                        <label>
                            <input type="checkbox"
                                   .checked="${this.stores.app.showThinking}"
                                   on-change="${this.handleThinkingChange}">
                            Show Thinking Blocks
                        </label>
                    </div>
                `)}
            </div>
        `;
    }
});
