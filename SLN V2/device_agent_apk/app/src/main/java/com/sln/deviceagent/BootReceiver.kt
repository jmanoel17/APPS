package com.sln.deviceagent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        Log.i("SLNAgent", "BOOT_RECEIVED ${intent.action}")
        try {
            val p = Prefs(ctx)
            if (p.controllerUrl.isNotBlank()) {
                Log.i("SLNAgent", "SERVICE_START_REQUESTED")
                AgentService.start(ctx)
            }
        } catch (e: Exception) {
            Log.w("SLNAgent", "SERVICE_START_BLOCKED ${e.javaClass.simpleName}")
        }
    }
}
