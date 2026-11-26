package com.dpn.agent

import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.min
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/** Estado exibido na UI (singleton simples). */
object AgentState {
    @Volatile var controllerStatus = "desconectado"
    @Volatile var atomosStatus = "-"
    @Volatile var observedId = "-"
    @Volatile var lastCommand = "-"
    @Volatile var lastError = "-"
}

/** Executa comandos tipados no AtomOS (mesmos endpoints comprovados). */
object Operations {
    // Cliente persistente: mantem cookies/sessao entre comandos (nao reloga a cada
    // consulta). Recriado se a configuracao mudar ou ao perder autenticacao.
    @Volatile private var cached: AtomOsClient? = null
    @Volatile private var cachedKey: String = ""

    @Synchronized
    fun reset() {
        cached = null
        cachedKey = ""
    }

    @Synchronized
    private fun client(prefs: Prefs): AtomOsClient {
        val key = prefs.atomosUrl + "|" + prefs.atomosFp
        val c = cached
        if (c != null && key == cachedKey) return c
        val nc = AtomOsClient(prefs.atomosUrl, prefs.atomosFp.ifBlank { null })
        cached = nc
        cachedKey = key
        return nc
    }

    fun execute(prefs: Prefs, op: String, p: JSONObject, expectedId: String?): Any? {
        val pw = prefs.atomosPassword
        val c = client(prefs)
        val out: Any? = try {
            when (op) {
                Ops.GET_DEVICE_ID -> c.getDeviceId(pw)
                Ops.GET_DEVICE_INFO -> c.getDeviceInfo(pw)
                Ops.LOGIN -> { c.login("admin", pw ?: throw AtomOsError("no_password", "sem senha")); JSONObject().put("success", true) }
                Ops.GET_DPN_MODE -> c.getDpnMode(pw)
                Ops.LIST_DEVICES -> c.listDevices(pw)
                Ops.LIST_TUNNELS -> c.listTunnels(pw)
                Ops.ACCESS_GET_SWITCH -> c.accessSwitchGet(pw)
                Ops.NODE_LB_MODE_GET -> c.nodeLbModeGet(pw)
                Ops.SET_DPN_MODE -> c.setDpnMode(p, expectedId, pw)
                Ops.ACCESS_SET_SWITCH -> c.accessSwitchSet(p, expectedId, pw)
                Ops.ACCESS_SET_ONE -> c.accessSetOne(p.optJSONObject("record") ?: JSONObject(), expectedId, pw)
                Ops.ACCESS_DELETE -> c.accessDelete(p, expectedId, pw)
                Ops.SWITCH_NODE -> c.switchNode(p, expectedId, pw)
                Ops.REFRESH_TUNNEL -> c.refreshTunnel(p, expectedId, pw)
                Ops.NODE_LB_MODE_SET -> c.nodeLbModeSet(p, expectedId, pw)
                Ops.ADD_TUNNEL -> c.addTunnel(p, expectedId, pw)
                Ops.REBOOT -> c.reboot(expectedId, pw)
                else -> throw AtomOsError("unknown_op", "operação desconhecida: $op")
            }
        } catch (e: AtomOsError) {
            if (e.reason == "unauthorized") reset()  // forca novo login no proximo
            throw e
        }
        if (c.observedFingerprint != null && prefs.atomosFp.isBlank()) {
            AgentState.lastError = "fingerprint AtomOS observado: ${c.observedFingerprint}"
        }
        return out
    }
}

/**
 * Conexao persistente WSS com o controlador, com pinning do certificado do
 * servidor, heartbeat, reconexao com backoff+jitter e dedupe de request_id.
 */
class WsClient(private val prefs: Prefs, private val onState: () -> Unit = {}) {

    companion object { private const val TAG = "DPNAgent" }

    private val main = Handler(Looper.getMainLooper())
    private val mutatingExecutor = Executors.newSingleThreadExecutor()
    private val handled = LinkedHashSet<String>()
    private var ws: WebSocket? = null
    private var running = false
    private var retry = 0
    @Volatile private var connected = false
    private val client: OkHttpClient

