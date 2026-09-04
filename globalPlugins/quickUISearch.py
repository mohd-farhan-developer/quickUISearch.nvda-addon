# -*- coding: UTF-8 -*-
# Quick UI Search Add-on for NVDA
# TalkBack-style Screen Search:
# Searches strictly within the active visible interface container (voice channel, chat view, dialog, or tab panel),
# excluding background views, other Discord servers/channels, and off-screen elements.

import collections
import threading
import wx
import api
import ui
import core
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

# Clean up any leftover configuration entries from Search Mode
try:
    import config
    if "quickUISearch" in config.conf:
        del config.conf["quickUISearch"]
    if hasattr(config.conf, 'spec') and "quickUISearch" in config.conf.spec:
        del config.conf.spec["quickUISearch"]
except Exception:
    pass

def _(s):
    # Fallback gettext translator if not already in builtins
    try:
        import builtins
        if hasattr(builtins, '_'):
            return builtins._(s)
    except Exception:
        pass
    return s


# Interactive Control Roles definition
INTERACTIVE_ROLES = {
    getattr(controlTypes.Role, 'BUTTON', None),
    getattr(controlTypes.Role, 'TOGGLE_BUTTON', None),
    getattr(controlTypes.Role, 'MENUITEM', None),
    getattr(controlTypes.Role, 'MENUBUTTON', None),
    getattr(controlTypes.Role, 'POPUPMENU', None),
    getattr(controlTypes.Role, 'LINK', None),
    getattr(controlTypes.Role, 'EDITABLETEXT', None),
    getattr(controlTypes.Role, 'PASSWORDEDIT', None),
    getattr(controlTypes.Role, 'SPINBUTTON', None),
    getattr(controlTypes.Role, 'CHECKBOX', None),
    getattr(controlTypes.Role, 'RADIOBUTTON', None),
    getattr(controlTypes.Role, 'COMBOBOX', None),
    getattr(controlTypes.Role, 'TAB', None),
    getattr(controlTypes.Role, 'TABCONTROL', None),
    getattr(controlTypes.Role, 'LISTITEM', None),
    getattr(controlTypes.Role, 'TREEVIEWITEM', None),
    getattr(controlTypes.Role, 'SLIDER', None),
}
INTERACTIVE_ROLES.discard(None)

INTERACTIVE_ROLE_KEYWORDS = (
    "button", "toggle", "menu", "link", "edit", "box", "radio",
    "combo", "tab", "list item", "tree item", "slider", "check",
    "channel", "switch", "picker", "text field", "entry"
)


def getRoleName(obj):
    """Retrieve clean, human-readable role name across NVDA versions."""
    try:
        if hasattr(obj, 'roleText') and obj.roleText:
            return obj.roleText
        role = getattr(obj, 'role', None)
        if role is not None:
            if hasattr(role, 'displayString') and role.displayString:
                return role.displayString
            if hasattr(controlTypes, 'roleLabels') and role in controlTypes.roleLabels:
                return controlTypes.roleLabels[role]
            if hasattr(controlTypes, 'role') and hasattr(controlTypes.role, 'Role'):
                return getattr(role, 'displayString', str(role))
            return str(role)
    except Exception:
        pass
    return _("Control")


def checkIsInteractive(obj, roleStr):
    """Determine if a control is interactive."""
    try:
        role = getattr(obj, 'role', None)
        if role in INTERACTIVE_ROLES:
            return True
    except Exception:
        pass

    roleLower = (roleStr or "").lower()
    for kw in INTERACTIVE_ROLE_KEYWORDS:
        if kw in roleLower:
            return True
    return False


