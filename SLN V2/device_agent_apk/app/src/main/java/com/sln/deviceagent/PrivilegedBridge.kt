package com.sln.deviceagent

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.RemoteException
import android.util.Log
import org.json.JSONObject
import rikka.shizuku.Shizuku

object PrivilegedBridge {
    enum class State {
        UNAVAILABLE, AVAILABLE_NOT_AUTHORIZED, AUTHORIZED, BINDING, READY, BINDER_DEAD, ERROR
    }

    class PrivilegedCallError(val code: String, val dispatched: Boolean) : Exception(code)

    private const val TAG = "SLNPrivileged"
    private const val PERMISSION_CODE = 1001
    private const val BIND_RETRY_MS = 5000L

    private val lock = Any()
    private val handler = Handler(Looper.getMainLooper())
    private var appContext: Context? = null
    private var initialized = false
    private var args: Shizuku.UserServiceArgs? = null
    private var iface: IPrivilegedService? = null
    private var connection: ServiceConnection? = null
    private var bindRunnable: Runnable? = null
    private var binding = false
    private var everBinder = false
    private var permissionRequested = false
    private var lastError = ""
    private var state = State.UNAVAILABLE
    private var available = false
    private var authorized = false
    private var binderAlive = false
    private var userServiceReady = false
    private var effectiveUid: Int? = null
    private var processName = ""
    private var changedAt = 0L

    private val receiveListener = Shizuku.OnBinderReceivedListener {
        synchronized(lock) { everBinder = true }
        refresh()
    }
    private val deadListener = Shizuku.OnBinderDeadListener {
        synchronized(lock) {
            available = false
            binderAlive = false
            authorized = false
            userServiceReady = false
            iface = null
            binding = false
            effectiveUid = null
            processName = ""
            state = State.BINDER_DEAD
            changedAt = System.currentTimeMillis()
            cancelBind()
        }
        Log.w(TAG, "binder_dead")
        broadcast()
    }
    private val permissionListener = Shizuku.OnRequestPermissionResultListener { code, grant ->
        if (code == PERMISSION_CODE) {
            synchronized(lock) { permissionRequested = false }
            refresh()
        }
    }

    fun ensureInit(ctx: Context) {
        synchronized(lock) {
            if (initialized) return
            appContext = ctx.applicationContext
            initialized = true
        }
        try {
            Shizuku.addBinderReceivedListenerSticky(receiveListener, handler)
            Shizuku.addBinderDeadListener(deadListener, handler)
            Shizuku.addRequestPermissionResultListener(permissionListener, handler)
        } catch (e: Exception) {
            synchronized(lock) {
                state = State.ERROR
                lastError = "LISTENER_INIT:${e.javaClass.simpleName}"
            }
        }
        refresh()
    }

    fun refresh() {
        synchronized(lock) {
            if (!initialized) return
            val ping = try { Shizuku.pingBinder() } catch (_: Exception) { false }
            available = ping
            binderAlive = ping
            if (!ping) {
                authorized = false
                userServiceReady = false
                iface = null
                binding = false
                effectiveUid = null
                processName = ""
                state = if (everBinder) State.BINDER_DEAD else State.UNAVAILABLE
                lastError = ""
                changedAt = System.currentTimeMillis()
                cancelBind()
                broadcastLocked()
                return
            }
            everBinder = true
            val granted = try {
                Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
            } catch (e: Exception) {
                false
            }
            authorized = granted
            if (!granted) {
                userServiceReady = false
                state = State.AVAILABLE_NOT_AUTHORIZED
                lastError = ""
                changedAt = System.currentTimeMillis()
                broadcastLocked()
                return
            }
            if (userServiceReady && iface != null && effectiveUid == 2000) {
                state = State.READY
                lastError = ""
                broadcastLocked()
                return
            }
            if (state != State.BINDING) {
                state = State.AUTHORIZED
                changedAt = System.currentTimeMillis()
                broadcastLocked()
            }
            bindIfNeededLocked()
        }
    }