    init {
        val tm = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, a: String?) {}
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, a: String?) {
                val leaf = chain?.firstOrNull() ?: throw java.security.cert.CertificateException("sem certificado")
                val fp = MessageDigest.getInstance("SHA-256").digest(leaf.encoded)
                    .joinToString("") { "%02x".format(it) }
                val pinned = prefs.controllerFp
                if (pinned.isBlank() || !fp.equals(pinned, true)) {
                    throw java.security.cert.CertificateException("pinning do controlador falhou")
                }
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        val ssl = SSLContext.getInstance("TLS")
        ssl.init(null, arrayOf<TrustManager>(tm), SecureRandom())
        client = OkHttpClient.Builder()
            .sslSocketFactory(ssl.socketFactory, tm)
            .pingInterval(20, TimeUnit.SECONDS)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()
    }

    fun start() {
        running = true
        connect()
    }

    fun stop() {
        running = false
        ws?.close(1000, "encerrando")
        ws = null
        AgentState.controllerStatus = "desconectado"
        onState()
    }

    /** Forca reconexao (ex.: mudanca de Wi-Fi/Ethernet). */
    fun reconnectNow() {
        if (!running || connected) return   // nao derruba conexao saudavel
        ws?.cancel()
        connect()
    }

    private fun setState(s: String) {
        AgentState.controllerStatus = s
        main.post { onState() }
    }

    private fun connect() {
        if (!running) return
        val raw = prefs.controllerUrl.trim()
        if (raw.isBlank() || prefs.controllerFp.isBlank()) {
            setState("configure endereço e fingerprint do controlador"); return
        }
        // OkHttp exige http/https; wss->https, ws->http
        val url = when {
            raw.startsWith("wss://", true) -> "https://" + raw.substring(6)
            raw.startsWith("ws://", true) -> "http://" + raw.substring(5)
            else -> raw
        }
        setState("conectando…")
        try {
            val req = Request.Builder().url(url).build()
            ws = client.newWebSocket(req, listener)
            Log.i(TAG, "connect -> $url")
        } catch (e: Exception) {
            Log.e(TAG, "connect falhou", e)
            setState("erro de URL: ${e.message}")
            reconnectLater()
        }
    }

    private val listener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            retry = 0
            connected = true
            Log.i(TAG, "onOpen")
            setState("conectado")
            val token = prefs.sessionToken
            val hello = if (!token.isNullOrBlank()) {
                JSONObject().put("type", "hello").put("agent_id", prefs.agentId)
                    .put("token", token)
            } else {
                JSONObject().put("type", "pair_request")
                    .put("agent_id", prefs.agentId)
                    .put("pairing_code", prefs.pairingCode)
                    .put("info", JSONObject().put("local_ip", "").put("name", android.os.Build.MODEL))
            }
            webSocket.send(hello.toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val m = try { JSONObject(text) } catch (_: Exception) { return }
            Log.i(TAG, "recv: ${m.optString("type")}")
            when (m.optString("type")) {
                "hello_ok" -> { setState("pareado/online"); scheduleHeartbeat() }
                "pair_ok" -> {
                    prefs.sessionToken = m.optString("token")
                    prefs.pairingCode = ""
                    setState("pareado/online"); scheduleHeartbeat()
                }
                "pair_denied" -> { setState("pareamento recusado"); prefs.pairingCode = "" }
                "error" -> { AgentState.lastError = m.optString("error"); onState() }
                "command" -> handleCommand(webSocket, m)
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.e(TAG, "onFailure", t)
            connected = false
            setState("falha: ${t.message}")
            reconnectLater()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            Log.i(TAG, "onClosed $code $reason")
            connected = false
            setState("desconectado")
            reconnectLater()
        }
    }

    private fun reconnectLater() {
        if (!running) return
        retry = min(retry + 1, 6)
        val base = (1000L shl retry).coerceAtMost(60_000L)
        val jitter = (Math.random() * 1000).toLong()
        main.postDelayed({ connect() }, base + jitter)
    }

    private fun scheduleHeartbeat() {
        main.postDelayed(object : Runnable {
            override fun run() {
                if (!running) return
                val hb = JSONObject().put("type", "heartbeat")
                    .put("info", JSONObject().put("atomos_status", AgentState.atomosStatus)
                        .put("observed_dpn_id", AgentState.observedId))
                ws?.send(hb.toString())
                main.postDelayed(this, 15_000)
            }
        }, 15_000)
    }

    private fun handleCommand(webSocket: WebSocket, m: JSONObject) {
        val requestId = m.optString("request_id")
        // dedupe: nao executa o mesmo request_id duas vezes
        synchronized(handled) {
            if (handled.contains(requestId)) {
                return
            }
            handled.add(requestId)
            if (handled.size > 500) {
                val iter = handled.iterator()
                repeat(200) { if (iter.hasNext()) { iter.next(); iter.remove() } }
            }
        }
        val op = m.optString("op")
        val params = m.optJSONObject("params") ?: JSONObject()
        val expectedId = m.optString("expected_dpn_id").ifBlank { null }
        val deadline = m.optDouble("deadline", 0.0)
        AgentState.lastCommand = op
        main.post { onState() }

        // configuracao sensivel enviada pelo controlador apos o pareamento
        if (op == Ops.CONFIGURE) {
            if (deadline > 0 && System.currentTimeMillis() / 1000.0 > deadline) {
                sendResult(webSocket, requestId, op, ResultStatus.EXPIRED, null,
                    "comando expirado", "")
                return
            }
            val url = params.optString("atomos_url", "https://34.34.34.34")
            val pwd = params.optString("atomos_password", "")
            val assoc = params.optString("association_name", "")
            prefs.atomosUrl = url
            if (pwd.isNotEmpty()) prefs.atomosPassword = pwd
            if (assoc.isNotEmpty()) prefs.associationName = assoc
            Operations.reset()   // reinicia a sessao AtomOS com a nova config
            sendResult(webSocket, requestId, op, ResultStatus.SUCCESS,
                JSONObject().put("configured", true), "", "")
            return
        }

        val task = Runnable {
            if (deadline > 0 && System.currentTimeMillis() / 1000.0 > deadline) {
                sendResult(webSocket, requestId, op, ResultStatus.EXPIRED, null, "comando expirado", "")
                return@Runnable
            }
            try {
                val data = Operations.execute(prefs, op, params, expectedId)
                val observed = try {
                    if (op == Ops.GET_DEVICE_ID) (data as? JSONObject)?.optString("deviceId")
                    else null
                } catch (_: Exception) { null }
                if (op == Ops.GET_DEVICE_ID) AgentState.observedId = observed ?: "-"
                AgentState.atomosStatus = "connected"
                AgentState.lastError = "-"
                sendResult(webSocket, requestId, op, ResultStatus.SUCCESS, data, "", "", observed)
            } catch (e: AtomOsError) {
                val status = if (e.reason == "identity_mismatch") ResultStatus.FAILURE
                else if (e.reason == "unauthorized") ResultStatus.FAILURE else ResultStatus.FAILURE
                AgentState.atomosStatus =
                    if (e.reason == "unauthorized") "auth_failed" else "unreachable"
                AgentState.lastError = e.message ?: e.reason
                sendResult(webSocket, requestId, op, status, null, e.message ?: e.reason, e.reason)
            } catch (e: Exception) {
                AgentState.atomosStatus = "unreachable"
                AgentState.lastError = e.message ?: "erro"
                sendResult(webSocket, requestId, op, ResultStatus.FAILURE, null,
                    e.message ?: "erro", "")
            }
            main.post { onState() }
        }
        if (Ops.MUTATING.contains(op)) mutatingExecutor.execute(task) else Thread(task).start()
    }

    private fun sendResult(webSocket: WebSocket, requestId: String, op: String, status: String,
                           data: Any?, error: String, reason: String, observed: String? = null) {
        val r = JSONObject()
            .put("protocol", Protocol.VERSION)
            .put("type", "result")
            .put("request_id", requestId)
            .put("agent_id", prefs.agentId)
            .put("op", op)
            .put("status", status)
        if (data != null) r.put("data", data)
        if (error.isNotEmpty()) r.put("error", error)
        if (reason.isNotEmpty()) r.put("reason", reason)
        if (!observed.isNullOrEmpty()) r.put("observed_dpn_id", observed)
        webSocket.send(r.toString())
    }
}
