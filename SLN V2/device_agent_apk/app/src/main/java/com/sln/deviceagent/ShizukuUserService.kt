package com.sln.deviceagent

import android.os.Bundle
import android.os.Process
import android.util.Log
import java.io.File
import java.util.concurrent.TimeUnit

class ShizukuUserService : IPrivilegedService.Stub() {
    companion object { private const val TAG = "SLNPrivilegedUser" }

    override fun destroy() {
        System.exit(0)
    }

    override fun getUid(): Int = Process.myUid()

    override fun getProcessName(): String {
        return try {
            File("/proc/self/cmdline").readText().trimEnd('\u0000')
        } catch (_: Exception) {
            "pid=${Process.myPid()}"
        }
    }

    override fun stopApp(packageName: String?): Bundle {
        val pkg = packageName ?: ""
        val t0 = System.currentTimeMillis()
        if (!PrivilegedPolicy.isSafeTarget(pkg)) return result(false, "PACKAGE_NOT_ALLOWED", t0, false)
        if (!installed(pkg)) return result(false, "PACKAGE_NOT_FOUND", t0, false)
        Log.i(TAG, "STOP pkg=$pkg")
        val r = exec("/system/bin/am", 5000, "force-stop", pkg)
        val stopped = !running(pkg)
        Log.i(TAG, "STOP_RESULT pkg=$pkg success=${r.exit == 0 && stopped} stopped=$stopped")
        return result(r.exit == 0 && stopped, if (r.exit == 0 && stopped) "" else "STOP_FAILED", t0, stopped)
    }

    override fun clearAppData(packageName: String?): Bundle {
        val pkg = packageName ?: ""
        val t0 = System.currentTimeMillis()
        if (!PrivilegedPolicy.isSafeTarget(pkg)) return result(false, "PACKAGE_NOT_ALLOWED", t0, false)
        if (!installed(pkg)) return result(false, "PACKAGE_NOT_FOUND", t0, false)
        Log.i(TAG, "CLEAR pkg=$pkg")
        exec("/system/bin/am", 5000, "force-stop", pkg)
        val r = exec("/system/bin/pm", 8000, "clear", pkg)
        val ok = r.exit == 0 && r.output.contains("Success", ignoreCase = true) && !running(pkg)
        Log.i(TAG, "CLEAR_RESULT pkg=$pkg success=$ok stopped=${!running(pkg)}")
        return result(ok, if (ok) "" else "CLEAR_FAILED", t0, !running(pkg))
    }

    private fun result(ok: Boolean, err: String, t0: Long, stopped: Boolean): Bundle =
        Bundle().apply {
            putBoolean("success", ok)
            putString("error", err)
            putInt("effective_uid", Process.myUid())
            putInt("pid", Process.myPid())
            putBoolean("verified_stopped", stopped)
            putLong("duration_ms", System.currentTimeMillis() - t0)
        }

    private data class ExecResult(val exit: Int, val output: String)

    private fun exec(path: String, timeoutMs: Long, vararg args: String): ExecResult {
        return try {
            val p = ProcessBuilder(listOf(path, *args)).redirectErrorStream(true).start()
            if (!p.waitFor(timeoutMs, TimeUnit.MILLISECONDS)) {
                p.destroyForcibly()
                ExecResult(124, "")
            } else {
                val out = p.inputStream.bufferedReader().use { it.readText() }
                ExecResult(p.exitValue(), out.take(400))
            }
        } catch (_: Exception) {
            ExecResult(1, "")
        }
    }

    private fun running(pkg: String): Boolean {
        val r = exec("/system/bin/pidof", 2000, pkg)
        return r.exit == 0 && r.output.trim().isNotEmpty()
    }

    private fun installed(pkg: String): Boolean {
        val r = exec("/system/bin/pm", 4000, "path", pkg)
        return r.exit == 0 && r.output.contains("package:")
    }
}
