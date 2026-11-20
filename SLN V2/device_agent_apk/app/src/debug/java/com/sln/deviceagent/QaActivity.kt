package com.sln.deviceagent

import android.app.Activity
import android.content.Intent
import android.content.pm.ActivityInfo
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.text.InputType
import kotlin.math.roundToInt

class QaActivity : Activity() {
    companion object {
        private const val TAG = "SLNQA"
        @Volatile private var taps = 0
        @Volatile private var lastLog = 0L
    }

    private lateinit var status: TextView
    private lateinit var button: Button
    private lateinit var hscroll: HorizontalScrollView
    private lateinit var scroll: ScrollView
    private val ui = Handler(Looper.getMainLooper())
    private val logScroll = Runnable { logState("SCROLL") }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).roundToInt()

    private fun applyOrientation(intent: Intent?) {
        val o = when {
            intent?.getBooleanExtra("landscape", false) == true ->
                ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
            intent?.getBooleanExtra("portrait", false) == true ->
                ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
            else -> ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
        if (o != ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED) {
            requestedOrientation = o
            Log.i(TAG, "QA_ROTATE requested=$o")
        }
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        applyOrientation(intent)
        window.decorView.post { logLayout() }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (intent?.getBooleanExtra("selftest", false) == true) {
            val results = GestureLifecycle.selfTest() + ConnectionGuard.selfTest() +
                CommandExecutor.validationSelfTest(Prefs(this).agentId)
            for ((name, ok, detail) in results) {
                Log.i(TAG, "QA_SELFTEST $name ${if (ok) "PASS" else "FAIL"} $detail")
            }
        }
        applyOrientation(intent)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.WHITE)
            setPadding(dp(8), dp(8), dp(8), dp(8))
        }
        status = TextView(this).apply {
            textSize = 14f
            setTextColor(Color.BLACK)
        }
        root.addView(status, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))

        button = Button(this).apply {
            text = "BOTAO A"
            textSize = 24f
            contentDescription = "qa_button_a"
            setOnClickListener {
                taps++
                logState("TAP")
                updateStatus()
            }
        }
        root.addView(button, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dp(160)))

        val pwd = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setText("secret123")
            contentDescription = "qa_password"
            textSize = 18f
        }
        root.addView(pwd, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dp(72)))

        val hcontent = TextView(this).apply {
            text = "QA HORIZONTAL  " + "0123456789 ".repeat(200)
            textSize = 18f
            gravity = Gravity.CENTER_VERTICAL
            setTextColor(Color.DKGRAY)
        }
        hscroll = HorizontalScrollView(this).apply {
            isFillViewport = true
            addView(hcontent, FrameLayout.LayoutParams(dp(3000), dp(180)))
            setOnScrollChangeListener { _, _, _, _, _ ->
                ui.removeCallbacks(logScroll); ui.postDelayed(logScroll, 120)
            }
        }
        root.addView(hscroll, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dp(200)))

        val vcontent = TextView(this).apply {
            text = (1..120).joinToString("\n") { "QA LINHA $it - area scrollavel de teste" }
            textSize = 18f
            setTextColor(Color.BLACK)
        }
        scroll = ScrollView(this).apply {
            isFillViewport = true
            addView(vcontent, FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, dp(3000)))
            setOnScrollChangeListener { _, _, _, _, _ ->
                ui.removeCallbacks(logScroll); ui.postDelayed(logScroll, 120)
            }
        }
        root.addView(scroll, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))

        setContentView(root)
        updateStatus()
        root.post { logLayout() }
        Log.i(TAG, "QA_START taps=$taps")
    }

    override fun onResume() {
        super.onResume()
        window.decorView.post { logLayout() }
    }

    private fun updateStatus() {
        val d = DeviceStatus.display(this)
        status.text = "taps=$taps  scrollY=${scroll.scrollY}  scrollX=${hscroll.scrollX}  " +
            "display=${d.optInt("width_px")}x${d.optInt("height_px")} rot=${d.optInt("rotation")} " +
            "dpi=${d.optInt("density_dpi")}"
    }

    private fun logState(why: String) {
        val now = System.currentTimeMillis()
        if (why != "TAP" && now - lastLog < 40) return
        lastLog = now
        updateStatus()
        val d = DeviceStatus.display(this)
        Log.i(TAG, "QA_STATE why=$why taps=$taps scrollY=${scroll.scrollY} " +
            "scrollX=${hscroll.scrollX} rotation=${d.optInt("rotation")} " +
            "width=${d.optInt("width_px")} height=${d.optInt("height_px")}")
    }

    private fun logLayout() {
        val b = IntArray(2); button.getLocationOnScreen(b)
        val h = IntArray(2); hscroll.getLocationOnScreen(h)
        val s = IntArray(2); scroll.getLocationOnScreen(s)
        val d = DeviceStatus.display(this)
        Log.i(TAG, "QA_LAYOUT button=[${b[0]},${b[1]},${b[0] + button.width},${b[1] + button.height}] " +
            "hscroll=[${h[0]},${h[1]},${h[0] + hscroll.width},${h[1] + hscroll.height}] " +
            "scroll=[${s[0]},${s[1]},${s[0] + scroll.width},${s[1] + scroll.height}] " +
            "screen=${d.optInt("width_px")}x${d.optInt("height_px")} " +
            "rotation=${d.optInt("rotation")} dpi=${d.optInt("density_dpi")} taps=$taps")
    }
}
