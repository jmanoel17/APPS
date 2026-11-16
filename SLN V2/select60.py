import subprocess, concurrent.futures, os, time

PKG = "com.flixdigital.dramaclassic"
AB30 = [l.strip() for l in open(r"C:\AndroidProjects\APPS\SLN V2\benchmarks\ab30_devices_20260929_224929.txt", encoding="utf-8") if l.strip() and not l.startswith("#")]

def adb(dev, args, timeout=15):
    try:
        return subprocess.run(["adb","-s",dev]+args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""

def online():
    out = subprocess.run(["adb","devices"], capture_output=True, text=True, timeout=10).stdout
    return [ln.split("\t")[0] for ln in out.strip().split("\n")[1:]
            if ln.strip() and len(ln.split())==2 and ln.split()[1]=="device" and ln.split()[0].endswith(":5555")]

def check(dev):
    logd = adb(dev,["shell","getprop","init.svc.logd"],8).strip()
    wake = "mWakefulness=Awake" in adb(dev,["shell","dumpsys","power"],10)
    pkg = PKG in adb(dev,["shell","pm","list","packages"],12)
    conn = adb(dev,["shell","dumpsys","connectivity"],12)
    net = ("VALIDATED" in conn) or ("INTERNET" in conn and "Active default network" in conn)
    ready = (logd=="running") and wake and pkg and net
    return dev, ready, logd, wake, pkg, net

def main():
    devs = online()
    print("online:", len(devs), flush=True)
    res = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        for dev, ready, logd, wake, pkg, net in ex.map(check, devs):
            res[dev] = (ready, logd, wake, pkg, net)
    ready_devs = [d for d in devs if res[d][0]]
    print("READY:", len(ready_devs), flush=True)
    # preserva os 30 do C30 se READY
    chosen = [d for d in AB30 if d in res and res[d][0]]
    sub = [d for d in AB30 if d not in chosen]
    if sub:
        print("ab30 nao-READY:", sub, flush=True)
    for d in sorted(ready_devs):
        if d not in chosen and len(chosen) < 60:
            chosen.append(d)
    chosen = chosen[:60]
    ts = time.strftime("%Y%m%d_%H%M%S")
    f = r"C:\AndroidProjects\APPS\SLN V2\benchmarks\60_devices_%s.txt" % ts
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("# 60-device set; READY(real)=%d; base=ab30(%d de 30)\n" % (len(ready_devs), len([d for d in AB30 if d in res and res[d][0]])))
        if sub:
            fh.write("# substituicoes (ab30 nao-READY): %s\n" % ",".join(sub))
        for d in chosen:
            fh.write(d + "\n")
    print("escolhidos:", len(chosen), "->", f, flush=True)
    # resumo dos nao-ready (amostra)
    nr = [(d, res[d]) for d in devs if not res[d][0]]
    print("nao-READY (motivos, amostra 20):", flush=True)
    for d, r in nr[:20]:
        print("  %-22s logd=%s awake=%s pkg=%s net=%s" % (d, r[1], r[2], r[3], r[4]), flush=True)

if __name__ == "__main__":
    main()