def getCenterCoordinates(obj):
    """Extract center (X, Y) pixel coordinates from an accessible object."""
    try:
        loc = getattr(obj, 'location', None)
        if loc:
            if hasattr(loc, 'left') and hasattr(loc, 'top') and hasattr(loc, 'width') and hasattr(loc, 'height'):
                return (loc.left + loc.width // 2, loc.top + loc.height // 2)
            elif isinstance(loc, (tuple, list)) and len(loc) >= 4:
                return (loc[0] + loc[2] // 2, loc[1] + loc[3] // 2)
    except Exception:
        pass
    return None


def getWindowBoundingRect(hwnd):
    """Get the bounding rectangle (left, top, width, height) of a window."""
    if not hwnd:
        return None
    try:
        rect = winUser.getWindowRect(hwnd)
        if rect:
            if hasattr(rect, 'left') and hasattr(rect, 'top') and hasattr(rect, 'right') and hasattr(rect, 'bottom'):
                return (rect.left, rect.top, max(0, rect.right - rect.left), max(0, rect.bottom - rect.top))
            elif isinstance(rect, (tuple, list)) and len(rect) >= 4:
                return (rect[0], rect[1], max(0, rect[2] - rect[0]), max(0, rect[3] - rect[1]))
    except Exception:
        pass
    return None


def isControlVisibleOnScreen(obj, windowRect=None):
    """
    Strict Screen-Search Visibility Check.
    Ensures the control is physically visible on the active screen and not hidden,
    collapsed, offscreen, zero-size, or clipped outside the window viewport.
    """
    if obj is None:
        return False

    try:
        states = getattr(obj, 'states', set())

        # 1. State filtering: Must not be invisible, unavailable, or offscreen
        if (
            controlTypes.State.INVISIBLE in states or
            controlTypes.State.UNAVAILABLE in states or
            controlTypes.State.OFFSCREEN in states
        ):
            return False

        # If collapsed container with no direct interactivity, skip child branch
        if controlTypes.State.COLLAPSED in states:
            role = getattr(obj, 'role', None)
            if role not in INTERACTIVE_ROLES:
                return False

    except Exception:
        return False

    try:
        # 2. Location & Dimensions Check
        loc = getattr(obj, 'location', None)
        if not loc:
            if hasattr(obj, 'isLocationOnScreen') and not obj.isLocationOnScreen:
                return False
            return True

        left = getattr(loc, 'left', None)
        top = getattr(loc, 'top', None)
        width = getattr(loc, 'width', None)
        height = getattr(loc, 'height', None)

        if left is None and isinstance(loc, (tuple, list)) and len(loc) >= 4:
            left, top, width, height = loc[0], loc[1], loc[2], loc[3]

        if width is not None and height is not None:
            # Must have positive dimensions on screen
            if width <= 0 or height <= 0:
                return False

        # 3. Window Viewport Boundary Check
        if windowRect and left is not None and top is not None and width is not None and height is not None:
            wLeft, wTop, wWidth, wHeight = windowRect
            wRight = wLeft + wWidth
            wBottom = wTop + wHeight

            objRight = left + width
            objBottom = top + height

            # Must intersect with the active window viewport
            if objRight <= wLeft or left >= wRight or objBottom <= wTop or top >= wBottom:
                return False

    except Exception as e:
        log.debug(f"QuickUISearch: visibility check error: {e}")

    return True


def getObjectId(obj):
    """Generate a stable, unique identifier for an accessible object."""
    if obj is None:
        return None
    try:
        uiaElem = getattr(obj, 'UIAutomationElement', None) or getattr(obj, 'UIAElement', None)
        if uiaElem:
            try:
                rtId = uiaElem.GetRuntimeId()
                if rtId:
                    return ("UIA_RT", tuple(rtId))
            except Exception:
                pass

        hwnd = getattr(obj, 'windowHandle', None)
        childId = getattr(obj, 'IAccessibleChildID', None)
        role = getattr(obj, 'role', None)

        iacc = getattr(obj, 'IAccessibleObject', None)
        if iacc and hwnd is not None:
            try:
                iaccPtr = iacc._com_pointers.get(iacc._iid, None) if hasattr(iacc, '_com_pointers') else None
                if iaccPtr:
                    return ("IACC_PTR", hwnd, childId, iaccPtr)
            except Exception:
                pass

        loc = getattr(obj, 'location', None)
        locTuple = (loc.left, loc.top, loc.width, loc.height) if loc else None
        name = getattr(obj, 'name', '') or ''
        autoId = getattr(obj, 'UIAAutomationId', None)

        if autoId:
            return ("UIA_AUTOID", hwnd, autoId, role)

        return ("ACC_COMPOSITE", hwnd, childId, role, name, locTuple)
    except Exception:
        return ("OBJ_FALLBACK", getattr(obj, 'windowHandle', None), id(obj))


def extractControlInfo(obj, windowRect=None):
    """
    Inspect and extract searchable metadata for an accessible object.
    Only returns metadata if the control is confirmed visible on screen.
    """
    if not isControlVisibleOnScreen(obj, windowRect):
        return None

    try:
        name = getattr(obj, 'name', '') or ''
        desc = getattr(obj, 'description', '') or ''
        val = getattr(obj, 'value', '')
        valStr = str(val) if val is not None else ''
        roleStr = getRoleName(obj)

        name = name.strip()
        desc = desc.strip()
        valStr = valStr.strip()

        if name or desc or valStr:
            stableId = getObjectId(obj)
            isInteractive = checkIsInteractive(obj, roleStr)
            center = getCenterCoordinates(obj)
            hwnd = getattr(obj, 'windowHandle', None)

            return {
                "id": stableId,
                "object": obj,
                "name": name,
                "description": desc,
                "valStr": valStr,
                "role": roleStr,
                "isInteractive": isInteractive,
                "center": center,
                "hwnd": hwnd,
                "searchIndex": f"{name} {desc} {valStr} {roleStr}".lower(),
            }
    except Exception as e:
        log.debug(f"QuickUISearch: error extracting control info: {e}")
    return None


def formatDisplayStrings(controls):
    """
    Format display strings for a list of controls.
    Displays 'Name — Role'.
    Includes description only when needed to distinguish identical names.
    """
    counts = collections.Counter((c["name"].lower(), c["role"].lower()) for c in controls if c.get("name"))
    formatted = []
    for c in controls:
        name = c.get("name", "").strip()
        roleStr = c.get("role", "").strip() or _("Control")
        desc = c.get("description", "").strip()

        isDuplicateName = counts[(name.lower(), roleStr.lower())] > 1

        if not name:
            if desc:
                display = f"{desc} — {roleStr}"
            else:
                display = roleStr
        elif isDuplicateName and desc and desc.lower() != name.lower():
            display = f"{name} ({desc}) — {roleStr}"
        else:
            display = f"{name} — {roleStr}"

        formatted.append(display)
    return formatted


def rankAndFilterControls(allControls, query, focusInfo):
    """
    Rank visible screen controls by:
    1. Query matching tiers (exact name > starts with name > contains name > desc/value > role)
    2. Interactive controls first
    3. Proximity to current keyboard focus
    4. Shorter / cleaner names
    """
    queryLower = (query or "").strip().lower()

    if queryLower:
        candidates = [c for c in allControls if queryLower in c["searchIndex"]]
    else:
        candidates = list(allControls)

    focusHwnd, focusCenter = focusInfo

    def sortKey(c):
        nameLower = c["name"].lower()
        descLower = c["description"].lower()
        valLower = c.get("valStr", "").lower()
        roleLower = c["role"].lower()

        # 1. Match Tier
        if not queryLower:
            matchTier = 0
        elif nameLower == queryLower:
            matchTier = 0  # Exact name match
        elif nameLower.startswith(queryLower):
            matchTier = 1  # Name starts with search text
        elif queryLower in nameLower:
            matchTier = 2  # Name contains search text
        elif queryLower in descLower or queryLower in valLower:
            matchTier = 3  # Description/value contains search text
        elif queryLower in roleLower:
            matchTier = 4  # Role contains search text
        else:
            matchTier = 5

        # 2. Interactive controls first
        interactiveTier = 0 if c.get("isInteractive", False) else 1

        # 3. Focus proximity distance
        locCenter = c.get("center")
        if focusCenter and locCenter:
            dist = int(((focusCenter[0] - locCenter[0]) ** 2 + (focusCenter[1] - locCenter[1]) ** 2) ** 0.5)
        else:
            dist = 999999

        if focusHwnd and c.get("hwnd") != focusHwnd:
            dist += 10000

        return (matchTier, interactiveTier, dist, len(c["name"]), nameLower)

    candidates.sort(key=sortKey)
    return candidates


def countInteractiveDescendants(candidateContainer, windowRect, maxLimit=30):
    """
    Fast validation helper: counts visible interactive descendants within candidateContainer.
    Returns the count (capped at maxLimit for speed).
    """
    if not candidateContainer:
        return 0

    count = 0
    visited = set()
    queue = collections.deque([candidateContainer])

    while queue and count < maxLimit:
        node = queue.popleft()
        if not node:
            continue

        nodeId = getObjectId(node)
        if nodeId in visited:
            continue
        visited.add(nodeId)

        if not isControlVisibleOnScreen(node, windowRect):
            continue

        roleStr = getRoleName(node)
        if checkIsInteractive(node, roleStr):
            name = (getattr(node, 'name', '') or '').strip()
            desc = (getattr(node, 'description', '') or '').strip()
            val = str(getattr(node, 'value', '') or '').strip()
            if name or desc or val:
                count += 1

        try:
            children = node.children
            if children:
                for ch in children:
                    if ch:
                        queue.append(ch)
        except Exception:
            pass

    return count


def resolveActiveInteractiveScope(originalFocus, topRoot, windowRect):
    """
    Reliable Active Container Detection Algorithm:
    1. Start from the focused control.
    2. Ascend the parent hierarchy, evaluating each ancestor candidate.
    3. Count visible interactive descendants.
    4. If count = 0, automatically fall back to the next parent.
    5. Stop at the first ancestor with verified interactive controls.
    6. Falls back safely to topRoot so search never returns 0 results when controls are present.
    """
    log.info("QuickUISearch: Starting resolveActiveInteractiveScope")

    if not originalFocus:
        log.info(f"QuickUISearch: No originalFocus, using topRoot: {getRoleName(topRoot)}")
        return topRoot

    # Build chain of ancestors up to topRoot
    ancestors = []
    curr = originalFocus
    while curr and curr != topRoot:
        ancestors.append(curr)
        try:
            curr = getattr(curr, 'parent', None)
        except Exception:
            break
    ancestors.append(topRoot)

    log.info(f"QuickUISearch: Ancestor chain length: {len(ancestors)}")

    selectedScope = None
    selectedCount = 0

    for idx, candidate in enumerate(ancestors):
        roleName = getRoleName(candidate)
        candName = getattr(candidate, 'name', '') or ''

        log.info(f"QuickUISearch: [Attempt #{idx+1}] Evaluating candidate container: {roleName} ('{candName}')")

        count = countInteractiveDescendants(candidate, windowRect, maxLimit=30)
        log.info(f"QuickUISearch: Candidate {roleName} ('{candName}') interactive descendant count: {count}")

        if count > 0:
            if candidate == originalFocus and count <= 1 and len(ancestors) > idx + 1:
                parentCand = ancestors[idx + 1]
                parentCount = countInteractiveDescendants(parentCand, windowRect, maxLimit=30)
                if parentCount > 1:
                    log.info(f"QuickUISearch: Focused element expanded to parent {getRoleName(parentCand)} with {parentCount} controls")
                    selectedScope = parentCand
                    selectedCount = parentCount
                    break

            selectedScope = candidate
            selectedCount = count
            break
        else:
            log.info(f"QuickUISearch: Candidate {roleName} has 0 interactive controls; attempting fallback to next parent")

    if not selectedScope:
        selectedScope = topRoot
        selectedCount = countInteractiveDescendants(topRoot, windowRect, maxLimit=30)
        log.info(f"QuickUISearch: All intermediate ancestors empty, fell back to topRoot: {getRoleName(topRoot)} with {selectedCount} controls")

    finalRole = getRoleName(selectedScope)
    finalName = getattr(selectedScope, 'name', '') or ''
    log.info(f"QuickUISearch: Final selected scope: {finalRole} ('{finalName}') with {selectedCount} verified interactive controls")

    return selectedScope


class TreeCacheManager:
    """
    Manages active visible screen scoping and real-time synchronization.
    Builds a fresh visible scope on screen changes and purges background/stale controls.
    """

    def __init__(self):
        # scopeKey -> OrderedDict of { stableId: controlDict }
        self._scopeCache = {}
        self._currentScopeKey = None
        self._lock = threading.RLock()
        self._indexingThread = None
        self._liveScanThread = None
        self._cancelIndexingEvent = threading.Event()
        self._cancelLiveScanEvent = threading.Event()
        self.isIndexing = False

    def getVisibleControls(self, scopeKey):
        with self._lock:
            if scopeKey in self._scopeCache:
                return list(self._scopeCache[scopeKey].values())
            return []

    def setVisibleScope(self, scopeKey, controlsList):
        with self._lock:
            self._currentScopeKey = scopeKey
            self._scopeCache[scopeKey] = collections.OrderedDict((c["id"], c) for c in controlsList)

    def upsertVisibleControl(self, scopeKey, controlDict):
        if not controlDict:
            return False
        with self._lock:
            if scopeKey not in self._scopeCache:
                self._scopeCache[scopeKey] = collections.OrderedDict()
            isNew = controlDict["id"] not in self._scopeCache[scopeKey]
            self._scopeCache[scopeKey][controlDict["id"]] = controlDict
            return isNew

    def removeVisibleControl(self, scopeKey, objId):
        with self._lock:
            if scopeKey in self._scopeCache and objId in self._scopeCache[scopeKey]:
                del self._scopeCache[scopeKey][objId]
                return True
            return False

    def clearScope(self, scopeKey):
        with self._lock:
            if scopeKey in self._scopeCache:
                del self._scopeCache[scopeKey]

    def clearAll(self):
        with self._lock:
            self._scopeCache.clear()
            self._currentScopeKey = None

    def stopAllWorkers(self):
        """Immediately signals all background threads to stop."""
        log.info("QuickUISearch: stopAllWorkers requested")
        self._cancelIndexingEvent.set()
        self._cancelLiveScanEvent.set()
        self.isIndexing = False

    def startVisibleScopeIndexing(self, scopeRoot, scopeKey, windowRect, onBatch=None, onProgress=None, onFinished=None):
        """Start asynchronous BFS indexing strictly restricted to the visible screen scope."""
        self.stopAllWorkers()
        self._cancelIndexingEvent = threading.Event()
        cancelEvent = self._cancelIndexingEvent
        self.isIndexing = True

        thread = threading.Thread(
            target=self._visibleIndexingWorker,
            args=(scopeRoot, scopeKey, windowRect, cancelEvent, onBatch, onProgress, onFinished),
            name="QuickUISearchVisibleIndexingThread",
            daemon=True
        )
        self._indexingThread = thread
        log.info("QuickUISearch: Starting visible screen scope indexing worker")
        thread.start()

    def _visibleIndexingWorker(self, scopeRoot, scopeKey, windowRect, cancelEvent, onBatch, onProgress, onFinished):
        log.info(f"QuickUISearch: Visible indexing worker started for scope {scopeKey}")
        comInitialized = False
        try:
            import comtypes
            comtypes.CoInitialize()
            comInitialized = True
        except Exception as e:
            log.debug(f"QuickUISearch: CoInitialize exception: {e}")

        visited = set()
        queue = collections.deque([scopeRoot])
        processedCount = 0
        batch = []
        visibleDict = collections.OrderedDict()

        try:
            while queue and not cancelEvent.is_set():
                currentObj = queue.popleft()
                if not currentObj or cancelEvent.is_set():
                    break

                processedCount += 1
                if processedCount % 500 == 0:
                    log.debug(f"QuickUISearch: Visible indexing progress {processedCount} controls")
                    if onProgress and not cancelEvent.is_set():
                        wx.CallAfter(onProgress, processedCount)

                objId = getObjectId(currentObj)
                if objId in visited:
                    continue
                visited.add(objId)

                # Skip completely non-visible or off-screen branches
                if not isControlVisibleOnScreen(currentObj, windowRect):
                    continue

                info = extractControlInfo(currentObj, windowRect)
                if info:
                    visibleDict[info["id"]] = info
                    batch.append(info)

                if len(batch) >= 20:
                    if onBatch and not cancelEvent.is_set():
                        batchCopy = list(batch)
                        wx.CallAfter(onBatch, batchCopy)
                    batch.clear()

                if cancelEvent.is_set():
                    break

                # Enqueue visible children
                try:
                    children = currentObj.children
                    if children:
                        for child in children:
                            if child and not cancelEvent.is_set():
                                queue.append(child)
                except Exception as e:
                    log.debug(f"QuickUISearch: error getting children: {e}")

            if batch and onBatch and not cancelEvent.is_set():
                batchCopy = list(batch)
                wx.CallAfter(onBatch, batchCopy)

        except Exception as e:
            log.error(f"QuickUISearch: visible indexing error: {e}", exc_info=True)
        finally:
            if comInitialized:
                try:
                    import comtypes
                    comtypes.CoUninitialize()
                except Exception:
                    pass

        log.info(f"QuickUISearch: Visible indexing completed. Found {len(visibleDict)} visible controls")

        if not cancelEvent.is_set():
            self.setVisibleScope(scopeKey, list(visibleDict.values()))
            self.isIndexing = False
            if onFinished and not cancelEvent.is_set():
                wx.CallAfter(onFinished, list(visibleDict.values()))

    def startVisibleLiveScan(self, scopeRoot, focusedObj, scopeKey, windowRect, onNewMatches=None, onFinished=None):
        """Live scan strictly restricted to visible screen elements in the active container."""
        self._cancelLiveScanEvent.set()
        self._cancelLiveScanEvent = threading.Event()
        cancelEvent = self._cancelLiveScanEvent

        thread = threading.Thread(
            target=self._visibleLiveScanWorker,
            args=(scopeRoot, focusedObj, scopeKey, windowRect, cancelEvent, onNewMatches, onFinished),
            name="QuickUISearchVisibleLiveScanThread",
            daemon=True
        )
        self._liveScanThread = thread
        log.info("QuickUISearch: Starting visible live scan worker")
        thread.start()

    def _visibleLiveScanWorker(self, scopeRoot, focusedObj, scopeKey, windowRect, cancelEvent, onNewMatches, onFinished):
        comInitialized = False
        try:
            import comtypes
            comtypes.CoInitialize()
            comInitialized = True
        except Exception as e:
            log.debug(f"QuickUISearch: CoInitialize exception: {e}")

        newDiscovered = []
        visited = set()
        queue = collections.deque()

        if focusedObj:
            queue.append(focusedObj)
        if scopeRoot and scopeRoot != focusedObj:
            queue.append(scopeRoot)

        try:
            while queue and not cancelEvent.is_set():
                currentObj = queue.popleft()
                if not currentObj or cancelEvent.is_set():
                    break

                objId = getObjectId(currentObj)
                if objId in visited:
                    continue
                visited.add(objId)

                if not isControlVisibleOnScreen(currentObj, windowRect):
                    continue

                info = extractControlInfo(currentObj, windowRect)
                if info:
                    isNew = self.upsertVisibleControl(scopeKey, info)
                    if isNew:
                        newDiscovered.append(info)

                if cancelEvent.is_set():
                    break

                try:
                    children = currentObj.children
                    if children:
                        for child in children:
                            if child and not cancelEvent.is_set():
                                queue.append(child)
                except Exception as e:
                    log.debug(f"QuickUISearch: live scan children error: {e}")

        except Exception as e:
            log.debug(f"QuickUISearch: live scan error: {e}")
        finally:
            if comInitialized:
                try:
                    import comtypes
                    comtypes.CoUninitialize()
                except Exception:
                    pass

        log.info(f"QuickUISearch: Live scan worker exiting. Cancelled={cancelEvent.is_set()}, NewDiscovered={len(newDiscovered)}")

        if not cancelEvent.is_set():
            if newDiscovered and onNewMatches and not cancelEvent.is_set():
                wx.CallAfter(onNewMatches, list(newDiscovered))
            if onFinished and not cancelEvent.is_set():
                wx.CallAfter(onFinished)

    def scanVisibleBranch(self, branchRoot, scopeKey, windowRect):
        """Synchronously scan a small branch of visible controls on the main thread."""
        if not branchRoot or not isControlVisibleOnScreen(branchRoot, windowRect):
            return []

        results = []
        visited = set()
        queue = collections.deque([branchRoot])

        while queue:
            currentObj = queue.popleft()
            if not currentObj:
                continue

            objId = getObjectId(currentObj)
            if objId in visited:
                continue
            visited.add(objId)

            if not isControlVisibleOnScreen(currentObj, windowRect):
                continue

            info = extractControlInfo(currentObj, windowRect)
            if info:
                self.upsertVisibleControl(scopeKey, info)
                results.append(info)

            try:
                children = currentObj.children
                if children:
                    for child in children:
                        if child:
                            queue.append(child)
            except Exception:
                pass

        return results


# Global singleton cache manager
cacheManager = TreeCacheManager()


def jumpToControl(targetObj, originalFocus):
    """Move OS and NVDA focus directly to target control on the main thread, or announce if unavailable."""
    log.info("QuickUISearch: jumpToControl executing on main thread")
    if not targetObj:
        log.warning("QuickUISearch: targetObj is None")
        try:
            ui.message(_("Control no longer available."))
        except Exception:
            pass
        restoreFocus(originalFocus)
        return

    try:
        states = getattr(targetObj, 'states', None)
        if states is None or controlTypes.State.UNAVAILABLE in states:
            log.warning("QuickUISearch: targetObj is unavailable")
            try:
                ui.message(_("Control no longer available."))
            except Exception:
                pass
            restoreFocus(originalFocus)
            return

        hwnd = getattr(targetObj, 'windowHandle', None)
        if hwnd:
            try:
                winUser.setForegroundWindow(hwnd)
            except Exception as e:
                log.debug(f"QuickUISearch: setForegroundWindow error: {e}")

        try:
            targetObj.setFocus()
        except Exception as e:
            log.debug(f"QuickUISearch: targetObj.setFocus error: {e}")

        try:
            api.setFocusObject(targetObj)
        except Exception as e:
            log.debug(f"QuickUISearch: api.setFocusObject error: {e}")

        try:
            targetObj.reportFocus()
        except Exception as e:
            log.debug(f"QuickUISearch: reportFocus error: {e}")

        log.info("QuickUISearch: jumpToControl successfully completed")
    except Exception as e:
        log.error(f"QuickUISearch: exception in jumpToControl: {e}", exc_info=True)
        try:
            ui.message(_("Control no longer available."))
        except Exception:
            pass
        restoreFocus(originalFocus)


def restoreFocus(origObj):
    """Restore keyboard focus to original focus object on the main thread upon dialog cancellation."""
    log.info("QuickUISearch: restoreFocus executing on main thread")
    if not origObj:
        log.warning("QuickUISearch: origObj is None")
        return

    try:
        hwnd = getattr(origObj, 'windowHandle', None)
        if hwnd:
            try:
                winUser.setForegroundWindow(hwnd)
            except Exception as e:
                log.debug(f"QuickUISearch: restore setForegroundWindow error: {e}")

        try:
            origObj.setFocus()
        except Exception as e:
            log.debug(f"QuickUISearch: restore origObj.setFocus error: {e}")

        try:
            api.setFocusObject(origObj)
        except Exception as e:
            log.debug(f"QuickUISearch: restore api.setFocusObject error: {e}")

        log.info("QuickUISearch: restoreFocus successfully completed")
    except Exception as e:
        log.error(f"QuickUISearch: exception in restoreFocus: {e}", exc_info=True)


class QuickSearchDialog(wx.Dialog):
    """
    TalkBack-Style Screen Search Dialog.
    Searches ONLY the validated active visible container.
    """

    def __init__(self, parent, rootObj, originalFocus, hwnd):
        log.info(f"QuickUISearch: QuickSearchDialog __init__ starting for HWND {hwnd}")
        self._isAlive = True

        self.rootObj = rootObj
        self.originalFocus = originalFocus
        self.hwnd = hwnd
        self.windowRect = getWindowBoundingRect(hwnd)

        # Detect and validate the nearest active container with verified interactive controls
        self.scopeRoot = resolveActiveInteractiveScope(originalFocus, rootObj, self.windowRect)

        # Scope key identifying the active visible interface
        self.scopeKey = (
            hwnd,
            getObjectId(self.scopeRoot),
            getattr(self.scopeRoot, 'name', '') or getattr(rootObj, 'name', '')
        )

        appName = ""
        try:
            appName = getattr(self.scopeRoot, 'name', None) or getattr(rootObj, 'name', None) or rootObj.appModule.appName
        except Exception:
            pass

        title = _("Quick UI Search")
        if appName:
            title = f"{title} - {appName}"

        super().__init__(
            parent,
            title=title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            size=(540, 480)
        )

        # Focus info for ranking proximity
        focusHwnd = getattr(originalFocus, 'windowHandle', None) or hwnd
        focusCenter = getCenterCoordinates(originalFocus)
        self.focusInfo = (focusHwnd, focusCenter)

        self.allControls = []
        self.displayedMatches = []
        self.selectedObject = None

        mainSizer = wx.BoxSizer(wx.VERTICAL)

        # Search query field
        searchLabel = wx.StaticText(self, label=_("&Search for control:"))
        mainSizer.Add(searchLabel, 0, wx.ALL, 6)

        self.searchTextCtrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        mainSizer.Add(self.searchTextCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        # Controls List / Results Label
        self.listLabel = wx.StaticText(self, label=_("&Visible Controls List:"))
        mainSizer.Add(self.listLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)

        # Controls List Box
        self.resultsListBox = wx.ListBox(self, style=wx.LB_SINGLE)
        mainSizer.Add(self.resultsListBox, 1, wx.EXPAND | wx.ALL, 6)

        # Buttons
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)

        self.okBtn = wx.Button(self, wx.ID_OK, label=_("&Focus Control"))
        self.okBtn.Enable(False)
        btnSizer.Add(self.okBtn, 0, wx.RIGHT, 6)

        self.cancelBtn = wx.Button(self, wx.ID_CANCEL, label=_("Cancel"))
        btnSizer.Add(self.cancelBtn, 0, 0)

        mainSizer.Add(btnSizer, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        self.SetSizer(mainSizer)
        self.CenterOnScreen()

        # Bindings
        self.searchTextCtrl.Bind(wx.EVT_TEXT, self.onSearchTextChanged)
        self.searchTextCtrl.Bind(wx.EVT_TEXT_ENTER, self.onSearchEnter)
        self.resultsListBox.Bind(wx.EVT_LISTBOX_DCLICK, self.onConfirm)
        self.okBtn.Bind(wx.EVT_BUTTON, self.onConfirm)
        self.cancelBtn.Bind(wx.EVT_BUTTON, self.onCancel)
        self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)
        self.Bind(wx.EVT_CLOSE, self.onCloseWindow)

        self.searchTextCtrl.SetFocus()
        log.info(f"QuickUISearch: Active scope initialized: {self.scopeKey}")
        self.initScopeIndex()

    def isValid(self):
        """Check if dialog instance is valid and not destroyed."""
        if not getattr(self, '_isAlive', False):
            return False
        try:
            return bool(super().__nonzero__())
        except Exception:
            return False

    def cleanupAndClose(self, resultCode):
        """Safely stops workers, marks dialog as dead, and ends modal."""
        log.info(f"QuickUISearch: cleanupAndClose called with resultCode {resultCode}")
        self._isAlive = False
        cacheManager.stopAllWorkers()
        try:
            if self.IsModal():
                self.EndModal(resultCode)
        except Exception as e:
            log.error(f"QuickUISearch: error in EndModal: {e}")

    def destroyDialog(self):
        """Safely destroys wx dialog on the main thread."""
        log.info("QuickUISearch: destroyDialog starting")
        self._isAlive = False
        cacheManager.stopAllWorkers()
        try:
            self.Destroy()
        except Exception as e:
            log.error(f"QuickUISearch: error destroying dialog: {e}")
        log.info("QuickUISearch: destroyDialog completed")

    def initScopeIndex(self):
        """Load or build the active visible screen scope index."""
        cached = cacheManager.getVisibleControls(self.scopeKey)
        if cached:
            log.info(f"QuickUISearch: Loading {len(cached)} cached visible controls for scope")
            self.allControls = cached
            self.applyFilter(preserveSelection=False)
            self.startVisibleLiveScan()
        else:
            log.info("QuickUISearch: Starting visible scope indexing for active screen")
            self.allControls = []
            self.applyFilter(preserveSelection=False)
            self.startVisibleScopeIndexing()

    def startVisibleScopeIndexing(self):
        """Initiate background indexing strictly for visible screen controls."""
        try:
            ui.message(_("Indexing visible screen controls…"))
        except Exception:
            pass

        def onBatch(newBatch):
            if not self.isValid():
                return
            existingIds = {c["id"] for c in self.allControls}
            added = False
            for item in newBatch:
                if item["id"] not in existingIds:
                    self.allControls.append(item)
                    existingIds.add(item["id"])
                    added = True
            if added:
                self.applyFilter(preserveSelection=True)

        def onProgress(count):
            if self.isValid():
                try:
                    ui.message(_("{count} controls indexed").format(count=count))
                except Exception:
                    pass

        def onFinished(allItems):
            if not self.isValid():
                return
            log.info(f"QuickUISearch: Visible indexing finished with {len(allItems)} controls")
            self.allControls = allItems
            self.applyFilter(preserveSelection=True)
            try:
                ui.message(_("Ready"))
            except Exception:
                pass

        cacheManager.startVisibleScopeIndexing(
            self.scopeRoot,
            self.scopeKey,
            self.windowRect,
            onBatch=onBatch,
            onProgress=onProgress,
            onFinished=onFinished
        )

    def startVisibleLiveScan(self):
        """Asynchronously scan for new visible controls in active container and merge."""
        def onNewDiscovered(newDiscoveredList):
            if not self.isValid() or not newDiscoveredList:
                return
            existingIds = {c["id"] for c in self.allControls}
            newAddedCount = 0
            for item in newDiscoveredList:
                if item["id"] not in existingIds:
                    self.allControls.append(item)
                    existingIds.add(item["id"])
                    newAddedCount += 1

            if newAddedCount > 0:
                log.info(f"QuickUISearch: Visible live scan merged {newAddedCount} new controls")
                query = self.searchTextCtrl.GetValue().strip().lower()
                self.applyFilter(preserveSelection=True)
                if query:
                    matchingNew = [it for it in newDiscoveredList if query in it["searchIndex"]]
                    if len(matchingNew) == 1:
                        ui.message(_("1 new match found."))
                    elif len(matchingNew) > 1:
                        ui.message(_("{count} new matches found.").format(count=len(matchingNew)))

        cacheManager.startVisibleLiveScan(
            self.scopeRoot,
            self.originalFocus,
            self.scopeKey,
            self.windowRect,
            onNewMatches=onNewDiscovered
        )

    def onSearchTextChanged(self, evt):
        """Real-time filter as the user types without requiring Enter."""
        if not self.isValid():
            return
        self.applyFilter(preserveSelection=False)

    def applyFilter(self, preserveSelection=True):
        """Apply smart ranking to visible controls and update display with 'Name — Role'."""
        if not self.isValid():
            return

        query = self.searchTextCtrl.GetValue().strip()

        # Rank and filter strictly within visible screen controls
        self.displayedMatches = rankAndFilterControls(self.allControls, query, self.focusInfo)

        # Update Label
        if not query:
            self.listLabel.SetLabel(_("&Visible Controls List ({count}):").format(count=len(self.displayedMatches)))
        else:
            self.listLabel.SetLabel(_("&Matching controls ({count}):").format(count=len(self.displayedMatches)))

        displayStrings = formatDisplayStrings(self.displayedMatches)

        # Flicker-free ListBox update
        self.resultsListBox.Freeze()
        try:
            curSel = self.resultsListBox.GetSelection()
            self.resultsListBox.Set(displayStrings)

            matchCount = len(displayStrings)
            if matchCount > 0:
                if preserveSelection and curSel != wx.NOT_FOUND and curSel < matchCount:
                    self.resultsListBox.SetSelection(curSel)
                else:
                    self.resultsListBox.SetSelection(0)
                self.okBtn.Enable(True)
            else:
                self.okBtn.Enable(False)
        finally:
            self.resultsListBox.Thaw()

    def onSearchEnter(self, evt):
        """Enter key in search box moves focus to list or confirms selection."""
        if not self.isValid():
            return
        if len(self.displayedMatches) > 0:
            self.resultsListBox.SetFocus()
        else:
            if cacheManager.isIndexing:
                try:
                    ui.message(_("Searching visible screen..."))
                except Exception:
                    pass
            else:
                try:
                    ui.message(_("No matching controls found on the current screen."))
                except Exception:
                    pass

    def handleObjectEvent(self, obj, eventType):
        """Handle real-time UI Automation / IAccessible events for the active visible screen."""
        if not self.isValid():
            return

        try:
            objId = getObjectId(obj)

            if eventType in ("hide",):
                cacheManager.removeVisibleControl(self.scopeKey, objId)
                self.allControls = [c for c in self.allControls if c["id"] != objId]
                self.applyFilter(preserveSelection=True)
                return

            if eventType == "stateChange":
                if not isControlVisibleOnScreen(obj, self.windowRect):
                    cacheManager.removeVisibleControl(self.scopeKey, objId)
                    self.allControls = [c for c in self.allControls if c["id"] != objId]
                    self.applyFilter(preserveSelection=True)
                    return

            if eventType in ("structureChanged", "show"):
                updatedControls = cacheManager.scanVisibleBranch(obj, self.scopeKey, self.windowRect)
                existingIds = {c["id"] for c in self.allControls}
                added = False
                for item in updatedControls:
                    if item["id"] not in existingIds:
                        self.allControls.append(item)
                        existingIds.add(item["id"])
                        added = True
                    else:
                        idx = next((i for i, c in enumerate(self.allControls) if c["id"] == item["id"]), None)
                        if idx is not None:
                            self.allControls[idx] = item
                if added or updatedControls:
                    self.applyFilter(preserveSelection=True)
                return

            if eventType in ("nameChange", "descriptionChange", "valueChange", "stateChange"):
                info = extractControlInfo(obj, self.windowRect)
                if not info:
                    cacheManager.removeVisibleControl(self.scopeKey, objId)
                    self.allControls = [c for c in self.allControls if c["id"] != objId]
                    self.applyFilter(preserveSelection=True)
                    return

                cacheManager.upsertVisibleControl(self.scopeKey, info)
                idx = next((i for i, c in enumerate(self.allControls) if c["id"] == objId), None)
                if idx is not None:
                    self.allControls[idx] = info
                else:
                    self.allControls.append(info)
                self.applyFilter(preserveSelection=True)

        except Exception as e:
            log.error(f"QuickUISearch: error in handleObjectEvent: {e}", exc_info=True)

    def onConfirm(self, evt):
        if not self.isValid():
            return

        log.info("QuickUISearch: onConfirm (Enter / OK) triggered")
        sel = self.resultsListBox.GetSelection()
        if sel != wx.NOT_FOUND and sel < len(self.displayedMatches):
            self.selectedObject = self.displayedMatches[sel]["object"]
            log.info(f"QuickUISearch: Selection confirmed for match '{self.displayedMatches[sel]['name']}'")
            self.cleanupAndClose(wx.ID_OK)
        else:
            log.info("QuickUISearch: onConfirm called with no list selection")

    def onCancel(self, evt):
        log.info("QuickUISearch: onCancel (Esc / Cancel) triggered")
        self.selectedObject = None
        self.cleanupAndClose(wx.ID_CANCEL)

    def onCloseWindow(self, evt):
        log.info("QuickUISearch: onCloseWindow (Window Close Event) triggered")
        self.onCancel(None)

    def reindexCurrentWindow(self):
        """Re-index the current visible screen (Ctrl+R)."""
        if not self.isValid():
            return
        log.info(f"QuickUISearch: Re-indexing visible screen scope {self.scopeKey}")
        cacheManager.clearScope(self.scopeKey)
        self.allControls = []
        self.displayedMatches = []
        self.resultsListBox.Clear()
        self.okBtn.Enable(False)
        self.startVisibleScopeIndexing()

    def clearCacheAndRebuild(self):
        """Clear all scopes completely and rebuild (Ctrl+Shift+R)."""
        if not self.isValid():
            return
        log.info("QuickUISearch: Clearing all cached scopes (Ctrl+Shift+R)")
        cacheManager.clearAll()
        self.allControls = []
        self.displayedMatches = []
        self.resultsListBox.Clear()
        self.okBtn.Enable(False)
        self.startVisibleScopeIndexing()

    def onCharHook(self, evt):
        if not self.isValid():
            return

        try:
            keyCode = evt.GetKeyCode()
            controlDown = evt.ControlDown()
            shiftDown = evt.ShiftDown()

            # Ctrl+Shift+R: Clear all cache and rebuild
            if controlDown and shiftDown and keyCode == ord('R'):
                self.clearCacheAndRebuild()
                return

            # Ctrl+R: Re-index current visible scope
            if controlDown and not shiftDown and keyCode == ord('R'):
                self.reindexCurrentWindow()
                return

            # Enter key handling
            if keyCode in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
                focused = self.FindFocus()
                if focused == self.searchTextCtrl:
                    if len(self.displayedMatches) > 0:
                        self.resultsListBox.SetFocus()
                        return
                    else:
                        self.onSearchEnter(None)
                        return
                elif focused == self.resultsListBox or focused == self.okBtn:
                    self.onConfirm(None)
                    return
                else:
                    self.onConfirm(None)
                    return

            # Down arrow in search box moves focus into Controls List
            if keyCode == wx.WXK_DOWN and self.FindFocus() == self.searchTextCtrl:
                if len(self.displayedMatches) > 0:
                    self.resultsListBox.SetFocus()
                    return

            # Escape key cancel
            if keyCode == wx.WXK_ESCAPE:
                self.onCancel(None)
                return
        except Exception as e:
            log.error(f"QuickUISearch: error in onCharHook: {e}", exc_info=True)

        evt.Skip()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """Quick UI Search Global Plugin with TalkBack-style screen search."""

    scriptCategory = _("Quick UI Search")

    def __init__(self):
        super().__init__()
        self._isDialogOpen = False
        self._activeDialog = None
        log.info("QuickUISearch: GlobalPlugin initialized")

    @scriptHandler.script(
        description=_("Searches accessible controls on the currently visible screen and moves focus to the selected control."),
        category=_("Quick UI Search"),
        gestures=["kb:NVDA+j"]
    )
    def script_quickUISearch(self, gesture):
        if self._isDialogOpen:
            log.info("QuickUISearch: Dialog already open, ignoring shortcut gesture")
            return
        log.info("QuickUISearch: Shortcut NVDA+J pressed, scheduling dialog on main thread")
        wx.CallAfter(self._openSearchDialog)

    def _openSearchDialog(self):
        if self._isDialogOpen:
            return

        originalFocus = api.getFocusObject()
        rootObj = api.getForegroundObject() or originalFocus

        if not rootObj:
            log.warning("QuickUISearch: No accessible foreground object available")
            try:
                ui.message(_("No accessible foreground application found."))
            except Exception:
                pass
            return

        hwnd = getattr(rootObj, 'windowHandle', None) or winUser.getForegroundWindow()
        log.info(f"QuickUISearch: _openSearchDialog starting for HWND {hwnd}")

        self._isDialogOpen = True
        import gui
        gui.mainFrame.prePopup()
        dialog = None
        res = wx.ID_CANCEL
        selectedObj = None

        try:
            dialog = QuickSearchDialog(gui.mainFrame, rootObj, originalFocus, hwnd)
            self._activeDialog = dialog
            res = dialog.ShowModal()
            selectedObj = dialog.selectedObject
        except Exception as e:
            log.error(f"QuickUISearch: exception during dialog ShowModal: {e}", exc_info=True)
        finally:
            log.info("QuickUISearch: Dialog modal completed, performing cleanup")
            self._isDialogOpen = False
            self._activeDialog = None
            cacheManager.stopAllWorkers()
            if dialog:
                dialog.destroyDialog()
            gui.mainFrame.postPopup()

        log.info(f"QuickUISearch: Dialog finished with result: {'OK' if res == wx.ID_OK else 'CANCEL'}")

        if res == wx.ID_OK and selectedObj:
            log.info("QuickUISearch: Scheduling jumpToControl on main thread (callLater 60ms)")
            core.callLater(60, jumpToControl, selectedObj, originalFocus)
        else:
            log.info("QuickUISearch: Scheduling restoreFocus on main thread (callLater 60ms)")
            core.callLater(60, restoreFocus, originalFocus)

    # Real-time event hooks
    def _dispatchRealtimeEvent(self, obj, eventType):
        if not self._isDialogOpen or self._activeDialog is None:
            return
        dialog = self._activeDialog
        if not dialog or not dialog.isValid():
            return

        try:
            dialogHwnd = getattr(dialog, 'hwnd', None)
            objHwnd = getattr(obj, 'windowHandle', None)
            if objHwnd and dialogHwnd:
                if winUser.getAncestor(objHwnd, winUser.GA_ROOT) != winUser.getAncestor(dialogHwnd, winUser.GA_ROOT):
                    return

            def eventCallback():
                try:
                    if dialog and dialog.isValid():
                        dialog.handleObjectEvent(obj, eventType)
                except Exception as ex:
                    log.error(f"QuickUISearch: exception in eventCallback ({eventType}): {ex}", exc_info=True)

            wx.CallAfter(eventCallback)
        except Exception as e:
            log.error(f"QuickUISearch: exception in _dispatchRealtimeEvent ({eventType}): {e}", exc_info=True)

    def event_nameChange(self, obj, nextHandler):
        self._dispatchRealtimeEvent(obj, "nameChange")
        nextHandler()

    def event_descriptionChange(self, obj, nextHandler):
        self._dispatchRealtimeEvent(obj, "descriptionChange")
        nextHandler()

    def event_valueChange(self, obj, nextHandler):
        self._dispatchRealtimeEvent(obj, "valueChange")
        nextHandler()

    def event_stateChange(self, obj, nextHandler):
        self._dispatchRealtimeEvent(obj, "stateChange")
        nextHandler()

    def event_show(self, obj, nextHandler):
        self._dispatchRealtimeEvent(obj, "show")
        nextHandler()

    def event_hide(self, obj, nextHandler):
        self._dispatchRealtimeEvent(obj, "hide")
        nextHandler()

    def event_UIA_structureChanged(self, obj, nextHandler):
        self._dispatchRealtimeEvent(obj, "structureChanged")
        nextHandler()

    __gestures__ = {
        "kb:NVDA+j": "quickUISearch",
    }
