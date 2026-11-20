package com.sln.deviceagent

import android.app.Activity
import android.os.Bundle
import android.util.Log

class ProvisionActivity : Activity() {
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        val p = Prefs(this)
        val url = intent.getStringExtra("controller_url")
        if (!url.isNullOrBlank()) {
            if (url.startsWith("wss://")) {
                p.controllerUrl = url
            } else {
                Log.w("SLNAgent", "PROVISION_REJECTED_URL (wss obrigatorio)")
            }
        }
        intent.getStringExtra("pairing_code")?.let { if (it.isNotBlank()) p.pairingCode = it }
        Log.i("SLNAgent", "PROVISION agent=${p.agentId} url=${p.controllerUrl} " +
            "has_code=${p.pairingCode.isNotBlank()} caller=${callingPackage}")
        AgentService.start(this)
        finish()
    }
}
