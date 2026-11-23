package com.sln.deviceagent

import android.app.Activity
import android.os.Bundle
import java.io.File

class DpnCheckActivity : Activity() {
    override fun onCreate(b: Bundle?) {
        super.onCreate(b)
        Thread {
            val result = try {
                DpnClient(Prefs(this)).identity().toString()
            } catch (e: Exception) {
                org.json.JSONObject().put("reachable", false)
                    .put("error", e.javaClass.simpleName)
                    .put("message", e.message ?: "").toString()
            }
            try { File(filesDir, "dpn_check.json").writeText(result) } catch (_: Exception) {}
            finish()
        }.start()
    }
}
