package com.dpn.agent

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import org.json.JSONObject

/**
 * One-way, local migration bridge for the unified SLN Device Agent.
 * The AtomOS secret is never sent over WSS; access is signature-protected.
 */
class MigrationProvider : ContentProvider() {
    override fun onCreate(): Boolean = true

    override fun query(uri: Uri, projection: Array<out String>?, selection: String?,
                       selectionArgs: Array<out String>?, sortOrder: String?): Cursor? {
        if (uri.path != "/config") return null
        val ctx = context ?: return null
        val p = Prefs(ctx)
        val out = JSONObject()
            .put("atomos_url", p.atomosUrl)
            .put("atomos_fp", p.atomosFp)
            .put("atomos_password", p.atomosPassword ?: "")
        val c = MatrixCursor(arrayOf("json"))
        c.addRow(arrayOf(out.toString()))
        return c
    }

    override fun getType(uri: Uri): String = "application/json"
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, selection: String?,
                        selectionArgs: Array<out String>?): Int = 0
}
