package com.sln.deviceagent

object Proto {
    const val VERSION = "sln-device/1"
    const val AGENT_VERSION = "0.5"

    const val HELLO = "hello"
    const val HELLO_OK = "hello_ok"
    const val PAIR_REQUEST = "pair_request"
    const val PAIR_OK = "pair_ok"
    const val PAIR_DENIED = "pair_denied"
    const val COMMAND = "command"
    const val RESULT = "result"
    const val HEARTBEAT = "heartbeat"
    const val ERROR = "error"

    const val PING = "PING"
    const val GET_STATUS = "GET_STATUS"
    const val GET_DEVICE_INFO = "GET_DEVICE_INFO"
    const val GET_NETWORK_INFO = "GET_NETWORK_INFO"
    const val GET_FOREGROUND_APP = "GET_FOREGROUND_APP"
    const val GET_ACCESSIBILITY_STATE = "GET_ACCESSIBILITY_STATE"
    const val START_APP = "START_APP"
    const val BACK = "BACK"
    const val HOME = "HOME"
    const val TAP = "TAP"
    const val SWIPE = "SWIPE"
    const val GET_UI_TREE = "GET_UI_TREE"
    const val CAPTURE_SCREEN = "CAPTURE_SCREEN"
    const val GET_PRIVILEGED_BRIDGE_STATUS = "GET_PRIVILEGED_BRIDGE_STATUS"
    const val REQUEST_SHIZUKU_PERMISSION = "REQUEST_SHIZUKU_PERMISSION"
    const val STOP_APP = "STOP_APP"
    const val CLEAR_APP_DATA = "CLEAR_APP_DATA"

    const val GET_DPN_IDENTITY = "GET_DPN_IDENTITY"
    const val GET_DPN_STATUS = "GET_DPN_STATUS"
    const val LIST_DPN_TUNNELS = "LIST_DPN_TUNNELS"
    const val GET_DPN_MODE = "GET_DPN_MODE"
    const val GET_DPN_NODE_LB_MODE = "GET_DPN_NODE_LB_MODE"
    const val LIST_DPN_ACCESS_CONTROL = "LIST_DPN_ACCESS_CONTROL"
    const val DPN_SWITCH_NODE = "DPN_SWITCH_NODE"
    const val DPN_REFRESH_ROUTE_SET = "DPN_REFRESH_ROUTE_SET"

    val READ_OPS = setOf(PING, GET_STATUS, GET_DEVICE_INFO, GET_NETWORK_INFO,
        GET_FOREGROUND_APP, GET_ACCESSIBILITY_STATE, GET_UI_TREE, CAPTURE_SCREEN,
        GET_PRIVILEGED_BRIDGE_STATUS, REQUEST_SHIZUKU_PERMISSION)
    val DPN_READ_OPS = setOf(GET_DPN_IDENTITY, GET_DPN_STATUS, LIST_DPN_TUNNELS,
        GET_DPN_MODE, GET_DPN_NODE_LB_MODE, LIST_DPN_ACCESS_CONTROL)
    val DPN_MUTATING_OPS = setOf(DPN_SWITCH_NODE, DPN_REFRESH_ROUTE_SET)
    val MUTATING = setOf(START_APP, BACK, HOME, TAP, SWIPE, STOP_APP, CLEAR_APP_DATA) +
        DPN_MUTATING_OPS
    val OPS = READ_OPS + DPN_READ_OPS + MUTATING
}
