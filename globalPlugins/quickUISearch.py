# -*- coding: UTF-8 -*-
# Quick UI Search Add-on for NVDA
# TalkBack-style screen search: press NVDA+J, type part of a control's name,
# press Enter and NVDA jumps to that control.
#
# Design rules (these are what keep the add-on from disturbing NVDA):
# - No event handlers. Nothing runs until NVDA+J is pressed.
# - Every accessibility (COM) call happens on NVDA's main thread. The scan is
#   split into small time slices so NVDA keeps speaking while it runs.
# - Nothing is kept after the dialog closes.

import time
import wx
import api
import ui
import core
import speech
import controlTypes
import globalPluginHandler
import scriptHandler
import winUser
from logHandler import log

try:
    import addonHandler
    addonHandler.initTranslation()
except Exception:
    pass

try:
    _
except NameError:
    def _(s):
        return s


Role = controlTypes.Role
State = controlTypes.State

# Time spent scanning per slice before handing control back to NVDA.
SCAN_SLICE_SECONDS = 0.02
# Hard limits so a huge tree can never stall NVDA.
MAX_NODES = 6000
MAX_DEPTH = 64
# Delay after the dialog closes before moving focus, so Windows has given the
# foreground back to the target application.
JUMP_DELAY_MS = 120
# How long to wait before checking whether a focus move worked.
FOCUS_VERIFY_DELAY_MS = 350
# Containers with more children than this are walked one child at a time.
LARGE_CONTAINER_CHILDREN = 20
# When walking such a container, stop after this many scrolled-away items in
# a row once visible items have been seen.
SCROLLED_AWAY_LIMIT = 5
# While scanning, the results list is refreshed at most this often, so NVDA
# does not keep re-reading the list while you arrow through it.
LIST_REFRESH_MS = 300


def _roles(*names):
    return frozenset(r for r in (getattr(Role, n, None) for n in names) if r is not None)


INTERACTIVE_ROLES = _roles(
    "BUTTON", "TOGGLE_BUTTON", "MENUITEM", "CHECKMENUITEM", "RADIOMENUITEM",
    "MENUBUTTON", "DROPDOWNBUTTON", "SPLITBUTTON", "LINK", "EDITABLETEXT",
    "PASSWORDEDIT", "SPINBUTTON", "CHECKBOX", "RADIOBUTTON", "COMBOBOX", "TAB",
    "LISTITEM", "TREEVIEWITEM", "SLIDER", "SWITCH", "TABLECELL", "HOTKEYFIELD",
)

# Containers whose names only repeat what their children already say.
SKIPPED_ROLES = _roles(
    "WINDOW", "PANE", "APPLICATION", "FRAME", "SCROLLBAR", "SCROLLPANE",
    "DOCUMENT", "TITLEBAR", "UNKNOWN", "FILLER", "SEPARATOR", "WHITESPACE",
)

# Roles that mark a modal or temporary surface; when focus is inside one,
# the search is limited to it.
FOCUS_TRAP_ROLES = _roles("DIALOG", "ALERT", "POPUPMENU", "MENU")


def getRoleName(obj):
    """Human-readable role name, e.g. 'button'."""
    try:
        roleText = obj.roleText
        if roleText:
            return roleText
    except Exception:
        pass
    try:
        return obj.role.displayString
    except Exception:
        return _("control")


def cleanText(text):
    """One line of text: line breaks and runs of spaces become single spaces."""
    return " ".join((text or "").split())


def getRect(obj):
    """Return (left, top, width, height) or None. Reads location once."""
    try:
        loc = obj.location
    except Exception:
        return None
    if not loc:
        return None
    try:
        return (loc.left, loc.top, loc.width, loc.height)
    except AttributeError:
        try:
            return tuple(loc[:4])
        except Exception:
            return None


def getWindowRect(hwnd):
    if not hwnd:
        return None
    try:
        rect = winUser.getWindowRect(hwnd)
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        return None


def getRootWindow(hwnd):
    if not hwnd:
        return None
    try:
        return winUser.getAncestor(hwnd, winUser.GA_ROOT) or hwnd
    except Exception:
        return hwnd


def rectsIntersect(a, b):
    return not (
        a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
        or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]
    )


