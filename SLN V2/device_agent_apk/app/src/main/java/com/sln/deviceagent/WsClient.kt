package com.sln.deviceagent

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.Executors
import kotlin.math.min

class WsClient(private val ctx: Context, private val prefs: Prefs) {
    companion object {
        private const val TAG = "SLNAgent"
        private const val HEARTBEAT_MS = 15000L
    }

    private val client: OkHttpClient = TlsPin.client(ctx)
    private val handler = Handler(Looper.getMainLooper())
    private val guard = ConnectionGuard()
    private var ws: WebSocket? = null
    private var retry = 0
    private var authFailures = 0
    private var suppress = false
    private var reconnectRunnable: Runnable? = null
    @Volatile private var lastAcc = false
    @Volatile private var lastDisplay = ""
    @Volatile private var lastBridge = ""
    @Volatile var lastCommandStatus = ""
    @Volatile private var dpnPresence: JSONObject? = null
    @Volatile private var dpnProbeAt = 0L
    @Volatile private var dpnProbeInFlight = false
    private val dpnProbePool = Executors.newSingleThreadExecutor()

    private val heartbeat = object : Runnable {
        override fun run() {
            if (!guard.isConnected()) return
            sendHeartbeat()
            if (guard.isConnected()) handler.postDelayed(this, HEARTBEAT_MS)
        }
    }

    fun start() {
        guard.start()
        connect()
    }

    fun stop() {
        guard.stop()
        cancelReconnect()
        handler.removeCallbacks(heartbeat)
        try { ws?.close(1000, "stop") } catch (_: Exception) {}
        ws = null
    }

    fun reconnectNow() {
        if (guard.state() != ConnectionGuard.State.DISCONNECTED) return
        cancelReconnect()
        connect()
    }

    fun onAccessibilityChanged() {
        if (guard.isConnected()) sendRaw(hello(prefs.token ?: ""))
    }

    fun onDisplayChanged() {
        if (guard.isConnected()) sendRaw(hello(prefs.token ?: ""))
    }

    private fun connect() {
        val gen = guard.beginConnect() ?: return
        cancelReconnect()
        val url = prefs.controllerUrl.trim()
        if (url.isBlank() || !url.startsWith("wss://")) {
            Log.w(TAG, if (url.isBlank()) "NO_CONTROLLER" else "URL_NOT_WSS")
            guard.onDisconnected(gen)
            return
        }
        try {
            ws = client.newWebSocket(Request.Builder().url(url).build(), listener(gen))
            Log.i(TAG, "CONNECT $url gen=$gen")
        } catch (e: Exception) {
            Log.w(TAG, "CONNECT_ERROR ${e.javaClass.simpleName}")
            guard.onDisconnected(gen)
            scheduleReconnect(gen)
        }
    }

