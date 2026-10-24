// gb_hostbatt -- host battery bridge for the Googlebook VM.
//
// VMware forwards only the host's FIRST battery to a guest. The Surface Book 3 has two (clipboard + keyboard base) and
// drains the base first, so the guest would sit at the clipboard's 100% while Windows shows the real combined level.
// This helper reads Windows' composite battery (GetSystemPowerStatus = the taskbar figure) every 10 s and writes it to
// the VM as guestinfo.gb.battery = "<percent> <ac 0|1> <charging|discharging|not-charging|full>" via vmrun; the guest's
// gb_vmtools mirrors it into Android's battery (test_power supply). Writes happen on change, plus every 2 min so a
// freshly booted VM picks the value up. No window; one instance per user session.
//
// Build: csc -nologo -target:winexe -out:gb_hostbatt.exe gb_hostbatt.cs
// Usage: gb_hostbatt.exe [path\to\vm.vmx]   (default C:\GB-x86\lapis-boot.vmx)
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

static class GbHostBatt
{
    [StructLayout(LayoutKind.Sequential)]
    struct SystemPowerStatus
    {
        public byte ACLineStatus, BatteryFlag, BatteryLifePercent, SystemStatusFlag;
        public int BatteryLifeTime, BatteryFullLifeTime;
    }

    [DllImport("kernel32.dll")]
    static extern bool GetSystemPowerStatus(out SystemPowerStatus s);

    static string Vmrun()
    {
        foreach (var d in new[] { @"C:\Program Files\VMware\VMware Workstation", @"C:\Program Files (x86)\VMware\VMware Workstation" })
        {
            var p = Path.Combine(d, "vmrun.exe");
            if (File.Exists(p)) return p;
        }
        return null;
    }

    // "<pct> <ac> <status>", or null when Windows reports no battery
    static string Read()
    {
        SystemPowerStatus s;
        if (!GetSystemPowerStatus(out s) || (s.BatteryFlag & 128) != 0 || s.BatteryLifePercent > 100) return null;
        bool ac = s.ACLineStatus == 1;
        int pct = s.BatteryLifePercent;
        string st = !ac ? "discharging" : pct >= 100 ? "full" : (s.BatteryFlag & 8) != 0 ? "charging" : "not-charging";
        return pct + " " + (ac ? 1 : 0) + " " + st;
    }

    static bool Write(string vmrun, string vmx, string value)
    {
        var psi = new ProcessStartInfo(vmrun, "-T ws writeVariable \"" + vmx + "\" guestVar gb.battery \"" + value + "\"")
        {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
        };
        try
        {
            using (var p = Process.Start(psi))
            {
                p.StandardOutput.ReadToEnd();
                if (!p.WaitForExit(20000)) { try { p.Kill(); } catch { } return false; }
                return p.ExitCode == 0;
            }
        }
        catch { return false; }
    }

    static int Main(string[] args)
    {
        string vmx = args.Length > 0 ? args[0] : @"C:\GB-x86\lapis-boot.vmx";
        string vmrun = Vmrun();
        if (vmrun == null) return 2;
        bool created;
        using (new Mutex(true, @"Local\gb_hostbatt", out created))
        {
            if (!created) return 0;                       // already running
            string last = null;
            DateTime lastOk = DateTime.MinValue, lastTry = DateTime.MinValue;
            bool failed = false;
            for (;;)
            {
                string v = Read();
                var now = DateTime.UtcNow;
                bool due = v != null && (v != last || (now - lastOk).TotalSeconds >= 120);
                // VM off: vmrun fails; retry at most every 30 s
                if (due && (!failed || (now - lastTry).TotalSeconds >= 30))
                {
                    lastTry = now;
                    failed = !Write(vmrun, vmx, v);
                    if (failed) last = null; else { last = v; lastOk = now; }
                }
                Thread.Sleep(10000);
            }
        }
    }
}
