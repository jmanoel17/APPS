package com.sln.deviceagent

import android.util.Base64
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.security.spec.X509EncodedKeySpec
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

class DpnError(val code: String, message: String) : Exception(message)

/** Read-only AtomOS client embedded in the unified Device Agent. */
class DpnClient(private val prefs: Prefs) {
    companion object {
        private val JSON = "application/json; charset=utf-8".toMediaType()
        private const val PUB_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAxZ9YwQ1CVM4zNHVNPxoD
Sz6uFGEcyHUOFkoA2hnijjJccCNRGnYlQmnCmaKPtCPiJ26ibXcL9BpfputJpE7Q
cJcJx8CN0Pr/MceYQraFS3UG+zNdI6tGzLDGrBoB+5WFSbK6aOdHFJfcoBfdULHb
g2eGp2IJwSPal3lFNwE/oTL3K1z7EiwbDq0LrY7FcwMGmG3EFaGtMxRy/cq3r0xR
M1V7WIu1I6gw463luLs6NFCdrY/fiXoSrXRf6sOTZClXeRhKjA6c0wLIxizgw6ll
4EeffYVBQSKlEjJJR2y7cxxbp1XkC19evxe0DYbnsemogDcSkmDCj75hsgwuzoTM
FwIDAQAB
-----END PUBLIC KEY-----"""

        private fun sha256(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it) }

        @Volatile private var shared: DpnClient? = null
        @Volatile private var sharedKey: String = ""

        fun session(prefs: Prefs): DpnClient {
            val key = prefs.atomosUrl + "|" + (prefs.atomosPassword ?: "")
            val cur = shared
            if (cur != null && key == sharedKey) return cur
            synchronized(this) {
                val cur2 = shared
                if (cur2 != null && key == sharedKey) return cur2
                val n = DpnClient(prefs)
                shared = n
                sharedKey = key
                return n
            }
        }
    }

    private val cookies = ConcurrentHashMap<String, Cookie>()
    private val cookieJar = object : CookieJar {
        override fun saveFromResponse(url: HttpUrl, list: List<Cookie>) {
            list.forEach { cookies[it.name] = it }
        }
        override fun loadForRequest(url: HttpUrl): List<Cookie> = cookies.values.toList()
    }

    private val trust = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            val leaf = chain?.firstOrNull() ?: throw DpnError("DPN_TLS", "sem certificado")
            val fp = sha256(leaf.encoded)
            val pinned = prefs.atomosFp
            if (pinned.isNotBlank() && !fp.equals(pinned, ignoreCase = true)) {
                throw DpnError("DPN_TLS_PIN", "certificado AtomOS divergente")
            }
        }
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private val client: OkHttpClient = run {
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf<TrustManager>(trust), SecureRandom())
        OkHttpClient.Builder()
            .cookieJar(cookieJar)
            .sslSocketFactory(ctx.socketFactory, trust)
            .hostnameVerifier { _, _ -> true }
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .build()
    }

    @Volatile private var loggedIn = false
    @Volatile private var authRetried = false

    private fun baseUrl(): String = prefs.atomosUrl.trimEnd('/')

    private fun encrypt(password: String): String {
        val der = Base64.decode(PUB_KEY.replace(Regex("-----[^-]+-----"), "")
            .replace("\\s".toRegex(), ""), Base64.DEFAULT)
        val pub = KeyFactory.getInstance("RSA").generatePublic(X509EncodedKeySpec(der))
        val cipher = Cipher.getInstance("RSA/ECB/OAEPWithSHA-1AndMGF1Padding")
        cipher.init(Cipher.ENCRYPT_MODE, pub)
        return Base64.encodeToString(cipher.doFinal(password.toByteArray(Charsets.UTF_8)),
            Base64.NO_WRAP)
    }

    private fun request(method: String, path: String, body: JSONObject? = null): String {
        val b = Request.Builder().url(baseUrl() + path)
        if (method == "POST") b.post((body ?: JSONObject()).toString().toRequestBody(JSON))
        else b.get()
        client.newCall(b.build()).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            if (resp.code == 401 || resp.code == 403) {
                if (!authRetried) {
                    authRetried = true
                    loggedIn = false
                    login()
                    return request(method, path, body)
                }
                throw DpnError("DPN_UNAUTHORIZED", "HTTP ${resp.code}: ${text.take(160)}")
            }
            if (resp.code >= 400) throw DpnError("DPN_HTTP", "HTTP ${resp.code}")
            return text
        }
    }

    private fun getJson(path: String): JSONObject = JSONObject(request("GET", path))
    private fun getArray(path: String): JSONArray {
        val t = request("GET", path).trim()
        return if (t.startsWith("[")) JSONArray(t)
        else JSONObject(t).optJSONArray("list") ?: JSONArray()
    }

    @Synchronized
    private fun login() {
        if (loggedIn) return
        val pw = prefs.atomosPassword
        if (pw.isNullOrBlank()) throw DpnError("DPN_AUTH_REQUIRED", "senha AtomOS ausente")
        val resp = JSONObject(request("POST", "/api/admin/login",
            JSONObject().put("username", "admin").put("password", encrypt(pw))))
        if (resp.has("success") && !resp.optBoolean("success", true)) {
            throw DpnError("DPN_AUTH_FAILED", "login recusado")
        }
        loggedIn = true
        authRetried = false
    }

    private fun get(path: String): JSONObject { login(); return getJson(path) }

    fun identity(): JSONObject {
        val start = System.currentTimeMillis()
        login()
        val id = getJson("/api/admin/getDeviceId")
        val hw = try { getJson("/api/system-info/hardware-info") } catch (_: Exception) { JSONObject() }
        val sw = try { getJson("/api/system-info/software-info") } catch (_: Exception) { JSONObject() }
        val mode = try { getJson("/api/smartRoute/getDpnMode") } catch (_: Exception) { JSONObject() }
        return JSONObject()
            .put("reachable", true)
            .put("physical_dpn_id", id.optString("deviceId", ""))
            .put("device_id", id.optString("deviceId", ""))
            .put("hardware", hw)
            .put("software_version", sw.optString("softwareVersion",
                sw.optString("version", "")))
            .put("software", sw)
            .put("dpn_mode", mode.opt("mode") ?: mode.opt("dpnMode") ?: "")
            .put("latency_ms", System.currentTimeMillis() - start)
            .put("observed_at_ms", System.currentTimeMillis())
    }

    fun status(): JSONObject {
        val id = identity()
        val mode = get("/api/smartRoute/getDpnMode")
        val lb = try { get("/api/smartRoute/nodeLbMode") } catch (_: Exception) { JSONObject() }
        return id.put("dpn_mode", mode).put("node_lb_mode", lb)
    }

    fun tunnels(): JSONArray { login(); return getArray("/api/smartRoute/listTunnels") }
    fun accessControl(): JSONObject { login(); return getJson("/api/accessControl/list") }
    fun mode(): JSONObject { return get("/api/smartRoute/getDpnMode") }
    fun nodeLbMode(): JSONObject { return get("/api/smartRoute/nodeLbMode") }

    private fun validTunnel(code: String): Boolean = DpnMutationRules.validTunnel(code)

    private fun validIp(ip: String): Boolean = DpnMutationRules.validIp(ip)

    private fun mutationResponse(path: String, body: JSONObject): JSONObject {
        login()
        val req = Request.Builder().url(baseUrl() + path)
            .post(body.toString().toRequestBody(JSON)).build()
        val resp = try {
            client.newCall(req).execute()
        } catch (e: Exception) {
            throw DpnError("DPN_UNCERTAIN", e.javaClass.simpleName)
        }
        resp.use {
            val text = it.body?.string() ?: ""
            if (it.code == 401 || it.code == 403) {
                if (text.contains("tooManyAttempts", ignoreCase = true))
                    throw DpnError("DPN_AUTH_BACKOFF", "HTTP ${it.code}: tooManyAttempts")
                throw DpnError("DPN_UNAUTHORIZED", "HTTP ${it.code}")
            }
            if (it.code >= 500) throw DpnError("DPN_UNCERTAIN", "HTTP ${it.code}")
            if (it.code >= 400) {
                if (DpnMutationRules.isIpNotChanged(text))
                    return JSONObject().put("result", "IP_NOT_CHANGED")
                throw DpnError("DPN_FAILURE", "HTTP ${it.code}: ${text.take(120)}")
            }
            val data = try { if (text.isBlank()) JSONObject() else JSONObject(text) }
            catch (_: Exception) { JSONObject() }
            val reason = data.optString("reason", "")
            if (data.optBoolean("success", true) == false) {
                if (DpnMutationRules.isIpNotChanged(reason))
                    return JSONObject().put("result", "IP_NOT_CHANGED")
                throw DpnError("DPN_FAILURE", reason.ifBlank { "success=false" })
            }
            return data
        }
    }

    private fun checkExpected(expected: String) {
        if (expected.isBlank()) throw DpnError("DPN_BAD_PARAMS", "expected_dpn_id ausente")
        val actual = identity().optString("physical_dpn_id", "")
        if (actual.isBlank() || actual != expected)
            throw DpnError("DPN_IDENTITY_MISMATCH", "expected=${expected.take(64)} actual=${actual.take(64)}")
    }

    fun switchNode(expectedDpnId: String, tunnelCode: String, currentIp: String): JSONObject {
        if (!validTunnel(tunnelCode)) throw DpnError("DPN_BAD_PARAMS", "tunnel_code invalido")
        if (!validIp(currentIp)) throw DpnError("DPN_BAD_PARAMS", "current_ip invalido")
        checkExpected(expectedDpnId)
        val out = mutationResponse("/api/smartRoute/switchNode",
            JSONObject().put("tunnelCode", tunnelCode).put("currentIp", currentIp))
        if (out.optString("result") == "IP_NOT_CHANGED") return out
        out.put("result", "REQUESTED").put("tunnel_code", tunnelCode)
            .put("current_ip", currentIp)
        return out
    }

    fun refreshRouteSet(expectedDpnId: String, tunnelCode: String): JSONObject {
        if (!validTunnel(tunnelCode)) throw DpnError("DPN_BAD_PARAMS", "tunnel_code invalido")
        checkExpected(expectedDpnId)
        mutationResponse("/api/smartRoute/refreshTunnel",
            JSONObject().put("tunnelCode", tunnelCode))
        return JSONObject().put("result", "REQUESTED").put("tunnel_code", tunnelCode)
    }

    fun addTunnel(tunnelCode: String): JSONObject {
        if (!validTunnel(tunnelCode)) throw DpnError("DPN_BAD_PARAMS", "tunnel_code invalido")
        val out = mutationResponse("/api/smartRoute/addTunnel",
            JSONObject().put("tunnelCode", tunnelCode))
        return out.put("result", out.optString("result", "REQUESTED")).put("tunnel_code", tunnelCode)
    }

    fun deleteTunnel(tunnelCode: String): JSONObject {
        if (!validTunnel(tunnelCode)) throw DpnError("DPN_BAD_PARAMS", "tunnel_code invalido")
        val out = mutationResponse("/api/smartRoute/deleteTunnel",
            JSONObject().put("tunnelCode", tunnelCode))
        return out.put("result", out.optString("result", "REQUESTED")).put("tunnel_code", tunnelCode)
    }

    fun accessControlMode(mode: String): JSONObject {
        if (mode !in setOf("open", "restrict", "off")) {
            throw DpnError("DPN_BAD_PARAMS", "mode invalido")
        }
        val out = mutationResponse("/api/accessControl/setMode",
            JSONObject().put("mode", mode))
        return out.put("result", out.optString("result", "REQUESTED")).put("mode", mode)
    }

    fun accessControlAddDevice(name: String, mac: String): JSONObject {
        if (name.isBlank() || mac.isBlank()) {
            throw DpnError("DPN_BAD_PARAMS", "name/mac obrigatorio")
        }
        val out = mutationResponse("/api/accessControl/addDevice",
            JSONObject().put("name", name).put("mac", mac))
        return out.put("result", out.optString("result", "REQUESTED")).put("mac", mac)
    }

    fun accessControlRemoveDevice(mac: String): JSONObject {
        if (mac.isBlank()) throw DpnError("DPN_BAD_PARAMS", "mac obrigatorio")
        val out = mutationResponse("/api/accessControl/deleteDevice",
            JSONObject().put("mac", mac))
        return out.put("result", out.optString("result", "REQUESTED")).put("mac", mac)
    }
}
