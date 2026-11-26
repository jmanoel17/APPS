package com.dpn.agent

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Configuracao e segredos do agente.
 *
 * - agent_id: persistente (gerado uma vez). Apagar os dados exige novo pareamento.
 * - token de sessao e senha do AtomOS: cifrados com AES/GCM usando chave do
 *   Android Keystore (nao ficam em texto puro).
 * - URLs/fingerprints sao configuracao (nao segredo).
 */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("dpn_agent", Context.MODE_PRIVATE)
    private val alias = "dpn_agent_keystore_key"

    private fun keyStore(): KeyStore =
        KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    private fun key(): SecretKey {
        val ks = keyStore()
        (ks.getEntry(alias, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        kg.init(
            KeyGenParameterSpec.Builder(alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return kg.generateKey()
    }

    private fun putSecret(name: String, value: String?) {
        if (value == null) {
            sp.edit().remove(name).apply(); return
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val enc = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        val blob = Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" +
                Base64.encodeToString(enc, Base64.NO_WRAP)
        sp.edit().putString(name, blob).apply()
    }

    private fun getSecret(name: String): String? {
        val blob = sp.getString(name, null) ?: return null
        return try {
            val (ivB64, ctB64) = blob.split(":", limit = 2)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(),
                GCMParameterSpec(128, Base64.decode(ivB64, Base64.NO_WRAP)))
            String(cipher.doFinal(Base64.decode(ctB64, Base64.NO_WRAP)), Charsets.UTF_8)
        } catch (e: Exception) {
            null
        }
    }

    val agentId: String
        get() {
            var id = sp.getString("agent_id", null)
            if (id == null) {
                id = UUID.randomUUID().toString()
                sp.edit().putString("agent_id", id).apply()
            }
            return id
        }

    var sessionToken: String?
        get() = getSecret("session_token")
        set(v) = putSecret("session_token", v)

    var atomosPassword: String?
        get() = getSecret("atomos_password")
        set(v) = putSecret("atomos_password", v)

    var controllerUrl: String
        get() = sp.getString("controller_url", "") ?: ""
        set(v) = sp.edit().putString("controller_url", v).apply()

    var controllerFp: String
        get() = sp.getString("controller_fp", "") ?: ""
        set(v) = sp.edit().putString("controller_fp", v).apply()

    var pairingCode: String
        get() = sp.getString("pairing_code", "") ?: ""
        set(v) = sp.edit().putString("pairing_code", v).apply()

    var associationName: String
        get() = sp.getString("association_name", "") ?: ""
        set(v) = sp.edit().putString("association_name", v).apply()

    var atomosUrl: String
        get() = sp.getString("atomos_url", "https://34.34.34.34") ?: "https://34.34.34.34"
        set(v) = sp.edit().putString("atomos_url", v).apply()

    var atomosFp: String
        get() = sp.getString("atomos_fp", "") ?: ""
        set(v) = sp.edit().putString("atomos_fp", v).apply()

    fun resetPairing() {
        sessionToken = null
        sp.edit().remove("agent_id").apply()
    }

    /** Configurado = tem controlador + fingerprint + (token de sessao ou codigo). */
    fun isConfigured(): Boolean =
        controllerUrl.isNotBlank() && controllerFp.isNotBlank() &&
            (!sessionToken.isNullOrBlank() || pairingCode.isNotBlank())
}
