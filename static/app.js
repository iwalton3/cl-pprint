import { defineComponent, html } from './lib/framework.js';
import { enableRouting } from './lib/router.js';

// Import components
import './components/settings-panel.js';
import './components/transcript-list.js';
import './components/transcript-viewer.js';

defineComponent('app-root', {
    mounted() {
        const outlet = this.querySelector('router-outlet');
        enableRouting(outlet, {
            '/': { component: 'transcript-list' },
            '/view/:sessionId/': { component: 'transcript-viewer' },
        });
    },

    template() {
        return html`
            <header class="app-header">
                <h1><a href="#/">Claude Transcripts</a></h1>
                <settings-panel></settings-panel>
            </header>
            <main class="app-content">
                <router-outlet></router-outlet>
            </main>
        `;
    }
});