def getVisibleArea(searchRoot, fallbackHwnd):
    """
    The area a control must touch to count as visible: the search root's own
    top-level window (so a popup menu is not clipped to the app window),
    limited to the screen(s).
    """
    hwnd = getattr(searchRoot, "windowHandle", None) or fallbackHwnd
    rect = getWindowRect(getRootWindow(hwnd))
    try:
        import ctypes
        metrics = ctypes.windll.user32.GetSystemMetrics
        # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
        screen = (metrics(76), metrics(77), metrics(78), metrics(79))
    except Exception:
        return rect
    if not rect:
        return screen
    left = max(rect[0], screen[0])
    top = max(rect[1], screen[1])
    right = min(rect[0] + rect[2], screen[0] + screen[2])
    bottom = min(rect[1] + rect[3], screen[1] + screen[3])
    if right <= left or bottom <= top:
        return screen
    return (left, top, right - left, bottom - top)


def resolveSearchRoot(focus, foreground):
    """
    Search the whole foreground window, unless focus is inside a dialog,
    alert or open menu: then search only that, like TalkBack does.
    """
    obj = focus
    depth = 0
    while obj and depth < MAX_DEPTH:
        try:
            if obj == foreground:
                break
            if obj.role in FOCUS_TRAP_ROLES:
                return obj
            obj = obj.parent
        except Exception:
            break
        depth += 1
    return foreground


class ScreenScanner:
    """
    Walks the accessibility tree of the search root in document order, on the
    main thread, a few milliseconds at a time.
    """

    def __init__(self, root, windowRect, onBatch, onDone):
        self._windowRect = windowRect
        self._onBatch = onBatch
        self._onDone = onDone
        # Stack items: (object, depth, name of the nearest interactive
        # ancestor lowercased, sibling chain)
        # The sibling chain is a dict for children of large containers that are
        # walked one sibling at a time instead of fetched all at once.
        self._stack = [(root, 0, "", None)]
        self._seen = set()
        self._visited = 0
        self._order = 0
        # Name of the last listed interactive control, lowercased.
        self._lastInteractiveName = ""
        self._cancelled = False
        self.finished = False

    def start(self):
        self._schedule()

    def _schedule(self):
        # CallAfter runs as soon as NVDA has handled pending input and events;
        # a CallLater timer would add roughly 100 ms between slices.
        wx.CallAfter(self._step)

    def cancel(self):
        self._cancelled = True

    def _step(self):
        if self._cancelled:
            return
        deadline = time.perf_counter() + SCAN_SLICE_SECONDS
        batch = []
        while self._stack and self._visited < MAX_NODES:
            obj, depth, parentName, chain = self._stack.pop()
            self._visited += 1
            try:
                self._visit(obj, depth, parentName, chain, batch)
            except Exception:
                log.debugWarning("QuickUISearch: error reading object", exc_info=True)
            if time.perf_counter() >= deadline:
                break
        if self._cancelled:
            return
        if batch:
            self._onBatch(batch)
        if self._stack and self._visited < MAX_NODES:
            self._schedule()
        else:
            if self._visited >= MAX_NODES:
                log.info(f"QuickUISearch: scan stopped at the {MAX_NODES} object limit")
            self.finished = True
            self._onDone()

    def _isVisible(self, states, rect):
        if State.INVISIBLE in states or State.OFFSCREEN in states:
            return False
        if rect is not None:
            if rect[2] <= 0 or rect[3] <= 0:
                return False
            if self._windowRect and not rectsIntersect(rect, self._windowRect):
                return False
        return True

    def _isScrolledAway(self, states, rect):
        if State.OFFSCREEN in states:
            return True
        return bool(rect and self._windowRect and rect[2] > 0 and rect[3] > 0
            and not rectsIntersect(rect, self._windowRect))

    def _visit(self, obj, depth, parentName, chain, batch):
        states = obj.states
        rect = getRect(obj)
        visible = self._isVisible(states, rect)

        if chain is not None:
            # The visible items of a long list form one block; once several
            # items in a row after it are scrolled away, the rest are too.
            # Hidden items (tooltips, collapsed parts) can sit inside the
            # block, so they do not end it.
            if visible:
                chain["seenVisible"] = True
                chain["scrolledAway"] = 0
            elif chain["seenVisible"] and self._isScrolledAway(states, rect):
                chain["scrolledAway"] += 1
            if chain["scrolledAway"] < SCROLLED_AWAY_LIMIT:
                nextObj = obj.next
                if nextObj:
                    # Pushed before the children so they are visited first.
                    self._stack.append((nextObj, depth, parentName, chain))
        if not visible:
            return

        role = obj.role
        name = cleanText(obj.name)
        nameLower = name.lower()
        interactive = role in INTERACTIVE_ROLES or State.FOCUSABLE in states

        if role not in SKIPPED_ROLES:
            item = self._makeItem(obj, role, states, interactive, name, nameLower, parentName, rect)
            if item:
                batch.append(item)

        if depth >= MAX_DEPTH:
            return
        # A collapsed control's children are not on screen.
        if State.COLLAPSED in states:
            return
        childParentName = nameLower if interactive and nameLower else parentName
        if hasattr(obj, "IAccessibleObject") and obj.childCount > LARGE_CONTAINER_CHILDREN:
            # Fetching thousands of children at once (e.g. a big file list)
            # would block NVDA; walk them one by one instead.
            firstChild = obj.firstChild
            if firstChild:
                self._stack.append((firstChild, depth + 1, childParentName, {"seenVisible": False, "scrolledAway": 0}))
            return
        children = obj.children
        if not children:
            return
        # Reversed so the first child is popped first (document order).
        for child in reversed(children):
            if child:
                self._stack.append((child, depth + 1, childParentName, None))

    def _makeItem(self, obj, role, states, interactive, name, nameLower, parentName, rect):
        value = ""
        # A link's value is its address, which is only noise here.
        if interactive and role != Role.LINK:
            try:
                value = cleanText(obj.value)
            except Exception:
                value = ""
        if not name and not value:
            return None
        # Text inside or right after a control usually repeats part of the
        # control's own name (Discord: "Sound Arena" after the server item, or
        # the "2" of "2 mentions").
        if not interactive and nameLower and (
            nameLower in parentName or nameLower in self._lastInteractiveName
        ):
            return None

        key = (role, nameLower, value, rect)
        if key in self._seen:
            return None
        self._seen.add(key)

        roleName = getRoleName(obj)
        description = ""
        try:
            description = cleanText(obj.description)
        except Exception:
            pass
        self._order += 1
        self._lastInteractiveName = nameLower if interactive else ""
        return {
            "object": obj,
            "name": name,
            "value": value,
            "description": description,
            "role": roleName,
            "unavailable": State.UNAVAILABLE in states,
            "isInteractive": interactive,
            "order": self._order,
            "searchText": f"{name} {value} {description} {roleName}".lower(),
        }


