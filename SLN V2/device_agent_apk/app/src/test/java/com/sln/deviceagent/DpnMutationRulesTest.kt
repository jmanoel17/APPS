package com.sln.deviceagent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DpnMutationRulesTest {
    @Test fun validIps() {
        assertTrue(DpnMutationRules.validIp("1.1.1.1"))
        assertTrue(DpnMutationRules.validIp("255.255.255.255"))
        assertTrue(DpnMutationRules.validIp("2001:db8::1"))
        assertFalse(DpnMutationRules.validIp("..."))
        assertFalse(DpnMutationRules.validIp("999.1.1.1"))
        assertFalse(DpnMutationRules.validIp("1.2.3"))
    }

    @Test fun validTunnels() {
        assertTrue(DpnMutationRules.validTunnel("BR"))
        assertTrue(DpnMutationRules.validTunnel("UB-1"))
        assertFalse(DpnMutationRules.validTunnel("../x"))
        assertFalse(DpnMutationRules.validTunnel(""))
    }

    @Test fun errorStatusNeverDowngradesUncertain() {
        assertEquals("uncertain", DpnMutationRules.statusForDpnError("DPN_UNCERTAIN"))
        assertEquals("failure", DpnMutationRules.statusForDpnError("DPN_IDENTITY_MISMATCH"))
        assertEquals("failure", DpnMutationRules.statusForDpnError("DPN_UNAUTHORIZED"))
    }

    @Test fun ipNotChangedIsTyped() {
        assertTrue(DpnMutationRules.isIpNotChanged("{\"reason\":\"ipNotChanged\"}"))
        assertTrue(DpnMutationRules.isIpNotChanged("IPNOTCHANGED"))
        assertFalse(DpnMutationRules.isIpNotChanged("connection lost"))
    }
}
