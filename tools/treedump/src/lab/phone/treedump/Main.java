package lab.phone.treedump;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.Binder;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.os.Process;
import android.util.SparseArray;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class Main {
    private static final String DEFAULT_TEXT_PACKAGES = "ai.cua.fixture.notes,ai.cua.android.demo";
    private static final String DEFAULT_ACT_PACKAGES = "ai.cua.fixture.notes,ai.cua.android.demo";
    private static final int DEFAULT_MAX_NODES = 2000;
    private static final int DEFAULT_MAX_DEPTH = 40;

    public static void main(final String[] args) {
        // app_process has no main looper; the accessibility client builds Handlers on it, so prepare one
        // and keep the main thread looping while the command loop runs on its own thread.
        if (Looper.getMainLooper() == null) {
            Looper.prepareMainLooper();
        }
        Thread worker = new Thread(new Runnable() {
            @Override
            public void run() {
                serve(args);
            }
        }, "treedump-commands");
        worker.setDaemon(false);
        worker.start();
        Looper.loop();
    }

    private static void serve(String[] args) {
        Set<String> textPackages = new HashSet<>(Arrays.asList(DEFAULT_TEXT_PACKAGES.split(",")));
        Set<String> actPackages = new HashSet<>(Arrays.asList(DEFAULT_ACT_PACKAGES.split(",")));
        int maxNodes = DEFAULT_MAX_NODES;
        int maxDepth = DEFAULT_MAX_DEPTH;

        for (int i = 0; i < args.length; i++) {
            if ("--text-packages".equals(args[i]) && i + 1 < args.length) {
                textPackages.clear();
                for (String p : args[++i].split(",")) {
                    String trimmed = p.trim();
                    if (!trimmed.isEmpty()) {
                        textPackages.add(trimmed);
                    }
                }
            } else if ("--act-packages".equals(args[i]) && i + 1 < args.length) {
                actPackages.clear();
                for (String p : args[++i].split(",")) {
                    String trimmed = p.trim();
                    if (!trimmed.isEmpty()) {
                        actPackages.add(trimmed);
                    }
                }
            } else if ("--max-nodes".equals(args[i]) && i + 1 < args.length) {
                try {
                    maxNodes = Integer.parseInt(args[++i]);
                } catch (NumberFormatException ignored) {}
            } else if ("--max-depth".equals(args[i]) && i + 1 < args.length) {
                try {
                    maxDepth = Integer.parseInt(args[++i]);
                } catch (NumberFormatException ignored) {}
            }
        }

        HandlerThread thread = new HandlerThread("treedump");
        thread.start();
        Looper looper = thread.getLooper();

        UiAutomation uiAutomation;
        int connectFlags = 1; // UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES
        boolean suppressesServices = false;

        try {
            Class<?> connClass = Class.forName("android.app.UiAutomationConnection");
            Object conn = connClass.getDeclaredConstructor().newInstance();
            Class<?> iConnClass = Class.forName("android.app.IUiAutomationConnection");
            Constructor<UiAutomation> ctor = UiAutomation.class.getDeclaredConstructor(Looper.class, iConnClass);
            ctor.setAccessible(true);
            uiAutomation = ctor.newInstance(looper, conn);

            try {
                Method connectMethod = UiAutomation.class.getMethod("connect", int.class);
                connectMethod.setAccessible(true);
                connectMethod.invoke(uiAutomation, connectFlags);
                suppressesServices = false;
            } catch (Throwable t) {
                Method connectNoArg = UiAutomation.class.getMethod("connect");
                connectNoArg.setAccessible(true);
                connectNoArg.invoke(uiAutomation);
                suppressesServices = true;
                connectFlags = 0;
            }

            AccessibilityServiceInfo info = uiAutomation.getServiceInfo();
            if (info == null) {
                info = new AccessibilityServiceInfo();
            }
            info.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                    | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                    | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            uiAutomation.setServiceInfo(info);
        } catch (Throwable t) {
            try {
                JSONObject failHello = new JSONObject();
                failHello.put("ok", false);
                String msg = t.getMessage();
                if (msg == null || msg.isEmpty()) {
                    msg = t.toString();
                }
                failHello.put("error", "could not connect UiAutomation: " + msg);
                System.out.println(failHello.toString());
                System.out.flush();
            } catch (JSONException ignored) {}
            System.exit(2);
            return;
        }

        try {
            JSONObject hello = new JSONObject();
            hello.put("ok", true);
            hello.put("hello", "phone-lab treedump 1");
            hello.put("pid", Process.myPid());
            hello.put("connect_flags", connectFlags);
            hello.put("suppresses_services", suppressesServices);
            JSONArray textPkgs = new JSONArray();
            for (String p : textPackages) {
                textPkgs.put(p);
            }
            hello.put("text_packages", textPkgs);
            System.out.println(hello.toString());
            System.out.flush();
        } catch (JSONException ignored) {}

        Map<Integer, List<AccessibilityNodeInfo>> lastNodesByDisplay = new HashMap<>();
        BufferedReader reader = new BufferedReader(new InputStreamReader(System.in));
        String line;

        try {
            while ((line = reader.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) {
                    continue;
                }

                if ("quit".equals(line)) {
                    JSONObject reply = new JSONObject();
                    reply.put("ok", true);
                    reply.put("bye", true);
                    System.out.println(reply.toString());
                    System.out.flush();
                    break;
                }

                if ("displays".equals(line)) {
                    SparseArray<List<AccessibilityWindowInfo>> windows = uiAutomation.getWindowsOnAllDisplays();
                    JSONArray ids = new JSONArray();
                    for (int i = 0; i < windows.size(); i++) {
                        ids.put(windows.keyAt(i));
                    }
                    JSONObject reply = new JSONObject();
                    reply.put("ok", true);
                    reply.put("display_ids", ids);
                    System.out.println(reply.toString());
                    System.out.flush();
                    continue;
                }

                if (line.startsWith("tree")) {
                    handleTree(line, uiAutomation, lastNodesByDisplay, textPackages, maxNodes, maxDepth);
                    continue;
                }

                if (line.startsWith("act")) {
                    handleAct(line, lastNodesByDisplay, actPackages);
                    continue;
                }

                if (line.startsWith("toast")) {
                    handleToast(line);
                    continue;
                }

                JSONObject reply = new JSONObject();
                reply.put("ok", false);
                reply.put("error", "unknown command");
                System.out.println(reply.toString());
                System.out.flush();
            }
        } catch (Throwable t) {
            // Unexpected stream or command error
        } finally {
            for (List<AccessibilityNodeInfo> nodes : lastNodesByDisplay.values()) {
                if (nodes != null) {
                    for (AccessibilityNodeInfo n : nodes) {
                        try { n.recycle(); } catch (Throwable ignored) {}
                    }
                }
            }
            lastNodesByDisplay.clear();
        }
        System.exit(0);
    }

    private static void handleTree(
            String line,
            UiAutomation uiAutomation,
            Map<Integer, List<AccessibilityNodeInfo>> lastNodesByDisplay,
            Set<String> textPackages,
            int maxNodes,
            int maxDepth
    ) throws JSONException {
        String[] parts = line.split("\\s+");
        if (parts.length != 2) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "usage: tree <displayId>");
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        int displayId;
        try {
            displayId = Integer.parseInt(parts[1]);
        } catch (NumberFormatException e) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "usage: tree <displayId>");
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        long startMs = System.currentTimeMillis();
        long startNano = System.nanoTime();
        SparseArray<List<AccessibilityWindowInfo>> allDisplays = uiAutomation.getWindowsOnAllDisplays();
        List<AccessibilityWindowInfo> windowList = allDisplays.get(displayId);

        if (windowList == null) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "no windows on display " + displayId);
            reply.put("display_id", displayId);
            reply.put("windows", new JSONArray());
            reply.put("nodes", new JSONArray());
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        List<AccessibilityNodeInfo> oldNodes = lastNodesByDisplay.remove(displayId);
        if (oldNodes != null) {
            for (AccessibilityNodeInfo n : oldNodes) {
                try { n.recycle(); } catch (Throwable ignored) {}
            }
        }

        List<AccessibilityNodeInfo> keptNodes = new ArrayList<>();
        JSONArray windowsJson = new JSONArray();
        JSONArray nodesJson = new JSONArray();
        boolean[] truncated = new boolean[]{false};
        Rect boundsRect = new Rect();

        for (int w = 0; w < windowList.size(); w++) {
            AccessibilityWindowInfo win = windowList.get(w);
            JSONObject winObj = new JSONObject();
            winObj.put("w", w);
            winObj.put("id", win.getId());
            winObj.put("type", win.getType());

            String typeName;
            switch (win.getType()) {
                case AccessibilityWindowInfo.TYPE_APPLICATION:
                    typeName = "application";
                    break;
                case AccessibilityWindowInfo.TYPE_INPUT_METHOD:
                    typeName = "input_method";
                    break;
                case AccessibilityWindowInfo.TYPE_SYSTEM:
                    typeName = "system";
                    break;
                case AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY:
                    typeName = "accessibility_overlay";
                    break;
                case AccessibilityWindowInfo.TYPE_SPLIT_SCREEN_DIVIDER:
                    typeName = "split_screen_divider";
                    break;
                case 6: // TYPE_MAGNIFICATION_OVERLAY
                    typeName = "magnification_overlay";
                    break;
                default:
                    typeName = "unknown";
                    break;
            }
            winObj.put("type_name", typeName);

            AccessibilityNodeInfo root = win.getRoot();
            CharSequence rootPkg = root != null ? root.getPackageName() : null;
            winObj.put("package", rootPkg != null ? rootPkg.toString() : JSONObject.NULL);

            // Window titles can carry personal content on the human's display: same allow-list as node text.
            CharSequence winTitle = win.getTitle();
            boolean titleAllowed = rootPkg != null && textPackages.contains(rootPkg.toString());
            winObj.put("title", (winTitle != null && titleAllowed) ? winTitle.toString() : JSONObject.NULL);
            winObj.put("title_len", winTitle != null ? winTitle.length() : 0);
            winObj.put("layer", win.getLayer());

            win.getBoundsInScreen(boundsRect);
            JSONArray winBounds = new JSONArray();
            winBounds.put(boundsRect.left);
            winBounds.put(boundsRect.top);
            winBounds.put(boundsRect.right);
            winBounds.put(boundsRect.bottom);
            winObj.put("bounds", winBounds);

            winObj.put("focused", win.isFocused());
            winObj.put("active", win.isActive());
            windowsJson.put(winObj);

            if (root != null) {
                walkNode(root, w, null, 0, nodesJson, keptNodes, textPackages, maxNodes, maxDepth, truncated, boundsRect);
            }
        }

        lastNodesByDisplay.put(displayId, keptNodes);
        long costMs = (System.nanoTime() - startNano) / 1_000_000L;

        JSONObject reply = new JSONObject();
        reply.put("ok", true);
        reply.put("display_id", displayId);
        reply.put("captured_at_ms", startMs);
        reply.put("cost_ms", costMs);
        reply.put("windows", windowsJson);
        reply.put("nodes", nodesJson);
        if (truncated[0]) {
            reply.put("truncated", true);
        }
        System.out.println(reply.toString());
        System.out.flush();
    }

    private static void walkNode(
            AccessibilityNodeInfo node,
            int windowIndex,
            Integer parentIndex,
            int depth,
            JSONArray nodesJson,
            List<AccessibilityNodeInfo> keptNodes,
            Set<String> textPackages,
            int maxNodes,
            int maxDepth,
            boolean[] truncated,
            Rect boundsRect
    ) throws JSONException {
        if (nodesJson.length() >= maxNodes) {
            truncated[0] = true;
            return;
        }

        int currentIndex = nodesJson.length();
        keptNodes.add(node);

        JSONObject nodeObj = new JSONObject();
        nodeObj.put("i", currentIndex);
        nodeObj.put("parent", parentIndex != null ? parentIndex : JSONObject.NULL);
        nodeObj.put("w", windowIndex);
        nodeObj.put("depth", depth);

        CharSequence cls = node.getClassName();
        nodeObj.put("class", cls != null ? cls.toString() : "");

        CharSequence pkg = node.getPackageName();
        String pkgStr = pkg != null ? pkg.toString() : "";
        nodeObj.put("package", pkgStr);

        String viewId = node.getViewIdResourceName();
        nodeObj.put("id", (viewId != null && !viewId.isEmpty()) ? viewId : JSONObject.NULL);

        boolean textAllowed = textPackages.contains(pkgStr);
        CharSequence rawText = node.getText();
        CharSequence rawDesc = node.getContentDescription();
        int textLen = 0;
        if (rawText != null) {
            textLen = rawText.length();
        } else if (rawDesc != null) {
            textLen = rawDesc.length();
        }

        String text = null;
        String desc = null;
        if (textAllowed) {
            if (rawText != null) text = rawText.toString();
            if (rawDesc != null) desc = rawDesc.toString();
        }

        nodeObj.put("text", text != null ? text : JSONObject.NULL);
        nodeObj.put("desc", desc != null ? desc : JSONObject.NULL);
        nodeObj.put("text_len", textLen);

        node.getBoundsInScreen(boundsRect);
        JSONArray bounds = new JSONArray();
        bounds.put(boundsRect.left);
        bounds.put(boundsRect.top);
        bounds.put(boundsRect.right);
        bounds.put(boundsRect.bottom);
        nodeObj.put("bounds", bounds);

        nodeObj.put("clickable", node.isClickable());
        nodeObj.put("long_clickable", node.isLongClickable());
        nodeObj.put("editable", node.isEditable());
        nodeObj.put("checkable", node.isCheckable());
        nodeObj.put("checked", node.isChecked());
        nodeObj.put("enabled", node.isEnabled());
        nodeObj.put("focusable", node.isFocusable());
        nodeObj.put("focused", node.isFocused());
        nodeObj.put("visible", node.isVisibleToUser());
        nodeObj.put("scrollable", node.isScrollable());

        int childCount = node.getChildCount();
        nodeObj.put("children", childCount);
        nodesJson.put(nodeObj);

        if (depth >= maxDepth) {
            if (childCount > 0) {
                truncated[0] = true;
            }
            return;
        }

        for (int c = 0; c < childCount; c++) {
            if (nodesJson.length() >= maxNodes) {
                truncated[0] = true;
                break;
            }
            AccessibilityNodeInfo child = node.getChild(c);
            if (child != null) {
                walkNode(child, windowIndex, currentIndex, depth + 1, nodesJson, keptNodes, textPackages, maxNodes, maxDepth, truncated, boundsRect);
            }
        }
    }

    private static void handleAct(
            String line,
            Map<Integer, List<AccessibilityNodeInfo>> lastNodesByDisplay,
            Set<String> actPackages
    ) throws JSONException {
        String[] parts = line.split("\\s+");
        if (parts.length != 4) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "usage: act <displayId> <nodeIndex> <focus|click>");
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        int displayId;
        int nodeIndex;
        try {
            displayId = Integer.parseInt(parts[1]);
            nodeIndex = Integer.parseInt(parts[2]);
        } catch (NumberFormatException e) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "usage: act <displayId> <nodeIndex> <focus|click>");
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        String action = parts[3];
        int actionId;
        if ("focus".equals(action)) {
            actionId = AccessibilityNodeInfo.ACTION_FOCUS;
        } else if ("click".equals(action)) {
            actionId = AccessibilityNodeInfo.ACTION_CLICK;
        } else {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "unknown action: " + action);
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        List<AccessibilityNodeInfo> nodes = lastNodesByDisplay.get(displayId);
        if (nodes == null || nodeIndex < 0 || nodeIndex >= nodes.size()) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "node not found");
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        AccessibilityNodeInfo targetNode = nodes.get(nodeIndex);
        CharSequence pkg = targetNode.getPackageName();
        String pkgStr = pkg != null ? pkg.toString() : "";
        if (!actPackages.contains(pkgStr)) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "package not allowed");
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        boolean performed = targetNode.performAction(actionId);
        JSONObject reply = new JSONObject();
        reply.put("ok", true);
        reply.put("performed", performed);
        System.out.println(reply.toString());
        System.out.flush();
    }

    private static void handleToast(String line) throws JSONException {
        int firstSpace = line.indexOf(' ');
        if (firstSpace < 0) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "usage: toast <displayId> <text>");
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }
        int secondSpace = line.indexOf(' ', firstSpace + 1);
        if (secondSpace < 0) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "usage: toast <displayId> <text>");
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        int displayId;
        try {
            displayId = Integer.parseInt(line.substring(firstSpace + 1, secondSpace).trim());
        } catch (NumberFormatException e) {
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", "usage: toast <displayId> <text>");
            System.out.println(reply.toString());
            System.out.flush();
            return;
        }

        String text = line.substring(secondSpace + 1).trim();

        try {
            Class<?> smClass = Class.forName("android.os.ServiceManager");
            Method getService = smClass.getMethod("getService", String.class);
            IBinder binder = (IBinder) getService.invoke(null, "notification");
            if (binder == null) {
                JSONObject reply = new JSONObject();
                reply.put("ok", false);
                reply.put("error", "notification service not available");
                System.out.println(reply.toString());
                System.out.flush();
                return;
            }

            Class<?> stubClass = Class.forName("android.app.INotificationManager$Stub");
            Method asInterface = stubClass.getMethod("asInterface", IBinder.class);
            Object notifManager = asInterface.invoke(null, binder);

            Method toastMethod = null;
            for (Method m : notifManager.getClass().getMethods()) {
                if ("enqueueTextToast".equals(m.getName())) {
                    toastMethod = m;
                    break;
                }
            }
            if (toastMethod == null) {
                for (Method m : notifManager.getClass().getDeclaredMethods()) {
                    if ("enqueueTextToast".equals(m.getName())) {
                        toastMethod = m;
                        break;
                    }
                }
            }
            if (toastMethod == null) {
                JSONObject reply = new JSONObject();
                reply.put("ok", false);
                reply.put("error", "enqueueTextToast method not found");
                System.out.println(reply.toString());
                System.out.flush();
                return;
            }

            Class<?>[] paramTypes = toastMethod.getParameterTypes();
            Object[] toastArgs = new Object[paramTypes.length];
            int intCount = 0;
            for (int i = 0; i < paramTypes.length; i++) {
                Class<?> p = paramTypes[i];
                if (p == String.class) {
                    toastArgs[i] = "com.android.shell";
                } else if (IBinder.class.isAssignableFrom(p)) {
                    toastArgs[i] = new Binder();
                } else if (CharSequence.class.isAssignableFrom(p)) {
                    toastArgs[i] = text;
                } else if (p == int.class || p == Integer.class) {
                    if (intCount == 0) {
                        toastArgs[i] = 1; // duration 1 (long)
                    } else {
                        toastArgs[i] = displayId;
                    }
                    intCount++;
                } else if (p == boolean.class || p == Boolean.class) {
                    toastArgs[i] = false;
                } else {
                    toastArgs[i] = null;
                }
            }

            toastMethod.setAccessible(true);
            toastMethod.invoke(notifManager, toastArgs);

            JSONObject reply = new JSONObject();
            reply.put("ok", true);
            reply.put("method", toastMethod.toString());
            System.out.println(reply.toString());
            System.out.flush();
        } catch (Throwable t) {
            String msg = t.getMessage();
            if (msg == null || msg.isEmpty()) {
                msg = t.toString();
            }
            JSONObject reply = new JSONObject();
            reply.put("ok", false);
            reply.put("error", msg);
            System.out.println(reply.toString());
            System.out.flush();
        }
    }
}
