// gbdisk.cs - disk helper for the Googlebook OS dual-boot installer (Surface Book 3).
// C# 5 / .NET Framework 4: build with build_gbdisk.ps1 (csc.exe v4.0.30319). No third-party code.
//
// One program, two backends behind the same interface, so the installer logic can be tested on image files:
//   --disk N    \\.\PhysicalDriveN: IOCTL_DISK_GET/SET_DRIVE_LAYOUT_EX for the GPT (names, GUIDs, attributes),
//               raw sector I/O at the partition offsets, volumes on the target range locked and dismounted first.
//   --image P   a raw disk image file: the GPT (protective MBR, primary and backup header and entries, CRC32s) is
//               parsed and written by this program, the same way partmgr does for SET_DRIVE_LAYOUT_EX.
//
// SAFETY: without --apply NOTHING is written. The disk is opened with access 0 (query only, layout/plan) or
// GENERIC_READ (verify/backup), the image with FileAccess.Read, so a dry run cannot write by construction.
// Every partition write is bounds-checked against a fresh layout read; a layout change must keep every
// pre-existing partition entry exactly as it was (offset, length, type, GUID, attributes, name).
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

[assembly: System.Reflection.AssemblyTitle("gbdisk - Googlebook dual-boot disk helper")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]

namespace GbDualBoot
{
    class GbError : Exception
    {
        public readonly int Code;
        public GbError(string msg) : base(msg) { Code = 2; }
        public GbError(string msg, int code) : base(msg) { Code = code; }
    }

    static class K
    {
        public const long MiB = 1L << 20;
        public const long Align = MiB;
        public const int Chunk = 4 << 20;
        public const long BigBlank = 256 * MiB;   // blank partitions larger than this: zero head + tail only
        public const long HeadTail = 64 * MiB;
        public static readonly Guid EspType = new Guid("c12a7328-f81f-11d2-ba4b-00a0c93ec93b");
        public static readonly Guid LinuxType = new Guid("0fc63daf-8483-4772-8e79-3d69d8477de4");
        public static readonly Guid BasicDataType = new Guid("ebd0a0a2-b9e5-4433-87c0-68b6b72699c7");
        public static readonly Guid MsrType = new Guid("e3c9e316-0b5c-4db8-817d-f92df00215ae");
        public static readonly Guid RecoveryType = new Guid("de94bba4-06d1-4d40-a16a-bfd50179d6ac");
        // Partition names of the Googlebook set (Android finds partitions by GPT name: /dev/block/by-name/<name>).
        public static readonly string[] GbNames = {
            "EFI-SYSTEM", "boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b",
            "vbmeta_a", "vbmeta_b", "pvmfw_a", "pvmfw_b", "misc", "metadata", "frp_persist",
            "desktop_security_persist", "desktop_security_storage", "super", "userdata" };
        // Types Windows itself uses: a partition of these types is never removed, whatever its name.
        public static readonly Guid[] WindowsTypes = { BasicDataType, MsrType, RecoveryType,
            new Guid("5808c8aa-7e8f-42e0-85d2-e1e90434cfb3"), new Guid("af9b60a0-1431-4f62-bc68-3311714a69ad"),  // LDM
            new Guid("e75caf8f-f680-4cee-afa3-b001e56efc2d") };                                              // Storage Spaces
        // The only attribute bits a new partition may carry: android-desktop/ChromeOS slot state (vbmeta_a/b),
        // priority 48-51, tries 52-55, successful 56. Never 0 (platform required), 1, 2 or 60-63.
        public const ulong AllowedNewAttrs = 0x01FF000000000000UL;
    }

    // ------------------------------------------------------------------------------------------------ model
    class Part
    {
        public int Number;
        public int Index = -1;            // GPT entry slot (image backend)
        public long Offset, Length;
        public Guid Type, Id;
        public ulong Attributes;
        public string Name = "";
        public byte[] Raw;                // PARTITION_INFORMATION_EX as returned by GET (disk backend), reused on SET
        public long End { get { return Offset + Length; } }

