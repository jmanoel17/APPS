package com.sln.deviceagent

import android.content.Context
import android.net.Uri
import android.util.Log
import org.json.JSONObject

/** One-way migration from the legacy DPN Agent. Signature-protected provider. */
object DpnMigration {
    private const val TAG = "SLNAgent"
    private val URI = Uri.parse("content://com.dpn.agent.migration/config")

    fun importIfNeeded(ctx: Context, prefs: Prefs): Boolean {
        if (!prefs.atomosPassword.isNullOrBlank()) return true
        try {
            ctx.contentResolver.query(URI, arrayOf("json"), null, null, null)?.use { c ->
                if (!c.moveToFirst()) return false
                val json = JSONObject(c.getString(0))
                val pw = json.optString("atomos_password", "")
                if (pw.isBlank()) return false
                val url = json.optString("atomos_url", "https://34.34.34.34")
                if (url.startsWith("https://")) prefs.atomosUrl = url
                prefs.atomosFp = json.optString("atomos_fp", "")
                prefs.atomosPassword = pw
                Log.i(TAG, "DPN_MIGRATION_OK atomos=${prefs.atomosUrl} has_pw=true")
                return true
            }
        } catch (e: Exception) {
            Log.w(TAG, "DPN_MIGRATION_UNAVAILABLE ${e.javaClass.simpleName}")
        }
        return false
    }
}
