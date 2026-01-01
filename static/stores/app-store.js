import { createStore } from '../lib/framework.js';

const STORAGE_KEY = 'claude-transcript-settings';

// Load saved settings
let savedSettings = { showTools: false, showThinking: false };
try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
        const parsed = JSON.parse(saved);
        if (typeof parsed.showTools === 'boolean') savedSettings.showTools = parsed.showTools;
        if (typeof parsed.showThinking === 'boolean') savedSettings.showThinking = parsed.showThinking;
    }
} catch (e) {}

// Session tracking (not persisted)
const viewedItems = new Set();
const downloadedItems = new Set();

// Create the store with settings
export const appStore = createStore({
    showTools: savedSettings.showTools,
    showThinking: savedSettings.showThinking,
});

function saveToStorage() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
            showTools: appStore.showTools,
            showThinking: appStore.showThinking,
        }));
    } catch (e) {}
}

export function setShowTools(value) {
    appStore.showTools = value;
    saveToStorage();
}

export function setShowThinking(value) {
    appStore.showThinking = value;
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
