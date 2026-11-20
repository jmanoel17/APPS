package com.sln.deviceagent

class ConnectionGuard {
    enum class State { DISCONNECTED, CONNECTING, AUTHENTICATING, CONNECTED, STOPPED }

    private val lock = Any()
    private var state = State.DISCONNECTED
    private var generation = 0
    private var reconnectScheduled = false

    fun state(): State = synchronized(lock) { state }

    fun isConnected(): Boolean = synchronized(lock) { state == State.CONNECTED }

    fun isStopped(): Boolean = synchronized(lock) { state == State.STOPPED }

    fun start() {
        synchronized(lock) {
            if (state == State.STOPPED) state = State.DISCONNECTED
            reconnectScheduled = false
        }
    }

    fun stop() {
        synchronized(lock) {
            state = State.STOPPED
            generation++
            reconnectScheduled = false
        }
    }

    fun beginConnect(): Int? = synchronized(lock) {
        if (state != State.DISCONNECTED) {
            null
        } else {
            state = State.CONNECTING
            generation++
            reconnectScheduled = false
            generation
        }
    }

    fun onOpen(gen: Int): Boolean = synchronized(lock) {
        if (gen == generation && state == State.CONNECTING) {
            state = State.AUTHENTICATING
            true
        } else {
            false
        }
    }

    fun onAuthenticated(gen: Int): Boolean = synchronized(lock) {
        if (gen == generation && state == State.AUTHENTICATING) {
            state = State.CONNECTED
            reconnectScheduled = false
            true
        } else {
            false
        }
    }

    fun onDisconnected(gen: Int): Boolean = synchronized(lock) {
        if (gen == generation && state != State.STOPPED) {
            state = State.DISCONNECTED
            reconnectScheduled = false
            true
        } else {
            false
        }
    }

    fun isCurrent(gen: Int): Boolean = synchronized(lock) { gen == generation }

    fun generation(): Int = synchronized(lock) { generation }

    fun markReconnectScheduled(gen: Int): Boolean = synchronized(lock) {
        if (gen == generation && state == State.DISCONNECTED && !reconnectScheduled) {
            reconnectScheduled = true
            true
        } else {
            false
        }
    }

    fun clearReconnectScheduled(gen: Int): Boolean = synchronized(lock) {
        if (gen == generation) {
            reconnectScheduled = false
            true
        } else {
            false
        }
    }

    companion object {
        fun selfTest(): List<Triple<String, Boolean, String>> {
            val out = mutableListOf<Triple<String, Boolean, String>>()
            val g = ConnectionGuard()
            val gen = g.beginConnect()
            val second = g.beginConnect()
            val third = g.beginConnect()
            out.add(Triple("single_flight_begin",
                gen != null && second == null && third == null && g.state() == State.CONNECTING,
                "gen=$gen second=$second third=$third state=${g.state()}"))

            val g2 = ConnectionGuard()
            val old = g2.beginConnect()!!
            g2.onDisconnected(old)
            val fresh = g2.beginConnect()!!
            g2.onOpen(fresh)
            val staleIgnored = !g2.onDisconnected(old)
            val freshAuth = g2.onAuthenticated(fresh)
            out.add(Triple("stale_generation_ignored",
                staleIgnored && freshAuth && g2.isConnected(),
                "staleIgnored=$staleIgnored freshAuth=$freshAuth state=${g2.state()}"))

            val g3 = ConnectionGuard()
            val x = g3.beginConnect()!!
            g3.onDisconnected(x)
            val first = g3.markReconnectScheduled(x)
            val second2 = g3.markReconnectScheduled(x)
            val cleared = g3.clearReconnectScheduled(x)
            out.add(Triple("reconnect_single_scheduled",
                first && !second2 && cleared,
                "first=$first second=$second2 cleared=$cleared"))
            return out
        }
    }
}
