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
        intent.getStringExtra("atomos_url")?.let {
            if (it.startsWith("https://")) p.atomosUrl = it else
                Log.w("SLNAgent", "PROVISION_REJECTED_ATOMOS_URL (https obrigatorio)")
        }
        intent.getStringExtra("atomos_fp")?.let { if (it.isNotBlank()) p.atomosFp = it }
        intent.getStringExtra("atomos_password")?.let { if (it.isNotBlank()) p.atomosPassword = it }
        if (intent.hasExtra("dpn_auto_probe")) {
            p.dpnAutoProbe = intent.getBooleanExtra("dpn_auto_probe", false)
        }
        Log.i("SLNAgent", "PROVISION agent=${p.agentId} url=${p.controllerUrl} " +
            "has_code=${p.pairingCode.isNotBlank()} atomos=${p.atomosUrl} " +
            "has_atomos_pw=${!p.atomosPassword.isNullOrBlank()} caller=${callingPackage}")
        AgentService.start(this)
        finish()
    }
}