    fun isReady(): Boolean = synchronized(lock) {
        state == State.READY && binderAlive && authorized && userServiceReady && effectiveUid == 2000
    }

    fun signature(): String = synchronized(lock) {
        "${state.name}:${effectiveUid ?: -1}:${binderAlive}:${authorized}:${userServiceReady}"
    }

    fun statusJson(): JSONObject {
        refresh()
        synchronized(lock) {
            val ctx = appContext
            val apiVersion = try { if (binderAlive) Shizuku.getVersion() else 0 } catch (_: Exception) { 0 }
            val shizukuVersion = try {
                ctx?.packageManager?.getPackageInfo("moe.shizuku.privileged.api", 0)?.versionName ?: ""
            } catch (_: Exception) { "" }
            return JSONObject()
                .put("backend", "shizuku")
                .put("available", available)
                .put("authorized", authorized)
                .put("binder_alive", binderAlive)
                .put("user_service_ready", userServiceReady)
                .put("effective_uid", effectiveUid ?: JSONObject.NULL)
                .put("state", state.name)
                .put("api_version", apiVersion)
                .put("shizuku_version", shizukuVersion)
                .put("process_name", processName)
                .put("permission_requested", permissionRequested)
                .put("error", lastError)
                .put("changed_at_ms", changedAt)
        }
    }

    fun requestPermission(): JSONObject {
        ensureInit(appContext ?: throw IllegalStateException("bridge not initialized"))
        val ping = try { Shizuku.pingBinder() } catch (_: Exception) { false }
        if (!ping) return statusJson()
        val granted = try {
            Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
        } catch (_: Exception) { false }
        if (granted) {
            refresh()
            return statusJson()
        }
        val rationale = try { Shizuku.shouldShowRequestPermissionRationale() } catch (_: Exception) { false }
        if (rationale) {
            synchronized(lock) {
                state = State.AVAILABLE_NOT_AUTHORIZED
                lastError = "PERMISSION_DENIED_PERMANENTLY"
            }
            broadcast()
            return statusJson()
        }
        synchronized(lock) { permissionRequested = true }
        handler.post {
            try {
                Shizuku.requestPermission(PERMISSION_CODE)
                Log.i(TAG, "permission_requested")
            } catch (e: Exception) {
                synchronized(lock) {
                    permissionRequested = false
                    state = State.ERROR
                    lastError = "PERMISSION_REQUEST:${e.javaClass.simpleName}"
                }
                broadcast()
            }
        }
        return statusJson()
    }

    fun stopApp(pkg: String): Bundle = call("STOP_APP", pkg) { svc, p ->
        svc.stopApp(p)
    }

    fun clearAppData(pkg: String): Bundle = call("CLEAR_APP_DATA", pkg) { svc, p ->
        svc.clearAppData(p)
    }

    private fun call(op: String, pkg: String, fn: (IPrivilegedService, String) -> Bundle): Bundle {
        if (!PrivilegedPolicy.isSafeTarget(pkg)) {
            throw PrivilegedCallError("PACKAGE_NOT_ALLOWED", false)
        }
        val svc: IPrivilegedService
        synchronized(lock) {
            if (!isReadyLocked()) throw PrivilegedCallError("PRIVILEGED_BRIDGE_NOT_READY", false)
            svc = iface ?: throw PrivilegedCallError("PRIVILEGED_BRIDGE_NOT_READY", false)
        }
        return try {
            fn(svc, pkg)
        } catch (e: RemoteException) {
            synchronized(lock) {
                userServiceReady = false
                iface = null
                binding = false
                effectiveUid = null
                state = if (try { Shizuku.pingBinder() } catch (_: Exception) { false }) {
                    State.AUTHORIZED
                } else {
                    State.BINDER_DEAD
                }
                lastError = "PRIVILEGED_BRIDGE_LOST"
                changedAt = System.currentTimeMillis()
            }
            broadcast()
            throw PrivilegedCallError("PRIVILEGED_BRIDGE_LOST", true)
        } catch (e: Exception) {
            throw PrivilegedCallError("PRIVILEGED_CALL_FAILED:${e.javaClass.simpleName}", false)
        }
    }

