# Quick UI Search for NVDA

Quick UI Search is an NVDA add-on for Windows that provides **TalkBack-style screen search**: find any control on the current screen by typing part of its name, then jump straight to it.

## How to use

1. Press **`NVDA+Alt+Q`** from anywhere (Q for Quick). You can change it in NVDA menu → Preferences → Input gestures → Quick UI Search.
2. Start typing. The list filters as you type; every word you type must appear in the control.
3. Press **Enter** (or **Down Arrow** to browse the list first, then Enter) to jump to the control.

## What it searches

- Only controls that are actually **visible** in the foreground window. Hidden, off-screen, scrolled-away and collapsed controls are left out.
- If focus is inside a **dialog, alert or open menu**, only that dialog or menu is searched.
- Text that just repeats a button's own name is shown once, not twice.
- Very long lists (thousands of files, for example) are walked item by item and only the part on screen is read, so NVDA never stalls.

## Results

- Shown as **`Name, role`** (e.g. `Mute, button`); disabled controls are marked *(unavailable)*.
- Ranking: exact name → name starts with your text → a word in the name starts with it → name contains it → other matches. Buttons, links, fields and other interactive controls come before plain text, then screen order.

## Jumping to a control

- In a web page or Electron app (Discord, etc.) in **browse mode**, the browse mode cursor moves to the control, like NVDA's Elements List.
- Otherwise keyboard focus moves to the control.
- If the control cannot take focus (plain text, for example), NVDA's review cursor (navigator object) moves there and reads it.

## Keys in the dialog

- **Down Arrow** (in the search box): move into the results list.
- **Enter**: jump to the selected control.
- **Escape**: close and return to where you were.
- Switching to another window closes the dialog and leaves you where you switched to.
- **Ctrl+R**: scan the screen again.

## Performance

The add-on has no background hooks: it does nothing until you press `NVDA+Alt+Q`, and keeps nothing after the dialog closes. The screen scan runs in small slices on NVDA's main thread, so NVDA keeps speaking normally while it runs.

## Installation

1. Open NVDA Menu (`NVDA+N`) → **Tools** → **Add-on store** (or **Manage add-ons** in older versions).
2. Choose **Install from external source** and select `quickUISearch.nvda-addon`.
3. Restart NVDA when prompted.

## Building

```
python build.py
```

## License

GNU General Public License, version 2. See [LICENSE](LICENSE).
