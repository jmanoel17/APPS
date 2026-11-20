package com.sln.deviceagent

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.hardware.HardwareBuffer
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.ArrayDeque
import android.util.Base64
import java.util.IdentityHashMap
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

data class GestureOutcome(val status: String, val error: String, val gestureMs: Long)

data class ForegroundInfo(
    val packageName: String = "",
    val activity: String = "",
    val component: String = "",
    val windowId: Int = 0,
    val observedAtMs: Long = 0L
)

class PerceptionError(val code: String) : Exception(code)

class DeviceAccessibilityService : AccessibilityService() {
    companion object {
        private const val TAG = "SLNPerception"
        const val ACTION_CHANGED = "com.sln.deviceagent.ACCESSIBILITY_CHANGED"
        @Volatile var instance: DeviceAccessibilityService? = null
        @Volatile var currentPackage: String = ""
        @Volatile private var foreground = ForegroundInfo()

        fun foregroundInfo(): ForegroundInfo = foreground
    }

    fun foregroundSnapshot(): ForegroundInfo {
        val f = foregroundInfo()
        val now = System.currentTimeMillis()
        if (f.activity.isEmpty() && now - usageAt > 1000L) {
            usageAt = now
            val u = DeviceStatus.foregroundFromUsage(this, f.packageName)
            if (u != null) {
                usagePkg = u.first
                usageAct = u.second
            }
        }
        if (f.activity.isEmpty() && usageAct.isNotEmpty() &&
            (f.packageName.isEmpty() || f.packageName == usagePkg)) {
            val pkg = if (f.packageName.isNotEmpty()) f.packageName else usagePkg
            return ForegroundInfo(pkg, usageAct, if (pkg.isNotEmpty()) "$pkg/$usageAct" else "",
                f.windowId, now)
        }
        if (f.packageName.isNotEmpty()) return f
        val root = try { rootInActiveWindow } catch (_: Exception) { null } ?: return f
        return try {
            val pkg = root.packageName?.toString() ?: ""
            val wid = root.windowId
            ForegroundInfo(pkg, "", if (pkg.isNotEmpty()) "$pkg/" else "", wid,
                System.currentTimeMillis())
        } finally {
            try { root.recycle() } catch (_: Exception) {}
        }
    }

    private val rateLock = Any()
    private var lastTreeAt = 0L
    private var lastCaptureAt = 0L
    private var usageAt = 0L
    private var usagePkg = ""
    private var usageAct = ""
    private var usageWindowId = -1

    override fun onServiceConnected() {
        instance = this
        try {
            val info = serviceInfo
            if (info != null) {
                info.flags = info.flags or
                    AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                    AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS
                serviceInfo = info
            }
        } catch (_: Exception) {}
        sendBroadcast(Intent(ACTION_CHANGED).setPackage(packageName))
    }

