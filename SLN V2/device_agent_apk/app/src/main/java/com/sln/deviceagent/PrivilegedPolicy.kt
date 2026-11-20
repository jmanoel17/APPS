package com.sln.deviceagent

object PrivilegedPolicy {
    private val PKG = Regex("^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+$")
    private val DENY_EXACT = setOf(
        "com.sln.deviceagent",
        "moe.shizuku.privileged.api",
        "moe.shizuku.manager"
    )
    private val DENY_PREFIX = listOf(
        "android", "com.android", "com.google.android", "com.samsung.android"
    )

    fun validPackage(pkg: String): Boolean =
        pkg.isNotEmpty() && pkg.length <= 200 && PKG.matches(pkg)

    fun isDenied(pkg: String): Boolean =
        pkg in DENY_EXACT || DENY_PREFIX.any { pkg == it || pkg.startsWith("$it.") }

    fun isSafeTarget(pkg: String): Boolean = validPackage(pkg) && !isDenied(pkg)
}
