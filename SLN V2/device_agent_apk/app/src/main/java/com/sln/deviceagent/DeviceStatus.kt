package com.sln.deviceagent

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import android.app.KeyguardManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.provider.Settings
import android.util.DisplayMetrics
import android.view.WindowManager
import org.json.JSONObject
import java.net.Inet4Address

object DeviceStatus {
    @Suppress("DEPRECATION")
    fun foregroundFromUsage(ctx: Context, wantPackage: String = ""): Pair<String, String>? {
        return try {
            val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
            val now = System.currentTimeMillis()
            val events = usm.queryEvents(now - 300_000L, now)
            val e = UsageEvents.Event()
            var anyPkg = ""
            var anyCls = ""
            var anyTs = 0L
            var wantCls = ""
            var wantTs = 0L
            while (events.hasNextEvent()) {
                events.getNextEvent(e)
                if (e.eventType != UsageEvents.Event.MOVE_TO_FOREGROUND) continue
                val p = e.packageName ?: ""
                val c = e.className ?: ""
                if (e.timeStamp >= anyTs) {
                    anyTs = e.timeStamp
                    anyPkg = p
                    anyCls = c
                }
                if (wantPackage.isNotEmpty() && p == wantPackage && e.timeStamp >= wantTs) {
                    wantTs = e.timeStamp
                    wantCls = c
                }
            }
            when {
                wantPackage.isNotEmpty() && wantCls.isNotEmpty() -> wantPackage to wantCls
                anyPkg.isNotEmpty() -> anyPkg to anyCls
                else -> null
            }
        } catch (_: Exception) {
            null
        }
    }

    @Suppress("DEPRECATION")
    fun display(ctx: Context): JSONObject {
        val o = JSONObject()
        var w = 0
        var h = 0
        var rot = 0
        var dpi = 0
        try {
            val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val d = wm.defaultDisplay
            val m = DisplayMetrics()
            d.getRealMetrics(m)
            w = m.widthPixels
            h = m.heightPixels
            rot = d.rotation
            dpi = m.densityDpi
        } catch (_: Exception) {}
        o.put("width_px", w)
        o.put("height_px", h)
        o.put("rotation", rot)
        o.put("density_dpi", dpi)
        return o
    }

    fun displayValid(ctx: Context): Boolean {
        val d = display(ctx)
        return d.optInt("width_px") > 0 && d.optInt("height_px") > 0 && d.optInt("density_dpi") > 0
    }

    fun displaySignature(ctx: Context): String {
        val d = display(ctx)
        return "${d.optInt("width_px")}:${d.optInt("height_px")}:" +
            "${d.optInt("rotation")}:${d.optInt("density_dpi")}"
    }

    private fun putDisplay(o: JSONObject, ctx: Context) {
        val d = display(ctx)
        o.put("display", d)
        o.put("width_px", d.optInt("width_px"))
        o.put("height_px", d.optInt("height_px"))
        o.put("rotation", d.optInt("rotation"))
        o.put("density_dpi", d.optInt("density_dpi"))
    }

    fun deviceInfo(ctx: Context): JSONObject {
        val o = JSONObject()
        o.put("android_id", Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ANDROID_ID) ?: "")
        o.put("manufacturer", Build.MANUFACTURER)
        o.put("model", Build.MODEL)
        o.put("sdk", Build.VERSION.SDK_INT)
        o.put("build_fingerprint", Build.FINGERPRINT)
        o.put("agent_version", Proto.AGENT_VERSION)
        putDisplay(o, ctx)
        return o
    }

    private fun ipToString(ip: Int): String =
        if (ip == 0) "" else String.format("%d.%d.%d.%d",
            ip and 0xff, ip shr 8 and 0xff, ip shr 16 and 0xff, ip shr 24 and 0xff)

    @Suppress("DEPRECATION")
    fun networkInfo(ctx: Context): JSONObject {
        val o = JSONObject()
        var ip = ""
        try {
            val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val lp = cm.getLinkProperties(cm.activeNetwork)
            lp?.linkAddresses?.forEach { la ->
                val a = la.address
                if (a is Inet4Address && !a.isLoopbackAddress && ip.isEmpty()) {
                    ip = a.hostAddress ?: ""
                }
            }
        } catch (e: Exception) {}
        var mac = ""
        try {
            val wm = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            val wi = wm.connectionInfo
            if (ip.isEmpty()) ip = ipToString(wi?.ipAddress ?: 0)
            mac = wi?.macAddress ?: ""
        } catch (e: Exception) {}
        o.put("lan_ip", ip)
        o.put("mac", mac)
        return o
    }

    private fun battery(ctx: Context): Pair<Int, Boolean> {
        var pct = 0; var charging = false
        try {
            val i = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val level = i?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale = i?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
            if (level >= 0 && scale > 0) pct = level * 100 / scale
            val st = i?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
            charging = st == BatteryManager.BATTERY_STATUS_CHARGING || st == BatteryManager.BATTERY_STATUS_FULL
        } catch (e: Exception) {}
        return Pair(pct, charging)
    }

    fun status(ctx: Context, lastCommandStatus: String): JSONObject {
        val o = JSONObject()
        o.put("uptime_ms", SystemClock.elapsedRealtime())
        val (pct, charging) = battery(ctx)
        o.put("battery_pct", pct)
        o.put("charging", charging)
        var screenOn = false
        try {
            val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
            screenOn = pm.isInteractive
        } catch (e: Exception) {}
        o.put("screen_on", screenOn)
        o.put("foreground_package", DeviceAccessibilityService.currentPackage)
        o.put("lan_ip", networkInfo(ctx).optString("lan_ip"))
        o.put("agent_version", Proto.AGENT_VERSION)
        o.put("accessibility_enabled", DeviceAccessibilityService.instance != null)
        o.put("last_command_status", lastCommandStatus)
        o.put("device_time_ms", System.currentTimeMillis())
        o.put("elapsed_realtime_ms", SystemClock.elapsedRealtime())
        var keyguard = false
        try {
            val km = ctx.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
            keyguard = km.isKeyguardLocked
        } catch (_: Exception) {}
        o.put("keyguard_locked", keyguard)
        val fg = DeviceAccessibilityService.instance?.foregroundSnapshot()
            ?: DeviceAccessibilityService.foregroundInfo()
        o.put("foreground_activity", fg.activity)
        o.put("foreground_component", fg.component)
        o.put("foreground_window_id", fg.windowId)
        o.put("foreground_observed_at_ms", fg.observedAtMs)
        putDisplay(o, ctx)
        return o
    }
}
