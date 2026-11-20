package com.sln.deviceagent

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class GestureLifecycle(
    private val dispatch: () -> Boolean,
    private val now: () -> Long
) {
    enum class State {
        NOT_DISPATCHED, DISPATCHING, DISPATCHED, COMPLETED, CANCELLED, UNKNOWN_AFTER_DISPATCH
    }

    data class Outcome(
        val status: String,
        val error: String,
        val gestureMs: Long,
        val sideEffectPossible: Boolean
    )

    private val lock = Any()
    private val latch = CountDownLatch(1)
    private val stateRef = AtomicReference(State.NOT_DISPATCHED)
    private val started = now()
    private var outcome: Outcome? = null

    fun state(): State = stateRef.get()

    fun result(): Outcome? = synchronized(lock) { outcome }

    fun tryDispatch(dispatchDeadline: Long): Boolean {
        synchronized(lock) {
            if (outcome != null) return false
            val t = now()
            if (t > dispatchDeadline) {
                outcome = Outcome("expired", "EXPIRED", 0L, false)
                stateRef.set(State.NOT_DISPATCHED)
                latch.countDown()
                return false
            }
            stateRef.set(State.DISPATCHING)
            val ok = try {
                dispatch()
            } catch (_: Exception) {
                false
            }
            if (ok) {
                if (outcome == null) stateRef.set(State.DISPATCHED)
                return true
            }
            if (outcome == null) {
                outcome = Outcome("failure", "GESTURE_CANCELLED", now() - started, false)
                stateRef.set(State.CANCELLED)
                latch.countDown()
            }
            return false
        }
    }

    fun onCompleted() = onCallback(true)

    fun onCancelled() = onCallback(false)

    private fun onCallback(completed: Boolean) {
        synchronized(lock) {
            val ms = now() - started
            if (outcome == null) {
                outcome = Outcome(if (completed) "success" else "failure",
                    if (completed) "" else "GESTURE_CANCELLED", ms,
                    stateRef.get() != State.NOT_DISPATCHED)
                stateRef.set(if (completed) State.COMPLETED else State.CANCELLED)
                latch.countDown()
            } else {
                val st = stateRef.get()
                if (st == State.DISPATCHING || st == State.DISPATCHED ||
                    st == State.UNKNOWN_AFTER_DISPATCH) {
                    stateRef.set(if (completed) State.COMPLETED else State.CANCELLED)
                }
            }
        }
    }

    fun timeout(): Outcome {
        synchronized(lock) {
            outcome?.let { return it }
            val ms = now() - started
            if (stateRef.get() == State.NOT_DISPATCHED) {
                outcome = Outcome("expired", "EXPIRED", ms, false)
                stateRef.set(State.NOT_DISPATCHED)
            } else {
                stateRef.set(State.UNKNOWN_AFTER_DISPATCH)
                outcome = Outcome("uncertain", "GESTURE_UNCERTAIN", ms, true)
            }
            latch.countDown()
            return outcome!!
        }
    }

    fun await(timeoutMs: Long): Outcome? {
        try {
            latch.await(timeoutMs.coerceAtLeast(50), TimeUnit.MILLISECONDS)
        } catch (_: InterruptedException) {
            return timeout()
        }
        return synchronized(lock) { outcome }
    }

    companion object {
        fun selfTest(): List<Triple<String, Boolean, String>> {
            val out = mutableListOf<Triple<String, Boolean, String>>()
            var dispatched = false
            val a = GestureLifecycle({ dispatched = true; true }, { 0L })
            a.timeout()
            a.tryDispatch(1000L)
            out.add(Triple("timeout_pre_dispatch_no_dispatch",
                !dispatched && a.result()?.status == "expired" && a.state() == State.NOT_DISPATCHED,
                "dispatched=$dispatched state=${a.state()} result=${a.result()}"))

            var bDispatched = false
            val b = GestureLifecycle({ bDispatched = true; true }, { 0L })
            b.tryDispatch(1000L)
            val bOut = b.timeout()
            out.add(Triple("timeout_post_dispatch_uncertain",
                bDispatched && bOut.status == "uncertain" && bOut.sideEffectPossible &&
                    b.state() == State.UNKNOWN_AFTER_DISPATCH,
                "dispatched=$bDispatched state=${b.state()} result=$bOut"))

            b.onCompleted()
            out.add(Triple("late_callback_keeps_terminal",
                b.result()?.status == "uncertain" && b.state() == State.COMPLETED,
                "state=${b.state()} result=${b.result()}"))

            val d1 = GestureLifecycle({ true }, { 0L })
            val d2 = GestureLifecycle({ true }, { 0L })
            d1.tryDispatch(1000L)
            d2.tryDispatch(1000L)
            d2.onCompleted()
            d1.onCancelled()
            out.add(Triple("callback_isolation",
                d1.result()?.status == "failure" && d2.result()?.status == "success",
                "d1=${d1.result()} d2=${d2.result()}"))
            return out
        }
    }
}
