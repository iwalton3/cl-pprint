import { createStore } from '../lib/framework.js';

const STORAGE_KEY = 'claude-transcript-settings';

// Default settings
const defaultSettings = {
    showTools: false,
    showThinking: false,
    truncateToolCalls: true,
    truncateToolResults: true,
    excludeEditTools: false,
    excludeViewTools: false,
    showExploreFull: false,
    showSubagentsFull: false,
    showCompactionSummary: false,
};

// Load saved settings
let savedSettings = { ...defaultSettings };
try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
        const parsed = JSON.parse(saved);
        // Only use saved values for known keys
        for (const key of Object.keys(defaultSettings)) {
            if (typeof parsed[key] === 'boolean') {
                savedSettings[key] = parsed[key];
            }
        }
    }
} catch (e) {}

// Session tracking (not persisted)
const viewedItems = new Set();
const downloadedItems = new Set();

// Create the store with settings
export const appStore = createStore({ ...savedSettings });

function saveToStorage() {
    try {
        const toSave = {};
        for (const key of Object.keys(defaultSettings)) {
            toSave[key] = appStore.state[key];
        }
        localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
    } catch (e) {}
}

// Generic setter for any setting
export function setSetting(key, value) {
    appStore.state[key] = value;
    saveToStorage();
}

// Convenience setters
export function setShowTools(value) { setSetting('showTools', value); }
export function setShowThinking(value) { setSetting('showThinking', value); }
export function setTruncateToolCalls(value) { setSetting('truncateToolCalls', value); }
export function setTruncateToolResults(value) { setSetting('truncateToolResults', value); }
export function setExcludeEditTools(value) { setSetting('excludeEditTools', value); }
export function setExcludeViewTools(value) { setSetting('excludeViewTools', value); }
export function setShowExploreFull(value) { setSetting('showExploreFull', value); }
export function setShowSubagentsFull(value) { setSetting('showSubagentsFull', value); }

export function markViewed(sessionId) {
    viewedItems.add(sessionId);
}

export function markDownloaded(sessionId) {
    downloadedItems.add(sessionId);
}

export function isViewed(sessionId) {
    return viewedItems.has(sessionId) || downloadedItems.has(sessionId);
}

// Build query string for API calls
export function buildQueryString() {
    const params = new URLSearchParams();
    params.set('show_tools', appStore.state.showTools ? '1' : '0');
    params.set('show_thinking', appStore.state.showThinking ? '1' : '0');
    params.set('truncate_tool_calls', appStore.state.truncateToolCalls ? '1' : '0');
    params.set('truncate_tool_results', appStore.state.truncateToolResults ? '1' : '0');
    params.set('exclude_edit_tools', appStore.state.excludeEditTools ? '1' : '0');
    params.set('exclude_view_tools', appStore.state.excludeViewTools ? '1' : '0');
    params.set('show_explore_full', appStore.state.showExploreFull ? '1' : '0');
    params.set('show_subagents_full', appStore.state.showSubagentsFull ? '1' : '0');
    params.set('show_compaction_summary', appStore.state.showCompactionSummary ? '1' : '0');
    return params.toString();
}