    private fun listener(gen: Int) = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!guard.onOpen(gen)) {
                try { webSocket.close(1000, "stale") } catch (_: Exception) {}
                return
            }
            Log.i(TAG, "WS_OPEN gen=$gen")
            val token = prefs.token
            val msg: JSONObject? = if (!token.isNullOrBlank()) hello(token)
            else if (prefs.pairingCode.isNotBlank()) pairRequest()
            else null
            if (msg == null) {
                Log.w(TAG, "PAIRING_REQUIRED")
                guard.onDisconnected(gen)
                return
            }
            webSocket.send(msg.toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!guard.isCurrent(gen)) return
            val m = try { JSONObject(text) } catch (e: Exception) { return }
            when (m.optString("type")) {
                Proto.HELLO_OK -> {
                    if (!guard.onAuthenticated(gen)) return
                    retry = 0
                    authFailures = 0
                    handler.removeCallbacks(heartbeat)
                    handler.postDelayed(heartbeat, HEARTBEAT_MS)
                    Log.i(TAG, "HELLO_OK gen=$gen")
                }
                Proto.PAIR_OK -> {
                    prefs.token = m.optString("token")
                    prefs.pairingCode = ""
                    authFailures = 0
                    retry = 0
                    suppress = true
                    try { webSocket.close(1000, "repair") } catch (_: Exception) {}
                    Log.i(TAG, "PAIR_OK")
                }
                Proto.PAIR_DENIED -> {
                    Log.w(TAG, "PAIR_DENIED ${m.optString("error")}")
                    guard.onDisconnected(gen)
                }
                Proto.COMMAND -> {
                    if (guard.isConnected()) {
                        CommandExecutor.execute(ctx, prefs, m,
                            { r -> sendRaw(r) }, { st -> lastCommandStatus = st })
                    }
                }
                Proto.ERROR -> {
                    val err = m.optString("error")
                    Log.w(TAG, "SERVER_ERROR $err")
                    if (err == "UNAUTHORIZED") {
                        authFailures++
                        Log.w(TAG, "AUTH_REVOKED")
                        if (authFailures >= 3) {
                            guard.stop()
                            Log.w(TAG, "AUTH_REVOKED_STOP (no aggressive reconnect)")
                        }
                    }
                }
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!guard.isCurrent(gen)) return
            guard.onDisconnected(gen)
            Log.w(TAG, "WS_FAIL ${t.javaClass.simpleName} gen=$gen")
            if (suppress) {
                suppress = false
                scheduleReconnect(gen, 300L)
            } else {
                scheduleReconnect(gen)
            }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!guard.isCurrent(gen)) return
            guard.onDisconnected(gen)
            Log.i(TAG, "WS_CLOSED $code gen=$gen")
            if (suppress) {
                suppress = false
                scheduleReconnect(gen, 300L)
            } else {
                scheduleReconnect(gen)
            }
        }
    }

    private fun scheduleReconnect(gen: Int, fixedDelayMs: Long? = null) {
        if (!guard.isCurrent(gen)) return
        if (!guard.markReconnectScheduled(gen)) return
        retry = min(retry + 1, 6)
        val base = fixedDelayMs ?: if (retry <= 1) 1000L else min(1000L shl (retry - 1), 60000L)
        val jitter = if (fixedDelayMs != null) 0L else (Math.random() * 1000).toLong()
        cancelReconnect()
        val r = Runnable {
            reconnectRunnable = null
            val g = guard.generation()
            if (guard.state() == ConnectionGuard.State.DISCONNECTED) {
                guard.clearReconnectScheduled(g)
                connect()
            }
        }
        reconnectRunnable = r
        Log.i(TAG, "RECONNECT_IN ${base + jitter}ms")
        handler.postDelayed(r, base + jitter)
    }

    private fun cancelReconnect() {
        reconnectRunnable?.let { handler.removeCallbacks(it) }
        reconnectRunnable = null
    }

    private fun hello(token: String): JSONObject {
        lastAcc = DeviceAccessibilityService.instance != null
        lastDisplay = DeviceStatus.displaySignature(ctx)
        lastBridge = PrivilegedBridge.signature()
        return JSONObject()
            .put("protocol", Proto.VERSION).put("type", Proto.HELLO)
            .put("agent_id", prefs.agentId).put("token", token)
            .put("capabilities", capabilities())
            .put("agent_version", Proto.AGENT_VERSION)
            .put("device_id", prefs.agentId)
            .put("info", DeviceStatus.deviceInfo(ctx))
    }

    private fun pairRequest(): JSONObject = JSONObject()
        .put("protocol", Proto.VERSION).put("type", Proto.PAIR_REQUEST)
        .put("agent_id", prefs.agentId)
        .put("pairing_code", prefs.pairingCode)
        .put("info", DeviceStatus.deviceInfo(ctx))

    private fun capabilities(): JSONObject {
        val svc = DeviceAccessibilityService.instance
        val acc = svc != null
        val caps = try { svc?.serviceInfo?.capabilities ?: 0 } catch (_: Exception) { 0 }
        val canTree = acc &&
            (caps and AccessibilityServiceInfo.CAPABILITY_CAN_RETRIEVE_WINDOW_CONTENT) != 0
        val canShot = acc && Build.VERSION.SDK_INT >= 30 &&
            (caps and AccessibilityServiceInfo.CAPABILITY_CAN_TAKE_SCREENSHOT) != 0
        val gestures = acc && DeviceStatus.displayValid(ctx)
        val o = JSONObject()
        for (op in Proto.READ_OPS) o.put(op, true)
        val dpnReady = !prefs.atomosPassword.isNullOrBlank()
        for (op in Proto.DPN_READ_OPS) o.put(op, dpnReady)
        for (op in Proto.DPN_MUTATING_OPS) o.put(op, dpnReady)
        o.put(Proto.START_APP, true)
        o.put(Proto.BACK, acc)
        o.put(Proto.HOME, acc)
        o.put(Proto.TAP, gestures)
        o.put(Proto.SWIPE, gestures)
        o.put(Proto.GET_UI_TREE, canTree)
        o.put(Proto.CAPTURE_SCREEN, canShot)
        o.put(Proto.GET_PRIVILEGED_BRIDGE_STATUS, true)
        o.put(Proto.REQUEST_SHIZUKU_PERMISSION, true)
        val ready = PrivilegedBridge.isReady()
        o.put(Proto.STOP_APP, ready)
        o.put(Proto.CLEAR_APP_DATA, ready)
        return o
    }

    private fun sendHeartbeat() {
        if (!guard.isConnected()) return
        try {
            val acc = DeviceAccessibilityService.instance != null
            val disp = DeviceStatus.displaySignature(ctx)
            val bridge = PrivilegedBridge.signature()
            if (acc != lastAcc || disp != lastDisplay || bridge != lastBridge) {
                lastAcc = acc
                lastDisplay = disp
                lastBridge = bridge
                sendRaw(hello(prefs.token ?: ""))
            }
            val info = DeviceStatus.status(ctx, lastCommandStatus)
            dpnPresence?.let { info.put("dpn_presence", it) }
            maybeProbeDpn()
            sendRaw(JSONObject().put("protocol", Proto.VERSION).put("type", Proto.HEARTBEAT)
                .put("agent_id", prefs.agentId)
                .put("info", info))
        } catch (_: Exception) {}
    }

    private fun maybeProbeDpn() {
        if (!prefs.dpnAutoProbe || prefs.atomosPassword.isNullOrBlank()) return
        val now = System.currentTimeMillis()
        val interval = if (dpnPresence?.optBoolean("reachable") == true) 300_000L else 900_000L
        if (dpnProbeInFlight || now - dpnProbeAt < interval) return
        dpnProbeInFlight = true
        dpnProbeAt = now
        dpnProbePool.execute {
            val presence = try {
                val id = DpnClient(prefs).identity()
                JSONObject()
                    .put("reachable", true)
                    .put("physical_dpn_id", id.optString("physical_dpn_id"))
                    .put("latency_ms", id.optLong("latency_ms"))
                    .put("authenticated", true)
                    .put("capabilities", org.json.JSONArray().apply {
                        for (op in Proto.DPN_READ_OPS) put(op)
                    })
                    .put("observed_at_ms", System.currentTimeMillis())
            } catch (e: DpnError) {
                JSONObject().put("reachable", false)
                    .put("physical_dpn_id", dpnPresence?.optString("physical_dpn_id") ?: "")
                    .put("authenticated", false)
                    .put("error", e.code)
                    .put("capabilities", org.json.JSONArray())
                    .put("observed_at_ms", System.currentTimeMillis())
            } catch (e: Exception) {
                JSONObject().put("reachable", false)
                    .put("physical_dpn_id", dpnPresence?.optString("physical_dpn_id") ?: "")
                    .put("authenticated", false)
                    .put("error", e.javaClass.simpleName)
                    .put("capabilities", org.json.JSONArray())
                    .put("observed_at_ms", System.currentTimeMillis())
            }
            dpnPresence = presence
            dpnProbeInFlight = false
        }
    }

    private fun sendRaw(o: JSONObject) { try { ws?.send(o.toString()) } catch (_: Exception) {} }
}
