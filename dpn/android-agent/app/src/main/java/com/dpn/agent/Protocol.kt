package com.dpn.agent

/** Protocolo de comandos (espelha dpn/agent/protocol.py do controlador). */
object Protocol {
    const val VERSION = "dpn-agent/1"
}

object Ops {
    const val CONFIGURE = "configure"
    const val GET_DEVICE_ID = "get_device_id"
    const val GET_DEVICE_INFO = "get_device_info"
    const val LOGIN = "login"
    const val GET_DPN_MODE = "get_dpn_mode"
    const val LIST_DEVICES = "list_devices"
    const val ACCESS_GET_SWITCH = "access_get_switch"
    const val LIST_TUNNELS = "list_tunnels"
    const val NODE_LB_MODE_GET = "node_lb_mode_get"

    const val SET_DPN_MODE = "set_dpn_mode"
    const val ACCESS_SET_SWITCH = "access_set_switch"
    const val ACCESS_SET_ONE = "access_set_one"
    const val ACCESS_DELETE = "access_delete"
    const val SWITCH_NODE = "switch_node"
    const val REFRESH_TUNNEL = "refresh_tunnel"
    const val NODE_LB_MODE_SET = "node_lb_mode_set"
    const val ADD_TUNNEL = "add_tunnel"
    const val REBOOT = "reboot"

    val MUTATING = setOf(
        SET_DPN_MODE, ACCESS_SET_SWITCH, ACCESS_SET_ONE, ACCESS_DELETE,
        SWITCH_NODE, REFRESH_TUNNEL, NODE_LB_MODE_SET, ADD_TUNNEL, REBOOT,
    )
}

object ResultStatus {
    const val SUCCESS = "success"
    const val FAILURE = "failure"
    const val EXPIRED = "expired"
    const val UNCERTAIN = "uncertain"
}