def rankAndFilterControls(controls, query):
    """
    Every word of the query must appear somewhere in the control's text.
    Ranking: exact name > name starts with query > a word in the name starts
    with query > name contains query > other text matches; interactive controls
    before plain text; then screen (document) order.
    """
    query = (query or "").strip().lower()
    if not query:
        return list(controls)
    words = query.split()
    matches = [c for c in controls if all(w in c["searchText"] for w in words)]

    def sortKey(c):
        name = c["name"].lower()
        if name == query:
            tier = 0
        elif name.startswith(query):
            tier = 1
        elif any(w.startswith(query) for w in name.split()):
            tier = 2
        elif query in name:
            tier = 3
        elif all(w in name for w in words):
            tier = 4
        else:
            tier = 5
        return (tier, 0 if c["isInteractive"] else 1, c["order"])

    matches.sort(key=sortKey)
    return matches


def formatDisplayStrings(controls):
    """'Name, role'. Value/description are added only when they help.
    A comma, not a dash, so NVDA reads it as a natural pause."""
    result = []
    for c in controls:
        label = c["name"] or c["value"]
        if c["name"] and c["value"] and c["value"].lower() != c["name"].lower():
            label = f"{c['name']}: {c['value']}"
        text = f"{label}, {c['role']}"
        if c["unavailable"]:
            text += " " + _("(unavailable)")
        result.append(text)
    # Add the description to entries that would otherwise read the same.
    counts = {}
    for t in result:
        counts[t] = counts.get(t, 0) + 1
    for i, c in enumerate(controls):
        desc = c["description"]
        if counts[result[i]] > 1 and desc and desc.lower() != c["name"].lower():
            result[i] = f"{result[i]} ({desc})"
    return result


