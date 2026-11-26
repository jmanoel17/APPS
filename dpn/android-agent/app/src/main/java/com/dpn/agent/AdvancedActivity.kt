package com.dpn.agent

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

/** Tela tecnica (URLs, fingerprints, codigo, diagnóstico). */
class AdvancedActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var advStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_advanced)
        prefs = Prefs(this)
        advStatus = findViewById(R.id.advStatus)

        val url = findViewById<EditText>(R.id.controllerUrl)
        val fp = findViewById<EditText>(R.id.controllerFp)
        val code = findViewById<EditText>(R.id.pairingCode)
        val atomos = findViewById<EditText>(R.id.atomosUrl)
        val atomosFp = findViewById<EditText>(R.id.atomosFp)
        val pwd = findViewById<EditText>(R.id.atomosPassword)

        url.setText(prefs.controllerUrl)
        fp.setText(prefs.controllerFp)
        atomos.setText(prefs.atomosUrl)
        atomosFp.setText(prefs.atomosFp)
        pwd.setText(prefs.atomosPassword ?: "")

        findViewById<Button>(R.id.btnSave).setOnClickListener {
            prefs.controllerUrl = url.text.toString().trim()
            prefs.controllerFp = fp.text.toString().trim().lowercase()
            prefs.pairingCode = code.text.toString().trim()
            prefs.atomosUrl = atomos.text.toString().trim().ifBlank { "https://34.34.34.34" }
            prefs.atomosFp = atomosFp.text.toString().trim().lowercase()
            prefs.atomosPassword = pwd.text.toString()
            Operations.reset()
            Toast.makeText(this, "Salvo", Toast.LENGTH_SHORT).show()
        }

        findViewById<Button>(R.id.btnDiag).setOnClickListener {
            advStatus.text = "Consultando AtomOS…"
            Thread {
                val text = try {
                    val c = AtomOsClient(prefs.atomosUrl, prefs.atomosFp.ifBlank { null })
                    val id = c.getDeviceId(prefs.atomosPassword)
                    "AtomOS OK — deviceId=${id.optString("deviceId")}\n" +
                        "fingerprint: ${c.observedFingerprint}"
                } catch (e: Exception) {
                    "Falha: ${e.message}"
                }
                Handler(Looper.getMainLooper()).post { advStatus.text = text }
            }.start()
        }

        findViewById<Button>(R.id.btnReset).setOnClickListener {
            prefs.resetPairing()
            Operations.reset()
            Toast.makeText(this, "Pareamento zerado (precisa parear de novo)",
                Toast.LENGTH_LONG).show()
        }
    }
}
