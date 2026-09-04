# Quick UI Search for NVDA

Quick UI Search is a high-performance NVDA add-on for Windows that provides **TalkBack-style Screen Search** across the currently visible, active user interface.

## Key Features

- **Shortcut**: Press **`NVDA+J`** from anywhere to open the Quick UI Search dialog.
- **Visible Screen Search (TalkBack-Style)**:
  - Searches strictly within the **active visible interface container** (voice channel, chat view, dialog, or tab panel).
  - Automatically isolates the active view, completely excluding background panels, other Discord servers/channels, and off-screen controls.
  - Dynamically updates the active search scope when you switch to another server, channel, dialog, or window.
- **Visible Controls List**:
  - When the search box is empty, automatically displays every visible, accessible control in the active interface.
- **Smart Ranking**:
  - Prioritizes interactive controls (**Buttons, Toggle Buttons, Menu Items, Links, Edit Fields, Checkboxes, Tabs, List Items, Tree Items**) above static text.
  - Multi-tier matching: Exact name match → Starts with name → Contains name → Description/Value → Role.
  - Ranks controls closest to the current keyboard focus first.
- **Clean Result Display**:
  - Formatted as **`Name — Role`** (e.g. `Watch Stream — Button`, `General — Voice Channel`).
  - Descriptions are hidden by default and only shown to distinguish controls with identical names.
- **Real-Time As-You-Type Filtering**:
  - Filters results in real-time as you type without requiring Enter.
- **Keyboard Navigation**:
  - **Down Arrow** (from search box): Moves keyboard focus directly into the Controls List.
  - **Up / Down Arrow**: Move through results.
  - **Enter** (on a result): Dismisses the dialog and jumps keyboard focus directly to that control.
  - **Escape**: Closes the dialog and restores your original keyboard focus.
  - **Ctrl+R**: Re-indexes the current visible screen scope.
  - **Ctrl+Shift+R**: Clears all cached scopes and rebuilds from scratch.

## Installation

1. Open NVDA Menu (`NVDA+N`) -> **Tools** -> **Manage Add-ons**.
2. Click **Install...** and select `quickUISearch.nvda-addon`.
3. Confirm installation and restart NVDA when prompted.
