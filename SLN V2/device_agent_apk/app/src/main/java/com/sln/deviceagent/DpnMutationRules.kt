package com.sln.deviceagent

object DpnMutationRules {
    fun validTunnel(code: String): Boolean =
        code.matches(Regex("^[A-Za-z0-9_-]{1,16}$"))

    fun validIp(ip: String): Boolean {
        if (ip.contains(':')) return ip.matches(Regex("^[0-9A-Fa-f:]{2,64}$")) &&
            ip.count { it == ':' } >= 2
        val p = ip.split('.')
        if (p.size != 4) return false
        return p.all { part ->
            part.isNotEmpty() && part.toIntOrNull()?.let {
                it in 0..255 && it.toString() == part
            } == true
        }
    }

    fun statusForDpnError(code: String): String =
        if (code == "DPN_UNCERTAIN") "uncertain" else "failure"

    fun isIpNotChanged(body: String): Boolean =
        body.contains("ipNotChanged", ignoreCase = true)
}
