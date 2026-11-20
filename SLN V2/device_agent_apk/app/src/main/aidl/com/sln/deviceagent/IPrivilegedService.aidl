package com.sln.deviceagent;

import android.os.Bundle;

interface IPrivilegedService {
    void destroy() = 16777114;
    int getUid() = 1;
    String getProcessName() = 2;
    Bundle stopApp(String packageName) = 3;
    Bundle clearAppData(String packageName) = 4;
}
