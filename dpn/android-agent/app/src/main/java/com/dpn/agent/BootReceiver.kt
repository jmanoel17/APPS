package com.dpn.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Sobe o agente automaticamente apos o reinicio do aparelho (se configurado).
 * O servico em primeiro plano cuida da reconexao com backoff.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        if (action == Intent.ACTION_BOOT_COMPLETED ||
            action == "android.intent.action.QUICKBOOT_POWERON" ||
            action == "com.htc.intent.action.QUICKBOOT_POWERON") {
            val prefs = Prefs(context)
            if (prefs.isConfigured()) {
                AgentService.start(context)
            }
        }
    }
}