    override fun onDestroy() {
        instance = null
        foreground = ForegroundInfo()
        currentPackage = ""
        sendBroadcast(Intent(ACTION_CHANGED).setPackage(packageName))
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            val pkg = event.packageName?.toString() ?: ""
            val cls = event.className?.toString() ?: ""
            val comp = if (pkg.isNotEmpty() && cls.isNotEmpty()) "$pkg/$cls" else ""
            foreground = ForegroundInfo(pkg, cls, comp, event.windowId, System.currentTimeMillis())
            currentPackage = pkg
        }
    }

    override fun onInterrupt() {}

    fun back(): Boolean = performGlobalAction(GLOBAL_ACTION_BACK)
    fun home(): Boolean = performGlobalAction(GLOBAL_ACTION_HOME)

    fun canDispatchGestures(): Boolean = Build.VERSION.SDK_INT >= 24

    fun performGesture(points: FloatArray, durationMs: Long,
                       dispatchDeadlineElapsed: Long, hardWaitMs: Long): GestureOutcome {
        var life: GestureLifecycle? = null
        val l = GestureLifecycle(dispatch = {
            val path = Path()
            path.moveTo(points[0], points[1])
            if (points.size >= 4) path.lineTo(points[2], points[3])
            val stroke = GestureDescription.StrokeDescription(path, 0, durationMs)
            val desc = GestureDescription.Builder().addStroke(stroke).build()
            dispatchGesture(desc, object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    life?.onCompleted()
                }

                override fun onCancelled(gestureDescription: GestureDescription?) {
                    life?.onCancelled()
                }
            }, null)
        }, now = { SystemClock.elapsedRealtime() })
        life = l
        Handler(Looper.getMainLooper()).post { l.tryDispatch(dispatchDeadlineElapsed) }
        val out = l.await(hardWaitMs) ?: l.timeout()
        return GestureOutcome(out.status, out.error, out.gestureMs)
    }

    fun buildUiTree(maxDepth: Int, maxNodes: Int, maxTextLength: Int,
                    maxPayloadBytes: Int): JSONObject {
        synchronized(rateLock) {
            val now = SystemClock.elapsedRealtime()
            if (now - lastTreeAt < 20L) throw PerceptionError("RATE_LIMITED")
            lastTreeAt = now
        }
        val t0 = SystemClock.elapsedRealtime()
        val root = rootInActiveWindow ?: throw PerceptionError("UI_TREE_UNAVAILABLE")
        val rootPackage = root.packageName?.toString() ?: ""
        val windowId = root.windowId
        val fg = foregroundSnapshot()
        val pkg = if (fg.packageName.isNotEmpty()) fg.packageName else rootPackage
        val activity = if (fg.packageName.isEmpty() || fg.packageName == rootPackage) fg.activity else ""
        val component = if (fg.packageName.isEmpty() || fg.packageName == rootPackage) fg.component else ""

        val nodes = JSONArray()
        val stack = ArrayDeque<Pair<AccessibilityNodeInfo, Array<Any>>>()
        stack.addLast(Pair(root, arrayOf(JSONObject.NULL, 0)))
        val seen = IdentityHashMap<AccessibilityNodeInfo, Boolean>()
        var seq = 0
        var truncated = false
        var estimate = 0
        val hardDeadline = t0 + 1500L

        while (stack.isNotEmpty()) {
            val (node, meta) = stack.removeLast()
            if (seen.containsKey(node)) {
                continue
            }
            seen[node] = true
            val parentId = meta[0] as Any
            val depth = meta[1] as Int
            if (depth > maxDepth || seq >= maxNodes || SystemClock.elapsedRealtime() > hardDeadline ||
                estimate > maxPayloadBytes) {
                truncated = true
                try { node.recycle() } catch (_: Exception) {}
                continue
            }
            val cls = node.className?.toString() ?: ""
            val password = node.isPassword
            val text = if (password) "" else clip(node.text?.toString() ?: "", maxTextLength)
            val desc = if (password) "" else clip(node.contentDescription?.toString() ?: "", maxTextLength)
            val rid = node.viewIdResourceName ?: ""
            val b = Rect()
            node.getBoundsInScreen(b)
            val childCount = node.childCount
            val nodeId = "n$seq"
            seq++
            val obj = JSONObject()
            obj.put("node_id", nodeId)
            obj.put("parent_id", parentId)
            obj.put("depth", depth)
            obj.put("class", cls)
            obj.put("text", text)
            obj.put("content_desc", desc)
            obj.put("resource_id", rid)
            obj.put("bounds", JSONArray(listOf(b.left, b.top, b.right, b.bottom)))
            obj.put("clickable", node.isClickable)
            obj.put("enabled", node.isEnabled)
            obj.put("visible", node.isVisibleToUser)
            obj.put("scrollable", node.isScrollable)
            obj.put("password", password)
            obj.put("child_count", childCount)
            nodes.put(obj)
            estimate += 120 + cls.length + text.length + desc.length + rid.length
            if (depth < maxDepth && seq < maxNodes) {
                val children = ArrayList<AccessibilityNodeInfo>(childCount)
                for (i in 0 until childCount) {
                    try { node.getChild(i)?.let { children.add(it) } } catch (_: Exception) {}
                }
                for (i in children.indices.reversed()) {
                    stack.addLast(Pair(children[i], arrayOf(nodeId, depth + 1)))
                }
            }
            try { node.recycle() } catch (_: Exception) {}
        }
        while (stack.isNotEmpty()) {
            val n = stack.removeLast().first
            try { n.recycle() } catch (_: Exception) {}
        }

        val display = DeviceStatus.display(this)
        val out = JSONObject()
        out.put("snapshot_id", UUID.randomUUID().toString())
        out.put("captured_at_ms", System.currentTimeMillis())
        out.put("package", pkg)
        out.put("activity", activity)
        out.put("window_id", windowId)
        out.put("display", display)
        out.put("nodes", nodes)
        out.put("truncated", truncated)
        out.put("capture_ms", SystemClock.elapsedRealtime() - t0)
        var bytes = out.toString().toByteArray(Charsets.UTF_8).size
        while (bytes > maxPayloadBytes && nodes.length() > 1) {
            nodes.remove(nodes.length() - 1)
            truncated = true
            bytes = out.toString().toByteArray(Charsets.UTF_8).size
        }
        out.put("truncated", truncated)
        out.put("payload_bytes", bytes)
        Log.i(TAG, "UI_TREE nodes=${nodes.length()} bytes=$bytes ms=${SystemClock.elapsedRealtime() - t0} " +
            "pkg=$pkg activity=$activity truncated=$truncated")
        return out
    }

    fun captureScreen(maxDim: Int, maxPayloadBytes: Int): JSONObject {
        if (Build.VERSION.SDK_INT < 30) throw PerceptionError("CAPABILITY_NOT_SUPPORTED")
        synchronized(rateLock) {
            val now = SystemClock.elapsedRealtime()
            if (now - lastCaptureAt < 200L) throw PerceptionError("RATE_LIMITED")
            lastCaptureAt = now
        }
        val t0 = SystemClock.elapsedRealtime()
        val latch = CountDownLatch(1)
        val resultRef = AtomicReference<AccessibilityService.ScreenshotResult?>(null)
        val errorRef = AtomicInteger(0)
        val executor = Executor { command -> command.run() }
        takeScreenshot(Display.DEFAULT_DISPLAY, executor,
            object : AccessibilityService.TakeScreenshotCallback {
                override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                    resultRef.set(result)
                    latch.countDown()
                }

                override fun onFailure(errorCode: Int) {
                    errorRef.set(errorCode)
                    latch.countDown()
                }
            })
        if (!latch.await(2500L, TimeUnit.MILLISECONDS)) {
            throw PerceptionError("SCREENSHOT_FAILED:TIMEOUT")
        }
        val res = resultRef.get() ?: throw PerceptionError("SCREENSHOT_FAILED:${errorRef.get()}")
        val captureMs = SystemClock.elapsedRealtime() - t0
        val hb: HardwareBuffer = res.hardwareBuffer ?: throw PerceptionError("SCREENSHOT_FAILED:NO_BUFFER")
        var src: Bitmap? = null
        var outBmp: Bitmap? = null
        try {
            src = Bitmap.wrapHardwareBuffer(hb, res.colorSpace)
                ?: throw PerceptionError("SCREENSHOT_FAILED:WRAP")
            val maxSide = maxOf(src.width, src.height)
            outBmp = if (maxDim in 1 until maxSide) {
                val scale = maxDim.toFloat() / maxSide.toFloat()
                Bitmap.createScaledBitmap(src, (src.width * scale).toInt().coerceAtLeast(1),
                    (src.height * scale).toInt().coerceAtLeast(1), true)
            } else {
                src.copy(Bitmap.Config.ARGB_8888, false)
            }
            val baos = ByteArrayOutputStream()
            val tEnc = SystemClock.elapsedRealtime()
            if (!outBmp.compress(Bitmap.CompressFormat.PNG, 100, baos)) {
                throw PerceptionError("SCREENSHOT_FAILED:ENCODE")
            }
            val encodingMs = SystemClock.elapsedRealtime() - tEnc
            val payload = baos.toByteArray()
            if (payload.size > maxPayloadBytes) throw PerceptionError("SCREENSHOT_TOO_LARGE")
            val display = DeviceStatus.display(this)
            val out = JSONObject()
            out.put("captured_at_ms", System.currentTimeMillis())
            out.put("width", outBmp.width)
            out.put("height", outBmp.height)
            out.put("rotation", display.optInt("rotation"))
            out.put("format", "png")
            out.put("size_bytes", payload.size)
            out.put("sha256", sha256(payload))
            out.put("payload", Base64.encodeToString(payload, Base64.NO_WRAP))
            out.put("capture_ms", captureMs)
            out.put("encoding_ms", encodingMs)
            Log.i(TAG, "SCREENSHOT w=${outBmp.width} h=${outBmp.height} bytes=${payload.size} " +
                "capture_ms=$captureMs encode_ms=$encodingMs")
            return out
        } finally {
            try { hb.close() } catch (_: Exception) {}
            try { if (outBmp !== src) outBmp?.recycle() } catch (_: Exception) {}
            try { src?.recycle() } catch (_: Exception) {}
        }
    }

    private fun clip(s: String, maxLen: Int): String =
        if (maxLen <= 0 || s.length <= maxLen) s else s.substring(0, maxLen)

    private fun sha256(data: ByteArray): String {
        val md = MessageDigest.getInstance("SHA-256")
        return md.digest(data).joinToString("") { "%02x".format(it) }
    }
}

