package com.sln.deviceagent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.res.Configuration
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.IBinder
import android.util.Log

class AgentService : Service() {
    companion object {
        private const val TAG = "SLNAgent"
        private const val CH = "sln_agent"
        fun start(ctx: Context) {
            val i = Intent(ctx, AgentService::class.java)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }
    }

    private lateinit var prefs: Prefs
    private var ws: WsClient? = null
    private var receiver: BroadcastReceiver? = null
    private var cm: ConnectivityManager? = null
    private var cb: ConnectivityManager.NetworkCallback? = null

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        DpnMigration.importIfNeeded(applicationContext, prefs)
        PrivilegedBridge.ensureInit(applicationContext)
        createChannel()
        startForeground(1, notification())
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        intent?.let {
            if (it.hasExtra("controller_url")) prefs.controllerUrl = it.getStringExtra("controller_url") ?: ""
            if (it.hasExtra("pairing_code")) prefs.pairingCode = it.getStringExtra("pairing_code") ?: ""
        }
        if (ws == null) { ws = WsClient(this, prefs); ws?.start() }
        registerAccessibilityReceiver()
        registerNetwork()
        Log.i(TAG, "SERVICE_STARTED agent=${prefs.agentId}")
        return START_STICKY
    }

    private fun registerAccessibilityReceiver() {
        if (receiver != null) return
        receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, i: Intent?) { ws?.onAccessibilityChanged() }
        }
        val f = IntentFilter(DeviceAccessibilityService.ACTION_CHANGED)
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(receiver, f, Context.RECEIVER_NOT_EXPORTED)
        else @Suppress("DEPRECATION") registerReceiver(receiver, f)
    }

    private fun registerNetwork() {
        if (cm != null) return
        cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { ws?.reconnectNow() }
        }
        try { cm?.registerDefaultNetworkCallback(cb!!) } catch (_: Exception) {}
    }

    override fun onDestroy() {
        try { receiver?.let { unregisterReceiver(it) } } catch (_: Exception) {}
        try { cb?.let { cm?.unregisterNetworkCallback(it) } } catch (_: Exception) {}
        ws?.stop()
        ws = null
        Log.i(TAG, "SERVICE_STOPPED")
        super.onDestroy()
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        ws?.onDisplayChanged()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(CH) == null) {
            nm.createNotificationChannel(NotificationChannel(CH, "SLN Device Agent",
                NotificationManager.IMPORTANCE_LOW))
        }
    }

    private fun notification(): Notification {
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CH)
        else @Suppress("DEPRECATION") Notification.Builder(this)
        return b.setContentTitle("SLN Device Agent")
            .setContentText("agent ativo")
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setOngoing(true)
            .build()
    }
}