def _bringToForeground(obj):
    root = getRootWindow(getattr(obj, "windowHandle", None))
    if root and root != winUser.getForegroundWindow():
        try:
            winUser.setForegroundWindow(root)
        except Exception:
            log.debugWarning("QuickUISearch: setForegroundWindow failed", exc_info=True)


def _moveBrowseModeCaret(obj):
    """
    If the control is inside a document in browse mode, move the browse mode
    caret to it, like NVDA's Elements List does. Returns True on success.
    """
    try:
        import browseMode
        ti = obj.treeInterceptor
        if (
            not isinstance(ti, browseMode.BrowseModeDocumentTreeInterceptor)
            or ti.passThrough
            or not getattr(ti, "isReady", True)
        ):
            return False
        info = ti.makeTextInfo(obj)
        item = browseMode.TextInfoQuickNavItem("quickUISearch", ti, info)
        item.moveTo()
        # Drop NVDA's announcement of the window regaining focus, so the
        # control we moved to is what gets read.
        speech.cancelSpeech()
        item.report()
        return True
    except Exception:
        log.debugWarning("QuickUISearch: browse mode move failed", exc_info=True)
        return False


def _moveNavigator(obj):
    """Last resort for controls that cannot take focus: move NVDA's navigator
    object (review position) there and read it."""
    try:
        api.setNavigatorObject(obj)
        speech.cancelSpeech()
        speech.speakObject(obj, reason=controlTypes.OutputReason.QUERY)
        return True
    except Exception:
        log.debugWarning("QuickUISearch: navigator move failed", exc_info=True)
        return False


def jumpToControl(obj, originalFocus, onDone):
    """Move to the chosen control. Never pulls focus back afterwards."""
    try:
        _bringToForeground(obj)
        if _moveBrowseModeCaret(obj):
            onDone()
            return
        states = obj.states
        canFocus = State.FOCUSABLE in states and State.UNAVAILABLE not in states
        if canFocus:
            obj.setFocus()
            core.callLater(FOCUS_VERIFY_DELAY_MS, _verifyFocus, obj, originalFocus, onDone)
            return
        if not _moveNavigator(obj):
            ui.message(_("Unable to move to that control."))
    except Exception:
        log.debugWarning("QuickUISearch: jump failed", exc_info=True)
        if not _moveNavigator(obj):
            ui.message(_("Unable to move to that control."))
    onDone()


def _verifyFocus(obj, originalFocus, onDone):
    try:
        focus = api.getFocusObject()
        # If focus did not leave where it was, fall back to the review cursor.
        if focus != obj and (originalFocus is None or focus == originalFocus):
            log.debug("QuickUISearch: focus did not move, using navigator object")
            if not _moveNavigator(obj):
                ui.message(_("Unable to move to that control."))
    except Exception:
        log.debugWarning("QuickUISearch: focus verification failed", exc_info=True)
    finally:
        onDone()


def returnToOriginalFocus(originalFocus):
    """After Cancel, make sure the original application is in front again.
    Only touches focus if Windows did not already return it."""
    if not originalFocus:
        return
    try:
        focus = api.getFocusObject()
        origRoot = getRootWindow(getattr(originalFocus, "windowHandle", None))
        focusRoot = getRootWindow(getattr(focus, "windowHandle", None))
        if focus and focusRoot == origRoot:
            return
        _bringToForeground(originalFocus)
        originalFocus.setFocus()
    except Exception:
        log.debugWarning("QuickUISearch: could not restore focus", exc_info=True)


