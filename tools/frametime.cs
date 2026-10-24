// frametime.exe -- on-screen update cadence of one window, measured with the DXGI Desktop Duplication API.
// Every time the composed desktop changes, Desktop Duplication reports the present time (QPC) plus the dirty and move
// rectangles. Only updates that touch the target window's rectangle are counted, so the result is what the user
// actually sees of that window: a native Windows app, or the VMware window showing a guest.
// No pixels are copied (metadata only), so the measurement barely loads the host.
//
// Build: C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /optimize /out:frametime.exe frametime.cs
// Usage: frametime.exe SECONDS "window title substring" [label] [child window class or -] [pixels]
// pixels: count only updates whose PIXELS visibly changed (three thin vertical strips across the window are copied and
// compared; >= 16 bytes changing by more than 24 levels, which ignores the compositor's dithering noise),
// so a window that repaints identical frames (VMware's GDI present loop) is measured by what actually moves.
// (child class: measure only that child, e.g. MKSEmbedded = the VMware guest display, not the toolbar/status bar)
// Stats are computed inside bursts of activity (a gap > 200 ms ends a burst), so idle time between scroll gestures
// does not count as stutter. A stutter is an interval longer than 1.5 refresh periods (a skipped frame at 60 Hz).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

static class FrameTime
{
    [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }
    [StructLayout(LayoutKind.Sequential)] struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] struct MOVE_RECT { public POINT Src; public RECT Dst; }
    [StructLayout(LayoutKind.Sequential)] struct FRAME_INFO
    {
        public long LastPresentTime, LastMouseUpdateTime; public uint AccumulatedFrames;
        public int RectsCoalesced, ProtectedContentMaskedOut; public POINT PointerPos; public int PointerVisible;
        public uint TotalMetadataBufferSize, PointerShapeBufferSize;
    }
    [StructLayout(LayoutKind.Sequential)] struct DUPL_DESC
    {
        public uint Width, Height, RefreshNum, RefreshDen; public int Format, ScanlineOrdering, Scaling, Rotation;
        public int DesktopImageInSystemMemory;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct OUTPUT_DESC
    {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string DeviceName;
        public RECT Desktop; public int Attached; public int Rotation; public IntPtr Monitor;
    }

    // vtable order matters: every inherited method is re-declared (unused ones as stubs).
    [ComImport, Guid("770aae78-f26f-4dba-a829-253c83d1b387"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IDXGIFactory1
    {
        void SetPrivateData(); void SetPrivateDataInterface(); void GetPrivateData(); void GetParent();
        void EnumAdapters(); void MakeWindowAssociation(); void GetWindowAssociation(); void CreateSwapChain();
        void CreateSoftwareAdapter();
        [PreserveSig] int EnumAdapters1(uint i, out IDXGIAdapter1 adapter);
        void IsCurrent();
    }
    [ComImport, Guid("29038f61-3839-4626-91fd-086879011a05"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IDXGIAdapter1
    {
        void SetPrivateData(); void SetPrivateDataInterface(); void GetPrivateData(); void GetParent();
        [PreserveSig] int EnumOutputs(uint i, out IDXGIOutput1 output);
        void GetDesc(); void CheckInterfaceSupport(); void GetDesc1();
    }
    [ComImport, Guid("00cddea8-939b-4b83-a340-a685226666cc"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IDXGIOutput1
    {
        void SetPrivateData(); void SetPrivateDataInterface(); void GetPrivateData(); void GetParent();
        [PreserveSig] int GetDesc(out OUTPUT_DESC desc);
        void GetDisplayModeList(); void FindClosestMatchingMode(); void WaitForVBlank(); void TakeOwnership();
        void ReleaseOwnership(); void GetGammaControlCapabilities(); void SetGammaControl(); void GetGammaControl();
        void SetDisplaySurface(); void GetDisplaySurfaceData(); void GetFrameStatistics();
        void GetDisplayModeList1(); void FindClosestMatchingMode1(); void GetDisplaySurfaceData1();
        [PreserveSig] int DuplicateOutput(IntPtr device, out IDXGIOutputDuplication dup);
    }
    [ComImport, Guid("191cfac3-a341-470d-b26e-a864f428319c"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IDXGIOutputDuplication
    {
        void SetPrivateData(); void SetPrivateDataInterface(); void GetPrivateData(); void GetParent();
        [PreserveSig] int GetDesc(out DUPL_DESC desc);
        [PreserveSig] int AcquireNextFrame(uint timeoutMs, out FRAME_INFO info, out IntPtr resource);
        [PreserveSig] int GetFrameDirtyRects(uint size, IntPtr buf, out uint required);
        [PreserveSig] int GetFrameMoveRects(uint size, IntPtr buf, out uint required);
        void GetFramePointerShape(); void MapDesktopSurface(); void UnMapDesktopSurface();
        [PreserveSig] int ReleaseFrame();
    }

    [DllImport("dxgi.dll")] static extern int CreateDXGIFactory1(ref Guid riid, out IDXGIFactory1 f);
    [DllImport("d3d11.dll")] static extern int D3D11CreateDevice(IntPtr adapter, int driverType, IntPtr sw, uint flags,
        IntPtr levels, uint nLevels, uint sdk, out IntPtr device, out int level, out IntPtr ctx);
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr ctx);
    delegate bool EnumProc(IntPtr h, IntPtr p);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr p);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, EnumProc cb, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);

    const int WAIT_TIMEOUT = unchecked((int)0x887A0027), ACCESS_LOST = unchecked((int)0x887A0026);
    const int MORE_DATA = unchecked((int)0x887A0003);

    [StructLayout(LayoutKind.Sequential)] struct TEX2D_DESC
    {
        public uint Width, Height, MipLevels, ArraySize; public int Format; public uint SampleCount, SampleQuality;
        public int Usage; public uint BindFlags, CPUAccessFlags, MiscFlags;
    }
    [StructLayout(LayoutKind.Sequential)] struct BOX { public uint left, top, front, right, bottom, back; }
    [StructLayout(LayoutKind.Sequential)] struct MAPPED { public IntPtr pData; public uint RowPitch, DepthPitch; }
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int CreateTexture2DFn(IntPtr self, ref TEX2D_DESC d, IntPtr init, out IntPtr tex);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void GetDescFn(IntPtr self, out TEX2D_DESC d);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void CopyRegionFn(IntPtr self, IntPtr dst, uint dstSub, uint x, uint y, uint z, IntPtr src, uint srcSub, ref BOX box);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate int MapFn(IntPtr self, IntPtr res, uint sub, int type, uint flags, out MAPPED m);
    [UnmanagedFunctionPointer(CallingConvention.StdCall)] delegate void UnmapFn(IntPtr self, IntPtr res, uint sub);
    static T VFn<T>(IntPtr obj, int idx) where T : class
    {
        return (T)(object)Marshal.GetDelegateForFunctionPointer(Marshal.ReadIntPtr(Marshal.ReadIntPtr(obj), idx * IntPtr.Size), typeof(T));
    }
    static IntPtr gDev, gCtx, gStage; static int gStageFmt = -1; static uint gStripW = 8, gStripH;
    static byte[] gDump; static ulong gSeq = 1; static int gDebug = Environment.GetEnvironmentVariable("FT_DEBUG") != null ? 12 : 0;
    static Guid IID_Tex2D = new Guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c");

    // Hash three 8-px vertical strips (at 1/4, 1/2, 3/4 of the target width) of the current desktop image.
    static ulong StripHash(IntPtr resource, RECT t, RECT desk)
    {
        IntPtr tex; Guid iid = IID_Tex2D;
        if (Marshal.QueryInterface(resource, ref iid, out tex) != 0) return 0;
        try
        {
            TEX2D_DESC dd; VFn<GetDescFn>(tex, 10)(tex, out dd);           // ID3D11Texture2D::GetDesc
            int top = Math.Max(0, t.T), bottom = Math.Min((int)dd.Height, t.B);
            if (bottom <= top) return 0;
            uint h = (uint)(bottom - top);
            if (gStage == IntPtr.Zero || gStageFmt != dd.Format || gStripH != h)
            {
                if (gStage != IntPtr.Zero) Marshal.Release(gStage);
                var sd = new TEX2D_DESC { Width = 3 * gStripW, Height = h, MipLevels = 1, ArraySize = 1, Format = dd.Format,
                    SampleCount = 1, Usage = 3 /* STAGING */, CPUAccessFlags = 0x20000 /* READ */ };
                Marshal.ThrowExceptionForHR(VFn<CreateTexture2DFn>(gDev, 5)(gDev, ref sd, IntPtr.Zero, out gStage));
                gStageFmt = dd.Format; gStripH = h;
            }
            var copy = VFn<CopyRegionFn>(gCtx, 46);                         // ID3D11DeviceContext::CopySubresourceRegion
            for (int k = 0; k < 3; k++)
            {
                int x = Math.Max(0, Math.Min((int)dd.Width - (int)gStripW, t.L + (t.R - t.L) * (k + 1) / 4));
                var box = new BOX { left = (uint)x, top = (uint)top, front = 0, right = (uint)x + gStripW, bottom = (uint)bottom, back = 1 };
                copy(gCtx, gStage, 0, (uint)(k * gStripW), 0, 0, tex, 0, ref box);
            }
            MAPPED m;
            if (VFn<MapFn>(gCtx, 14)(gCtx, gStage, 0, 1 /* READ */, 0, out m) != 0) return 0;
            int bpp = dd.Format == 10 ? 8 : 4; var row = new byte[3 * gStripW * bpp]; ulong hsh = 1469598103934665603UL;
            if (gDump == null || gDump.Length != h * row.Length) gDump = new byte[h * row.Length];
            var prev = (byte[])gDump.Clone(); int firstDiff = -1, nDiff = 0;
            for (int y = 0; y < h; y++)
            {
                Marshal.Copy(m.pData + (int)(y * m.RowPitch), row, 0, row.Length);
                foreach (byte b in row) { hsh ^= b; hsh *= 1099511628211UL; }
                for (int i = 0; i < row.Length; i++)          // ignore compositor dithering (LSB noise): only real changes
                    if (Math.Abs(prev[y * row.Length + i] - row[i]) > 24) { if (firstDiff < 0) firstDiff = y * row.Length + i; nDiff++; }
                Buffer.BlockCopy(row, 0, gDump, y * row.Length, row.Length);
            }
            if (gDebug > 0 && nDiff > 0)
            {
                gDebug--;
                int rb = row.Length, yy = firstDiff / rb, xx = (firstDiff % rb) / bpp;
                Console.WriteLine(string.Format("  diff: fmt={0} bytes={1} first at strip {2} x+{3} y={4} (abs y {5}) old={6:X2} new={7:X2}",
                    dd.Format, nDiff, xx / (int)gStripW, xx % gStripW, yy, top + yy, prev[firstDiff], gDump[firstDiff]));
            }
            VFn<UnmapFn>(gCtx, 15)(gCtx, gStage, 0);
            if (nDiff < 16) return 1;                          // no visible change: same "hash" as before
            gSeq++;
            return gSeq;
        }
        finally { Marshal.Release(tex); }
    }

    static bool Hit(RECT a, RECT b) { return a.L < b.R && b.L < a.R && a.T < b.B && b.T < a.B; }

    static RECT FindChild(IntPtr top, string cls)
    {
        RECT best = new RECT(); long bestArea = 0;
        EnumChildWindows(top, (h, p) =>
        {
            if (!IsWindowVisible(h)) return true;
            var sb = new StringBuilder(256); GetClassName(h, sb, sb.Capacity);
            if (sb.ToString().IndexOf(cls, StringComparison.OrdinalIgnoreCase) < 0) return true;
            RECT r; GetWindowRect(h, out r);
            long area = (long)(r.R - r.L) * (r.B - r.T);
            if (area > bestArea) { bestArea = area; best = r; }
            return true;
        }, IntPtr.Zero);
        return best;
    }

    static IntPtr foundHwnd;
    static RECT FindWindow(string title, out string found)
    {
        RECT best = new RECT(); string name = null; long bestArea = 0;
        EnumWindows((h, p) =>
        {
            if (!IsWindowVisible(h)) return true;
            var sb = new StringBuilder(512); GetWindowText(h, sb, sb.Capacity);
            string t = sb.ToString();
            if (t.IndexOf(title, StringComparison.OrdinalIgnoreCase) < 0) return true;
            RECT r; GetWindowRect(h, out r);
            long area = (long)(r.R - r.L) * (r.B - r.T);
            if (area > bestArea) { bestArea = area; best = r; name = t; foundHwnd = h; }
            return true;
        }, IntPtr.Zero);
        found = name;
        return best;
    }

    static IDXGIOutputDuplication Duplicate(out RECT desktop, out double refreshHz)
    {
        desktop = new RECT(); refreshHz = 60;
        Guid g = typeof(IDXGIFactory1).GUID; IDXGIFactory1 f;
        Marshal.ThrowExceptionForHR(CreateDXGIFactory1(ref g, out f));
        for (uint a = 0; ; a++)
        {
            IDXGIAdapter1 ad;
            if (f.EnumAdapters1(a, out ad) != 0) break;
            IDXGIOutput1 o;
            if (ad.EnumOutputs(0, out o) != 0) continue;            // the panel hangs off this adapter (Intel)
            OUTPUT_DESC od; o.GetDesc(out od); desktop = od.Desktop;
            IntPtr pad = Marshal.GetIUnknownForObject(ad), dev, ctx; int lvl;
            Marshal.ThrowExceptionForHR(D3D11CreateDevice(pad, 0, IntPtr.Zero, 0, IntPtr.Zero, 0, 7, out dev, out lvl, out ctx));
            gDev = dev; gCtx = ctx; gStage = IntPtr.Zero; gStageFmt = -1;
            IDXGIOutputDuplication d;
            Marshal.ThrowExceptionForHR(o.DuplicateOutput(dev, out d));
            DUPL_DESC dd; d.GetDesc(out dd);
            if (dd.RefreshDen != 0) refreshHz = (double)dd.RefreshNum / dd.RefreshDen;
            return d;
        }
        throw new Exception("no output attached to any adapter");
    }

    static double Pct(List<double> s, double p) { return s.Count == 0 ? 0 : s[Math.Min(s.Count - 1, (int)(p / 100.0 * s.Count))]; }

    static int Main(string[] args)
    {
        if (args.Length < 2) { Console.WriteLine("usage: frametime SECONDS \"window title substring\" [label]"); return 2; }
        double secs = double.Parse(args[0]); string label = args.Length > 2 ? args[2] : args[1];
        SetProcessDpiAwarenessContext((IntPtr)(-4));                  // physical pixels, like Desktop Duplication
        string wname; RECT win = FindWindow(args[1], out wname);
        if (wname == null) { Console.WriteLine("window not found: " + args[1]); return 1; }
        bool pixels = args.Length > 4 && args[4] == "pixels";
        if (args.Length > 3 && args[3] != "-")
        {
            win = FindChild(foundHwnd, args[3]);
            if (win.R <= win.L) { Console.WriteLine("child window class not found: " + args[3]); return 1; }
            wname += " / " + args[3];
        }
        RECT desk; double hz; var dup = Duplicate(out desk, out hz);
        RECT target = new RECT { L = win.L - desk.L, T = win.T - desk.T, R = win.R - desk.L, B = win.B - desk.T };
        var times = new List<long>(); IntPtr buf = Marshal.AllocHGlobal(1 << 20); var sw = Stopwatch.StartNew();
        int repaints = 0; ulong lastHash = 0;
        while (sw.Elapsed.TotalSeconds < secs)
        {
            FRAME_INFO fi; IntPtr res;
            int hr = dup.AcquireNextFrame(100, out fi, out res);
            if (hr == WAIT_TIMEOUT) continue;
            if (hr == ACCESS_LOST) { Marshal.ReleaseComObject(dup); dup = Duplicate(out desk, out hz); continue; }
            Marshal.ThrowExceptionForHR(hr);
            bool hit = false;
            if (fi.LastPresentTime != 0 && fi.TotalMetadataBufferSize > 0)
            {
                uint need;
                if (dup.GetFrameMoveRects(1 << 20, buf, out need) == 0)
                    for (int i = 0; i < need / Marshal.SizeOf(typeof(MOVE_RECT)) && !hit; i++)
                        hit = Hit(((MOVE_RECT)Marshal.PtrToStructure(buf + i * Marshal.SizeOf(typeof(MOVE_RECT)), typeof(MOVE_RECT))).Dst, target);
                if (!hit && dup.GetFrameDirtyRects(1 << 20, buf, out need) == 0)
                    for (int i = 0; i < need / Marshal.SizeOf(typeof(RECT)) && !hit; i++)
                        hit = Hit((RECT)Marshal.PtrToStructure(buf + i * Marshal.SizeOf(typeof(RECT)), typeof(RECT)), target);
            }
            if (hit) repaints++;
            if (hit && pixels)
            {
                ulong h = StripHash(res, target, desk);           // 0 = error, 1 = no visible change, >1 = changed
                hit = h > 1;
            }
            if (hit) times.Add(fi.LastPresentTime);
            if (res != IntPtr.Zero) Marshal.Release(res);
            dup.ReleaseFrame();
        }
        double period = 1000.0 / hz, gap = 200, qpc = Stopwatch.Frequency / 1000.0;
        var iv = new List<double>(); int bursts = times.Count > 0 ? 1 : 0; double active = 0;
        for (int i = 1; i < times.Count; i++)
        {
            double d = (times[i] - times[i - 1]) / qpc;
            if (d > gap) { bursts++; continue; }
            iv.Add(d); active += d;
        }
        iv.Sort();
        int st15 = iv.FindAll(d => d > 1.5 * period).Count, st25 = iv.FindAll(d => d > 2.5 * period).Count;
        Console.WriteLine(string.Format(
            "{0}: window \"{1}\" {2}x{3} @ {4:F1} Hz | " + (pixels ? "content changes" : "updates") + "={5} (repaints " + repaints + ") bursts={6} active={7:F1}s rate={8:F1}/s | " +
            "interval p50={9:F1} p90={10:F1} p99={11:F1} max={12:F1} ms | stutter(>1.5 frames)={13:F1}% (>2.5 frames)={14:F1}%",
            label, wname, win.R - win.L, win.B - win.T, hz, times.Count, bursts, active / 1000,
            active > 0 ? iv.Count / (active / 1000) : 0, Pct(iv, 50), Pct(iv, 90), Pct(iv, 99), iv.Count > 0 ? iv[iv.Count - 1] : 0,
            iv.Count > 0 ? 100.0 * st15 / iv.Count : 0, iv.Count > 0 ? 100.0 * st25 / iv.Count : 0));
        return 0;
    }
}