    private fun isReadyLocked(): Boolean =
        state == State.READY && binderAlive && authorized && userServiceReady && effectiveUid == 2000

    private fun bindIfNeededLocked() {
        if (!authorized || binding || iface != null) return
        val ctx = appContext ?: return
        val component = ComponentName(ctx.packageName, ShizukuUserService::class.java.name)
        val ua = Shizuku.UserServiceArgs(component)
            .daemon(false)
            .processNameSuffix("privileged")
            .debuggable(BuildConfig.DEBUG)
            .version(BuildConfig.VERSION_CODE)
            .tag("sln-privileged-v1")
        args = ua
        val conn = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
                synchronized(lock) {
                    binding = false
                    cancelBind()
                    if (binder == null) {
                        state = State.ERROR
                        lastError = "NULL_BINDER"
                        changedAt = System.currentTimeMillis()
                        broadcastLocked()
                        return
                    }
                    val svc = IPrivilegedService.Stub.asInterface(binder)
                    iface = svc
                    try {
                        val uid = svc.uid
                        val pname = try { svc.processName } catch (_: Exception) { "" }
                        effectiveUid = uid
                        processName = pname
                        if (uid == 2000) {
                            userServiceReady = true
                            state = State.READY
                            lastError = ""
                            Log.i(TAG, "ready uid=2000 process=$pname")
                        } else {
                            userServiceReady = false
                            state = State.ERROR
                            lastError = "EFFECTIVE_UID_NOT_SHELL:$uid"
                            Log.e(TAG, "effective uid rejected uid=$uid")
                        }
                    } catch (e: Exception) {
                        userServiceReady = false
                        state = State.ERROR
                        lastError = "GET_UID:${e.javaClass.simpleName}"
                    }
                    changedAt = System.currentTimeMillis()
                    broadcastLocked()
                }
            }

            override fun onServiceDisconnected(name: ComponentName?) {
                synchronized(lock) {
                    binding = false
                    iface = null
                    userServiceReady = false
                    effectiveUid = null
                    processName = ""
                    state = if (authorized && binderAlive) State.AUTHORIZED else State.BINDER_DEAD
                    lastError = "USER_SERVICE_DISCONNECTED"
                    changedAt = System.currentTimeMillis()
                    scheduleBindLocked(2000L)
                    broadcastLocked()
                }
            }
        }
        connection = conn
        binding = true
        state = State.BINDING
        changedAt = System.currentTimeMillis()
        broadcastLocked()
        handler.post {
            try {
                if (Shizuku.pingBinder()) {
                    Shizuku.bindUserService(ua, conn)
                } else {
                    synchronized(lock) {
                        binding = false
                        state = State.BINDER_DEAD
                        lastError = "BINDER_DEAD"
                    }
                    broadcast()
                }
            } catch (e: Exception) {
                synchronized(lock) {
                    binding = false
                    state = State.ERROR
                    lastError = "BIND:${e.javaClass.simpleName}"
                }
                scheduleBindLocked(BIND_RETRY_MS)
                broadcast()
            }
        }
    }

    private fun scheduleBindLocked(delayMs: Long) {
        if (!authorized || bindRunnable != null) return
        val r = Runnable {
            synchronized(lock) {
                bindRunnable = null
                if (authorized && !userServiceReady) {
                    state = State.AUTHORIZED
                    bindIfNeededLocked()
                }
            }
        }
        bindRunnable = r
        handler.postDelayed(r, delayMs.coerceAtLeast(1000L))
    }

    private fun cancelBind() {
        bindRunnable?.let { handler.removeCallbacks(it) }
        bindRunnable = null
    }

    private fun broadcast() {
        synchronized(lock) { broadcastLocked() }
    }

    private fun broadcastLocked() {
        try {
            val ctx = appContext ?: return
            ctx.sendBroadcast(Intent(DeviceAccessibilityService.ACTION_CHANGED).setPackage(ctx.packageName))
        } catch (_: Exception) {}
    }
}