class QuickSearchDialog(wx.Dialog):

    def __init__(self, parent, searchRoot, windowRect, title):
        super().__init__(
            parent,
            title=title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            size=(540, 480),
        )
        self.searchRoot = searchRoot
        self.windowRect = windowRect
        self.allControls = []
        self.displayedMatches = []
        self.selectedObject = None
        self._closing = False
        self._scanner = None
        self._refreshTimer = None
        self._shownStrings = []

        mainSizer = wx.BoxSizer(wx.VERTICAL)

        mainSizer.Add(wx.StaticText(self, label=_("&Search for control:")), 0, wx.ALL, 6)
        self.searchTextCtrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        mainSizer.Add(self.searchTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.listLabel = wx.StaticText(self, label=_("&Results:"))
        mainSizer.Add(self.listLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.resultsListBox = wx.ListBox(self, style=wx.LB_SINGLE)
        mainSizer.Add(self.resultsListBox, 1, wx.EXPAND | wx.ALL, 6)

        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.okBtn = wx.Button(self, wx.ID_OK, label=_("&Go to control"))
        self.okBtn.Enable(False)
        btnSizer.Add(self.okBtn, 0, wx.RIGHT, 6)
        btnSizer.Add(wx.Button(self, wx.ID_CANCEL, label=_("Cancel")), 0)
        mainSizer.Add(btnSizer, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        self.SetSizer(mainSizer)
        self.CenterOnScreen()

        self.searchTextCtrl.Bind(wx.EVT_TEXT, lambda evt: self.applyFilter(keepSelection=False))
        self.resultsListBox.Bind(wx.EVT_LISTBOX_DCLICK, self.onConfirm)
        self.okBtn.Bind(wx.EVT_BUTTON, self.onConfirm)
        self.Bind(wx.EVT_BUTTON, self.onCancel, id=wx.ID_CANCEL)
        self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)
        self.Bind(wx.EVT_CLOSE, self.onCancel)
        self.Bind(wx.EVT_ACTIVATE, self.onActivate)

        self.searchTextCtrl.SetFocus()
        self.startScan()

    # Scanning

    def startScan(self):
        self.stopScan()
        self.allControls = []
        self.applyFilter(keepSelection=False)
        self._scanner = ScreenScanner(self.searchRoot, self.windowRect, self.onBatch, self.onScanDone)
        self._scanner.start()

    def stopScan(self):
        if self._scanner:
            self._scanner.cancel()
            self._scanner = None
        self._cancelRefresh()

    def _cancelRefresh(self):
        if self._refreshTimer:
            try:
                self._refreshTimer.Stop()
            except Exception:
                pass
            self._refreshTimer = None

    def onBatch(self, batch):
        if self._closing:
            return
        self.allControls.extend(batch)
        # Refresh the list a few times a second rather than on every batch.
        if not self._refreshTimer:
            self._refreshTimer = wx.CallLater(LIST_REFRESH_MS, self._onRefreshTimer)

    def _onRefreshTimer(self):
        self._refreshTimer = None
        self.applyFilter(keepSelection=True)

    def onScanDone(self):
        if self._closing:
            return
        self._cancelRefresh()
        self.applyFilter(keepSelection=True)
        count = len(self.displayedMatches)
        if self.searchTextCtrl.GetValue().strip():
            if count == 0:
                ui.message(_("No matches"))
            elif count == 1:
                ui.message(_("1 match"))
            else:
                ui.message(_("{count} matches").format(count=count))
        elif count == 0:
            ui.message(_("No controls found"))
        elif count == 1:
            ui.message(_("1 control"))
        else:
            ui.message(_("{count} controls").format(count=count))

    @property
    def isScanning(self):
        return bool(self._scanner and not self._scanner.finished)

    # List

    def applyFilter(self, keepSelection=True):
        if self._closing:
            return
        query = self.searchTextCtrl.GetValue()
        selectedObj = None
        if keepSelection:
            sel = self.resultsListBox.GetSelection()
            if sel != wx.NOT_FOUND and sel < len(self.displayedMatches):
                selectedObj = self.displayedMatches[sel]["object"]

        self.displayedMatches = rankAndFilterControls(self.allControls, query)
        count = len(self.displayedMatches)
        self.listLabel.SetLabel(_("&Results ({count}):").format(count=count))
        newStrings = formatDisplayStrings(self.displayedMatches)
        oldStrings = self._shownStrings
        self._shownStrings = newStrings

        if keepSelection and newStrings == oldStrings:
            return
        if keepSelection and oldStrings and newStrings[:len(oldStrings)] == oldStrings:
            # New items only arrived at the end: add them without touching the
            # selection, so NVDA does not re-read the current item.
            self.resultsListBox.Append(newStrings[len(oldStrings):])
            self.okBtn.Enable(count > 0)
            return

        newSel = 0
        if selectedObj is not None:
            for i, c in enumerate(self.displayedMatches):
                if c["object"] is selectedObj:
                    newSel = i
                    break

        self.resultsListBox.Freeze()
        try:
            self.resultsListBox.Set(newStrings)
            if count:
                self.resultsListBox.SetSelection(newSel)
        finally:
            self.resultsListBox.Thaw()
        self.okBtn.Enable(count > 0)

    # Closing

    def onConfirm(self, evt=None):
        if self._closing:
            return
        sel = self.resultsListBox.GetSelection()
        if sel == wx.NOT_FOUND or sel >= len(self.displayedMatches):
            if self.isScanning:
                ui.message(_("Still scanning the screen…"))
            else:
                ui.message(_("No matching controls on this screen."))
            return
        self.selectedObject = self.displayedMatches[sel]["object"]
        self._close(wx.ID_OK)

    def onCancel(self, evt=None):
        if self._closing:
            return
        self.selectedObject = None
        self._close(wx.ID_CANCEL)

    def onActivate(self, evt):
        # If you switch to another window (or an app grabs the foreground),
        # close instead of staying open, hidden, and catching your typing.
        if not evt.GetActive() and not self._closing:
            wx.CallAfter(self._abandon)
        evt.Skip()

    def _abandon(self):
        if self._closing or self.IsActive():
            return
        self.selectedObject = None
        self._close(wx.ID_ABORT)

    def _close(self, result):
        self._closing = True
        self.stopScan()
        if self.IsModal():
            self.EndModal(result)

    def onCharHook(self, evt):
        if self._closing:
            return
        key = evt.GetKeyCode()
        focused = self.FindFocus()

        if evt.ControlDown() and not evt.AltDown() and key == ord("R"):
            ui.message(_("Rescanning"))
            self.startScan()
            return
        if key == wx.WXK_ESCAPE:
            self.onCancel()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if focused == self.searchTextCtrl and not self.displayedMatches:
                self.onConfirm()  # announces why nothing can be chosen
                return
            if focused in (self.searchTextCtrl, self.resultsListBox, self.okBtn):
                self.onConfirm()
                return
        if key == wx.WXK_DOWN and focused == self.searchTextCtrl and self.displayedMatches:
            self.resultsListBox.SetFocus()
            return
        evt.Skip()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """Has no event handlers: it does nothing until NVDA+J is pressed."""

    scriptCategory = _("Quick UI Search")

    def __init__(self):
        super().__init__()
        self._busy = False

    @scriptHandler.script(
        description=_("Searches the controls on the current screen and moves to the one you choose."),
        gesture="kb:NVDA+j",
    )
    def script_quickUISearch(self, gesture):
        if self._busy:
            return
        self._busy = True
        wx.CallAfter(self._openSearchDialog)

    def _done(self):
        self._busy = False

    def _openSearchDialog(self):
        try:
            originalFocus = api.getFocusObject()
            foreground = api.getForegroundObject() or originalFocus
            if not foreground:
                ui.message(_("No accessible application in the foreground."))
                self._done()
                return
            searchRoot = resolveSearchRoot(originalFocus, foreground)
            hwnd = getattr(foreground, "windowHandle", None) or winUser.getForegroundWindow()
            windowRect = getVisibleArea(searchRoot, hwnd)

            title = _("Quick UI Search")
            appTitle = (getattr(searchRoot, "name", None) or getattr(foreground, "name", None) or "").strip()
            if appTitle:
                title = f"{title} - {appTitle}"
        except Exception:
            log.error("QuickUISearch: could not prepare the search", exc_info=True)
            ui.message(_("Unable to search this screen."))
            self._done()
            return

        import gui
        result = wx.ID_CANCEL
        selected = None
        dialog = None
        gui.mainFrame.prePopup()
        try:
            dialog = QuickSearchDialog(gui.mainFrame, searchRoot, windowRect, title)
            result = dialog.ShowModal()
            selected = dialog.selectedObject
        except Exception:
            log.error("QuickUISearch: dialog error", exc_info=True)
        finally:
            if dialog:
                dialog.stopScan()
                dialog.Destroy()
            gui.mainFrame.postPopup()

        if result == wx.ID_OK and selected is not None:
            core.callLater(JUMP_DELAY_MS, jumpToControl, selected, originalFocus, self._done)
        elif result == wx.ID_ABORT:
            # You switched away yourself; leave focus where you put it.
            self._done()
        else:
            core.callLater(JUMP_DELAY_MS, self._cancelled, originalFocus)

    def _cancelled(self, originalFocus):
        try:
            returnToOriginalFocus(originalFocus)
        finally:
            self._done()
