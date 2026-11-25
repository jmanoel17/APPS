package com.sln.deviceagent

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import org.json.JSONObject
import java.util.LinkedHashMap
import java.util.UUID
import java.util.concurrent.Executors

object CommandExecutor {
    private val mutations = Executors.newSingleThreadExecutor()
    private val reads = Executors.newCachedThreadPool()
    private val handled = object : LinkedHashMap<String, JSONObject>(32, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, JSONObject>?): Boolean =
            size > 500
    }
    private val TERMINAL = setOf("success", "failure", "uncertain", "expired")
    private val largeHandled = object : LinkedHashMap<String, JSONObject>(4, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, JSONObject>?): Boolean =
            size > 4
    }
    private val LARGE_OPS = setOf(Proto.GET_UI_TREE, Proto.CAPTURE_SCREEN)

    class OpError(val status: String, val code: String) : Exception(code)

    @Synchronized private fun cacheGet(cid: String): JSONObject? = handled[cid]
    @Synchronized private fun cachePut(cid: String, r: JSONObject) { handled[cid] = r }
    @Synchronized private fun largeCacheGet(cid: String): JSONObject? = largeHandled[cid]
    @Synchronized private fun largeCachePut(cid: String, r: JSONObject) { largeHandled[cid] = r }

    private fun isUuid(v: String): Boolean = try {
        UUID.fromString(v)
        true
    } catch (_: Exception) {
        false
    }

    private fun validateEnvelope(msg: JSONObject, agentId: String): String? {
        if (msg.optString("protocol") != Proto.VERSION) return "PROTOCOL_MISMATCH"
        if (msg.optString("type") != Proto.COMMAND) return "INVALID_TYPE"
        if (!isUuid(msg.optString("command_id"))) return "INVALID_COMMAND_ID"
        if (msg.optString("agent_id") != agentId) return "AGENT_MISMATCH"
        if (msg.optString("device_id") != agentId) return "DEVICE_MISMATCH"
        if (!Proto.OPS.contains(msg.optString("op"))) return "UNKNOWN_OP"
        if (msg.optLong("deadline_ms", 0L) <= 0L) return "BAD_PARAMS"
        return null
    }

    private fun result(agentId: String, msg: JSONObject, status: String,
                       data: JSONObject?, error: String, started: Long): JSONObject {
        val o = JSONObject()
        o.put("protocol", Proto.VERSION)
        o.put("type", Proto.RESULT)
        o.put("command_id", msg.optString("command_id"))
        o.put("agent_id", agentId)
        o.put("device_id", msg.optString("device_id"))
        o.put("op", msg.optString("op"))
        o.put("status", status)
        if (data != null) o.put("data", data)
        o.put("error", error)
        o.put("started_at_ms", started)
        o.put("finished_at_ms", System.currentTimeMillis())
        return o
    }

    fun validationSelfTest(agentId: String): List<Triple<String, Boolean, String>> {
        val base = JSONObject()
            .put("protocol", Proto.VERSION).put("type", Proto.COMMAND)
            .put("command_id", UUID.randomUUID().toString())
            .put("agent_id", agentId).put("device_id", agentId)
            .put("op", Proto.PING).put("params", JSONObject())
            .put("deadline_ms", System.currentTimeMillis() + 5000)
        fun copy(): JSONObject = JSONObject(base.toString())
        val out = mutableListOf<Triple<String, Boolean, String>>()
        out.add(Triple("apk_valid_command", validateEnvelope(copy(), agentId) == null, ""))
        out.add(Triple("apk_protocol_mismatch",
            validateEnvelope(copy().put("protocol", "bad"), agentId) == "PROTOCOL_MISMATCH", ""))
        out.add(Triple("apk_agent_mismatch",
            validateEnvelope(copy().put("agent_id", "other"), agentId) == "AGENT_MISMATCH", ""))
        out.add(Triple("apk_device_mismatch",
            validateEnvelope(copy().put("device_id", "other"), agentId) == "DEVICE_MISMATCH", ""))
        out.add(Triple("apk_invalid_uuid",
            validateEnvelope(copy().put("command_id", "not-a-uuid"), agentId) == "INVALID_COMMAND_ID", ""))
        out.add(Triple("apk_unknown_op",
            validateEnvelope(copy().put("op", "EXEC_SHELL"), agentId) == "UNKNOWN_OP", ""))
        out.add(Triple("apk_bad_deadline",
            validateEnvelope(copy().put("deadline_ms", 0), agentId) == "BAD_PARAMS", ""))
        return out
    }

    fun execute(ctx: Context, prefs: Prefs, msg: JSONObject,
                send: (JSONObject) -> Unit, onStatus: (String) -> Unit) {
        val op = msg.optString("op")
        val cid = msg.optString("command_id")
        val agentId = prefs.agentId
        val deviceId = msg.optString("device_id")
        val deadline = msg.optLong("deadline_ms", 0L)
        val task = Runnable {
            val received = System.currentTimeMillis()
            val invalid = validateEnvelope(msg, agentId)
            if (invalid != null) {
                send(result(agentId, msg, "failure", null, invalid, received))
                onStatus("failure")
                return@Runnable
            }
            val cached = if (op in LARGE_OPS) largeCacheGet(cid) else cacheGet(cid)
            if (cached != null) { send(cached); onStatus("duplicate"); return@Runnable }
            if (deadline > 0 && System.currentTimeMillis() > deadline) {
                val r = result(agentId, msg, "expired", null, "EXPIRED", received)
                cachePut(cid, r)
                send(r)
                onStatus("expired")
                return@Runnable
            }
            var status = "success"
            var data: JSONObject? = null
            var error = ""
            try {
                data = runOp(ctx, prefs, op, msg.optJSONObject("params") ?: JSONObject(), received, deadline)
            } catch (e: OpError) {
                status = e.status; error = e.code
            } catch (e: PerceptionError) {
                status = "failure"; error = e.code
            } catch (e: DpnError) {
                status = DpnMutationRules.statusForDpnError(e.code)
                error = e.code
            } catch (e: Exception) {
                status = "failure"; error = e.javaClass.simpleName
            }
            val r = result(agentId, msg, status, data, error, received)
            if (TERMINAL.contains(status)) {
                if (op in LARGE_OPS) largeCachePut(cid, r) else cachePut(cid, r)
            }
            send(r)
            onStatus(status)
        }
        if (Proto.MUTATING.contains(op)) mutations.execute(task) else reads.execute(task)
    }

    @Suppress("DEPRECATION")
    private fun runOp(ctx: Context, prefs: Prefs, op: String, params: JSONObject,
                      receivedAt: Long, deadline: Long): JSONObject {
        return when (op) {
            Proto.PING -> JSONObject().put("pong", true)
            Proto.GET_STATUS -> DeviceStatus.status(ctx, DeviceAccessibilityService.currentPackage)
            Proto.GET_DEVICE_INFO -> DeviceStatus.deviceInfo(ctx)
            Proto.GET_NETWORK_INFO -> DeviceStatus.networkInfo(ctx)
            Proto.GET_FOREGROUND_APP -> foreground()
            Proto.GET_UI_TREE -> uiTree(params)
            Proto.CAPTURE_SCREEN -> captureScreen(params)
            Proto.GET_PRIVILEGED_BRIDGE_STATUS -> bridgeStatus(ctx)
            Proto.REQUEST_SHIZUKU_PERMISSION -> requestShizuku(ctx)
            Proto.STOP_APP -> privilegedStop(ctx, params)
            Proto.CLEAR_APP_DATA -> privilegedClear(ctx, params)
            Proto.GET_ACCESSIBILITY_STATE -> JSONObject().put("enabled", DeviceAccessibilityService.instance != null)
            Proto.GET_DPN_IDENTITY -> DpnClient.session(prefs).identity()
            Proto.GET_DPN_STATUS -> DpnClient.session(prefs).status()
            Proto.LIST_DPN_TUNNELS -> JSONObject().put("tunnels", DpnClient.session(prefs).tunnels())
            Proto.GET_DPN_MODE -> DpnClient.session(prefs).mode()
            Proto.GET_DPN_NODE_LB_MODE -> DpnClient.session(prefs).nodeLbMode()
            Proto.LIST_DPN_ACCESS_CONTROL -> DpnClient.session(prefs).accessControl()
            Proto.DPN_SWITCH_NODE -> DpnClient.session(prefs).switchNode(
                params.optString("expected_dpn_id"), params.optString("tunnel_code"),
                params.optString("current_ip"))
            Proto.DPN_REFRESH_ROUTE_SET -> DpnClient.session(prefs).refreshRouteSet(
                params.optString("expected_dpn_id"), params.optString("tunnel_code"))
            Proto.DPN_ADD_TUNNEL -> DpnClient.session(prefs).addTunnel(params.optString("tunnel_code"))
            Proto.DPN_DELETE_TUNNEL -> DpnClient.session(prefs).deleteTunnel(params.optString("tunnel_code"))
            Proto.DPN_ACCESS_CONTROL_MODE -> DpnClient.session(prefs).accessControlMode(
                params.optString("mode"))
            Proto.DPN_ACCESS_CONTROL_ADD_DEVICE -> DpnClient.session(prefs).accessControlAddDevice(
                params.optString("name"), params.optString("mac"))
            Proto.DPN_ACCESS_CONTROL_REMOVE_DEVICE -> DpnClient.session(prefs).accessControlRemoveDevice(
                params.optString("mac"))
            Proto.START_APP -> startApp(ctx, params)
            Proto.BACK, Proto.HOME -> action(op)
            Proto.TAP -> tap(ctx, params, receivedAt, deadline)
            Proto.SWIPE -> swipe(ctx, params, receivedAt, deadline)
            else -> throw OpError("failure", "UNSUPPORTED_OP")
        }
    }

    private fun intParam(params: JSONObject, key: String, def: Int, min: Int, max: Int): Int {
        if (!params.has(key)) return def
        val v = params.opt(key)
        if (v !is Int && v !is Long) throw OpError("failure", "BAD_PARAMS")
        val n = (v as Number).toInt()
        if ((v as Number).toDouble() != n.toDouble() || n < min || n > max) {
            throw OpError("failure", "BAD_PARAMS")
        }
        return n
    }

    private fun foreground(): JSONObject {
        val fg = DeviceAccessibilityService.instance?.foregroundSnapshot()
            ?: DeviceAccessibilityService.foregroundInfo()
        return JSONObject()
            .put("package", fg.packageName)
            .put("activity", fg.activity)
            .put("component", fg.component)
            .put("window_id", fg.windowId)
            .put("observed_at_ms", fg.observedAtMs)
    }

    private fun uiTree(params: JSONObject): JSONObject {
        val svc = DeviceAccessibilityService.instance ?: throw OpError("failure", "ACCESSIBILITY_DISABLED")
        val depth = intParam(params, "max_depth", 40, 1, 64)
        val nodes = intParam(params, "max_nodes", 1500, 1, 3000)
        val textLen = intParam(params, "max_text_length", 160, 0, 500)
        val maxBytes = intParam(params, "max_payload_bytes", 1_000_000, 1024, 2_000_000)
        return svc.buildUiTree(depth, nodes, textLen, maxBytes)
    }

    private fun captureScreen(params: JSONObject): JSONObject {
        if (Build.VERSION.SDK_INT < 30) throw OpError("failure", "CAPABILITY_NOT_SUPPORTED")
        val svc = DeviceAccessibilityService.instance ?: throw OpError("failure", "ACCESSIBILITY_DISABLED")
        val maxDim = intParam(params, "max_dim", 0, 0, 4096)
        val maxBytes = intParam(params, "max_payload_bytes", 2_000_000, 1024, 3_000_000)
        if (params.optString("format", "png") != "png") throw OpError("failure", "BAD_PARAMS")
        return svc.captureScreen(maxDim, maxBytes)
    }

    private fun bridgeStatus(ctx: Context, params: JSONObject? = null): JSONObject {
        PrivilegedBridge.ensureInit(ctx)
        return PrivilegedBridge.statusJson()
    }

    private fun requestShizuku(ctx: Context): JSONObject {
        PrivilegedBridge.ensureInit(ctx)
        return PrivilegedBridge.requestPermission()
    }

    private fun packageParam(params: JSONObject): String {
        val pkg = params.optString("package", "")
        if (!PrivilegedPolicy.isSafeTarget(pkg)) throw OpError("failure", "PACKAGE_NOT_ALLOWED")
        return pkg
    }

    private fun privilegedStop(ctx: Context, params: JSONObject): JSONObject {
        val pkg = packageParam(params)
        PrivilegedBridge.ensureInit(ctx)
        val b = try {
            PrivilegedBridge.stopApp(pkg)
        } catch (e: PrivilegedBridge.PrivilegedCallError) {
            throw OpError(if (e.dispatched) "uncertain" else "failure", e.code)
        }
        if (!b.getBoolean("success", false)) {
            throw OpError("failure", b.getString("error") ?: "STOP_FAILED")
        }
        val uid = b.getInt("effective_uid", 0)
        if (uid != 2000) throw OpError("failure", "EFFECTIVE_UID_NOT_SHELL:$uid")
        return JSONObject().put("package", pkg).put("stopped", true)
            .put("effective_uid", uid).put("duration_ms", b.getLong("duration_ms", 0L))
            .put("verified_stopped", b.getBoolean("verified_stopped", false))
    }

    private fun privilegedClear(ctx: Context, params: JSONObject): JSONObject {
        val pkg = packageParam(params)
        PrivilegedBridge.ensureInit(ctx)
        val b = try {
            PrivilegedBridge.clearAppData(pkg)
        } catch (e: PrivilegedBridge.PrivilegedCallError) {
            throw OpError(if (e.dispatched) "uncertain" else "failure", e.code)
        }
        if (!b.getBoolean("success", false)) {
            throw OpError("failure", b.getString("error") ?: "CLEAR_FAILED")
        }
        val uid = b.getInt("effective_uid", 0)
        if (uid != 2000) throw OpError("failure", "EFFECTIVE_UID_NOT_SHELL:$uid")
        return JSONObject().put("package", pkg).put("cleared", true)
            .put("effective_uid", uid).put("duration_ms", b.getLong("duration_ms", 0L))
            .put("verified_stopped", b.getBoolean("verified_stopped", false))
    }

    private fun startApp(ctx: Context, params: JSONObject): JSONObject {
        val pkg = params.optString("package", "")
        if (pkg.isBlank()) throw OpError("failure", "BAD_PARAMS")
        val pm = ctx.packageManager
        val exists = try { pm.getPackageInfo(pkg, 0); true } catch (e: PackageManager.NameNotFoundException) { false }
        if (!exists) throw OpError("failure", "PACKAGE_NOT_FOUND")
        val intent = pm.getLaunchIntentForPackage(pkg) ?: throw OpError("failure", "NOT_LAUNCHABLE")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        ctx.startActivity(intent)
        return JSONObject().put("started", true).put("package", pkg)
    }

    private fun action(op: String): JSONObject {
        val svc = DeviceAccessibilityService.instance ?: throw OpError("failure", "ACCESSIBILITY_DISABLED")
        val ok = if (op == Proto.BACK) svc.back() else svc.home()
        if (!ok) throw OpError("failure", "ACTION_FAILED")
        return JSONObject().put("ok", true)
    }

    private fun num(params: JSONObject, key: String): Double {
        if (!params.has(key)) throw OpError("failure", "BAD_PARAMS")
        val v = params.optDouble(key, Double.NaN)
        if (!v.isFinite()) throw OpError("failure", "BAD_PARAMS")
        return v
    }

    private fun duration(params: JSONObject, min: Long, max: Long): Long {
        val v = num(params, "duration_ms")
        if (v < min || v > max) throw OpError("failure", "BAD_PARAMS")
        return v.toLong()
    }

    private fun displayBounds(ctx: Context, params: JSONObject, keys: List<Pair<String, String>>) {
        val d = DeviceStatus.display(ctx)
        val w = d.optInt("width_px")
        val h = d.optInt("height_px")
        if (w <= 0 || h <= 0) throw OpError("failure", "DISPLAY_UNAVAILABLE")
        for ((xk, yk) in keys) {
            val x = num(params, xk)
            val y = num(params, yk)
            if (x < 0 || x >= w || y < 0 || y >= h) throw OpError("failure", "BAD_PARAMS")
        }
    }

    private fun dispatchDeadlineElapsed(deadline: Long): Long {
        val nowElapsed = SystemClock.elapsedRealtime()
        if (deadline <= 0) return nowElapsed + 4000L
        val remaining = deadline - System.currentTimeMillis()
        if (remaining <= 0) throw OpError("expired", "EXPIRED")
        return nowElapsed + remaining
    }

    private fun gestureOutcome(outcome: GestureOutcome): String {
        return when (outcome.status) {
            "success" -> "success"
            "uncertain" -> throw OpError("uncertain", "GESTURE_UNCERTAIN")
            "expired" -> throw OpError("expired", "EXPIRED")
            else -> throw OpError("failure", outcome.error.ifBlank { "GESTURE_CANCELLED" })
        }
    }

    private fun gestureData(ctx: Context, receivedAt: Long, queueMs: Long,
                            outcome: GestureOutcome): JSONObject {
        val d = DeviceStatus.display(ctx)
        val o = JSONObject()
        o.put("queue_ms", queueMs)
        o.put("gesture_ms", outcome.gestureMs)
        o.put("width_px", d.optInt("width_px"))
        o.put("height_px", d.optInt("height_px"))
        o.put("rotation", d.optInt("rotation"))
        o.put("side_effect_possible", true)
        return o
    }

    private fun tap(ctx: Context, params: JSONObject, receivedAt: Long, deadline: Long): JSONObject {
        val dur = duration(params, 10, 1000)
        displayBounds(ctx, params, listOf("x" to "y"))
        val svc = DeviceAccessibilityService.instance ?: throw OpError("failure", "ACCESSIBILITY_DISABLED")
        if (!svc.canDispatchGestures()) throw OpError("failure", "GESTURE_CANCELLED")
        val x = num(params, "x").toFloat()
        val y = num(params, "y").toFloat()
        val dispatchDeadline = dispatchDeadlineElapsed(deadline)
        val dispatchAt = System.currentTimeMillis()
        val outcome = svc.performGesture(floatArrayOf(x, y), dur, dispatchDeadline, dur + 1500)
        gestureOutcome(outcome)
        val data = gestureData(ctx, receivedAt, dispatchAt - receivedAt, outcome)
        data.put("x", x.toDouble()).put("y", y.toDouble()).put("duration_ms", dur)
        return data
    }

    private fun swipe(ctx: Context, params: JSONObject, receivedAt: Long, deadline: Long): JSONObject {
        val dur = duration(params, 10, 3000)
        displayBounds(ctx, params, listOf("x1" to "y1", "x2" to "y2"))
        val svc = DeviceAccessibilityService.instance ?: throw OpError("failure", "ACCESSIBILITY_DISABLED")
        if (!svc.canDispatchGestures()) throw OpError("failure", "GESTURE_CANCELLED")
        val x1 = num(params, "x1").toFloat()
        val y1 = num(params, "y1").toFloat()
        val x2 = num(params, "x2").toFloat()
        val y2 = num(params, "y2").toFloat()
        val dispatchDeadline = dispatchDeadlineElapsed(deadline)
        val dispatchAt = System.currentTimeMillis()
        val outcome = svc.performGesture(floatArrayOf(x1, y1, x2, y2), dur, dispatchDeadline, dur + 1500)
        gestureOutcome(outcome)
        val data = gestureData(ctx, receivedAt, dispatchAt - receivedAt, outcome)
        data.put("x1", x1.toDouble()).put("y1", y1.toDouble())
            .put("x2", x2.toDouble()).put("y2", y2.toDouble()).put("duration_ms", dur)
        return data
    }
}
