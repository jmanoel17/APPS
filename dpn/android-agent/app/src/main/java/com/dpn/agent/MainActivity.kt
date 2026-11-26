package com.dpn.agent

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import org.json.JSONObject

/** Tela principal simplificada. Detalhes tecnicos ficam em "Avançado". */
class MainActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var txtAssoc: TextView
    private lateinit var txtStatus: TextView
    private lateinit var btnPrimary: Button
    private val handler = Handler(Looper.getMainLooper())

    private val scanLauncher = registerForActivityResult(ScanContract()) { result ->
        val contents = result.contents
        if (!contents.isNullOrBlank()) onQr(contents)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)
        txtAssoc = findViewById(R.id.txtAssoc)
        txtStatus = findViewById(R.id.txtStatus)
        btnPrimary = findViewById(R.id.btnPrimary)

        btnPrimary.setOnClickListener {
            if (isConnected()) disconnect() else connect()
        }
        findViewById<Button>(R.id.btnQr).setOnClickListener { scanQr() }
        findViewById<Button>(R.id.btnAdvanced).setOnClickListener {
            startActivity(Intent(this, AdvancedActivity::class.java))
        }

        // autoconexao: se ja configurado, sobe o servico automaticamente
        if (prefs.isConfigured()) {
            connect()
        }

        handler.post(object : Runnable {
            override fun run() { render(); handler.postDelayed(this, 1000) }
        })
    }

    private fun isConnected(): Boolean {
        val s = AgentState.controllerStatus.lowercase()
        return s.contains("online") || s.contains("pareado")
    }

    private fun connect() {
        if (prefs.controllerUrl.isBlank() || prefs.controllerFp.isBlank()) {
            Toast.makeText(this, "Configure em 'Ler QR' ou 'Avançado'",
                Toast.LENGTH_LONG).show()
            return
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this,
                arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        AgentService.start(this)
    }

    private fun disconnect() {
        AgentService.stop(this)
        AgentState.controllerStatus = "desconectado"
    }

    private fun scanQr() {
        val opts = ScanOptions()
            .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            .setPrompt("Aponte para o QR do controlador")
            .setBeepEnabled(false)
            .setOrientationLocked(false)
        scanLauncher.launch(opts)
    }

    private fun onQr(contents: String) {
        val d = try { JSONObject(contents) } catch (_: Exception) { null }
        if (d == null || d.optString("url").isBlank() || d.optString("fp").isBlank()
            || d.optString("code").isBlank()) {
            Toast.makeText(this, "QR inválido", Toast.LENGTH_LONG).show()
            return
        }
        val url = d.optString("url")
        AlertDialog.Builder(this)
            .setTitle("Configurar via QR")
            .setMessage("Configurar este aparelho para o controlador:\n\n$url")
            .setPositiveButton("Configurar") { _, _ ->
                prefs.controllerUrl = url
                prefs.controllerFp = d.optString("fp").lowercase()
                prefs.pairingCode = d.optString("code")
                if (prefs.atomosUrl.isBlank()) prefs.atomosUrl = "https://34.34.34.34"
                prefs.sessionToken = null
                if (Build.VERSION.SDK_INT >= 33 &&
                    ContextCompat.checkSelfPermission(this,
                        Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                    ActivityCompat.requestPermissions(this,
                        arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
                }
                AgentService.start(this)
            }
            .setNegativeButton("Cancelar", null)
            .show()
    }

    private fun render() {
        val name = prefs.associationName.ifBlank { "(sem nome)" }
        txtAssoc.text = "Associação: $name"
        val id = prefs.agentId
        txtStatus.text = buildString {
            append("Agente: ").append(id.take(12)).append("…\n")
            append("Controlador: ").append(AgentState.controllerStatus).append('\n')
            append("AtomOS: ").append(AgentState.atomosStatus).append('\n')
            append("Identidade: ")
            append(if (AgentState.observedId == "-") "pendente"
                   else "observada (${AgentState.observedId})").append('\n')
            append("Último comando: ").append(AgentState.lastCommand).append('\n')
            append("Erro: ").append(AgentState.lastError)
        }
        btnPrimary.text = if (isConnected()) "Desconectar" else "Conectar"

        val st = AgentState.controllerStatus.lowercase()
        val color = when {
            isConnected() -> android.graphics.Color.rgb(21, 128, 61)          // verde
            st.contains("falha") || st.contains("recusad") || st.contains("erro") ->
                android.graphics.Color.rgb(185, 28, 28)                        // vermelho
            st.contains("conectando") -> android.graphics.Color.rgb(180, 83, 9) // laranja
            else -> android.graphics.Color.rgb(107, 114, 128)                 // cinza
        }
        txtAssoc.setTextColor(color)
        txtStatus.setTextColor(color)
    }
}
