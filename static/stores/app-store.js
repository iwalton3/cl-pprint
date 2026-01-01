import { createStore } from '../lib/framework.js';

const STORAGE_KEY = 'claude-transcript-settings';

// Load saved settings
let savedSettings = { showTools: false, showThinking: false, truncateTools: true };
try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
        const parsed = JSON.parse(saved);
        if (typeof parsed.showTools === 'boolean') savedSettings.showTools = parsed.showTools;
        if (typeof parsed.showThinking === 'boolean') savedSettings.showThinking = parsed.showThinking;
        if (typeof parsed.truncateTools === 'boolean') savedSettings.truncateTools = parsed.truncateTools;
    }
} catch (e) {}

// Session tracking (not persisted)
const viewedItems = new Set();
const downloadedItems = new Set();

// Create the store with settings
export const appStore = createStore({
    showTools: savedSettings.showTools,
    showThinking: savedSettings.showThinking,
    truncateTools: savedSettings.truncateTools,
});

function saveToStorage() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
            showTools: appStore.state.showTools,
            showThinking: appStore.state.showThinking,
            truncateTools: appStore.state.truncateTools,
        }));
    } catch (e) {}
}

export function setShowTools(value) {
    appStore.state.showTools = value;
    saveToStorage();
}

export function setShowThinking(value) {
    appStore.state.showThinking = value;
    saveToStorage();
}

export function setTruncateTools(value) {
    appStore.state.truncateTools = value;
    saveToStorage();
}

export function markViewed(sessionId) {
    viewedItems.add(sessionId);
}

export function markDownloaded(sessionId) {
    downloadedItems.add(sessionId);
}

export function isViewed(sessionId) {
    return viewedItems.has(sessionId) || downloadedItems.has(sessionId);
}
