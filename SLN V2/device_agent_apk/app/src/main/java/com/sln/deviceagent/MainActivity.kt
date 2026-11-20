package com.sln.deviceagent

import android.app.Activity
import android.os.Bundle
import android.util.Log
import android.widget.TextView

class MainActivity : Activity() {
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        val p = Prefs(this)
        val tv = TextView(this)
        tv.text = "SLN Device Agent\nagent_id=${p.agentId}\ncontroller=${p.controllerUrl}\npaired=${!p.token.isNullOrBlank()}"
        tv.setTextIsSelectable(true)
        setContentView(tv)
        if (p.controllerUrl.isBlank() || p.token.isNullOrBlank()) {
            Log.i("SLNAgent", "PAIRING_REQUIRED")
        }
        if (p.controllerUrl.isNotBlank()) {
            try { AgentService.start(this) } catch (_: Exception) {}
        }
    }
}
