package com.dpn.agent

import android.app.Activity
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity

/**
 * Activity EXPORTADA apenas para provisionamento explicito (via ADB).
 * - Aceita somente URL do controlador, fingerprint, codigo TEMPORARIO e AtomOS.
 * - NUNCA aceita senha AtomOS nem token permanente.
 * - Exige confirmacao no aparelho antes de aplicar (evita reconfiguracao
 *   silenciosa por outro app).
 */
class ProvisionActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(TextView(this).apply { text = "Provisionando agente…"; textSize = 16f })

        val url = intent.getStringExtra("controller_url")
        val fp = intent.getStringExtra("controller_fp")
        val code = intent.getStringExtra("pairing_code")
        val atomos = intent.getStringExtra("atomos_url") ?: "https://34.34.34.34"

        if (url.isNullOrBlank() || fp.isNullOrBlank() || code.isNullOrBlank()) {
            finish()
            return
        }
        AlertDialog.Builder(this)
            .setTitle("Configurar agente")
            .setMessage("Configurar este aparelho para o controlador:\n\n$url\n\n" +
                "A confirmação no aparelho evita reconfiguração indevida.")
            .setCancelable(false)
            .setPositiveButton("Configurar") { _, _ -> apply(url, fp, code, atomos) }
            .setNegativeButton("Cancelar") { _, _ -> finish() }
            .show()
    }

    private fun apply(url: String, fp: String, code: String, atomos: String) {
        val prefs = Prefs(this)
        prefs.controllerUrl = url
        prefs.controllerFp = fp.lowercase()
        prefs.pairingCode = code
        prefs.atomosUrl = atomos
        prefs.sessionToken = null      // exige novo pareamento
        AgentService.start(this)
        finish()
    }
}