        public Part Clone()
        {
            Part p = (Part)MemberwiseClone();
            if (Raw != null) p.Raw = (byte[])Raw.Clone();
            return p;
        }
        public bool SameAs(Part o)
        {
            return o != null && Offset == o.Offset && Length == o.Length && Type == o.Type && Id == o.Id &&
                   Attributes == o.Attributes && Name == o.Name;
        }
        public string Diff(Part o)
        {
            if (o == null) return "missing";
            var d = new List<string>();
            if (Offset != o.Offset) d.Add("offset " + o.Offset + " != " + Offset);
            if (Length != o.Length) d.Add("length " + o.Length + " != " + Length);
            if (Type != o.Type) d.Add("type " + o.Type + " != " + Type);
            if (Id != o.Id) d.Add("id " + o.Id + " != " + Id);
            if (Attributes != o.Attributes) d.Add("attributes " + U.Hex(o.Attributes) + " != " + U.Hex(Attributes));
            if (Name != o.Name) d.Add("name '" + o.Name + "' != '" + Name + "'");
            return string.Join(", ", d);
        }
        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["number"] = Number; d["name"] = Name; d["type"] = Type.ToString(); d["id"] = Id.ToString();
            d["offset"] = Offset; d["length"] = Length; d["attributes"] = U.Hex(Attributes);
            return d;
        }
        public static Part FromJson(IDictionary<string, object> d)
        {
            var p = new Part();
            p.Number = d.ContainsKey("number") ? (int)U.L(d["number"]) : 0;
            p.Name = d.ContainsKey("name") && d["name"] != null ? (string)d["name"] : "";
            p.Type = new Guid((string)d["type"]);
            p.Id = new Guid((string)d["id"]);
            p.Offset = U.L(d["offset"]);
            p.Length = U.L(d["length"]);
            p.Attributes = d.ContainsKey("attributes") ? U.ParseHex(d["attributes"]) : 0;
            return p;
        }
        public override string ToString()
        {
            return string.Format(CultureInfo.InvariantCulture,
                "#{0,-3} {1,-26} offset {2,15} length {3,15} ({4,9:0.##} MiB) type {5} id {6} attr {7}",
                Number, "'" + Name + "'", Offset, Length, Length / (double)K.MiB, Type, Id, U.Hex(Attributes));
        }
    }

    class Layout
    {
        public Guid DiskId;
        public long DiskSize;
        public int SectorSize = 512;
        public long FirstUsable;          // bytes, = DRIVE_LAYOUT_INFORMATION_GPT.StartingUsableOffset
        public long UsableLength;         // bytes
        public int MaxPartitionCount;
        public List<Part> Parts = new List<Part>();
        public long UsableEnd { get { return FirstUsable + UsableLength; } }
        public Part ById(Guid id) { foreach (Part p in Parts) if (p.Id == id) return p; return null; }
        public Part ByNumber(int n) { foreach (Part p in Parts) if (p.Number == n) return p; return null; }
        public List<Part> Sorted() { return Parts.OrderBy(p => p.Offset).ToList(); }
        public Layout Clone()
        {
            Layout l = (Layout)MemberwiseClone();
            l.Parts = Parts.Select(p => p.Clone()).ToList();
            return l;
        }
        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["disk_id"] = DiskId.ToString(); d["disk_size"] = DiskSize; d["sector_size"] = SectorSize;
            d["first_usable"] = FirstUsable; d["usable_length"] = UsableLength; d["max_partition_count"] = MaxPartitionCount;
            d["partitions"] = Sorted().Select(p => (object)p.ToJson()).ToList();
            return d;
        }
        public static Layout FromJson(IDictionary<string, object> d)
        {
            var l = new Layout();
            l.DiskId = new Guid((string)d["disk_id"]);
            l.DiskSize = U.L(d["disk_size"]);
            l.SectorSize = d.ContainsKey("sector_size") ? (int)U.L(d["sector_size"]) : 512;
            l.FirstUsable = U.L(d["first_usable"]);
            l.UsableLength = U.L(d["usable_length"]);
            l.MaxPartitionCount = d.ContainsKey("max_partition_count") ? (int)U.L(d["max_partition_count"]) : 128;
            foreach (object o in U.Arr(d["partitions"])) l.Parts.Add(Part.FromJson(U.Obj(o)));
            return l;
        }
        public void Print(TextWriter w)
        {
            w.WriteLine("disk {0}: {1} bytes, sector {2}, usable {3} .. {4}, {5} of max {6} entries", DiskId, DiskSize,
                        SectorSize, FirstUsable, UsableEnd, Parts.Count, MaxPartitionCount);
            long prev = FirstUsable;
            foreach (Part p in Sorted())
            {
                if (p.Offset > prev) w.WriteLine("     (free {0,15} bytes = {1:0.##} MiB)", p.Offset - prev, (p.Offset - prev) / (double)K.MiB);
                w.WriteLine("  " + p);
                prev = Math.Max(prev, p.End);
            }
            if (UsableEnd > prev) w.WriteLine("     (free {0,15} bytes = {1:0.##} MiB)", UsableEnd - prev, (UsableEnd - prev) / (double)K.MiB);
        }
    }

    // ------------------------------------------------------------------------------------------------ utils
    static class U
    {
        public static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue, RecursionLimit = 64 };
        public static string Hex(ulong v) { return "0x" + v.ToString("x16"); }
        public static ulong ParseHex(object o)
        {
            if (o == null) return 0;
            string s = Convert.ToString(o, CultureInfo.InvariantCulture).Trim();
            if (s.StartsWith("0x", StringComparison.OrdinalIgnoreCase)) return ulong.Parse(s.Substring(2), NumberStyles.HexNumber, CultureInfo.InvariantCulture);
            return ulong.Parse(s, CultureInfo.InvariantCulture);
        }
        public static long L(object o) { return Convert.ToInt64(o, CultureInfo.InvariantCulture); }
        public static IDictionary<string, object> Obj(object o)
        {
            var d = o as IDictionary<string, object>;
            if (d == null) throw new GbError("JSON: object expected");
            return d;
        }
        public static IEnumerable Arr(object o)
        {
            var a = o as IEnumerable;
            if (a == null || o is string) throw new GbError("JSON: array expected");
            return a;
        }
        public static IDictionary<string, object> LoadJson(string path)
        {
            string text = path == "-" ? Console.In.ReadToEnd() : File.ReadAllText(path);
            return Obj(Json.DeserializeObject(text));
        }
        public static string ToJson(object o) { return Json.Serialize(o); }
        public static long AlignUp(long v, long a) { return (v + a - 1) / a * a; }
        public static long AlignDown(long v, long a) { return v / a * a; }
        public static string Hexs(byte[] h) { var sb = new StringBuilder(); foreach (byte b in h) sb.Append(b.ToString("x2")); return sb.ToString(); }
        public static bool IsZero(byte[] b, int n)
        {
            int i = 0;
            for (; i + 8 <= n; i += 8) if (BitConverter.ToInt64(b, i) != 0) return false;
            for (; i < n; i++) if (b[i] != 0) return false;
            return true;
        }
        static readonly uint[] crcTable = MakeCrcTable();
        static uint[] MakeCrcTable()
        {
            var t = new uint[256];
            for (uint i = 0; i < 256; i++)
            {
                uint c = i;
                for (int k = 0; k < 8; k++) c = (c & 1) != 0 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
                t[i] = c;
            }
            return t;
        }
        public static uint Crc32(byte[] b, int off, int n)
        {
            uint c = 0xFFFFFFFFu;
            for (int i = off; i < off + n; i++) c = crcTable[(c ^ b[i]) & 0xFF] ^ (c >> 8);
            return c ^ 0xFFFFFFFFu;
        }
        public static string Mib(long b) { return (b / (double)K.MiB).ToString("0.##", CultureInfo.InvariantCulture) + " MiB"; }
    }

    // ------------------------------------------------------------------------------------------------ native
    static class N
    {
        public const uint GENERIC_READ = 0x80000000, GENERIC_WRITE = 0x40000000;
        public const uint FILE_SHARE_READ = 1, FILE_SHARE_WRITE = 2, OPEN_EXISTING = 3;
        public const uint FILE_FLAG_NO_BUFFERING = 0x20000000, FILE_FLAG_WRITE_THROUGH = 0x80000000;
        public const uint IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0;
        public const uint IOCTL_DISK_GET_DRIVE_LAYOUT_EX = 0x00070050;
        public const uint IOCTL_DISK_SET_DRIVE_LAYOUT_EX = 0x0007C054;
        public const uint IOCTL_DISK_UPDATE_PROPERTIES = 0x00070140;
        public const uint IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000;
        public const uint FSCTL_LOCK_VOLUME = 0x00090018, FSCTL_UNLOCK_VOLUME = 0x0009001C, FSCTL_DISMOUNT_VOLUME = 0x00090020;
        public const uint MEM_COMMIT = 0x1000, MEM_RESERVE = 0x2000, MEM_RELEASE = 0x8000, PAGE_READWRITE = 4;

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr sa, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool DeviceIoControl(SafeFileHandle h, uint code, IntPtr inBuf, int inSize, IntPtr outBuf, int outSize, out int returned, IntPtr ov);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool SetFilePointerEx(SafeFileHandle h, long dist, out long newPos, uint method);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool ReadFile(SafeFileHandle h, IntPtr buf, int n, out int done, IntPtr ov);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool WriteFile(SafeFileHandle h, IntPtr buf, int n, out int done, IntPtr ov);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool FlushFileBuffers(SafeFileHandle h);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr VirtualAlloc(IntPtr addr, UIntPtr size, uint type, uint protect);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool VirtualFree(IntPtr addr, UIntPtr size, uint type);
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern IntPtr FindFirstVolumeW(StringBuilder name, int len);
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern bool FindNextVolumeW(IntPtr h, StringBuilder name, int len);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool FindVolumeClose(IntPtr h);
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern int QueryDosDeviceW(string dev, StringBuilder target, int max);

        public static GbError Win32(string what)
        {
            int e = Marshal.GetLastWin32Error();
            return new GbError(what + ": " + new System.ComponentModel.Win32Exception(e).Message + " (win32 error " + e + ")");
        }
        public static void PutGuid(IntPtr b, int off, Guid g) { Marshal.Copy(g.ToByteArray(), 0, b + off, 16); }
        public static Guid GetGuid(IntPtr b, int off) { var t = new byte[16]; Marshal.Copy(b + off, t, 0, 16); return new Guid(t); }
        public static void Zero(IntPtr b, int n) { Marshal.Copy(new byte[n], 0, b, n); }
    }

    // ------------------------------------------------------------------------------------------------ backends
    interface IDisk : IDisposable
    {
        string Describe { get; }
        bool IsImage { get; }
        bool Writable { get; }
        long Size { get; }
        int SectorSize { get; }
        int DiskNumber { get; }
        Layout GetLayout();
        void SetLayout(Layout l);
        void Read(long offset, byte[] buf, int count);
        void Write(long offset, byte[] buf, int count);
        void Flush();
    }

    enum Access { Query, Read, Write }

    sealed class PhysicalDisk : IDisk
    {
        readonly SafeFileHandle h;
        readonly Access access;
        readonly int number;
        readonly long size;
        readonly int sector;
        IntPtr abuf = IntPtr.Zero; int abufSize;

        public PhysicalDisk(int num, Access acc)
        {
            number = num; access = acc;
            string path = @"\\.\PhysicalDrive" + num;
            uint a = acc == Access.Write ? (N.GENERIC_READ | N.GENERIC_WRITE) : acc == Access.Read ? N.GENERIC_READ : 0u;
            uint flags = acc == Access.Write ? (N.FILE_FLAG_NO_BUFFERING | N.FILE_FLAG_WRITE_THROUGH) : 0u;
            h = N.CreateFileW(path, a, N.FILE_SHARE_READ | N.FILE_SHARE_WRITE, IntPtr.Zero, N.OPEN_EXISTING, flags, IntPtr.Zero);
            if (h.IsInvalid) throw N.Win32("open " + path + (a != 0 ? " (needs an elevated prompt)" : ""));
            IntPtr g = Marshal.AllocHGlobal(256);
            try
            {
                N.Zero(g, 256);
                int ret;
                if (!N.DeviceIoControl(h, N.IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, IntPtr.Zero, 0, g, 256, out ret, IntPtr.Zero))
                    throw N.Win32("IOCTL_DISK_GET_DRIVE_GEOMETRY_EX");
                sector = Marshal.ReadInt32(g, 20);
                size = Marshal.ReadInt64(g, 24);
            }
            finally { Marshal.FreeHGlobal(g); }
        }
        public string Describe { get { return @"\\.\PhysicalDrive" + number; } }
        public bool IsImage { get { return false; } }
        public bool Writable { get { return access == Access.Write; } }
        public long Size { get { return size; } }
        public int SectorSize { get { return sector; } }
        public int DiskNumber { get { return number; } }

        public Layout GetLayout()
        {
            const int max = 256;
            int sz = 48 + max * 144;
            IntPtr b = Marshal.AllocHGlobal(sz);
            try
            {
                N.Zero(b, sz);
                int ret;
                if (!N.DeviceIoControl(h, N.IOCTL_DISK_GET_DRIVE_LAYOUT_EX, IntPtr.Zero, 0, b, sz, out ret, IntPtr.Zero))
                    throw N.Win32("IOCTL_DISK_GET_DRIVE_LAYOUT_EX");
                if (Marshal.ReadInt32(b, 0) != 1) throw new GbError(Describe + " is not a GPT disk");
                var l = new Layout();
                int count = Marshal.ReadInt32(b, 4);
                l.DiskId = N.GetGuid(b, 8);
                l.FirstUsable = Marshal.ReadInt64(b, 24);
                l.UsableLength = Marshal.ReadInt64(b, 32);
                l.MaxPartitionCount = Marshal.ReadInt32(b, 40);
                l.DiskSize = size; l.SectorSize = sector;
                for (int i = 0; i < count && i < max; i++)
                {
                    int o = 48 + i * 144;
                    if (Marshal.ReadInt32(b, o) != 1) continue;
                    var p = new Part();
                    p.Offset = Marshal.ReadInt64(b, o + 8);
                    p.Length = Marshal.ReadInt64(b, o + 16);
                    p.Number = Marshal.ReadInt32(b, o + 24);
                    p.Type = N.GetGuid(b, o + 32);
                    p.Id = N.GetGuid(b, o + 48);
                    p.Attributes = (ulong)Marshal.ReadInt64(b, o + 64);
                    string nm = Marshal.PtrToStringUni(b + o + 72, 36);
                    int z = nm.IndexOf('\0');
                    p.Name = z >= 0 ? nm.Substring(0, z) : nm;
                    p.Raw = new byte[144];
                    Marshal.Copy(b + o, p.Raw, 0, 144);
                    if (p.Type == Guid.Empty) continue;
                    p.Index = i;
                    l.Parts.Add(p);
                }
                return l;
            }
            finally { Marshal.FreeHGlobal(b); }
        }

        public void SetLayout(Layout l)
        {
            if (!Writable) throw new GbError("internal: SetLayout on a read-only handle");
            int count = l.Parts.Count;
            int sz = 48 + Math.Max(1, count) * 144;
            IntPtr b = Marshal.AllocHGlobal(sz);
            try
            {
                N.Zero(b, sz);
                Marshal.WriteInt32(b, 0, 1);               // PARTITION_STYLE_GPT
                Marshal.WriteInt32(b, 4, count);
                N.PutGuid(b, 8, l.DiskId);
                Marshal.WriteInt64(b, 24, l.FirstUsable);
                Marshal.WriteInt64(b, 32, l.UsableLength);
                Marshal.WriteInt32(b, 40, l.MaxPartitionCount);
                for (int i = 0; i < count; i++)
                {
                    Part p = l.Parts[i];
                    int o = 48 + i * 144;
                    if (p.Raw != null) Marshal.Copy(p.Raw, 0, b + o, 144);   // existing entries verbatim
                    Marshal.WriteInt32(b, o, 1);
                    Marshal.WriteInt64(b, o + 8, p.Offset);
                    Marshal.WriteInt64(b, o + 16, p.Length);
                    Marshal.WriteInt32(b, o + 24, p.Number);
                    Marshal.WriteByte(b, o + 28, 1);           // RewritePartition (docs: should be TRUE)
                    N.PutGuid(b, o + 32, p.Type);
                    N.PutGuid(b, o + 48, p.Id);
                    Marshal.WriteInt64(b, o + 64, (long)p.Attributes);
                    // name: an existing entry keeps its 72 name bytes exactly as GET returned them (including any
                    // bytes after the terminating NUL); only new or renamed entries get a freshly encoded name
                    bool keepName = false;
                    if (p.Raw != null)
                    {
                        string rn = Encoding.Unicode.GetString(p.Raw, 72, 72);
                        int z = rn.IndexOf('\0');
                        keepName = (z >= 0 ? rn.Substring(0, z) : rn) == p.Name;
                    }
                    if (!keepName)
                    {
                        var name = new byte[72];
                        byte[] nb = Encoding.Unicode.GetBytes(p.Name);
                        Array.Copy(nb, name, Math.Min(nb.Length, 72));
                        Marshal.Copy(name, 0, b + o + 72, 72);
                    }
                }
                int ret;
                if (!N.DeviceIoControl(h, N.IOCTL_DISK_SET_DRIVE_LAYOUT_EX, b, sz, IntPtr.Zero, 0, out ret, IntPtr.Zero))
                    throw N.Win32("IOCTL_DISK_SET_DRIVE_LAYOUT_EX");
                if (!N.DeviceIoControl(h, N.IOCTL_DISK_UPDATE_PROPERTIES, IntPtr.Zero, 0, IntPtr.Zero, 0, out ret, IntPtr.Zero))
                    Console.Error.WriteLine("gbdisk: warning: IOCTL_DISK_UPDATE_PROPERTIES failed (" + Marshal.GetLastWin32Error() + ")");
            }
            finally { Marshal.FreeHGlobal(b); }
        }

        IntPtr Buf(int n)
        {
            if (n > abufSize)
            {
                if (abuf != IntPtr.Zero) N.VirtualFree(abuf, UIntPtr.Zero, N.MEM_RELEASE);
                abuf = N.VirtualAlloc(IntPtr.Zero, (UIntPtr)(ulong)n, N.MEM_COMMIT | N.MEM_RESERVE, N.PAGE_READWRITE);
                if (abuf == IntPtr.Zero) throw N.Win32("VirtualAlloc");
                abufSize = n;
            }
            return abuf;
        }
        void CheckIo(long off, int n)
        {
            if (access == Access.Query) throw new GbError("internal: sector I/O on a query-only handle");
            if (off < 0 || n <= 0 || off % sector != 0 || n % sector != 0 || off + n > size)
                throw new GbError("internal: unaligned or out-of-disk I/O at " + off + " +" + n);
        }
        public void Read(long off, byte[] buf, int n)
        {
            CheckIo(off, n);
            IntPtr p = Buf(n);
            long np; int done;
            if (!N.SetFilePointerEx(h, off, out np, 0)) throw N.Win32("seek " + off);
            if (!N.ReadFile(h, p, n, out done, IntPtr.Zero) || done != n) throw N.Win32("read " + n + " bytes at " + off);
            Marshal.Copy(p, buf, 0, n);
        }
        public void Write(long off, byte[] buf, int n)
        {
            if (!Writable) throw new GbError("internal: write on a read-only handle");
            CheckIo(off, n);
            IntPtr p = Buf(n);
            Marshal.Copy(buf, 0, p, n);
            long np; int done;
            if (!N.SetFilePointerEx(h, off, out np, 0)) throw N.Win32("seek " + off);
            if (!N.WriteFile(h, p, n, out done, IntPtr.Zero) || done != n) throw N.Win32("write " + n + " bytes at " + off);
        }
        public void Flush() { if (Writable) N.FlushFileBuffers(h); }
        public void UpdateProperties()
        {
            int ret;
            if (!N.DeviceIoControl(h, N.IOCTL_DISK_UPDATE_PROPERTIES, IntPtr.Zero, 0, IntPtr.Zero, 0, out ret, IntPtr.Zero))
                throw N.Win32("IOCTL_DISK_UPDATE_PROPERTIES");
        }
        public void Dispose()
        {
            if (abuf != IntPtr.Zero) { N.VirtualFree(abuf, UIntPtr.Zero, N.MEM_RELEASE); abuf = IntPtr.Zero; }
            h.Dispose();
        }
    }

    class GptHeader
    {
        public bool Valid; public string Problem = "";
        public uint Revision, HeaderSize, HeaderCrc, EntryCount, EntrySize, EntriesCrc;
        public long MyLba, AltLba, FirstLba, LastLba, EntriesLba;
        public Guid DiskId;
        public byte[] Raw;
        public static GptHeader Parse(byte[] s, long expectLba)
        {
            var g = new GptHeader();
            g.Raw = (byte[])s.Clone();
            if (Encoding.ASCII.GetString(s, 0, 8) != "EFI PART") { g.Problem = "no EFI PART signature"; return g; }
            g.Revision = BitConverter.ToUInt32(s, 8);
            g.HeaderSize = BitConverter.ToUInt32(s, 12);
            g.HeaderCrc = BitConverter.ToUInt32(s, 16);
            g.MyLba = BitConverter.ToInt64(s, 24); g.AltLba = BitConverter.ToInt64(s, 32);
            g.FirstLba = BitConverter.ToInt64(s, 40); g.LastLba = BitConverter.ToInt64(s, 48);
            var gb = new byte[16]; Array.Copy(s, 56, gb, 0, 16); g.DiskId = new Guid(gb);
            g.EntriesLba = BitConverter.ToInt64(s, 72);
            g.EntryCount = BitConverter.ToUInt32(s, 80); g.EntrySize = BitConverter.ToUInt32(s, 84);
            g.EntriesCrc = BitConverter.ToUInt32(s, 88);
            if (g.HeaderSize < 92 || g.HeaderSize > 512) { g.Problem = "bad header size"; return g; }
            var t = new byte[g.HeaderSize]; Array.Copy(s, t, (int)g.HeaderSize); t[16] = t[17] = t[18] = t[19] = 0;
            if (U.Crc32(t, 0, t.Length) != g.HeaderCrc) { g.Problem = "header CRC mismatch"; return g; }
            if (g.MyLba != expectLba) { g.Problem = "MyLBA " + g.MyLba + " != " + expectLba; return g; }
            if (g.EntrySize != 128 || g.EntryCount == 0 || g.EntryCount > 1024) { g.Problem = "unsupported entry array"; return g; }
            g.Valid = true;
            return g;
        }
        public static byte[] Build(GptHeader tmpl, long my, long alt, long entriesLba, uint entriesCrc)
        {
            var s = new byte[512];
            Array.Copy(tmpl.Raw, s, (int)tmpl.HeaderSize);
            Array.Copy(BitConverter.GetBytes(my), 0, s, 24, 8);
            Array.Copy(BitConverter.GetBytes(alt), 0, s, 32, 8);
            Array.Copy(BitConverter.GetBytes(entriesLba), 0, s, 72, 8);
            Array.Copy(BitConverter.GetBytes(entriesCrc), 0, s, 88, 4);
            s[16] = s[17] = s[18] = s[19] = 0;
            uint c = U.Crc32(s, 0, (int)tmpl.HeaderSize);
            Array.Copy(BitConverter.GetBytes(c), 0, s, 16, 4);
            return s;
        }
    }

    sealed class ImageDisk : IDisk
    {
        readonly FileStream fs;
        readonly bool writable;
        readonly string path;
        public ImageDisk(string p, Access acc)
        {
            // never a device (\\.\PhysicalDriveN, \\?\GLOBALROOT\...) or network path: --image is for test files only
            if (p.StartsWith(@"\\") || p.StartsWith("//")) throw new GbError("--image must be a regular file on a local drive, not a device or network path: " + p);
            path = Path.GetFullPath(p);
            if (path.StartsWith(@"\\")) throw new GbError("--image must be a regular file on a local drive: " + path);
            writable = acc == Access.Write;   // Query and Read both open the file read-only
            if (!File.Exists(path)) throw new GbError("image not found: " + path);
            if ((File.GetAttributes(path) & (FileAttributes.Directory | FileAttributes.Device)) != 0) throw new GbError("--image is not a regular file: " + path);
            fs = new FileStream(path, FileMode.Open, writable ? FileAccess.ReadWrite : FileAccess.Read,
                                writable ? FileShare.Read : FileShare.ReadWrite, 1 << 16, FileOptions.None);
            if (fs.Length % 512 != 0 || fs.Length < 4 * K.MiB) throw new GbError("image size must be a multiple of 512 and >= 4 MiB");
        }
        public string Describe { get { return "image " + path; } }
        public bool IsImage { get { return true; } }
        public bool Writable { get { return writable; } }
        public long Size { get { return fs.Length; } }
        public int SectorSize { get { return 512; } }
        public int DiskNumber { get { return -1; } }
        long LastLba { get { return fs.Length / 512 - 1; } }

        byte[] ReadBytes(long off, int n) { var b = new byte[n]; Read(off, b, n); return b; }

        GptHeader Primary(out byte[] entries)
        {
            GptHeader g = GptHeader.Parse(ReadBytes(512, 512), 1);
            GptHeader use = g;
            if (!g.Valid)
            {
                GptHeader bk = GptHeader.Parse(ReadBytes(LastLba * 512, 512), LastLba);
                if (!bk.Valid) throw new GbError("image has no valid GPT (primary: " + g.Problem + "; backup: " + bk.Problem + ")");
                Console.Error.WriteLine("gbdisk: warning: primary GPT invalid (" + g.Problem + "), using the backup");
                use = bk;
            }
            int n = (int)(use.EntryCount * use.EntrySize);
            entries = ReadBytes(use.EntriesLba * 512, (n + 511) / 512 * 512);
            if (U.Crc32(entries, 0, n) != use.EntriesCrc) throw new GbError("GPT entry array CRC mismatch");
            return use;
        }

        public Layout GetLayout()
        {
            byte[] ents;
            GptHeader g = Primary(out ents);
            var l = new Layout();
            l.DiskId = g.DiskId; l.DiskSize = fs.Length; l.SectorSize = 512;
            l.FirstUsable = g.FirstLba * 512; l.UsableLength = (g.LastLba - g.FirstLba + 1) * 512;
            l.MaxPartitionCount = (int)g.EntryCount;
            int num = 0;
            for (int i = 0; i < g.EntryCount; i++)
            {
                int o = i * 128;
                var tb = new byte[16]; Array.Copy(ents, o, tb, 0, 16);
                var type = new Guid(tb);
                if (type == Guid.Empty) continue;
                var p = new Part();
                p.Type = type;
                var ib = new byte[16]; Array.Copy(ents, o + 16, ib, 0, 16); p.Id = new Guid(ib);
                long first = BitConverter.ToInt64(ents, o + 32), last = BitConverter.ToInt64(ents, o + 40);
                p.Offset = first * 512; p.Length = (last - first + 1) * 512;
                p.Attributes = BitConverter.ToUInt64(ents, o + 48);
                string nm = Encoding.Unicode.GetString(ents, o + 56, 72);
                int z = nm.IndexOf('\0'); p.Name = z >= 0 ? nm.Substring(0, z) : nm;
                p.Index = i; p.Number = ++num;
                l.Parts.Add(p);
            }
            return l;
        }

        // Entries keep their GPT slot (Index); new entries take the lowest free slots, removed ones are zeroed.
        public void SetLayout(Layout l)
        {
            if (!writable) throw new GbError("internal: SetLayout on a read-only image");
            byte[] old;
            GptHeader g = Primary(out old);
            int n = (int)(g.EntryCount * g.EntrySize);
            var ents = new byte[(n + 511) / 512 * 512];
            var used = new HashSet<int>();
            foreach (Part p in l.Parts) if (p.Index >= 0) { if (!used.Add(p.Index)) throw new GbError("internal: duplicate GPT slot"); }
            int next = 0;
            foreach (Part p in l.Parts)
            {
                int idx = p.Index;
                if (idx < 0) { while (used.Contains(next)) next++; idx = next; used.Add(idx); }
                if (idx >= g.EntryCount) throw new GbError("GPT entry array full");
                int o = idx * 128;
                Array.Copy(p.Type.ToByteArray(), 0, ents, o, 16);
                Array.Copy(p.Id.ToByteArray(), 0, ents, o + 16, 16);
                Array.Copy(BitConverter.GetBytes(p.Offset / 512), 0, ents, o + 32, 8);
                Array.Copy(BitConverter.GetBytes(p.End / 512 - 1), 0, ents, o + 40, 8);
                Array.Copy(BitConverter.GetBytes(p.Attributes), 0, ents, o + 48, 8);
                byte[] nb = Encoding.Unicode.GetBytes(p.Name);
                Array.Copy(nb, 0, ents, o + 56, Math.Min(nb.Length, 72));
            }
            uint crc = U.Crc32(ents, 0, n);
            long entSectors = ents.Length / 512;
            long backupEntries = LastLba - entSectors;
            long primEntries = g.MyLba == 1 ? g.EntriesLba : 2;     // header taken from the backup copy: standard LBA 2
            if (primEntries + entSectors > g.FirstLba || backupEntries <= g.LastLba)
                throw new GbError("GPT geometry does not leave room for the entry arrays");
            Write(primEntries * 512, ents, ents.Length);
            Write(512, GptHeader.Build(g, 1, LastLba, primEntries, crc), 512);
            Write(backupEntries * 512, ents, ents.Length);
            Write(LastLba * 512, GptHeader.Build(g, LastLba, 1, backupEntries, crc), 512);
            fs.Flush(true);
        }

        public void Read(long off, byte[] buf, int n)
        {
            if (off < 0 || off + n > fs.Length || off % 512 != 0 || n % 512 != 0) throw new GbError("internal: bad image read at " + off);
            fs.Position = off;
            int got = 0;
            while (got < n) { int r = fs.Read(buf, got, n - got); if (r <= 0) throw new GbError("short read at " + off); got += r; }
        }
        public void Write(long off, byte[] buf, int n)
        {
            if (!writable) throw new GbError("internal: write on a read-only image");
            if (off < 0 || off + n > fs.Length || off % 512 != 0 || n % 512 != 0) throw new GbError("internal: bad image write at " + off);
            fs.Position = off;
            fs.Write(buf, 0, n);
        }
        public void Flush() { if (writable) fs.Flush(true); }
        public void Dispose() { fs.Dispose(); }
    }

    // ------------------------------------------------------------------------------------------------ volumes
    static class Volumes
    {
        public class Vol { public string Name; public List<long[]> Extents = new List<long[]>(); }
        static readonly IntPtr Invalid = new IntPtr(-1);
        public static List<Vol> All()
        {
            var res = new List<Vol>();
            var sb = new StringBuilder(512);
            IntPtr fh = N.FindFirstVolumeW(sb, sb.Capacity);
            if (fh == Invalid) throw N.Win32("FindFirstVolume");
            try
            {
                do
                {
                    var v = new Vol { Name = sb.ToString() };
                    using (SafeFileHandle h = N.CreateFileW(v.Name.TrimEnd('\\'), 0, N.FILE_SHARE_READ | N.FILE_SHARE_WRITE, IntPtr.Zero, N.OPEN_EXISTING, 0, IntPtr.Zero))
                    {
                        if (!h.IsInvalid)
                        {
                            int sz = 8 + 24 * 64; IntPtr b = Marshal.AllocHGlobal(sz);
                            try
                            {
                                int ret;
                                if (N.DeviceIoControl(h, N.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, IntPtr.Zero, 0, b, sz, out ret, IntPtr.Zero))
                                {
                                    int n = Marshal.ReadInt32(b, 0);
                                    for (int i = 0; i < n && i < 64; i++)
                                    {
                                        int o = 8 + 24 * i;
                                        v.Extents.Add(new long[] { Marshal.ReadInt32(b, o), Marshal.ReadInt64(b, o + 8), Marshal.ReadInt64(b, o + 16) });
                                    }
                                }
                            }
                            finally { Marshal.FreeHGlobal(b); }
                        }
                    }
                    res.Add(v);
                    sb = new StringBuilder(512);
                } while (N.FindNextVolumeW(fh, sb, sb.Capacity));
            }
            finally { N.FindVolumeClose(fh); }
            return res;
        }
        public static string DosDevice(string volName)   // \\?\Volume{...}\ -> \Device\HarddiskVolumeN
        {
            string k = volName.StartsWith(@"\\?\") ? volName.Substring(4) : volName;
            k = k.TrimEnd('\\');
            var sb = new StringBuilder(1024);
            return N.QueryDosDeviceW(k, sb, sb.Capacity) == 0 ? null : sb.ToString();
        }
        public static List<Vol> InRange(int disk, long off, long len)
        {
            var r = new List<Vol>();
            foreach (Vol v in All())
                foreach (long[] e in v.Extents)
                    if (e[0] == disk && e[1] < off + len && off < e[1] + e[2]) { r.Add(v); break; }
            return r;
        }
    }

    // Locks and dismounts every volume inside [off, off+len) of the disk; refuses a volume that reaches outside.
    sealed class VolumeGuard : IDisposable
    {
        readonly List<SafeFileHandle> locked = new List<SafeFileHandle>();
        public readonly List<string> Names = new List<string>();
        public static VolumeGuard Lock(int disk, long off, long len)
        {
            var g = new VolumeGuard();
            try
            {
                foreach (Volumes.Vol v in Volumes.InRange(disk, off, len))
                {
                    foreach (long[] e in v.Extents)
                        if (e[0] != disk || e[1] < off || e[1] + e[2] > off + len)
                            throw new GbError("volume " + v.Name + " reaches outside the target partition - refusing");
                    SafeFileHandle h = N.CreateFileW(v.Name.TrimEnd('\\'), N.GENERIC_READ | N.GENERIC_WRITE,
                                                     N.FILE_SHARE_READ | N.FILE_SHARE_WRITE, IntPtr.Zero, N.OPEN_EXISTING, 0, IntPtr.Zero);
                    if (h.IsInvalid) throw N.Win32("open volume " + v.Name);
                    int ret; bool ok = false;
                    for (int t = 0; t < 20 && !ok; t++)
                    {
                        ok = N.DeviceIoControl(h, N.FSCTL_LOCK_VOLUME, IntPtr.Zero, 0, IntPtr.Zero, 0, out ret, IntPtr.Zero);
                        if (!ok) Thread.Sleep(500);
                    }
                    if (!ok) { h.Dispose(); throw N.Win32("lock volume " + v.Name + " (close Explorer windows on it)"); }
                    if (!N.DeviceIoControl(h, N.FSCTL_DISMOUNT_VOLUME, IntPtr.Zero, 0, IntPtr.Zero, 0, out ret, IntPtr.Zero))
                    { h.Dispose(); throw N.Win32("dismount volume " + v.Name); }
                    g.locked.Add(h); g.Names.Add(v.Name);
                    Console.WriteLine("  locked + dismounted " + v.Name);
                }
            }
            catch { g.Dispose(); throw; }
            return g;
        }
        public void Dispose()
        {
            foreach (SafeFileHandle h in locked)
            {
                int ret;
                N.DeviceIoControl(h, N.FSCTL_UNLOCK_VOLUME, IntPtr.Zero, 0, IntPtr.Zero, 0, out ret, IntPtr.Zero);
                h.Dispose();
            }
            locked.Clear();
        }
    }

    // ------------------------------------------------------------------------------------------------ plan
    class PlannedPart
    {
        public Part P;
        public string File;          // relative to the source dir, null = blank
        public long FileSize;
        public string Sha256;
        public bool Blank;
        public Dictionary<string, object> ToJson()
        {
            var d = P.ToJson();
            d.Remove("number");
            d["file"] = File; d["file_size"] = FileSize; d["sha256"] = Sha256; d["blank"] = Blank;
            return d;
        }
        public static PlannedPart FromJson(IDictionary<string, object> d)
        {
            var pp = new PlannedPart();
            pp.P = Part.FromJson(d);
            pp.File = d.ContainsKey("file") ? d["file"] as string : null;
            pp.FileSize = d.ContainsKey("file_size") && d["file_size"] != null ? U.L(d["file_size"]) : 0;
            pp.Sha256 = d.ContainsKey("sha256") ? d["sha256"] as string : null;
            pp.Blank = d.ContainsKey("blank") && d["blank"] is bool && (bool)d["blank"];
            return pp;
        }
    }

    class Plan
    {
        public Guid DiskId; public long DiskSize; public string Target = "";
        public bool Existing; public bool TestTruncated;
        public int AnchorNumber; public Guid AnchorId; public long AnchorLength;
        public long RegionStart, RegionEnd, Reserve;
        public List<Part> Base = new List<Part>();
        public List<PlannedPart> Parts = new List<PlannedPart>();
        public Dictionary<string, object> ToJson()
        {
            var d = new Dictionary<string, object>();
            d["format"] = "gb-dualboot-plan/1";
            d["disk_id"] = DiskId.ToString(); d["disk_size"] = DiskSize; d["target"] = Target;
            d["existing"] = Existing; d["test_truncated"] = TestTruncated;
            d["anchor"] = new Dictionary<string, object> { { "number", AnchorNumber }, { "id", AnchorId.ToString() }, { "length", AnchorLength } };
            d["region"] = new Dictionary<string, object> { { "start", RegionStart }, { "end", RegionEnd }, { "reserve", Reserve } };
            d["base"] = Base.Select(p => (object)p.ToJson()).ToList();
            d["partitions"] = Parts.Select(p => (object)p.ToJson()).ToList();
            return d;
        }
        public static Plan Load(string path)
        {
            IDictionary<string, object> d = U.LoadJson(path);
            if ((string)d["format"] != "gb-dualboot-plan/1") throw new GbError("not a gb-dualboot plan: " + path);
            var p = new Plan();
            p.DiskId = new Guid((string)d["disk_id"]); p.DiskSize = U.L(d["disk_size"]); p.Target = (string)d["target"];
            p.Existing = (bool)d["existing"]; p.TestTruncated = (bool)d["test_truncated"];
            var a = U.Obj(d["anchor"]); p.AnchorNumber = (int)U.L(a["number"]); p.AnchorId = new Guid((string)a["id"]); p.AnchorLength = U.L(a["length"]);
            var r = U.Obj(d["region"]); p.RegionStart = U.L(r["start"]); p.RegionEnd = U.L(r["end"]); p.Reserve = U.L(r["reserve"]);
            foreach (object o in U.Arr(d["base"])) p.Base.Add(Part.FromJson(U.Obj(o)));
            foreach (object o in U.Arr(d["partitions"])) p.Parts.Add(PlannedPart.FromJson(U.Obj(o)));
            return p;
        }
        public void Print(TextWriter w)
        {
            w.WriteLine("plan for disk {0} ({1}){2}", DiskId, Target, Existing ? " [existing partitions]" : "");
            if (!Existing)
                w.WriteLine("  after partition #{0} {1} (length {2}), region {3} .. {4} ({5}), {6} left free before the next partition",
                            AnchorNumber, AnchorId, AnchorLength, RegionStart, RegionEnd, U.Mib(RegionEnd - RegionStart), U.Mib(Reserve));
            foreach (PlannedPart pp in Parts)
                w.WriteLine("  {0,-26} offset {1,15} length {2,15} ({3,10}) type {4} id {5} attr {6} <- {7}",
                            pp.P.Name, pp.P.Offset, pp.P.Length, U.Mib(pp.P.Length), pp.P.Type, pp.P.Id, U.Hex(pp.P.Attributes),
                            pp.Blank ? "zeros" : pp.File + " (" + pp.FileSize + " B)");
        }
    }

    class ManifestPart
    {
        public string Name; public Guid Type, Id; public long Size; public ulong Attributes;
        public string File; public long FileSize; public string Sha256; public bool Fill; public long MinSize;
    }

    class Manifest
    {
        public List<ManifestPart> Parts = new List<ManifestPart>();
        public bool TestTruncated;
        public static Manifest Load(string path)
        {
            IDictionary<string, object> d = U.LoadJson(path);
            if (!d.ContainsKey("format") || (string)d["format"] != "gb-dualboot-manifest/1") throw new GbError("not a gb-dualboot manifest: " + path);
            var m = new Manifest();
            m.TestTruncated = d.ContainsKey("test_truncated") && d["test_truncated"] is bool && (bool)d["test_truncated"];
            foreach (object o in U.Arr(d["partitions"]))
            {
                var e = U.Obj(o);
                var p = new ManifestPart();
                p.Name = (string)e["name"]; p.Type = new Guid((string)e["type"]); p.Id = new Guid((string)e["guid"]);
                p.Size = e.ContainsKey("size") && e["size"] != null ? U.L(e["size"]) : 0;
                p.Attributes = e.ContainsKey("attributes") ? U.ParseHex(e["attributes"]) : 0;
                p.File = e.ContainsKey("file") ? e["file"] as string : null;
                p.FileSize = e.ContainsKey("file_size") && e["file_size"] != null ? U.L(e["file_size"]) : 0;
                p.Sha256 = e.ContainsKey("sha256") ? e["sha256"] as string : null;
                p.Fill = e.ContainsKey("fill") && e["fill"] is bool && (bool)e["fill"];
                p.MinSize = e.ContainsKey("min_size") && e["min_size"] != null ? U.L(e["min_size"]) : 0;
                if (p.Name.Length > 36) throw new GbError("partition name longer than 36 characters: " + p.Name);
                if (p.File != null && (p.File.Contains("..") || Path.IsPathRooted(p.File))) throw new GbError("manifest file path not allowed: " + p.File);
                m.Parts.Add(p);
            }
            if (m.Parts.Count(x => x.Fill) > 1) throw new GbError("manifest: more than one fill partition");
            var names = new HashSet<string>(); var ids = new HashSet<Guid>();
            foreach (ManifestPart p in m.Parts)
            {
                if (!names.Add(p.Name)) throw new GbError("manifest: duplicate name " + p.Name);
                if (!ids.Add(p.Id)) throw new GbError("manifest: duplicate GUID " + p.Id);
                if (Array.IndexOf(K.GbNames, p.Name) < 0) throw new GbError("manifest: unexpected partition name " + p.Name);
                if ((p.Attributes & ~K.AllowedNewAttrs) != 0) throw new GbError("manifest: attribute bits not allowed on " + p.Name);
            }
            return m;
        }
    }

    // ------------------------------------------------------------------------------------------------ program
    static class Program
    {
        static Dictionary<string, string> opt = new Dictionary<string, string>();
        static HashSet<string> flags = new HashSet<string>();

        static string Opt(string k) { string v; return opt.TryGetValue(k, out v) ? v : null; }
        static string Need(string k) { string v = Opt(k); if (v == null) throw new GbError("missing --" + k, 1); return v; }
        static bool Flag(string k) { return flags.Contains(k); }
        static bool Apply { get { return Flag("apply"); } }

        static int Main(string[] args)
        {
            if (args.Length == 0) { Usage(); return 1; }
            string cmd = args[0];
            for (int i = 1; i < args.Length; i++)
            {
                string a = args[i];
                if (!a.StartsWith("--")) { Console.Error.WriteLine("gbdisk: unexpected argument " + a); return 1; }
                string k = a.Substring(2);
                string[] valueless = { "apply", "json", "existing", "zero-full-userdata", "allow-truncated", "layout-only", "force" };
                if (Array.IndexOf(valueless, k) >= 0) { flags.Add(k); continue; }
                if (i + 1 >= args.Length) { Console.Error.WriteLine("gbdisk: --" + k + " needs a value"); return 1; }
                opt[k] = args[++i];
            }
            try
            {
                switch (cmd)
                {
                    case "layout": return CmdLayout();
                    case "backup": return CmdBackup();
                    case "plan": return CmdPlan();
                    case "create": return CmdCreate();
                    case "write": return CmdWrite(false);
                    case "verify": return CmdWrite(true);
                    case "checksrc": return CmdCheckSrc();
                    case "remove": return CmdRemove();
                    case "resize-image": return CmdResizeImage();
                    case "voldev": return CmdVolDev();
                    case "restore-gpt": return CmdRestoreGpt();
                    case "dump": return CmdDump();
                    default: Usage(); return 1;
                }
            }
            catch (GbError e) { Console.Error.WriteLine("gbdisk: ERROR: " + e.Message); return e.Code; }
            catch (Exception e) { Console.Error.WriteLine("gbdisk: ERROR: " + e); return 2; }
        }

        static void Usage()
        {
            Console.Error.WriteLine(@"gbdisk <command> (--disk N | --image FILE) [options]      nothing is written without --apply
  layout       [--json]                                      print the GPT layout (no admin needed for --disk)
  backup       --out DIR --tag T                             save LBA 0-33, the last 33 LBAs and the layout JSON
  plan         --manifest M --anchor NUM [--anchor-id GUID] [--anchor-new-length B] [--reserve B] [--userdata B]
               [--min-userdata B] [--allow-truncated] [--existing] [--layout-json F|-] --out F|-
  create       --plan P [--apply]                            add the planned partitions (names, GUIDs, attributes)
  write        --plan P --source-dir D [--only a,b] [--zero-full-userdata] [--apply]
  verify       --plan P --source-dir D [--only a,b] [--layout-only]
  checksrc     (--manifest M | --plan P) --source-dir D      sha256 of the source images (no disk access)
  remove       [--plan P] [--protect LAYOUT.json] [--anchor-id GUID] [--force] [--apply]
                                                             delete the Googlebook partitions after the anchor (D:)
  resize-image --number N --length B [--apply]               image only: stands in for Resize-Partition
  voldev       --id GUID                                     disk only: \Device\HarddiskVolumeN of a partition
  restore-gpt  --from DIR --tag T [--apply]                  write a 'backup' GPT copy back
  dump         --id GUID --out FILE                          read-only: a Googlebook partition raw into a new file
                                                             (sha256 printed, the file read back once)");
        }

        static IDisk OpenTarget(Access acc)
        {
            string img = Opt("image"), disk = Opt("disk");
            if ((img == null) == (disk == null)) throw new GbError("give exactly one of --disk N or --image FILE", 1);
            if (acc == Access.Write && !Apply) acc = Access.Query;   // dry run: layout queries only, no sector I/O
            if (img != null) return new ImageDisk(img, acc);
            return new PhysicalDisk(int.Parse(disk, CultureInfo.InvariantCulture), acc);
        }

        // ---------------------------------------------------------------- layout
        static int CmdLayout()
        {
            using (IDisk d = OpenTarget(Access.Query))
            {
                Layout l = d.GetLayout();
                if (Flag("json")) Console.WriteLine(U.ToJson(l.ToJson()));
                else { Console.WriteLine(d.Describe); l.Print(Console.Out); }
            }
            return 0;
        }

        // ---------------------------------------------------------------- backup
        static int CmdBackup()
        {
            string dir = Need("out"), tag = Need("tag");
            Directory.CreateDirectory(dir);
            using (IDisk d = OpenTarget(Access.Read))
            {
                Layout l = d.GetLayout();
                int ss = d.SectorSize;
                long last = d.Size / ss - 1;
                var prim = new byte[34 * ss];
                d.Read(0, prim, prim.Length);
                var bak = new byte[33 * ss];
                d.Read((last - 32) * ss, bak, bak.Length);
                // the two files must really hold the whole GPT (standard geometry: entries at LBA 2 and at last-32),
                // otherwise restore-gpt / dd from them would not bring the table back: refuse before anything changes
                CheckGptCopies(prim, bak, ss, last, "disk");
                string pf = Path.Combine(dir, "gpt_" + tag + "_primary.bin"), bf = Path.Combine(dir, "gpt_" + tag + "_backup.bin");
                File.WriteAllBytes(pf, prim);
                File.WriteAllBytes(bf, bak);
                var j = l.ToJson();
                j["target"] = d.Describe;
                j["time"] = DateTime.Now.ToString("o");
                j["primary_file"] = Path.GetFileName(pf); j["backup_file"] = Path.GetFileName(bf);
                j["backup_first_lba"] = last - 32;
                using (var sha = new SHA256Cng())
                {
                    j["primary_sha256"] = U.Hexs(sha.ComputeHash(prim));
                    j["backup_sha256"] = U.Hexs(sha.ComputeHash(bak));
                }
                string lf = Path.Combine(dir, "layout_" + tag + ".json");
                File.WriteAllText(lf, U.ToJson(j));
                // read-back check of what was just saved
                if (!File.ReadAllBytes(pf).SequenceEqual(prim) || !File.ReadAllBytes(bf).SequenceEqual(bak)) throw new GbError("backup read-back mismatch");
                Console.WriteLine("backup '{0}' of {1}: {2}, {3}, {4}", tag, d.Describe, pf, bf, lf);
            }
            return 0;
        }

        // prim = LBA 0..33, bak = LBA last-32..last. Both headers valid (CRC, MyLBA), entry arrays at LBA 2 and at
        // last-32 inside the copies, both entry-array CRCs correct, primary <-> backup consistent.
        static void CheckGptCopies(byte[] prim, byte[] bak, int ss, long last, string what)
        {
            if (prim.Length != 34 * ss || bak.Length != 33 * ss) throw new GbError(what + ": GPT copies have the wrong size");
            var h1 = new byte[ss]; Array.Copy(prim, ss, h1, 0, ss);
            GptHeader g1 = GptHeader.Parse(h1, 1);
            var h2 = new byte[ss]; Array.Copy(bak, 32 * ss, h2, 0, ss);
            GptHeader g2 = GptHeader.Parse(h2, last);
            if (!g1.Valid || !g2.Valid) throw new GbError(what + ": GPT headers not valid (primary: " + (g1.Valid ? "ok" : g1.Problem) + "; backup: " + (g2.Valid ? "ok" : g2.Problem) + ")");
            long ent = (long)g1.EntryCount * g1.EntrySize;
            if (g1.EntriesLba != 2 || 2L * ss + ent > prim.Length)
                throw new GbError(what + ": primary entry array not inside LBA 2-33 (EntriesLBA " + g1.EntriesLba + ", " + ent + " B): a 34-LBA copy would be incomplete");
            if (g1.AltLba != last || g2.AltLba != 1) throw new GbError(what + ": AlternateLBA fields do not match the disk size");
            if (g2.EntriesLba < last - 32 || (g2.EntriesLba - (last - 32)) * ss + ent > 32L * ss)
                throw new GbError(what + ": backup entry array (EntriesLBA " + g2.EntriesLba + ") not inside the last 33 LBAs");
            if (g2.EntryCount != g1.EntryCount || g2.EntrySize != g1.EntrySize || g2.DiskId != g1.DiskId || g2.FirstLba != g1.FirstLba || g2.LastLba != g1.LastLba)
                throw new GbError(what + ": primary and backup GPT headers disagree");
            if (U.Crc32(prim, 2 * ss, (int)ent) != g1.EntriesCrc) throw new GbError(what + ": primary entry array CRC mismatch");
            if (U.Crc32(bak, (int)((g2.EntriesLba - (last - 32)) * ss), (int)ent) != g2.EntriesCrc) throw new GbError(what + ": backup entry array CRC mismatch");
        }

        // ---------------------------------------------------------------- plan
        static int CmdPlan()
        {
            Manifest m = Manifest.Load(Need("manifest"));
            if (m.TestTruncated && !Flag("allow-truncated")) throw new GbError("manifest is a TEST export (truncated super) - refusing (--allow-truncated is for image tests)");
            Layout cur; string target;
            if (Opt("layout-json") != null) { cur = Layout.FromJson(U.LoadJson(Opt("layout-json"))); target = "layout-json"; }
            else using (IDisk d = OpenTarget(Access.Query)) { cur = d.GetLayout(); target = d.Describe; }
            Plan p = Flag("existing") ? PlanExisting(cur, m) : PlanNew(cur, m);
            p.Target = target; p.TestTruncated = m.TestTruncated;
            string outp = Need("out");
            string js = U.ToJson(p.ToJson());
            if (outp == "-") { Console.WriteLine(js); p.Print(Console.Error); }
            else { File.WriteAllText(outp, js); p.Print(Console.Out); Console.WriteLine("plan written to " + outp); }
            return 0;
        }

        static Plan PlanNew(Layout cur, Manifest m)
        {
            int anchorNum = int.Parse(Need("anchor"), CultureInfo.InvariantCulture);
            long reserve = Opt("reserve") != null ? long.Parse(Opt("reserve"), CultureInfo.InvariantCulture) : 1024 * K.MiB;
            long userdata = Opt("userdata") != null ? long.Parse(Opt("userdata"), CultureInfo.InvariantCulture) : 0;
            Layout l = cur.Clone();
            Part an = l.ByNumber(anchorNum);
            if (an == null) throw new GbError("no partition #" + anchorNum);
            if (Opt("anchor-id") != null && an.Id != new Guid(Opt("anchor-id")))
                throw new GbError("partition #" + anchorNum + " is " + an.Id + ", not the expected anchor " + Opt("anchor-id") + " - refusing");
            if (Array.IndexOf(K.GbNames, an.Name) >= 0) throw new GbError("the anchor partition #" + anchorNum + " is a Googlebook partition");
            if (Opt("anchor-new-length") != null)
            {
                long nl = long.Parse(Opt("anchor-new-length"), CultureInfo.InvariantCulture);
                if (nl > an.Length || nl <= 0 || nl % l.SectorSize != 0) throw new GbError("anchor new length must be a positive sector multiple <= the current length");
                an.Length = nl;
            }
            // refuse if a Googlebook partition (by name) or one of the manifest GUIDs is already on the disk
            foreach (Part p in l.Parts)
            {
                if (Array.IndexOf(K.GbNames, p.Name) >= 0) throw new GbError("disk already has a partition named '" + p.Name + "' (#" + p.Number + ") - uninstall first", 3);
                if (m.Parts.Any(x => x.Id == p.Id)) throw new GbError("disk already has partition GUID " + p.Id + " - uninstall first", 3);
            }
            var after = l.Parts.Where(p => p.Offset > an.Offset).OrderBy(p => p.Offset).ToList();
            long regionEnd = after.Count > 0 ? after[0].Offset : l.UsableEnd;
            foreach (Part p in l.Parts)
                if (p != an && p.Offset < an.End && p.End > an.Offset) throw new GbError("partition #" + p.Number + " overlaps the anchor");
            long start = U.AlignUp(Math.Max(an.End, l.FirstUsable), K.Align);
            long usableEnd = U.AlignDown(Math.Min(regionEnd, l.UsableEnd) - reserve, K.Align);
            long fixedSum = 0;
            foreach (ManifestPart mp in m.Parts) if (!mp.Fill) fixedSum += U.AlignUp(mp.Size, K.Align);
            ManifestPart fill = m.Parts.FirstOrDefault(x => x.Fill);
            long fillLen = 0;
            if (fill != null)
            {
                fillLen = userdata > 0 ? U.AlignUp(userdata, K.Align) : U.AlignDown(usableEnd - start - fixedSum, K.Align);
                long min = Opt("min-userdata") != null ? long.Parse(Opt("min-userdata"), CultureInfo.InvariantCulture) : fill.MinSize;
                if (fillLen < min)
                    throw new GbError(string.Format("not enough space: {0} would get {1}, minimum {2} (region {3} after keeping {4} free) - shrink more",
                                                    fill.Name, U.Mib(Math.Max(0, fillLen)), U.Mib(min), U.Mib(usableEnd - start), U.Mib(reserve)), 4);
            }
            long need = fixedSum + fillLen;
            if (start + need > usableEnd)
                throw new GbError(string.Format("does not fit: need {0}, region has {1} after keeping {2} free - shrink more",
                                                U.Mib(need), U.Mib(Math.Max(0, usableEnd - start)), U.Mib(reserve)), 4);
            if (l.Parts.Count + m.Parts.Count > l.MaxPartitionCount) throw new GbError("GPT has room for " + l.MaxPartitionCount + " entries only");
            var plan = new Plan();
            plan.DiskId = l.DiskId; plan.DiskSize = l.DiskSize;
            plan.AnchorNumber = an.Number; plan.AnchorId = an.Id; plan.AnchorLength = an.Length;
            plan.RegionStart = start; plan.RegionEnd = regionEnd; plan.Reserve = reserve;
            plan.Base = l.Sorted();
            long off = start;
            foreach (ManifestPart mp in m.Parts)
            {
                long len = mp.Fill ? fillLen : U.AlignUp(mp.Size, K.Align);
                if (mp.File != null && mp.FileSize > len) throw new GbError("source of " + mp.Name + " larger than its partition");
                var pp = new PlannedPart();
                pp.P = new Part { Name = mp.Name, Type = mp.Type, Id = mp.Id, Offset = off, Length = len, Attributes = mp.Attributes };
                pp.File = mp.File; pp.FileSize = mp.File != null ? mp.FileSize : 0; pp.Sha256 = mp.Sha256; pp.Blank = mp.File == null;
                plan.Parts.Add(pp);
                off += len;
            }
            return plan;
        }

        // For updating an installed set (e.g. only EFI-SYSTEM after a kernel rebuild): the partitions must already
        // exist with the manifest's GUIDs, names and types; offsets come from the disk.
        static Plan PlanExisting(Layout cur, Manifest m)
        {
            var plan = new Plan { Existing = true, DiskId = cur.DiskId, DiskSize = cur.DiskSize, AnchorId = Guid.Empty };
            plan.Base = cur.Sorted();
            foreach (ManifestPart mp in m.Parts)
            {
                Part p = cur.ById(mp.Id);
                if (p == null) throw new GbError("installed set has no partition with GUID " + mp.Id + " (" + mp.Name + ") - export with --reuse-guids from the installed manifest");
                if (p.Name != mp.Name || p.Type != mp.Type) throw new GbError("partition " + mp.Id + " is '" + p.Name + "' " + p.Type + ", manifest says '" + mp.Name + "' " + mp.Type);
                if (mp.File != null && mp.FileSize > p.Length) throw new GbError("new " + mp.Name + " image (" + mp.FileSize + " B) larger than the installed partition (" + p.Length + " B)");
                var pp = new PlannedPart { P = p.Clone(), File = mp.File, FileSize = mp.File != null ? mp.FileSize : 0, Sha256 = mp.Sha256, Blank = mp.File == null };
                plan.Parts.Add(pp);
            }
            return plan;
        }

        // ---------------------------------------------------------------- layout change checks
        // next = cur minus `removed` plus `added`; everything else must be untouched.
        static void CheckTransition(Layout cur, Layout next, ICollection<Guid> removed, ICollection<Part> added)
        {
            foreach (Part p in cur.Parts)
            {
                Part q = next.ById(p.Id);
                if (removed.Contains(p.Id)) { if (q != null) throw new GbError("internal: removed partition still present"); continue; }
                if (q == null || !q.SameAs(p)) throw new GbError("refusing: existing partition #" + p.Number + " would change (" + p.Diff(q) + ")");
            }
            foreach (Part q in next.Parts)
            {
                if (cur.ById(q.Id) != null) continue;
                Part a = added.FirstOrDefault(x => x.Id == q.Id);
                if (a == null || !a.SameAs(q)) throw new GbError("internal: unexpected new entry " + q.Id);
                if (q.Offset % K.Align != 0 || q.Length % K.Align != 0 || q.Length <= 0) throw new GbError(q.Name + " is not 1 MiB aligned");
                if ((q.Attributes & ~K.AllowedNewAttrs) != 0) throw new GbError(q.Name + ": attribute bits not allowed");
                if (q.Name.Length > 36) throw new GbError(q.Name + ": name too long");
            }
            var s = next.Sorted();
            for (int i = 0; i < s.Count; i++)
            {
                if (s[i].Offset < next.FirstUsable || s[i].End > next.UsableEnd) throw new GbError("partition " + s[i].Name + " outside the usable area");
                if (s[i].Offset % next.SectorSize != 0 || s[i].Length % next.SectorSize != 0) throw new GbError("partition " + s[i].Name + " not sector aligned");
                if (i > 0 && s[i].Offset < s[i - 1].End) throw new GbError("partitions overlap: " + s[i - 1].Name + " / " + s[i].Name);
            }
            if (next.Parts.Select(p => p.Id).Distinct().Count() != next.Parts.Count) throw new GbError("duplicate partition GUIDs");
            if (next.Parts.Count > next.MaxPartitionCount) throw new GbError("too many partitions");
        }

        static void CheckDisk(IDisk d, Layout cur, Plan plan)
        {
            if (cur.DiskId != plan.DiskId || cur.DiskSize != plan.DiskSize)
                throw new GbError("plan was made for disk " + plan.DiskId + " (" + plan.DiskSize + " B), target is " + cur.DiskId + " (" + cur.DiskSize + " B)");
            if (plan.TestTruncated && !d.IsImage) throw new GbError("plan comes from a TEST export - refusing on a real disk");
        }

        // ---------------------------------------------------------------- create
        static int CmdCreate()
        {
            Plan plan = Plan.Load(Need("plan"));
            if (plan.Existing) throw new GbError("plan is for existing partitions - nothing to create");
            using (IDisk d = OpenTarget(Access.Write))
            {
                Layout cur = d.GetLayout();
                CheckDisk(d, cur, plan);
                foreach (Part b in plan.Base)
                {
                    Part c = cur.ById(b.Id);
                    if (c == null || !c.SameAs(b)) throw new GbError("disk layout differs from the plan's base: #" + b.Number + " " + b.Diff(c) + " - re-plan");
                }
                if (cur.Parts.Count != plan.Base.Count) throw new GbError("disk has " + cur.Parts.Count + " partitions, plan base has " + plan.Base.Count + " - re-plan");
                Layout next = cur.Clone();
                var added = new List<Part>();
                foreach (PlannedPart pp in plan.Parts) { Part q = pp.P.Clone(); q.Number = 0; q.Index = -1; q.Raw = null; next.Parts.Add(q); added.Add(q); }
                CheckTransition(cur, next, new Guid[0], added);
                Console.WriteLine("{0}: adding {1} partitions after #{2}:", d.Describe, added.Count, plan.AnchorNumber);
                foreach (Part q in added) Console.WriteLine("  + " + q);
                if (!Apply) { Console.WriteLine("dry run: layout not written (add --apply)"); return 0; }
                d.SetLayout(next);
                Thread.Sleep(d.IsImage ? 0 : 3000);
                Layout after = d.GetLayout();
                foreach (Part p in cur.Parts)
                    if (!p.SameAs(after.ById(p.Id))) throw new GbError("POST-CHECK FAILED: existing partition #" + p.Number + " changed: " + p.Diff(after.ById(p.Id)));
                foreach (Part q in added)
                {
                    Part r = after.ById(q.Id);
                    if (r == null || !r.SameAs(q)) throw new GbError("POST-CHECK FAILED: new partition " + q.Name + ": " + q.Diff(r));
                }
                if (after.Parts.Count != next.Parts.Count) throw new GbError("POST-CHECK FAILED: partition count " + after.Parts.Count);
                Console.WriteLine("layout written and verified: {0} partitions", after.Parts.Count);
                after.Print(Console.Out);
            }
            return 0;
        }

        // ---------------------------------------------------------------- write / verify
        static void ZeroRanges(PlannedPart pp, bool zeroFull, List<long[]> ranges)
        {
            long L = pp.P.Length;
            if (!pp.Blank) { if (pp.FileSize < L) ranges.Add(new long[] { pp.FileSize, L - pp.FileSize }); return; }
            if (L <= K.BigBlank || zeroFull || L <= 2 * K.HeadTail) { ranges.Add(new long[] { 0, L }); return; }
            ranges.Add(new long[] { 0, K.HeadTail });
            ranges.Add(new long[] { L - K.HeadTail, K.HeadTail });
        }

        // the only way partition data reaches the disk: bounds-checked against the partition just re-read
        static void WriteIn(IDisk d, Part p, long rel, byte[] buf, int n, ref VolumeGuard guard)
        {
            if (rel < 0 || n <= 0 || rel + n > p.Length) throw new GbError("internal: write outside " + p.Name + " (" + rel + " +" + n + ")");
            try { d.Write(p.Offset + rel, buf, n); }
            catch (GbError e)
            {
                if (d.IsImage || !(e.Message.Contains("error 5)") || e.Message.Contains("error 19)"))) throw;
                Console.WriteLine("  write refused (" + e.Message + "), re-scanning volumes on " + p.Name + " and retrying once");
                if (guard != null) guard.Dispose();
                guard = VolumeGuard.Lock(d.DiskNumber, p.Offset, p.Length);
                d.Write(p.Offset + rel, buf, n);
            }
        }

        static int CmdWrite(bool verifyOnly)
        {
            Plan plan = Plan.Load(Need("plan"));
            string src = verifyOnly && Flag("layout-only") ? null : Need("source-dir");
            var only = Opt("only") != null ? new HashSet<string>(Opt("only").Split(',')) : null;
            bool zeroFull = Flag("zero-full-userdata");
            using (IDisk d = OpenTarget(verifyOnly ? Access.Read : Access.Write))
            {
                Layout cur = d.GetLayout();
                CheckDisk(d, cur, plan);
                var todo = plan.Parts.Where(x => only == null || only.Contains(x.P.Name)).ToList();
                if (only != null && todo.Count != only.Count) throw new GbError("--only names not all in the plan");
                // only Googlebook partitions are ever written: a plan (it is a JSON file) that names anything else -
                // a Windows type, a partition that existed before the install, or one outside the freed region - is refused
                foreach (PlannedPart pp in todo)
                {
                    if (Array.IndexOf(K.GbNames, pp.P.Name) < 0) throw new GbError("refusing: plan partition '" + pp.P.Name + "' is not a Googlebook partition");
                    if (K.WindowsTypes.Contains(pp.P.Type)) throw new GbError("refusing: plan partition '" + pp.P.Name + "' has a Windows type " + pp.P.Type);
                    if (!plan.Existing)
                    {
                        if (plan.Base.Any(b => b.Id == pp.P.Id)) throw new GbError("refusing: plan partition '" + pp.P.Name + "' " + pp.P.Id + " existed before the install");
                        if (pp.P.Offset < plan.RegionStart || pp.P.End > plan.RegionEnd)
                            throw new GbError("refusing: plan partition '" + pp.P.Name + "' lies outside the planned region " + plan.RegionStart + " .. " + plan.RegionEnd);
                        foreach (Part b in plan.Base)
                            if (pp.P.Offset < b.End && b.Offset < pp.P.End) throw new GbError("refusing: plan partition '" + pp.P.Name + "' overlaps pre-existing #" + b.Number + " '" + b.Name + "'");
                    }
                }
                // pre-existing partitions must be exactly as planned (fresh layout read)
                foreach (Part b in plan.Base)
                    if (plan.Parts.All(x => x.P.Id != b.Id) && !b.SameAs(cur.ById(b.Id)))
                        throw new GbError("existing partition #" + b.Number + " differs from the plan: " + b.Diff(cur.ById(b.Id)));
                int bad = 0;
                var buf = new byte[K.Chunk];
                foreach (PlannedPart pp in todo)
                {
                    Part p = cur.ById(pp.P.Id);
                    bool attrOnly = p != null && p.Offset == pp.P.Offset && p.Length == pp.P.Length && p.Type == pp.P.Type && p.Name == pp.P.Name;
                    if (p == null || !(p.SameAs(pp.P) || (verifyOnly && attrOnly)))
                        throw new GbError("partition " + pp.P.Name + " on disk does not match the plan: " + pp.P.Diff(p));
                    if (verifyOnly && !p.SameAs(pp.P))
                        Console.WriteLine("  note: {0} attributes now {1} (plan {2}) - the boot_control HAL updates vbmeta slot bits", p.Name, U.Hex(p.Attributes), U.Hex(pp.P.Attributes));
                    if (src == null) { Console.WriteLine("  layout ok: " + p); continue; }
                    var zr = new List<long[]>();
                    ZeroRanges(pp, zeroFull, zr);
                    VolumeGuard guard = null;
                    try
                    {
                        if (!verifyOnly && !d.IsImage && Apply) guard = VolumeGuard.Lock(d.DiskNumber, p.Offset, p.Length);
                        string writtenSha = null;
                        if (!verifyOnly)
                        {
                            if (!Apply) { Console.WriteLine("  dry run: would write {0} ({1}) at {2}", p.Name, pp.Blank ? "zeros" : pp.File, p.Offset); continue; }
                            if (!pp.Blank)
                            {
                                string f = Path.Combine(src, pp.File);
                                long fl = new FileInfo(f).Length;
                                if (fl != pp.FileSize) throw new GbError(f + ": size " + fl + " != plan " + pp.FileSize);
                                if (fl % d.SectorSize != 0) throw new GbError(f + ": size not a sector multiple");
                                using (var fsrc = new FileStream(f, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 16, FileOptions.SequentialScan))
                                using (var sha = new SHA256Cng())
                                {
                                    long done = 0, nextReport = 1L << 30;
                                    while (done < fl)
                                    {
                                        int n = (int)Math.Min(K.Chunk, fl - done), got = 0;
                                        while (got < n) { int r = fsrc.Read(buf, got, n - got); if (r <= 0) throw new GbError("short read " + f); got += r; }
                                        sha.TransformBlock(buf, 0, n, null, 0);
                                        WriteIn(d, p, done, buf, n, ref guard);
                                        done += n;
                                        if (done >= nextReport) { Console.WriteLine("    {0}: {1} written", p.Name, U.Mib(done)); nextReport += 1L << 30; }
                                    }
                                    sha.TransformFinalBlock(new byte[0], 0, 0);
                                    writtenSha = U.Hexs(sha.Hash);
                                }
                                if (pp.Sha256 != null && writtenSha != pp.Sha256) throw new GbError(pp.P.Name + ": source sha256 " + writtenSha + " != manifest " + pp.Sha256);
                            }
                            Array.Clear(buf, 0, buf.Length);
                            foreach (long[] z in zr)
                                for (long o = 0; o < z[1]; o += K.Chunk)
                                    WriteIn(d, p, z[0] + o, buf, (int)Math.Min(K.Chunk, z[1] - o), ref guard);
                            d.Flush();
                        }
                        // read back
                        string readSha = null;
                        if (!pp.Blank)
                        {
                            using (var sha = new SHA256Cng())
                            {
                                for (long o = 0; o < pp.FileSize; o += K.Chunk)
                                {
                                    int n = (int)Math.Min(K.Chunk, pp.FileSize - o);
                                    d.Read(p.Offset + o, buf, n);
                                    sha.TransformBlock(buf, 0, n, null, 0);
                                }
                                sha.TransformFinalBlock(new byte[0], 0, 0);
                                readSha = U.Hexs(sha.Hash);
                            }
                        }
                        bool zeroOk = true;
                        foreach (long[] z in zr)
                            for (long o = 0; o < z[1] && zeroOk; o += K.Chunk)
                            {
                                int n = (int)Math.Min(K.Chunk, z[1] - o);
                                d.Read(p.Offset + z[0] + o, buf, n);
                                if (!U.IsZero(buf, n)) zeroOk = false;
                            }
                        bool ok = zeroOk && (pp.Blank || (readSha == pp.Sha256 && (writtenSha == null || writtenSha == readSha)));
                        if (!ok) bad++;
                        Console.WriteLine("  {0} {1,-26} {2}{3}", ok ? "OK  " : "FAIL", p.Name,
                                          pp.Blank ? "zeros " + string.Join(",", zr.Select(z => U.Mib(z[0]) + "+" + U.Mib(z[1]))) : "sha256 " + readSha,
                                          zeroOk ? "" : " (zero range not zero)");
                    }
                    finally { if (guard != null) guard.Dispose(); }
                }
                if (bad > 0) throw new GbError(bad + " partition(s) failed verification");
                Console.WriteLine(verifyOnly ? "verify: all {0} partitions OK" : (Apply ? "write: all {0} partitions written and verified" : "dry run: {0} partitions would be written"), todo.Count);
            }
            return 0;
        }

        static int CmdCheckSrc()
        {
            var items = new List<PlannedPart>();
            if (Opt("manifest") != null)
                foreach (ManifestPart mp in Manifest.Load(Opt("manifest")).Parts.Where(x => x.File != null))
                    items.Add(new PlannedPart { P = new Part { Name = mp.Name }, File = mp.File, FileSize = mp.FileSize, Sha256 = mp.Sha256 });
            else
                items = Plan.Load(Need("plan")).Parts.Where(x => !x.Blank).ToList();
            string src = Need("source-dir");
            int bad = 0;
            foreach (PlannedPart pp in items)
            {
                string f = Path.Combine(src, pp.File);
                if (!File.Exists(f)) { Console.WriteLine("  MISSING " + f); bad++; continue; }
                long fl = new FileInfo(f).Length;
                string h;
                using (var fsrc = new FileStream(f, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 16, FileOptions.SequentialScan))
                using (var sha = new SHA256Cng()) h = U.Hexs(sha.ComputeHash(fsrc));
                bool ok = fl == pp.FileSize && h == pp.Sha256;
                if (!ok) bad++;
                Console.WriteLine("  {0} {1,-26} {2,12} B sha256 {3}", ok ? "OK  " : "BAD ", pp.P.Name, fl, h);
            }
            if (bad > 0) throw new GbError(bad + " source image(s) missing or corrupt");
            Console.WriteLine("checksrc: all source images match the manifest");
            return 0;
        }

        // ---------------------------------------------------------------- remove
        static int CmdRemove()
        {
            Plan plan = Opt("plan") != null ? Plan.Load(Opt("plan")) : null;
            Layout protect = Opt("protect") != null ? Layout.FromJson(U.LoadJson(Opt("protect"))) : null;
            if (plan == null && protect == null && !Flag("force"))
                throw new GbError("no --plan/--protect from the install: refusing to guess (use --force to remove by name and type only)");
            if (plan != null && plan.Existing) throw new GbError("--plan is an update plan (--existing), not the install plan");
            // the partition the Googlebook set was placed after (D:): from the install plan, or --anchor-id
            Guid anchorId = plan != null ? plan.AnchorId : Guid.Empty;
            if (Opt("anchor-id") != null)
            {
                Guid a = new Guid(Opt("anchor-id"));
                if (anchorId != Guid.Empty && a != anchorId) throw new GbError("--anchor-id " + a + " is not the install plan's anchor " + anchorId);
                anchorId = a;
            }
            if (anchorId == Guid.Empty) throw new GbError("the partition the Googlebook set follows (D:) is not known: give --plan or --anchor-id");
            using (IDisk d = OpenTarget(Access.Write))
            {
                Layout cur = d.GetLayout();
                if (protect != null && protect.DiskId != cur.DiskId) throw new GbError("--protect layout is from another disk");
                if (plan != null && plan.DiskId != cur.DiskId) throw new GbError("--plan is from another disk");
                Part anchor = cur.ById(anchorId);
                if (anchor == null) throw new GbError("anchor partition " + anchorId + " is not on " + d.Describe);
                if (Array.IndexOf(K.GbNames, anchor.Name) >= 0) throw new GbError("anchor partition " + anchorId + " has a Googlebook name - refusing");
                var gbTypes = new HashSet<Guid> { K.EspType, K.LinuxType };
                if (plan != null) foreach (PlannedPart pp in plan.Parts) gbTypes.Add(pp.P.Type);
                else gbTypes.Add(new Guid("88434509-d9d1-487d-b82c-15ef964cbd4b"));   // lapis vbmeta type
                foreach (Guid t in K.WindowsTypes) gbTypes.Remove(t);
                var victims = new List<Part>();
                foreach (Part p in cur.Sorted())
                {
                    bool named = Array.IndexOf(K.GbNames, p.Name) >= 0;
                    if (!named) continue;
                    string why = null;
                    PlannedPart planned = plan != null ? plan.Parts.FirstOrDefault(x => x.P.Id == p.Id) : null;
                    if (!gbTypes.Contains(p.Type)) why = "type " + p.Type + " is not a Googlebook type";
                    else if (protect != null && protect.ById(p.Id) != null) why = "it existed before the install (protected)";
                    else if (plan != null && planned == null) why = "its GUID is not in the install plan";
                    else if (p.Offset < anchor.End) why = "it lies before the end of #" + anchor.Number + " '" + anchor.Name + "' (not in the freed region)";
                    if (why != null) { Console.WriteLine("  keep   " + p + "   <- " + why); continue; }
                    if (planned != null)
                    {
                        // one of ours by GUID: it must be exactly where the install put it, inside the region it freed
                        if (planned.P.Offset != p.Offset || planned.P.Length != p.Length || planned.P.Type != p.Type || planned.P.Name != p.Name)
                            throw new GbError("refusing: '" + p.Name + "' " + p.Id + " is not where the install plan put it (" + planned.P.Diff(p) + ")");
                        if (p.Offset < plan.RegionStart || p.End > plan.RegionEnd)
                            throw new GbError("refusing: '" + p.Name + "' lies outside the install region " + plan.RegionStart + " .. " + plan.RegionEnd);
                    }
                    victims.Add(p);
                }
                if (victims.Count == 0) { Console.WriteLine("no Googlebook partitions found on " + d.Describe); return 0; }
                // the victims must be one run right after the anchor: no other partition between the anchor's end and
                // the last victim (so nothing outside the shrunk region, and nothing foreign in between, is touched)
                long lo = victims.Min(v => v.Offset), hi = victims.Max(v => v.End);
                foreach (Part p in cur.Parts)
                    if (!victims.Contains(p) && p != anchor && p.Offset < hi && p.End > anchor.End)
                        throw new GbError("partition #" + p.Number + " '" + p.Name + "' lies between #" + anchor.Number + " and the Googlebook partitions - refusing");
                if (plan != null)
                    foreach (PlannedPart pp in plan.Parts)
                        if (victims.All(v => v.Id != pp.P.Id)) Console.WriteLine("  note: planned partition " + pp.P.Name + " " + pp.P.Id + " not on disk");
                Layout next = cur.Clone();
                next.Parts.RemoveAll(p => victims.Any(v => v.Id == p.Id));
                CheckTransition(cur, next, victims.Select(v => v.Id).ToList(), new Part[0]);
                Console.WriteLine("{0}: removing {1} partitions ({2} .. {3}, {4}):", d.Describe, victims.Count, lo, hi, U.Mib(hi - lo));
                foreach (Part v in victims) Console.WriteLine("  - " + v);
                if (!Apply) { Console.WriteLine("dry run: nothing removed (add --apply)"); return 0; }
                var guards = new List<VolumeGuard>();
                try
                {
                    if (!d.IsImage) foreach (Part v in victims) guards.Add(VolumeGuard.Lock(d.DiskNumber, v.Offset, v.Length));
                    d.SetLayout(next);
                }
                finally { foreach (VolumeGuard g in guards) g.Dispose(); }
                Thread.Sleep(d.IsImage ? 0 : 3000);
                Layout after = d.GetLayout();
                foreach (Part p in next.Parts)
                    if (!p.SameAs(after.ById(p.Id))) throw new GbError("POST-CHECK FAILED: #" + p.Number + " " + p.Diff(after.ById(p.Id)));
                if (after.Parts.Count != next.Parts.Count) throw new GbError("POST-CHECK FAILED: partition count " + after.Parts.Count);
                Console.WriteLine("removed {0} partitions; {1} remain", victims.Count, after.Parts.Count);
                after.Print(Console.Out);
            }
            return 0;
        }

        // ---------------------------------------------------------------- resize-image (Resize-Partition stand-in)
        static int CmdResizeImage()
        {
            if (Opt("image") == null) throw new GbError("resize-image works on --image only (use Resize-Partition on a disk)", 1);
            int num = int.Parse(Need("number"), CultureInfo.InvariantCulture);
            long len = long.Parse(Need("length"), CultureInfo.InvariantCulture);
            using (IDisk d = OpenTarget(Access.Write))
            {
                Layout cur = d.GetLayout();
                Part p = cur.ByNumber(num);
                if (p == null) throw new GbError("no partition #" + num);
                if (len <= 0 || len % d.SectorSize != 0) throw new GbError("length must be a positive sector multiple");
                Layout next = cur.Clone();
                Part q = next.ById(p.Id);
                q.Length = len;
                var s = next.Sorted();
                for (int i = 1; i < s.Count; i++) if (s[i].Offset < s[i - 1].End) throw new GbError("new length overlaps " + s[i].Name);
                if (q.End > next.UsableEnd) throw new GbError("new length beyond the usable area");
                Console.WriteLine("{0}: partition #{1} '{2}' length {3} -> {4} ({5} -> {6})", d.Describe, num, p.Name, p.Length, len, U.Mib(p.Length), U.Mib(len));
                if (!Apply) { Console.WriteLine("dry run: not resized (add --apply)"); return 0; }
                d.SetLayout(next);
                Part r = d.GetLayout().ById(p.Id);
                if (r == null || r.Length != len || r.Offset != p.Offset) throw new GbError("POST-CHECK FAILED");
                Console.WriteLine("resized");
            }
            return 0;
        }

        // ---------------------------------------------------------------- voldev
        static int CmdVolDev()
        {
            if (Opt("disk") == null) throw new GbError("voldev needs --disk", 1);
            Guid id = new Guid(Need("id"));
            int disk = int.Parse(Opt("disk"), CultureInfo.InvariantCulture);
            Part p;
            using (IDisk d = OpenTarget(Access.Query)) p = d.GetLayout().ById(id);
            if (p == null) throw new GbError("no partition " + id + " on disk " + disk);
            foreach (Volumes.Vol v in Volumes.InRange(disk, p.Offset, p.Length))
                foreach (long[] e in v.Extents)
                    if (e[0] == disk && e[1] == p.Offset)
                    {
                        string dev = Volumes.DosDevice(v.Name);
                        if (dev == null) throw N.Win32("QueryDosDevice " + v.Name);
                        Console.WriteLine(dev);
                        Console.WriteLine(v.Name);
                        Console.Error.WriteLine("gbdisk: partition #" + p.Number + " '" + p.Name + "' = " + v.Name + " = " + dev);
                        return 0;
                    }
            throw new GbError("no volume for partition " + id + " yet (Windows creates it a few seconds after the layout change)", 3);
        }

        // ---------------------------------------------------------------- dump (wave 3, item 7)
        // Read-only copy of one Googlebook partition (install_dualboot.ps1 -UpdateEspOnly saves the installed EFI-SYSTEM
        // before it rewrites it: the rollback image). The disk is opened GENERIC_READ (Access.Read, like verify); only
        // FILE is written, which must not exist yet. The partition is found by GUID and must carry a Googlebook name.
        static int CmdDump()
        {
            Guid id = new Guid(Need("id"));
            string outp = Need("out");
            if (File.Exists(outp) || Directory.Exists(outp)) throw new GbError(outp + " already exists - refusing to overwrite it", 1);
            using (IDisk d = OpenTarget(Access.Read))
            {
                Part p = d.GetLayout().ById(id);
                if (p == null) throw new GbError("no partition " + id + " on " + d.Describe);
                if (Array.IndexOf(K.GbNames, p.Name) < 0 || K.WindowsTypes.Contains(p.Type))
                    throw new GbError("refusing: partition " + id + " '" + p.Name + "' is not a Googlebook partition");
                var buf = new byte[K.Chunk];
                string sha1, sha2;
                using (var fo = new FileStream(outp, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1 << 16))
                using (var sha = new SHA256Cng())
                {
                    for (long o = 0; o < p.Length; o += K.Chunk)
                    {
                        int n = (int)Math.Min(K.Chunk, p.Length - o);
                        d.Read(p.Offset + o, buf, n);
                        sha.TransformBlock(buf, 0, n, null, 0);
                        fo.Write(buf, 0, n);
                    }
                    sha.TransformFinalBlock(new byte[0], 0, 0);
                    sha1 = U.Hexs(sha.Hash);
                    fo.Flush(true);
                }
                using (var fi = new FileStream(outp, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 16, FileOptions.SequentialScan))
                using (var sha = new SHA256Cng())
                {
                    if (fi.Length != p.Length) throw new GbError(outp + ": " + fi.Length + " bytes written, partition has " + p.Length);
                    int r;
                    while ((r = fi.Read(buf, 0, buf.Length)) > 0) sha.TransformBlock(buf, 0, r, null, 0);
                    sha.TransformFinalBlock(new byte[0], 0, 0);
                    sha2 = U.Hexs(sha.Hash);
                }
                if (sha1 != sha2) throw new GbError(outp + ": read-back sha256 " + sha2 + " != " + sha1);
                Console.WriteLine("dump: #{0} '{1}' {2} bytes from {3} -> {4}", p.Number, p.Name, p.Length, d.Describe, outp);
                Console.WriteLine("sha256 " + sha1);
            }
            return 0;
        }

        // ---------------------------------------------------------------- restore-gpt
        static int CmdRestoreGpt()
        {
            string dir = Need("from"), tag = Need("tag");
            byte[] prim = File.ReadAllBytes(Path.Combine(dir, "gpt_" + tag + "_primary.bin"));
            byte[] bak = File.ReadAllBytes(Path.Combine(dir, "gpt_" + tag + "_backup.bin"));
            using (IDisk d = OpenTarget(Access.Write))
            {
                int ss = d.SectorSize;
                long last = d.Size / ss - 1;
                if (prim.Length != 34 * ss || bak.Length != 33 * ss) throw new GbError("backup files have the wrong size");
                var h1 = new byte[ss]; Array.Copy(prim, ss, h1, 0, ss);
                GptHeader g1 = GptHeader.Parse(h1, 1);
                if (g1.Valid && g1.AltLba != last) throw new GbError("backup was taken on a disk of another size");
                CheckGptCopies(prim, bak, ss, last, "backup files");   // headers, both entry arrays and their CRCs
                Layout cur = null;
                try { cur = d.GetLayout(); } catch (GbError) { }
                if (cur != null && cur.DiskId != g1.DiskId && !Flag("force")) throw new GbError("backup disk GUID " + g1.DiskId + " != current " + cur.DiskId);
                Console.WriteLine("{0}: restore GPT '{1}' (disk {2}): LBA 0-33 and LBA {3}-{4}", d.Describe, tag, g1.DiskId, last - 32, last);
                if (!Apply) { Console.WriteLine("dry run: not written (add --apply)"); return 0; }
                d.Write(0, prim, prim.Length);
                d.Write((last - 32) * ss, bak, bak.Length);
                d.Flush();
                var chk = new byte[prim.Length]; d.Read(0, chk, chk.Length);
                if (!chk.SequenceEqual(prim)) throw new GbError("read-back mismatch (primary)");
                chk = new byte[bak.Length]; d.Read((last - 32) * ss, chk, chk.Length);
                if (!chk.SequenceEqual(bak)) throw new GbError("read-back mismatch (backup)");
                var pd = d as PhysicalDisk;
                if (pd != null) pd.UpdateProperties();
                Console.WriteLine("GPT '" + tag + "' written back and read-back verified");
            }
            return 0;
        }
    }
}
