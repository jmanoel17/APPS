package com.dpn.agent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.IBinder
import android.util.Log

/**
 * Servico em primeiro plano que mantem a conexao WSS com o controlador.
 * Requer notificacao persistente (Android 8+) e, no Android 13+, permissao
 * POST_NOTIFICATIONS. Nao promete execucao permanente: depende das politicas de
 * Doze/rede do aparelho — faca os testes nos equipamentos reais.
 */
class AgentService : Service() {

    private var wsClient: WsClient? = null
    private var netCallback: ConnectivityManager.NetworkCallback? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        startForeground(NOTIF_ID, buildNotification("Iniciando…"))
        wsClient = WsClient(Prefs(applicationContext)) {
            updateNotification(AgentState.controllerStatus)
        }.also { it.start() }
        registerNetworkCallback()
    }

    private fun registerNetworkCallback() {
        try {
            val cm = getSystemService(ConnectivityManager::class.java)
            val cb = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    // rede mudou: invalida a sessao AtomOS; o socket antigo cai e o
                    // WsClient reconecta sozinho (nao derrubamos conexao saudavel)
                    Operations.reset()
                    AgentState.atomosStatus = "unknown"
                    Log.i("DPNAgent", "network onAvailable")
                }
            }
            cm?.registerDefaultNetworkCallback(cb)
            netCallback = cb
        } catch (e: Exception) {
            Log.w("DPNAgent", "network callback: ${e.message}")
        }
    }

    private fun unregisterNetworkCallback() {
        try {
            netCallback?.let {
                getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(it)
            }
        } catch (_: Exception) {
        }
        netCallback = null
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // segurança: garante o servico e a conexao mesmo apos o sistema reinicia-lo
        if (wsClient == null) {
            startForeground(NOTIF_ID, buildNotification(AgentState.controllerStatus))
            wsClient = WsClient(Prefs(applicationContext)) {
                updateNotification(AgentState.controllerStatus)
            }.also { it.start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        unregisterNetworkCallback()
        wsClient?.stop()
        wsClient = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL, "DPN Agent",
                NotificationManager.IMPORTANCE_LOW)
            getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
        }
    }

    private fun buildNotification(text: String): Notification {
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            Notification.Builder(this, CHANNEL) else @Suppress("DEPRECATION") Notification.Builder(this)
        return b.setContentTitle("DPN Agent")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_upload)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(text: String) {
        getSystemService(NotificationManager::class.java).notify(
            NOTIF_ID, buildNotification(text))
    }

    companion object {
        private const val CHANNEL = "dpn_agent"
        private const val NOTIF_ID = 1

        fun start(context: android.content.Context) {
            val i = Intent(context, AgentService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                context.startForegroundService(i) else context.startService(i)
        }

        fun stop(context: android.content.Context) {
            context.stopService(Intent(context, AgentService::class.java))
        }
    }
}
