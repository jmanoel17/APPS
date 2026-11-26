package com.dpn.agent

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

class AtomOsError(val reason: String, message: String) : Exception(message)

/**
 * Cliente HTTP do AtomOS DESTA unidade. A sessao (cookies) e a autenticacao
 * ficam no agente. O certificado proprio e fixado por impressao digital apos a
 * primeira conferencia (nao se usa trust-all global depois de vinculado).
 */
class AtomOsClient(private val baseUrl: String, private val pinnedFp: String?) {

    @Volatile var observedFingerprint: String? = null
        private set

    private val cookieStore = ConcurrentHashMap<String, Cookie>()
    private val cookieJar = object : CookieJar {
        override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
            cookies.forEach { cookieStore[it.name] = it }
        }
        override fun loadForRequest(url: HttpUrl): List<Cookie> = cookieStore.values.toList()
    }

    private val trustManager = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            val leaf = chain?.firstOrNull() ?: throw AtomOsError("tls", "sem certificado")
            val fp = sha256(leaf.encoded)
            observedFingerprint = fp
            if (!pinnedFp.isNullOrBlank() && !fp.equals(pinnedFp, ignoreCase = true)) {
                throw AtomOsError("tls_pin", "certificado do AtomOS não confere")
            }
        }
        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private val client: OkHttpClient = run {
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf<TrustManager>(trustManager), SecureRandom())
        OkHttpClient.Builder()
            .cookieJar(cookieJar)
            .sslSocketFactory(ctx.socketFactory, trustManager)
            .hostnameVerifier { _, _ -> true } // identidade garantida por pinning
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()
    }

    @Volatile private var loggedIn = false

    companion object {
        // Chave publica RSA do AtomOS (mesma do frontend da unidade).
        private const val PUB_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAxZ9YwQ1CVM4zNHVNPxoD
Sz6uFGEcyHUOFkoA2hnijjJccCNRGnYlQmnCmaKPtCPiJ26ibXcL9BpfputJpE7Q
cJcJx8CN0Pr/MceYQraFS3UG+zNdI6tGzLDGrBoB+5WFSbK6aOdHFJfcoBfdULHb
g2eGp2IJwSPal3lFNwE/oTL3K1z7EiwbDq0LrY7FcwMGmG3EFaGtMxRy/cq3r0xR
M1V7WIu1I6gw463luLs6NFCdrY/fiXoSrXRf6sOTZClXeRhKjA6c0wLIxizgw6ll
4EeffYVBQSKlEjJJR2y7cxxbp1XkC19evxe0DYbnsemogDcSkmDCj75hsgwuzoTM
FwIDAQAB
-----END PUBLIC KEY-----"""
        private val JSON = "application/json; charset=utf-8".toMediaType()

        fun sha256(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it) }
    }

    private fun encryptPassword(pw: String): String {
        val der = Base64.decode(
            PUB_KEY.replace(Regex("-----[^-]+-----"), "").replace("\\s".toRegex(), ""),
            Base64.DEFAULT,
        )
        val pub = KeyFactory.getInstance("RSA").generatePublic(X509EncodedKeySpec(der))
        val cipher = Cipher.getInstance("RSA/ECB/OAEPWithSHA-1AndMGF1Padding")
        cipher.init(Cipher.ENCRYPT_MODE, pub)
        return Base64.encodeToString(cipher.doFinal(pw.toByteArray(Charsets.UTF_8)),
            Base64.NO_WRAP)
    }

    // ------------------------------------------------------------------ HTTP
    private fun raw(method: String, path: String, body: JSONObject?): String {
        val builder = Request.Builder().url(baseUrl.trimEnd('/') + path)
        val reqBody = body?.toString()?.toRequestBody(JSON) ?: null
        when (method) {
            "GET" -> builder.get()
            "POST" -> builder.post(reqBody ?: "".toRequestBody(JSON))
            else -> throw AtomOsError("method", "método não suportado")
        }
        client.newCall(builder.build()).execute().use { resp ->
            val text = resp.body?.string() ?: ""
            if (resp.code == 401 || resp.code == 403) {
                throw AtomOsError("unauthorized", "não autorizado (${resp.code})")
            }
            if (resp.code >= 400) {
                throw AtomOsError("http", "HTTP ${resp.code}: ${text.take(200)}")
            }
            return text
        }
    }

    private fun getJson(path: String): JSONObject = JSONObject(raw("GET", path, null))

    private fun getArray(path: String): JSONArray {
        val t = raw("GET", path, null).trim()
        return if (t.startsWith("[")) JSONArray(t) else JSONObject(t).optJSONArray("list") ?: JSONArray()
    }

    private fun postJson(path: String, body: JSONObject, checkSuccess: Boolean = true): JSONObject {
        val obj = JSONObject(raw("POST", path, body))
        if (checkSuccess && obj.has("success") && !obj.optBoolean("success", true)) {
            throw AtomOsError(obj.optString("reason", "success=false"), "falha: ${obj.optString("reason")}")
        }
        return obj
    }

    // ------------------------------------------------------------------ sessao
    @Synchronized
    fun login(username: String, password: String) {
        val body = JSONObject().put("username", username)
            .put("password", encryptPassword(password))
        val resp = postJson("/api/admin/login", body, checkSuccess = false)
        if (resp.has("success") && !resp.optBoolean("success", true)) {
            throw AtomOsError(resp.optString("reason", "wrongField"), "login recusado")
        }
        loggedIn = true
    }

    private fun ensureLogin(password: String?) {
        if (loggedIn || password.isNullOrEmpty()) return
        login("admin", password)
    }

    // ------------------------------------------------------------------ ops
    fun getDeviceId(password: String?): JSONObject {
        ensureLogin(password); return getJson("/api/admin/getDeviceId")
    }

    fun getDeviceInfo(password: String?): JSONObject {
        ensureLogin(password)
        val out = JSONObject()
        for (p in listOf("/api/admin/getDeviceId", "/api/system-info/hardware-info",
            "/api/system-info/software-info", "/api/system-info/network-address")) {
            try { val o = getJson(p); o.keys().forEach { k -> out.put(k, o.get(k)) } } catch (_: Exception) {}
        }
        return out
    }

    fun listDevices(password: String?): JSONObject {
        ensureLogin(password); return getJson("/api/accessControl/list")
    }

    fun getDpnMode(password: String?): JSONObject {
        ensureLogin(password); return getJson("/api/smartRoute/getDpnMode")
    }

    fun setDpnMode(body: JSONObject, expectedId: String?, password: String?): JSONObject {
        ensureLogin(password); validateIdentity(expectedId, password)
        return postJson("/api/smartRoute/setDpnMode", body)
    }

    fun listTunnels(password: String?): JSONArray {
        ensureLogin(password); return getArray("/api/smartRoute/listTunnels")
    }

    fun switchNode(body: JSONObject, expectedId: String?, password: String?): JSONObject {
        ensureLogin(password); validateIdentity(expectedId, password)
        return postJson("/api/smartRoute/switchNode", body)
    }

    fun refreshTunnel(body: JSONObject, expectedId: String?, password: String?): JSONObject {
        ensureLogin(password); validateIdentity(expectedId, password)
        return postJson("/api/smartRoute/refreshTunnel", body)
    }

    fun nodeLbModeGet(password: String?): JSONObject {
        ensureLogin(password); return getJson("/api/smartRoute/nodeLbMode")
    }

    fun nodeLbModeSet(body: JSONObject, expectedId: String?, password: String?): JSONObject {
        ensureLogin(password); validateIdentity(expectedId, password)
        return postJson("/api/smartRoute/nodeLbMode", body)
    }

    fun addTunnel(body: JSONObject, expectedId: String?, password: String?): JSONObject {
        ensureLogin(password); validateIdentity(expectedId, password)
        return postJson("/api/smartRoute/addTunnel", body)
    }

    fun accessSwitchGet(password: String?): JSONObject {
        ensureLogin(password); return getJson("/api/accessControl/switch")
    }

    fun accessSwitchSet(body: JSONObject, expectedId: String?, password: String?): JSONObject {
        ensureLogin(password); validateIdentity(expectedId, password)
        return postJson("/api/accessControl/switch", body)
    }

    fun accessSetOne(body: JSONObject, expectedId: String?, password: String?): JSONObject {
        ensureLogin(password); validateIdentity(expectedId, password)
        return postJson("/api/accessControl/setOne", body)
    }

    fun accessDelete(body: JSONObject, expectedId: String?, password: String?): JSONObject {
        ensureLogin(password); validateIdentity(expectedId, password)
        return postJson("/api/accessControl/delete", body)
    }

    fun reboot(expectedId: String?, password: String?): JSONObject {
        ensureLogin(password); validateIdentity(expectedId, password)
        return postJson("/api/admin/reboot", JSONObject())
    }

    /** Confere a identidade fisica no MESMO caminho de rede da operacao. */
    private fun validateIdentity(expectedId: String?, password: String?) {
        if (expectedId.isNullOrBlank()) return
        val observed = getJson("/api/admin/getDeviceId").optString("deviceId", "")
        if (observed.isEmpty() || observed != expectedId) {
            throw AtomOsError("identity_mismatch", "ID físico divergente")
        }
    }
}
