<#
.SYNOPSIS
    Copies the Googlebook boot logs (\gblog\ on the stick's EFI-SYSTEM partition) to
    Documents\Googlebook-logs\<timestamp>\ and builds a redacted, zipped copy for sharing.

.DESCRIPTION
    Run this on Windows after the Googlebook USB stick has booted the Surface Book 3 natively and is plugged
    back into a Windows PC.

    1. Finds the stick ONLY by GPT partition names: a USB (or removable SD/MMC) disk with exactly one partition
       named 'EFI-SYSTEM' plus partitions named 'super' and 'boot_a'. Internal disks (NVMe, SATA, ...), the
       disk that holds the running Windows, and ambiguous situations are refused; every disk is listed with the
       reason. -DiskNumber picks one of several eligible sticks.
    2. Reads EFI-SYSTEM through its volume path (\\?\Volume{GUID}\), without a drive letter. Only if that is not
       possible (and the script runs as administrator) does it mount a temporary drive letter, which is always
       removed again (try/finally, also on errors and Ctrl+C).
    3. Copies \gblog\ read-only (files are opened for reading only; nothing on the stick is written, renamed or
       deleted) to Documents\Googlebook-logs\<timestamp>\gblog\, verifies every file by SHA-256 and marks the
       copies read-only (SHA256SUMS.txt, COPY-INFO.txt next to it).
    4. Builds share\ with a redaction pass (serial numbers, MAC addresses, Wi-Fi network names, account e-mail
       addresses, IMEI-like ids, machine/partition UUIDs, Android ids, user names, passwords/tokens, locations,
       global IPv6 addresses) and zips it as Googlebook-logs-<timestamp>-share.zip. A value found in one place is
       also replaced everywhere else, the same value always gets the same placeholder (e.g. <mac-1>), and a
       final check refuses to build the zip if anything it knows about is still there.

    Administrator rights: listing disks and partition names and reading the volume list work without them.
    Windows usually lets only administrators read files on an EFI system partition; in that case the script
    says so and offers to restart itself elevated (or run it from an elevated PowerShell). A temporary drive
    letter (the fallback) always needs administrator rights.

.PARAMETER OutputRoot
    Folder that receives the <timestamp> folders. Default: <Documents>\Googlebook-logs.
.PARAMETER SourcePath
    Skip the stick detection and copy from this folder instead (a gblog folder, a folder or volume root that
    contains gblog, or \\?\Volume{GUID}\). Useful when the partition already has a drive letter, for an older
    copy, or for tests.
.PARAMETER DiskNumber
    Use this disk number when more than one eligible stick is plugged in. It must still pass every check.
.PARAMETER RedactTerm
    Extra words to remove from the share copy (your name, your home network name, ...). Case-insensitive.
.PARAMETER IncludeAudio
    Also put the audio diagnostics' .wav recordings (microphone test captures) into the share copy. They cannot
    be redacted and may contain voices from the room, so they are left out by default.
.PARAMETER NoShare
    Only make the private copy; no redaction, no zip.
.PARAMETER ListOnly
    Only list the disks and what the script thinks of them; copy nothing.
.PARAMETER NoElevationPrompt
    Never ask to restart as administrator (just explain and exit with code 5).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Get-GooglebookLogs.ps1
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Get-GooglebookLogs.ps1 -RedactTerm 'JaneDoe','HomeNet'
.EXAMPLE
    .\Get-GooglebookLogs.ps1 -SourcePath E:\gblog

.NOTES
    Exit codes: 0 done; 1 unexpected error; 2 no stick / refused / ambiguous / no gblog folder; 3 the redaction
    check failed (share copy kept for inspection, no zip); 4 the copy could not be made or verified;
    5 administrator rights are needed.
    Windows PowerShell 5.1 and PowerShell 7. ASCII only on purpose (5.1 reads BOM-less scripts as ANSI).
#>
[CmdletBinding()]
param(
    [string]$OutputRoot,
    [string]$SourcePath,
    [int]$DiskNumber = -1,
    [string[]]$RedactTerm,
    [switch]$IncludeAudio,
    [switch]$NoShare,
    [switch]$ListOnly,
    [switch]$NoElevationPrompt
)

$script:GbVersion = '1.1.1'
$script:GbScriptPath = $PSCommandPath
$script:GbQuiet = $false

# ---------------------------------------------------------------------------------------------------------------
# Win32 helpers (read-only disk/volume queries, read-only file access, mount points for the fallback)
# ---------------------------------------------------------------------------------------------------------------
$script:GbNativeSource = @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;

namespace GbLogs
{
    public sealed class FileEntry
    {
        public string Name;
        public bool IsDirectory;
        public bool IsReparsePoint;
        public long Length;
        public uint Attributes;
        public long LastWriteUtcTicks;
    }

        public static class NativeV4
    {
        const uint GENERIC_READ = 0x80000000;
        const uint FILE_SHARE_READ = 0x1, FILE_SHARE_WRITE = 0x2;
        const uint OPEN_EXISTING = 3;
        const uint FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000;
        const int ERROR_INSUFFICIENT_BUFFER = 122, ERROR_MORE_DATA = 234, ERROR_NO_MORE_FILES = 18;
        static readonly IntPtr INVALID = new IntPtr(-1);

        // All four are FILE_ANY_ACCESS: they work on a handle opened with no access rights (no admin needed).
        public const uint IOCTL_DISK_GET_DRIVE_LAYOUT_EX = 0x00070050;
        public const uint IOCTL_DISK_GET_DRIVE_GEOMETRY_EX = 0x000700A0;
        public const uint IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400;
        public const uint IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct WIN32_FIND_DATAW
        {
            public uint dwFileAttributes;
            public uint ftCreationLow, ftCreationHigh;
            public uint ftLastAccessLow, ftLastAccessHigh;
            public uint ftLastWriteLow, ftLastWriteHigh;
            public uint nFileSizeHigh, nFileSizeLow;
            public uint dwReserved0, dwReserved1;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string cFileName;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 14)] public string cAlternateFileName;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern SafeFileHandle CreateFileW(string lpFileName, uint dwDesiredAccess, uint dwShareMode,
            IntPtr lpSecurityAttributes, uint dwCreationDisposition, uint dwFlagsAndAttributes, IntPtr hTemplateFile);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool DeviceIoControl(SafeFileHandle hDevice, uint dwIoControlCode, byte[] lpInBuffer,
            int nInBufferSize, byte[] lpOutBuffer, int nOutBufferSize, out int lpBytesReturned, IntPtr lpOverlapped);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr FindFirstVolumeW(StringBuilder lpszVolumeName, int cchBufferLength);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool FindNextVolumeW(IntPtr hFindVolume, StringBuilder lpszVolumeName, int cchBufferLength);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool FindVolumeClose(IntPtr hFindVolume);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool GetVolumePathNamesForVolumeNameW(string lpszVolumeName, [Out] char[] lpszVolumePathNames,
            int cchBufferLength, out int lpcchReturnLength);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool SetVolumeMountPointW(string lpszVolumeMountPoint, string lpszVolumeName);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool DeleteVolumeMountPointW(string lpszVolumeMountPoint);
        [DllImport("kernel32.dll")]
        static extern uint GetLogicalDrives();
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetFileAttributesW(string lpFileName);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr FindFirstFileExW(string lpFileName, int fInfoLevelId, out WIN32_FIND_DATAW lpFindFileData,
            int fSearchOp, IntPtr lpSearchFilter, int dwAdditionalFlags);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool FindNextFileW(IntPtr hFindFile, out WIN32_FIND_DATAW lpFindFileData);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool FindClose(IntPtr hFindFile);

        static Win32Exception Err(int code, string what)
        {
            return new Win32Exception(code, what + ": " + new Win32Exception(code).Message + " (Win32 error " + code + ")");
        }

        // 0 = the device can be opened (with no access rights), else the Win32 error (2 = no such device).
        public static int ProbeDevice(string path)
        {
            SafeFileHandle h = CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
            if (h.IsInvalid) { int e = Marshal.GetLastWin32Error(); h.Dispose(); return e; }
            h.Dispose();
            return 0;
        }

        static SafeFileHandle OpenNoAccess(string path)
        {
            SafeFileHandle h = CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
            if (h.IsInvalid) { int e = Marshal.GetLastWin32Error(); h.Dispose(); throw Err(e, "open " + path); }
            return h;
        }

        static byte[] Ioctl(string path, uint code, byte[] input)
        {
            using (SafeFileHandle h = OpenNoAccess(path))
            {
                int size = 4096;
                while (true)
                {
                    byte[] buf = new byte[size];
                    int ret;
                    if (DeviceIoControl(h, code, input, input == null ? 0 : input.Length, buf, size, out ret, IntPtr.Zero))
                    {
                        byte[] outb = new byte[ret];
                        Buffer.BlockCopy(buf, 0, outb, 0, ret);
                        return outb;
                    }
                    int e = Marshal.GetLastWin32Error();
                    if ((e == ERROR_INSUFFICIENT_BUFFER || e == ERROR_MORE_DATA) && size < (4 << 20)) { size *= 4; continue; }
                    throw Err(e, string.Format("ioctl 0x{0:X8} on {1}", code, path));
                }
            }
        }

        public static byte[] GetDriveLayoutRaw(int disk) { return Ioctl(@"\\.\PhysicalDrive" + disk, IOCTL_DISK_GET_DRIVE_LAYOUT_EX, null); }
        public static byte[] GetDriveGeometryRaw(int disk) { return Ioctl(@"\\.\PhysicalDrive" + disk, IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, null); }
        // STORAGE_PROPERTY_QUERY { StorageDeviceProperty (0), PropertyStandardQuery (0) }
        public static byte[] GetStorageDescriptorRaw(int disk) { return Ioctl(@"\\.\PhysicalDrive" + disk, IOCTL_STORAGE_QUERY_PROPERTY, new byte[12]); }
        // devicePath: \\?\Volume{GUID} (no trailing backslash) or \\.\C:
        public static byte[] GetVolumeExtentsRaw(string devicePath) { return Ioctl(devicePath, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, null); }

        public static string[] ListVolumes()
        {
            List<string> list = new List<string>();
            StringBuilder sb = new StringBuilder(1024);
            IntPtr h = FindFirstVolumeW(sb, sb.Capacity);
            if (h == INVALID) throw Err(Marshal.GetLastWin32Error(), "FindFirstVolume");
            try
            {
                while (true)
                {
                    list.Add(sb.ToString());
                    sb.Length = 0;
                    if (!FindNextVolumeW(h, sb, sb.Capacity))
                    {
                        int e = Marshal.GetLastWin32Error();
                        if (e != ERROR_NO_MORE_FILES) throw Err(e, "FindNextVolume");
                        break;
                    }
                }
            }
            finally { FindVolumeClose(h); }
            return list.ToArray();
        }

        public static string[] GetMountPoints(string volumeName)
        {
            char[] buf = new char[2048];
            int needed;
            if (!GetVolumePathNamesForVolumeNameW(volumeName, buf, buf.Length, out needed))
            {
                int e = Marshal.GetLastWin32Error();
                if (e != ERROR_MORE_DATA) throw Err(e, "GetVolumePathNamesForVolumeName");
                buf = new char[needed + 2];
                if (!GetVolumePathNamesForVolumeNameW(volumeName, buf, buf.Length, out needed))
                    throw Err(Marshal.GetLastWin32Error(), "GetVolumePathNamesForVolumeName");
            }
            List<string> r = new List<string>();
            int start = 0;
            for (int i = 0; i < buf.Length; i++)
            {
                if (buf[i] != '\0') continue;
                if (i == start) break;
                r.Add(new string(buf, start, i - start));
                start = i + 1;
            }
            return r.ToArray();
        }

        public static void MountVolume(string mountPoint, string volumeName)
        {
            if (!SetVolumeMountPointW(mountPoint, volumeName)) throw Err(Marshal.GetLastWin32Error(), "SetVolumeMountPoint " + mountPoint);
        }

        public static void UnmountVolume(string mountPoint)
        {
            if (!DeleteVolumeMountPointW(mountPoint)) throw Err(Marshal.GetLastWin32Error(), "DeleteVolumeMountPoint " + mountPoint);
        }

        public static uint LogicalDrivesMask() { return GetLogicalDrives(); }

        // -1 when the path cannot be queried; error then holds the Win32 error (2/3 = missing, 5 = access denied).
        public static long GetAttributes(string path, out int error)
        {
            uint a = GetFileAttributesW(path);
            if (a == 0xFFFFFFFF) { error = Marshal.GetLastWin32Error(); return -1; }
            error = 0;
            return a;
        }

        public static FileEntry[] ListDirectory(string dir)
        {
            string pattern = dir.EndsWith("\\") ? dir + "*" : dir + "\\*";
            WIN32_FIND_DATAW d;
            IntPtr h = FindFirstFileExW(pattern, 1 /* FindExInfoBasic */, out d, 0 /* name match */, IntPtr.Zero, 2 /* large fetch */);
            if (h == INVALID)
            {
                int e = Marshal.GetLastWin32Error();
                if (e == 2) return new FileEntry[0];   // an empty volume root
                throw Err(e, "list " + dir);
            }
            List<FileEntry> list = new List<FileEntry>();
            try
            {
                while (true)
                {
                    if (d.cFileName != "." && d.cFileName != "..")
                    {
                        FileEntry f = new FileEntry();
                        f.Name = d.cFileName;
                        f.Attributes = d.dwFileAttributes;
                        f.IsDirectory = (d.dwFileAttributes & 0x10) != 0;
                        f.IsReparsePoint = (d.dwFileAttributes & 0x400) != 0;
                        f.Length = ((long)d.nFileSizeHigh << 32) | d.nFileSizeLow;
                        long ft = ((long)d.ftLastWriteHigh << 32) | d.ftLastWriteLow;
                        try { f.LastWriteUtcTicks = DateTime.FromFileTimeUtc(ft).Ticks; } catch (ArgumentOutOfRangeException) { f.LastWriteUtcTicks = 0; }
                        list.Add(f);
                    }
                    if (!FindNextFileW(h, out d))
                    {
                        int e = Marshal.GetLastWin32Error();
                        if (e != ERROR_NO_MORE_FILES) throw Err(e, "list " + dir);
                        break;
                    }
                }
            }
            finally { FindClose(h); }
            return list.ToArray();
        }

        static SafeFileHandle OpenRead(string path)
        {
            SafeFileHandle h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING,
                FILE_FLAG_SEQUENTIAL_SCAN, IntPtr.Zero);
            if (h.IsInvalid) { int e = Marshal.GetLastWin32Error(); h.Dispose(); throw Err(e, "open " + path); }
            return h;
        }

        static string Hex(byte[] b)
        {
            StringBuilder s = new StringBuilder(b.Length * 2);
            foreach (byte x in b) s.Append(x.ToString("x2"));
            return s.ToString();
        }

        // Reads src (opened GENERIC_READ only), writes the NEW file dst (fails if it exists).
        // Returns the SHA-256 of the bytes read from src; bytes = how many.
        public static string CopyReadOnly(string src, string dst, out long bytes)
        {
            using (SHA256 sha = SHA256.Create())
            using (SafeFileHandle h = OpenRead(src))
            using (FileStream input = new FileStream(h, FileAccess.Read, 1 << 16, false))
            using (FileStream output = new FileStream(dst, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1 << 16))
            {
                byte[] buf = new byte[1 << 20];
                long total = 0;
                int n;
                while ((n = input.Read(buf, 0, buf.Length)) > 0)
                {
                    sha.TransformBlock(buf, 0, n, null, 0);
                    output.Write(buf, 0, n);
                    total += n;
                }
                sha.TransformFinalBlock(new byte[0], 0, 0);
                bytes = total;
                return Hex(sha.Hash);
            }
        }

        public static string HashFile(string path)
        {
            using (SHA256 sha = SHA256.Create())
            using (SafeFileHandle h = OpenRead(path))
            using (FileStream input = new FileStream(h, FileAccess.Read, 1 << 16, false))
            {
                return Hex(sha.ComputeHash(input));
            }
        }
    }

    // ------------------------------------------------------------------------------------------------------------
    // Redaction engine. Every rule is line-local; a rule's regex runs only on lines that contain one of its
    // trigger words (lower-case), which keeps a 2 MB snapshot well under a second.
    // ------------------------------------------------------------------------------------------------------------
    public sealed class RedactEntry
    {
        public string Token;
        public string Category;
        public string Value;
        public List<string> Raws = new List<string>();
    }

    public sealed class RedactResidue
    {
        public string File;
        public int Line;
        public string Check;
        public string Category;
    }

    public sealed class RedactorV4
    {
        public static readonly string[] Categories = new string[] { "serial", "mac", "ssid", "email", "account", "name", "imei", "uuid", "id", "secret", "loc", "ip6", "term" };
        const RegexOptions RO = RegexOptions.CultureInvariant;
        static readonly string CatAlt = string.Join("|", Categories);
        static readonly Regex TokenRe = new Regex("^<(?:" + CatAlt + @")-\d+>$", RO);
        static readonly Regex TokenCoreRe = new Regex("^(?:" + CatAlt + @")-\d+$", RO);
        static readonly string TokenAlt = "(?<tok><(?:" + CatAlt + @")-\d+>)";
        static readonly Regex MacShape = new Regex(@"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$", RO);
        static readonly Regex MacNorm = new Regex(@"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", RO);
        static readonly Regex UuidShape = new Regex(@"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", RO);
        static readonly Regex PciAddr = new Regex(@"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$", RO);
        static readonly Regex JavaRef = new Regex(@"^[\w$.]+@[0-9a-fA-F]+$", RO);
        static readonly Regex VersionLike = new Regex(@"^\d+\.\d+(\.\d+)+(-\S*)?$", RO);   // test_power reports the kernel release
        static readonly Regex SameChar = new Regex(@"^(.)\1*$", RO | RegexOptions.Singleline);
        static readonly Regex Dotted = new Regex(@"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+$", RO);
        static readonly Regex BareHexRx = new Regex(@"(?<![0-9A-Za-z])(?:[0-9A-Fa-f]{32}|[0-9A-Fa-f]{12})(?![0-9A-Za-z])", RO | RegexOptions.Compiled);

        static Dictionary<string, bool> Set(params string[] items)
        {
            Dictionary<string, bool> d = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            foreach (string s in items) d[s] = true;
            return d;
        }

        static readonly Dictionary<string, bool> Placeholders = Set("", "null", "(null)", "nil", "none", "<none>", "unknown", "<unknown>",
            "unknown ssid", "<unknown ssid>", "n/a", "na", "0", "default string", "to be filled by o.e.m.", "not specified", "not applicable",
            "system serial number", "0123456789abcdef", "0123456789", "android", "empty", "undefined", "invalid", "redacted", "<redacted>",
            "[redacted]", "hidden", "<hidden>", "true", "false", "localhost");
        static readonly Dictionary<string, bool> NamePlaceholders = Set("owner", "guest", "system", "work profile", "private space", "clone",
            "work", "private", "primary", "user");
        // Public, well-known UUIDs: GPT types, EFI, ACPI _OSC/_DSM, DRM schemes. Bluetooth SIG base UUIDs and the AOSP
        // audio-effect UUIDs (node 0002a5d5c51b) are kept by rule.
        static readonly Dictionary<string, bool> PublicUuids = Set("00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "0fc63daf-8483-4772-8e79-3d69d8477de4", "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
            "e3c9e316-0b5c-4db8-817d-f92df00215ae", "de94bba4-06d1-4d40-a16a-bfd50179d6ac", "21686148-6449-6e6f-744e-656564454649",
            "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f", "4f68bce3-e8cd-4db1-96e7-fbcaf984b709", "8be4df61-93ca-11d2-aa0d-00e098032b8c",
            "33db4d5b-1ff7-401c-9657-7441c03dd766", "0811b06e-4a27-44f9-8d60-3cbbc22e7b48", "e5c937d0-3553-4d7a-9117-ea4d19c3434d",
            "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed", "e2719d58-a985-b3c9-781a-b030af78d30e", "1077efec-c0b2-4d02-ace3-3c1e52e2fb4b",
            "9a04f079-9840-4286-ab92-e65be0885f95");
        // Printed by the kernel's own banners (maintainer lists), never by a user.
        static readonly Dictionary<string, bool> PublicEmailDomains = Set("lists.linux.dev", "vger.kernel.org", "kernel.org", "zx2c4.com",
            "linux.it", "lists.infradead.org", "lists.freedesktop.org");
        static readonly string[] ImeiPrefixes = new string[] { "01", "10", "30", "33", "35", "44", "45", "49", "50", "51", "52", "53", "54", "86", "91", "98", "99" };
        static readonly string[] Propagated = new string[] { "serial", "ssid", "email", "imei", "id", "secret", "ip6", "account", "name" };

        sealed class KeyRule
        {
            public Regex Re; public string Cat;
            public KeyRule(string re, string cat) { Re = new Regex(re, RO | RegexOptions.IgnoreCase); Cat = cat; }
        }
        // getprop keys whose values are personal; the first match wins (the first rule keeps build ids).
        static readonly KeyRule[] PropKeys = new KeyRule[] {
            new KeyRule(@"build\.(uuid|id|fingerprint)$|remote_provisioning", null),
            new KeyRule(@"serialno|serial_?number|(^|\.)serial$|product_?serial|board_?serial", "serial"),
            new KeyRule(@"imei|meid|iccid|imsi|msisdn|(^|\.)phone_?number", "imei"),
            new KeyRule(@"(^|[._])mac($|[._])|macaddr|mac_?address|wifimac|btmac|bt_?addr|bdaddr|bluetooth\.address|(wlan|wifi|eth)\.address", "mac"),
            new KeyRule(@"(^|[._])ssid($|[._])", "ssid"),
            new KeyRule(@"part_?uuid|product_?uuid|boot_?uuid|disk_?uuid|fs_?uuid|(^|\.)uuid$", "uuid"),
            new KeyRule(@"android_?id|ssaid|gsf_?id|advertising_?id", "id"),
            new KeyRule(@"(^|[._])(e_?mail|account_?name)$", "account"),
            new KeyRule(@"device_?name|(^|\.)bt\.name$|bluetooth_?name|(^|\.)hostname$", "name")
        };

        public static string PropCategory(string key)
        {
            foreach (KeyRule k in PropKeys) if (k.Re.IsMatch(key)) return k.Cat;
            return null;
        }

        sealed class Rule
        {
            public string Name;
            public Regex Rx;
            public string[] Triggers;          // lower-case words; the regex runs only on lines that contain one
            public Func<string, bool> Pre;     // or an exact pre-check of the line (a superset of what the regex matches)
            public Func<string, Match, string> Cat;
            public Func<string, bool> Check;
        }

        // value forms after "key=" / "key: ": "quoted", 'quoted' or a bare word
        const string VQ = @"(?:""(?<v>[^""\r\n]{1,256})""|'(?<v>[^'\r\n]{1,256})'|(?<v>[^ \t\r\n,;\]\}\)""'<>]{1,256}))";
        // (review 2026-10-29) \r?$: a value at the end of a CRLF line was not matched at all
        const string VQL = @"(?:""(?<v>[^""\r\n]{1,256})""|'(?<v>[^'\r\n]{1,256})'|(?<v>[^\r\n,;\]\}\)""<>]{1,128}?))(?=[ \t]*(?:[,;\]\}\)]|[A-Za-z0-9_-]+[ \t]*[:=]|\r?$))";
        const string VQS = @"(?:""(?<v>(?:[^""\\\r\n]|\\.){0,96})""|'(?<v>[^'\r\n]{0,96})'|<(?<v>[^>\r\n]{0,96})>|(?<v>[^\r\n,;\]\}\)""<>]{1,96}?))(?=[ \t]*(?:[,;\]\}\)]|[A-Za-z0-9_-]+[ \t]*[:=]|\r?$))";
        const string KV = @"""?[ \t]*[:=][ \t]*";

        static Rule R(string name, string pattern, string[] triggers, Func<string, Match, string> cat)
        {
            Rule r = new Rule();
            r.Name = name; r.Rx = new Regex(pattern, RO | RegexOptions.Compiled); r.Triggers = triggers; r.Cat = cat;
            return r;
        }
        static Func<string, Match, string> C(string cat) { return delegate(string v, Match m) { return cat; }; }
        static string UniqCat(string v, Match m) { return MacShape.IsMatch(v) ? "mac" : "serial"; }
        static string AccountCat(string v, Match m) { return v.IndexOf('@') >= 0 ? "email" : "account"; }
        static bool IsHex(char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }
        static bool IsWordish(char c) { return char.IsLetterOrDigit(c) || c == '_'; }

        // exactly <len> digits in a row somewhere
        public static bool HasDigitRun(string s, int len)
        {
            int run = 0;
            for (int i = 0; i <= s.Length; i++)
            {
                if (i < s.Length && s[i] >= '0' && s[i] <= '9') { run++; continue; }
                if (run == len) return true;
                run = 0;
            }
            return false;
        }

        // hh:hh:hh:hh:hh:hh or hh-hh-hh-hh-hh-hh somewhere
        public static bool HasMacLike(string s)
        {
            for (int i = 2; i + 14 < s.Length; i++)
            {
                char sep = s[i];
                if ((sep != ':' && sep != '-') || !IsHex(s[i - 1]) || !IsHex(s[i - 2])) continue;
                bool ok = true;
                for (int k = 0; k < 5 && ok; k++) { int q = i + 3 * k; ok = s[q] == sep && IsHex(s[q + 1]) && IsHex(s[q + 2]); }
                if (ok) return true;
            }
            return false;
        }

        // the dash pattern of a canonical UUID (8-4-4-4-12) somewhere
        public static bool HasUuidLike(string s)
        {
            for (int i = 8; i + 27 < s.Length; i++)
                if (s[i] == '-' && s[i + 5] == '-' && s[i + 10] == '-' && s[i + 15] == '-' && IsHex(s[i - 1]) && IsHex(s[i + 1]) && IsHex(s[i + 27])) return true;
            return false;
        }

        // a maximal run of hex digits and colons that could be an IPv6 address the ipv6 rule matches and accepts:
        // starts with a hex digit, 2..7 colons with "::" or exactly 7, not inside a word or a dotted name
        public static bool HasIpv6Like(string s)
        {
            int i = 0, n = s.Length;
            while (i < n)
            {
                if (!(IsHex(s[i]) || s[i] == ':')) { i++; continue; }
                int start = i, colons = 0; bool dbl = false;
                while (i < n && (IsHex(s[i]) || s[i] == ':'))
                {
                    if (s[i] == ':') { colons++; if (i > start && s[i - 1] == ':') dbl = true; }
                    i++;
                }
                if (colons < 2 || colons > 7 || !(dbl || colons == 7) || !IsHex(s[start])) continue;
                if (start > 0 && (IsWordish(s[start - 1]) || s[start - 1] == '.')) continue;
                if (i < n && (IsWordish(s[i]) || s[i] == '.')) continue;
                return true;
            }
            return false;
        }

        static List<Rule> rules;
        static List<Rule> Rules { get { if (rules == null) rules = BuildRules(); return rules; } }
        public static string[] RuleNames() { List<string> l = new List<string>(); foreach (Rule r in Rules) l.Add(r.Name); return l.ToArray(); }

        // All trigger words of all rules, indexed by their first two characters, found in one pass over the text.
        static List<KeyValuePair<string, int>>[] trigIndex;
        static List<KeyValuePair<char, int>> trigSingle;

        static void EnsureTriggerIndex()
        {
            if (trigIndex != null) return;
            List<KeyValuePair<string, int>>[] idx = new List<KeyValuePair<string, int>>[65536];
            List<KeyValuePair<char, int>> single = new List<KeyValuePair<char, int>>();
            List<Rule> rs = Rules;
            for (int ri = 0; ri < rs.Count; ri++)
            {
                if (rs[ri].Triggers == null) continue;
                foreach (string t in rs[ri].Triggers)
                {
                    if (t.Length == 1) { single.Add(new KeyValuePair<char, int>(t[0], ri)); continue; }
                    int key = ((t[0] & 0xFF) << 8) | (t[1] & 0xFF);
                    if (idx[key] == null) idx[key] = new List<KeyValuePair<string, int>>();
                    idx[key].Add(new KeyValuePair<string, int>(t, ri));
                }
            }
            trigSingle = single;
            trigIndex = idx;
        }

        // per rule with trigger words: which lines contain one (null for rules with a Pre check)
        static bool[][] Candidates(string text, int lineCount)
        {
            EnsureTriggerIndex();
            List<Rule> rs = Rules;
            bool[][] cand = new bool[rs.Count][];
            for (int i = 0; i < rs.Count; i++) if (rs[i].Triggers != null) cand[i] = new bool[lineCount];
            string low = text.ToLowerInvariant();
            int line = 0, n = low.Length;
            for (int p = 0; p < n; p++)
            {
                char c = low[p];
                if (c == '\n') { line++; continue; }
                for (int s = 0; s < trigSingle.Count; s++) if (trigSingle[s].Key == c) cand[trigSingle[s].Value][line] = true;
                if (p + 1 >= n) break;
                List<KeyValuePair<string, int>> l = trigIndex[((c & 0xFF) << 8) | (low[p + 1] & 0xFF)];
                if (l == null) continue;
                foreach (KeyValuePair<string, int> kv in l)
                {
                    if (cand[kv.Value][line]) continue;
                    if (p + kv.Key.Length <= n && string.CompareOrdinal(low, p, kv.Key, 0, kv.Key.Length) == 0) cand[kv.Value][line] = true;
                }
            }
            return cand;
        }

        static bool Wanted(Rule r, bool[] cand, string line, int li) { return cand != null ? cand[li] : r.Pre(line); }

        // Diagnostics for the self-tests: per rule "name lines-accepted matches milliseconds" over a text.
        public static string[] RuleStats(string text)
        {
            string[] lines = text.Split('\n');
            System.Diagnostics.Stopwatch sw0 = System.Diagnostics.Stopwatch.StartNew();
            bool[][] cand = Candidates(text, lines.Length);
            List<string> outl = new List<string>();
            outl.Add("(trigger-scan) " + lines.Length + " 0 " + sw0.ElapsedMilliseconds);
            List<Rule> rs = Rules;
            for (int ri = 0; ri < rs.Count; ri++)
            {
                Rule r = rs[ri];
                System.Diagnostics.Stopwatch sw = System.Diagnostics.Stopwatch.StartNew();
                int acc = 0, mt = 0;
                for (int i = 0; i < lines.Length; i++)
                {
                    if (lines[i].Length == 0 || !Wanted(r, cand[ri], lines[i], i)) continue;
                    acc++;
                    mt += r.Rx.Matches(lines[i]).Count;
                }
                outl.Add(r.Name + " " + acc + " " + mt + " " + sw.ElapsedMilliseconds);
            }
            return outl.ToArray();
        }

        static List<Rule> BuildRules()
        {
            List<Rule> l = new List<Rule>();
            Rule r;
            r = R("getprop", @"^\[(?<k>[^\]\r\n]{1,200})\]: \[(?<v>[^\]\r\n]{1,1000})\]", null,
                  delegate(string v, Match m) { return PropCategory(m.Groups["k"].Value); });
            r.Pre = delegate(string line) { return line.Length > 6 && line[0] == '['; };
            l.Add(r);
            l.Add(R("account-object", @"Account \{name=(?<v>[^,}\r\n]{1,256}), type=", new string[] { "account {name=" }, AccountCat));
            l.Add(R("account-key", @"(?i)(?<![a-z0-9_])m?(?:account_?name|user_?name|login_?name)" + KV + VQ,
                    new string[] { "accountname", "account_name", "username", "user_name", "loginname", "login_name" }, AccountCat));
            l.Add(R("android-user", @"UserInfo\{\d+:(?<v>[^:}\r\n]{1,128}):", new string[] { "userinfo{" }, C("name")));
            l.Add(R("serial-key", @"(?i)(?<![a-z0-9])(?:m|i)?(?:serial[ _-]?(?:no|num|number)|serialno|serial_id|product_serial|board_serial|chassis_serial)(?![a-z0-9])(?:\.?[ \t]*\]?[ \t]*[:=][ \t]*\[?[ \t]*|[ \t]+)" + VQ,
                    new string[] { "serial" }, C("serial")));
            r = R("serial-bare", @"(?i)(?<![a-z0-9_.])""?serial""?(?:[ \t]*[:=][ \t]*|[ \t]+)(?:""(?<v>[^""\r\n]{6,128})""|(?<v>[A-Za-z0-9][A-Za-z0-9-]{5,127})(?![A-Za-z0-9-]))",
                  new string[] { "serial" }, C("serial"));
            r.Check = delegate(string v) { foreach (char ch in v) if (ch >= '0' && ch <= '9') return true; return false; };
            l.Add(r);
            l.Add(R("input-uniq", @"^U: Uniq=(?<v>[^\r\n]*?)[ \t\r]*$", new string[] { "u: uniq=" }, UniqCat));
            l.Add(R("unique-id", @"(?i)(?<![a-z0-9_])m?(?:unique_?id|uniq)" + KV + VQ, new string[] { "uniq" }, UniqCat));
            l.Add(R("input-descriptor", @"(?i)(?<![a-z0-9_])descriptor[ \t]*[:=][ \t]*'?(?<v>[0-9a-f]{40})(?![0-9a-z])", new string[] { "descriptor" }, C("id")));
            l.Add(R("ssid-key", @"(?i)(?<![a-z0-9])(?:m?ssid|wifi_?ssid|ssid_?name)" + KV + VQS, new string[] { "ssid" }, C("ssid")));
            l.Add(R("ssid-quoted", @"(?i)(?<![a-z0-9_])ssid[ \t]+(?:'(?<v>[^'\r\n]{1,96})'|""(?<v>[^""\r\n]{1,96})"")", new string[] { "ssid" }, C("ssid")));
            r = R("ssid-before-bssid", @"(?:""(?<v>[^""\r\n]{1,64})""|(?<=:[ \t]+|/[ \t]+)(?<v>[A-Za-z0-9][^\r\n:/"",;]{0,62}?))(?=[ \t]*[:(][ \t]*(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})", null, C("ssid"));
            r.Pre = delegate(string line) { return (line.IndexOf('"') >= 0 || line.IndexOf(':') >= 0) && HasMacLike(line); };
            l.Add(r);
            l.Add(R("ssid-configkey", @"(?:""(?<v>[^""\r\n]{1,64})""|%5C%22(?<v>[^\r\n]{1,96}?)%5C%22|%22(?<v>[^%\r\n]{1,96}?)%22)-?(?=(?:WPA_PSK|WPA_EAP|WPA2_PSK|NONE|WEP|SAE|OWE|IEEE8021X|SUITE_B_192|WAPI_PSK|WAPI_CERT|DPP|PASSPOINT)\b)",
                    new string[] { "\"wpa", "\"-wpa", "\"none", "\"-none", "\"wep", "\"-wep", "\"sae", "\"-sae", "\"owe", "\"-owe", "\"ieee8021x",
                                   "\"-ieee8021x", "\"suite_b", "\"-suite_b", "\"wapi", "\"-wapi", "\"dpp", "\"-dpp", "\"passpoint", "\"-passpoint", "%22wpa", "%22none", "%22sae", "%22owe" }, C("ssid")));
            l.Add(R("ssid-networkinfo", @"(?i)type: WIFI\b[^\r\n]{0,400}?extra: ""(?<v>[^""\r\n]{0,96})""", new string[] { "extra: \"" }, C("ssid")));
            l.Add(R("device-name", @"(?i)(?<![a-z0-9_])(?:m?(?:device_?name|bt_?name|bluetooth_?name|friendly_?name|remote_?name|adapter_?name|local_?name|hostname)|device[ \t]+name|stored[ \t]+bluetooth[ \t]+name)" + KV + VQL,
                    new string[] { "devicename", "device_name", "device name", "btname", "bt_name", "bluetoothname", "bluetooth_name", "bluetooth name", "friendlyname", "friendly_name",
                                   "remotename", "remote_name", "adaptername", "adapter_name", "localname", "local_name", "hostname" }, C("name")));
            l.Add(R("secret-key", @"(?i)(?<![a-z0-9_])m?(?:psk|password|passwd|passphrase|pre_?shared_?key|wep_?key[0-3]?|auth_?token|access_?token|refresh_?token|id_?token|client_?secret|api_?key|session_?key)" + KV + VQ,
                    new string[] { "psk", "passw", "passphrase", "preshared", "pre_shared", "wep_key", "wepkey", "token", "secret", "api_key", "apikey", "session_key", "sessionkey" }, C("secret")));
            l.Add(R("bearer-token", @"(?i)\bBearer[ \t]+(?<v>[A-Za-z0-9._~+/=-]{16,})", new string[] { "bearer" }, C("secret")));
            l.Add(R("google-token", @"(?<![A-Za-z0-9])(?<v>ya29\.[0-9A-Za-z_-]{20,})", new string[] { "ya29." }, C("secret")));
            l.Add(R("location", @"Location\[[A-Za-z_]+ (?<v>-?\d{1,3}\.\d{2,},-?\d{1,3}\.\d{2,})", new string[] { "location[" }, C("loc")));
            l.Add(R("lat-lon", @"(?i)(?<![a-z0-9_])(?:lat|latitude|lon|lng|longitude)""?[ \t]*[:=][ \t]*(?<v>-?\d{1,3}\.\d{3,})", new string[] { "lat", "lon", "lng" }, C("loc")));
            l.Add(R("imei-key", @"(?i)(?<![a-z0-9_])(?:imei(?:sv)?|meid|imsi|iccid|esn|msisdn|subscriber_?id)""?[ \t]*[:=]?[ \t]*[""'\[]?(?<v>[0-9A-Fa-f]{8,20})(?![0-9A-Za-z])",
                    new string[] { "imei", "meid", "imsi", "iccid", "esn", "msisdn", "subscriber" }, C("imei")));
            r = R("imei-luhn", @"(?<![0-9A-Za-z:=._/-])(?<v>\d{15})(?![0-9A-Za-z.])", null, C("imei"));
            r.Pre = delegate(string line) { return HasDigitRun(line, 15); };
            r.Check = delegate(string v) { return Array.IndexOf(ImeiPrefixes, v.Substring(0, 2)) >= 0 && Luhn(v); };
            l.Add(r);
            l.Add(R("android-id", @"(?i)(?<![a-z0-9_])(?:android_?id|ssaid|gsf_?id|advertising_?id|adid|(?:obfuscated_?)?gaia_?id)""?[ \t]*[:=][ \t]*[""']?(?<v>[0-9A-Fa-f][0-9A-Fa-f-]{7,63})(?![0-9A-Za-z])",
                    new string[] { "androidid", "android_id", "ssaid", "gsfid", "gsf_id", "advertising", "adid", "gaiaid", "gaia_id" }, C("id")));
            l.Add(R("android-hostname", @"(?<![A-Za-z0-9])android-(?<v>[0-9a-f]{16})(?![0-9A-Za-z])", new string[] { "android-" }, C("id")));
            // (review 2026-10-29) labels may start with a digit (163.com, 3m.com, 1und1.de) and %2540 is matched;
            // still not: HAL versions (x@4.0-impl.so), @2x assets and Java Object@hash.member
            l.Add(R("email", @"(?<![A-Za-z0-9._%+-])(?<v>[A-Za-z0-9._+-]{1,64}(?:@|%40|%2540)(?!(?:(?:com|org|net|android|androidx|java|javax|kotlin|dalvik|vendor)\.(?:android|androidx|google|java|javax|kotlin|dalvik|vendor|example\.)|android\.hardware\.))(?!\d+\.\d|\d+x\.|(?=[a-f]*\d)[0-9a-f]{7,8}\.[a-z])(?:[A-Za-z0-9][A-Za-z0-9-]{0,62}\.)+[A-Za-z]{2,24})(?![A-Za-z0-9-])",
                    new string[] { "@", "%40", "%2540" }, C("email")));
            r = R("mac", @"(?<![0-9A-Fa-f]|[0-9A-Fa-f][:-])(?<v>[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4}|[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4})(?![0-9A-Fa-f]|[:.-][0-9A-Fa-f])", null, C("mac"));
            r.Pre = delegate(string line) { return HasMacLike(line) || line.IndexOf('.') >= 0; };
            l.Add(r);
            l.Add(R("mac-key", @"(?i)(?<![a-z0-9_])(?:mac|mac_?addr(?:ess)?|hw_?addr|bssid|bd_?addr|bt_?addr|eth_?addr|wifi_?mac_?addr|bt_?mac_?addr)""?[ \t]*[:=][ \t]*[""']?(?<v>[0-9A-Fa-f]{12})(?![0-9A-Za-z])",
                    new string[] { "mac", "addr", "bssid" }, C("mac")));
            r = R("uuid", @"(?<![0-9A-Fa-f-])(?<v>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})(?![0-9A-Fa-f-])", null, C("uuid"));
            r.Pre = HasUuidLike;
            l.Add(r);
            r = R("ipv6", @"(?<![\w:.])(?<v>[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{0,4}){2,7})(?![\w:.])", null, C("ip6"));
            r.Pre = HasIpv6Like;
            l.Add(r);
            return l;
        }

        public static bool Luhn(string d)
        {
            int sum = 0; bool alt = false;
            for (int i = d.Length - 1; i >= 0; i--)
            {
                int n = d[i] - '0';
                if (n < 0 || n > 9) return false;
                if (alt) { n *= 2; if (n > 9) n -= 9; }
                sum += n; alt = !alt;
            }
            return sum % 10 == 0;
        }

        public static bool IsSensitiveIPv6(string v)
        {
            IPAddress ip;
            if (!IPAddress.TryParse(v, out ip)) return false;
            if (ip.AddressFamily != AddressFamily.InterNetworkV6) return false;
            byte[] b = ip.GetAddressBytes();
            if ((b[0] & 0xE0) == 0x20) return true;            // global unicast 2000::/3
            if (b[0] == 0xFC || b[0] == 0xFD) return true;      // unique local (site prefix)
            if (b[11] == 0xFF && b[12] == 0xFE) return true;    // EUI-64 interface id: embeds a MAC
            return false;
        }

        public static string Normalize(string category, string value)
        {
            string v = value.Trim();
            switch (category)
            {
                case "mac":
                {
                    StringBuilder h = new StringBuilder();
                    foreach (char c in v) if (Uri.IsHexDigit(c)) h.Append(char.ToLowerInvariant(c));
                    if (h.Length != 12) return v.ToLowerInvariant();
                    StringBuilder s = new StringBuilder();
                    for (int i = 0; i < 12; i += 2) { if (i > 0) s.Append(':'); s.Append(h[i]).Append(h[i + 1]); }
                    return s.ToString();
                }
                case "uuid":
                    return v.ToLowerInvariant();
                case "email":
                    return v.Replace("%40", "@").Replace("%2540", "@").ToLowerInvariant();
                case "ssid":
                    return v.Replace("%20", " ").Replace("+", " ");
                case "ip6":
                {
                    IPAddress ip;
                    if (IPAddress.TryParse(v, out ip)) return ip.ToString().ToLowerInvariant();
                    return v.ToLowerInvariant();
                }
                default:
                    return v;
            }
        }

        // Is this captured value personal (not a placeholder, a public constant or one of our own placeholders)?
        public static bool IsSensitive(string category, string value)
        {
            if (value == null) return false;
            string v = value.Trim();
            if (v.Length == 0 || Placeholders.ContainsKey(v)) return false;
            if (TokenRe.IsMatch(v) || TokenCoreRe.IsMatch(v)) return false;
            switch (category)
            {
                case "mac":
                {
                    string n = Normalize("mac", v);
                    if (!MacNorm.IsMatch(n)) return false;
                    // all zero, broadcast, Android's fixed "MAC hidden from apps", group (multicast) addresses
                    if (n == "00:00:00:00:00:00" || n == "ff:ff:ff:ff:ff:ff" || n == "02:00:00:00:00:00") return false;
                    if ((Convert.ToInt32(n.Substring(0, 2), 16) & 1) != 0) return false;
                    return true;
                }
                case "uuid":
                {
                    string n = v.ToLowerInvariant();
                    if (!UuidShape.IsMatch(n)) return v.Length >= 8;   // other ids from uuid-named properties (a FAT volume id)
                    if (PublicUuids.ContainsKey(n)) return false;
                    if (n.EndsWith("-0000-1000-8000-00805f9b34fb", StringComparison.Ordinal)) return false;   // Bluetooth SIG base
                    if (n.EndsWith("-0002a5d5c51b", StringComparison.Ordinal)) return false;                  // AOSP audio effects
                    return true;
                }
                case "serial":
                    if (v.Length < 4) return false;
                    if (PciAddr.IsMatch(v) || JavaRef.IsMatch(v) || VersionLike.IsMatch(v) || SameChar.IsMatch(v)) return false;
                    foreach (char ch in v) if (char.IsLetterOrDigit(ch)) return true;
                    return false;
                case "email":
                {
                    string ne = Normalize("email", v);
                    int at = ne.LastIndexOf('@');
                    if (at < 1) return false;
                    return !PublicEmailDomains.ContainsKey(ne.Substring(at + 1));
                }
                case "name":
                case "account":
                    if (NamePlaceholders.ContainsKey(v)) return false;
                    return !v.StartsWith("/", StringComparison.Ordinal);
                case "secret":
                case "imei":
                    return !SameChar.IsMatch(v);
                case "ip6":
                    return IsSensitiveIPv6(v);
                default:
                    return true;
            }
        }

        // "none" | "bounded" (not next to a letter/digit) | "unbounded": is the value specific enough to be replaced
        // wherever it occurs? Plain words, short numbers and package/host names stay in their contexts only.
        public static string PropagationMode(string category, string v)
        {
            if (Array.IndexOf(Propagated, category) < 0) return "none";
            if (v.Length < 6) return "none";
            bool digit = false, letter = false, other = false, allDigits = true;
            foreach (char c in v)
            {
                if (c >= '0' && c <= '9') digit = true;
                else { allDigits = false; if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')) letter = true; else if (c != '.') other = true; }
            }
            if (allDigits && v.Length < 10) return "none";
            if (category != "email" && Dotted.IsMatch(v)) return "none";
            // (review 2026-10-29) a letters-only SSID of 6+ characters is replaced everywhere too: Android also logs
            // the bare SSID (WifiConfigManager "Adding/Updating network <ssid>"), outside every context rule
            if (!digit && !other) return category == "ssid" ? "bounded" : "none";
            if (v.Length >= 12 && letter && digit) return "unbounded";
            return "bounded";
        }

        readonly Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
        readonly Dictionary<string, RedactEntry> entries = new Dictionary<string, RedactEntry>(StringComparer.Ordinal);
        readonly Dictionary<string, int> counters = new Dictionary<string, int>(StringComparer.Ordinal);
        readonly Dictionary<string, int> hits = new Dictionary<string, int>(StringComparer.Ordinal);
        readonly string[] terms;
        Dictionary<string, string> bareHex, literal;
        Regex literalRx, termRx;
        Rule curRule;
        bool replaced;
        int spread;
        readonly MatchEvaluator evaluator;

        public RedactorV4(string[] extraTerms)
        {
            List<string> t = new List<string>();
            Dictionary<string, bool> seen = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            if (extraTerms != null)
                foreach (string s in extraTerms)
                {
                    if (s == null) continue;
                    string x = s.Trim();
                    if (x.Length >= 2 && !seen.ContainsKey(x)) { seen[x] = true; t.Add(x); }
                }
            t.Sort(delegate(string a, string b) { return b.Length.CompareTo(a.Length); });
            terms = t.ToArray();
            evaluator = new MatchEvaluator(EvaluateCurrent);
        }

        public string[] Terms { get { return (string[])terms.Clone(); } }
        public int CountOf(string cat) { int n; return counters.TryGetValue(cat, out n) ? n : 0; }
        public int HitsOf(string cat) { int n; return hits.TryGetValue(cat, out n) ? n : 0; }
        public int TotalHits { get { int s = 0; foreach (int v in hits.Values) s += v; return s; } }

        public RedactEntry[] GetEntries()
        {
            List<RedactEntry> l = new List<RedactEntry>(entries.Values);
            l.Sort(delegate(RedactEntry a, RedactEntry b)
            {
                int c = string.CompareOrdinal(a.Category, b.Category);
                return c != 0 ? c : TokenNumber(a.Token).CompareTo(TokenNumber(b.Token));
            });
            return l.ToArray();
        }
        static int TokenNumber(string tok) { int d = tok.LastIndexOf('-'); return int.Parse(tok.Substring(d + 1, tok.Length - d - 2)); }
        static string CatOf(string tok) { return tok.Substring(1, tok.IndexOf('-') - 1); }
        void AddHit(string cat) { int n; hits.TryGetValue(cat, out n); hits[cat] = n + 1; }

        string TokenFor(string cat, string raw)
        {
            string norm = Normalize(cat, raw);
            string key = cat + "\u0000" + norm;
            string tok;
            if (!map.TryGetValue(key, out tok))
            {
                int n; counters.TryGetValue(cat, out n); n++; counters[cat] = n;
                tok = "<" + cat + "-" + n + ">";
                map[key] = tok;
                RedactEntry e = new RedactEntry(); e.Token = tok; e.Category = cat; e.Value = norm;
                entries[tok] = e;
            }
            RedactEntry en = entries[tok];
            string r = raw.Trim();
            if (!en.Raws.Contains(r)) en.Raws.Add(r);
            AddHit(cat);
            return tok;
        }

        static string Categorize(Rule r, Match m)
        {
            Group g = m.Groups["v"];
            if (!g.Success) return null;
            string cat = r.Cat(g.Value, m);
            if (cat == null) return null;
            if (r.Check != null && !r.Check(g.Value)) return null;
            if (!IsSensitive(cat, g.Value)) return null;
            return cat;
        }

        string EvaluateCurrent(Match m)
        {
            string cat = Categorize(curRule, m);
            if (cat == null) return m.Value;
            Group g = m.Groups["v"];
            string tok = TokenFor(cat, g.Value);
            replaced = true;
            int off = g.Index - m.Index;
            return m.Value.Substring(0, off) + tok + m.Value.Substring(off + g.Length);
        }

        // Pass A: the context rules, line by line, in order. Line ends and all other bytes are kept as they are.
        // (Trigger words are looked up in the original text; a placeholder never contains a personal value, so a
        // replacement made by an earlier rule can only make a later rule's work unnecessary, never necessary.)
        public string RedactText(string text)
        {
            string[] lines = text.Split('\n');
            bool[][] cand = Candidates(text, lines.Length);
            List<Rule> rs = Rules;
            for (int li = 0; li < lines.Length; li++)
            {
                string line = lines[li];
                if (line.Length == 0) continue;
                for (int ri = 0; ri < rs.Count; ri++)
                {
                    Rule r = rs[ri];
                    if (!Wanted(r, cand[ri], line, li)) continue;
                    curRule = r; replaced = false;
                    string next = r.Rx.Replace(line, evaluator);
                    if (replaced) line = next;
                }
                lines[li] = line;
            }
            return string.Join("\n", lines);
        }

        // After pass A over every file: values found anywhere are replaced everywhere (pass B).
        public void CompletePropagation()
        {
            bareHex = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            literal = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            Dictionary<string, string> mode = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (RedactEntry e in entries.Values)
            {
                if (e.Category == "mac") { bareHex[e.Value.Replace(":", "")] = e.Token; continue; }
                if (e.Category == "uuid") { if (UuidShape.IsMatch(e.Value)) bareHex[e.Value.Replace("-", "")] = e.Token; continue; }
                List<string> vals = new List<string>(e.Raws);
                vals.Add(e.Value);
                foreach (string v in vals)
                {
                    string md = PropagationMode(e.Category, v);
                    if (md == "none") continue;
                    literal[v] = e.Token; mode[v] = md;
                }
            }
            RegexOptions opt = RegexOptions.IgnoreCase | RegexOptions.CultureInvariant;
            literalRx = null;
            if (literal.Count > 0)
            {
                List<string> keys = new List<string>(literal.Keys);
                keys.Sort(delegate(string a, string b) { return b.Length.CompareTo(a.Length); });
                StringBuilder sb = new StringBuilder(TokenAlt);   // our placeholders first: never replace inside one
                foreach (string k in keys)
                {
                    sb.Append('|');
                    string esc = Regex.Escape(k);
                    if (mode[k] == "unbounded") sb.Append(esc);
                    else sb.Append("(?<![A-Za-z0-9])").Append(esc).Append("(?![A-Za-z0-9])");
                }
                literalRx = new Regex(sb.ToString(), opt);
            }
            termRx = null;
            if (terms.Length > 0)
            {
                StringBuilder tb = new StringBuilder(TokenAlt);
                foreach (string t in terms) tb.Append('|').Append(Regex.Escape(t));
                termRx = new Regex(tb.ToString(), opt);
            }
        }

        public int LiteralCount { get { return literal == null ? 0 : literal.Count; } }

        string EvalBare(Match m)
        {
            string tok;
            if (bareHex.TryGetValue(m.Value, out tok)) { spread++; AddHit(CatOf(tok)); return tok; }
            return m.Value;
        }
        string EvalLiteral(Match m)
        {
            if (m.Groups["tok"].Success) return m.Value;
            string tok;
            if (literal.TryGetValue(m.Value, out tok)) { spread++; AddHit(CatOf(tok)); return tok; }
            return m.Value;
        }
        string EvalTerm(Match m)
        {
            if (m.Groups["tok"].Success) return m.Value;
            for (int i = 0; i < terms.Length; i++)
                if (string.Equals(terms[i], m.Value, StringComparison.OrdinalIgnoreCase)) { spread++; AddHit("term"); return "<term-" + (i + 1) + ">"; }
            return m.Value;
        }

        // Pass B. count = replacements made in this text.
        public string PropagateText(string text, out int count)
        {
            if (bareHex == null) throw new InvalidOperationException("CompletePropagation() has not run");
            spread = 0;
            if (bareHex.Count > 0) text = BareHexRx.Replace(text, new MatchEvaluator(EvalBare));
            if (literalRx != null) text = literalRx.Replace(text, new MatchEvaluator(EvalLiteral));
            if (termRx != null) text = termRx.Replace(text, new MatchEvaluator(EvalTerm));
            count = spread;
            return text;
        }

        static RedactResidue Res(string file, int line, string check, string cat)
        {
            RedactResidue r = new RedactResidue(); r.File = file; r.Line = line; r.Check = check; r.Category = cat; return r;
        }

        // Pass C: does anything still look personal? (where and what kind only, never the value)
        public RedactResidue[] FindResidue(string text, string file)
        {
            List<RedactResidue> res = new List<RedactResidue>();
            string[] lines = text.Split('\n');
            bool[][] cand = Candidates(text, lines.Length);
            List<Rule> rs = Rules;
            for (int li = 0; li < lines.Length && res.Count < 1000; li++)
            {
                string line = lines[li];
                int lineNo = li + 1;
                if (line.Length > 0)
                {
                    for (int ri = 0; ri < rs.Count; ri++)
                    {
                        Rule r = rs[ri];
                        if (!Wanted(r, cand[ri], line, li)) continue;
                        foreach (Match m in r.Rx.Matches(line)) { string cat = Categorize(r, m); if (cat != null) res.Add(Res(file, lineNo, r.Name, cat)); }
                    }
                    string tok;
                    if (bareHex != null && bareHex.Count > 0)
                        foreach (Match m in BareHexRx.Matches(line)) if (bareHex.TryGetValue(m.Value, out tok)) res.Add(Res(file, lineNo, "known value (hex)", CatOf(tok)));
                    if (literalRx != null)
                        foreach (Match m in literalRx.Matches(line))
                            if (!m.Groups["tok"].Success) res.Add(Res(file, lineNo, "known value", literal.TryGetValue(m.Value, out tok) ? CatOf(tok) : "literal"));
                    if (termRx != null)
                        foreach (Match m in termRx.Matches(line)) if (!m.Groups["tok"].Success) res.Add(Res(file, lineNo, "known value", "term"));
                }
            }
            return res.ToArray();
        }
    }
}
'@

function Initialize-GbNative {
    $haveNative = [bool]('GbLogs.NativeV4' -as [type])
    $haveRedactor = [bool]('GbLogs.RedactorV4' -as [type])
    if ($haveNative -and $haveRedactor) { return }
    if ($haveNative -or $haveRedactor) { throw 'another version of this script was loaded in this PowerShell window; open a new window and run it again' }
    if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {
        throw "PowerShell runs in $($ExecutionContext.SessionState.LanguageMode) mode on this PC (a device policy); this script needs FullLanguage."
    }
    Add-Type -TypeDefinition $script:GbNativeSource -Language CSharp -ErrorAction Stop
}

# ---------------------------------------------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------------------------------------------
function Write-GbMsg {
    param([string]$Text = '', [string]$Color)
    if ($script:GbQuiet) { return }
    if ($Color) { Write-Host $Text -ForegroundColor $Color } else { Write-Host $Text }
}

function Join-GbPath([string]$A, [string]$B) {
    if ($A.EndsWith('\')) { return $A + $B }
    return $A + '\' + $B
}

function Get-GbWin32Code($ErrorRecord) {
    $e = $ErrorRecord
    if ($e -is [System.Management.Automation.ErrorRecord]) { $e = $e.Exception }
    while ($e) {
        if ($e -is [System.ComponentModel.Win32Exception]) { return $e.NativeErrorCode }
        $e = $e.InnerException
    }
    return $null
}

function Get-GbErrText($ErrorRecord) {
    $e = $ErrorRecord
    if ($e -is [System.Management.Automation.ErrorRecord]) { $e = $e.Exception }
    while (($e -is [System.Management.Automation.MethodInvocationException] -or
            $e -is [System.Reflection.TargetInvocationException]) -and $e.InnerException) { $e = $e.InnerException }
    return $e.Message
}

function Format-GbSize($Bytes) {
    if ($null -eq $Bytes) { return '?' }
    $b = [double]$Bytes
    if ($b -ge 1GB) { return ('{0:N1} GB' -f ($b / 1GB)) }
    if ($b -ge 1MB) { return ('{0:N1} MB' -f ($b / 1MB)) }
    if ($b -ge 1KB) { return ('{0:N0} KB' -f ($b / 1KB)) }
    return ('{0} B' -f $b)
}

function Test-GbElevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# The self-tests set GB_LOGS_BLOCK_REAL_DISKS=1: every function that touches real disks, volumes or mount points
# then throws instead (the tests replace those functions with mocks).
function Assert-GbRealDiskAllowed {
    if ($env:GB_LOGS_BLOCK_REAL_DISKS -eq '1') { throw 'real disk access is blocked (GB_LOGS_BLOCK_REAL_DISKS=1, test mode)' }
}

# ---------------------------------------------------------------------------------------------------------------
# Parsers for the IOCTL output buffers (layouts from winioctl.h; unit-tested with synthetic buffers)
# ---------------------------------------------------------------------------------------------------------------
$script:GbBusTypes = @{
    0 = 'Unknown'; 1 = 'SCSI'; 2 = 'ATAPI'; 3 = 'ATA'; 4 = '1394'; 5 = 'SSA'; 6 = 'Fibre'; 7 = 'USB'; 8 = 'RAID';
    9 = 'iSCSI'; 10 = 'SAS'; 11 = 'SATA'; 12 = 'SD'; 13 = 'MMC'; 14 = 'Virtual'; 15 = 'FileBackedVirtual';
    16 = 'Spaces'; 17 = 'NVMe'; 18 = 'SCM'; 19 = 'UFS'; 20 = 'NVMeoF'
}

# DRIVE_LAYOUT_INFORMATION_EX: PartitionStyle u32 @0, PartitionCount u32 @4, union (40 bytes) @8, entries @48.
# PARTITION_INFORMATION_EX (144 bytes): style u32 @0, StartingOffset i64 @8, PartitionLength i64 @16,
# PartitionNumber u32 @24, GPT union @32: PartitionType GUID @32, PartitionId GUID @48, Attributes u64 @64,
# Name WCHAR[36] @72.
function ConvertFrom-GbDriveLayout([byte[]]$Buffer) {
    if ($null -eq $Buffer -or $Buffer.Length -lt 48) { throw "drive layout buffer too short ($(@($Buffer).Count) bytes)" }
    $style = [BitConverter]::ToUInt32($Buffer, 0)
    $count = [BitConverter]::ToUInt32($Buffer, 4)
    $styleName = switch ($style) { 0 { 'MBR' } 1 { 'GPT' } 2 { 'RAW' } default { "style$style" } }
    $parts = New-Object System.Collections.Generic.List[object]
    if ($style -eq 1) {
        if ($count -gt 1024) { throw "implausible partition count $count" }
        for ($i = 0; $i -lt $count; $i++) {
            $o = 48 + 144 * $i
            if ($o + 144 -gt $Buffer.Length) { throw "drive layout buffer truncated at entry $i" }
            if ([BitConverter]::ToUInt32($Buffer, $o) -ne 1) { continue }
            $tb = New-Object byte[] 16; [Array]::Copy($Buffer, $o + 32, $tb, 0, 16)
            $ib = New-Object byte[] 16; [Array]::Copy($Buffer, $o + 48, $ib, 0, 16)
            $type = New-Object Guid (, $tb)
            if ($type -eq [Guid]::Empty) { continue }
            $name = [Text.Encoding]::Unicode.GetString($Buffer, $o + 72, 72)
            $nul = $name.IndexOf([char]0)
            if ($nul -ge 0) { $name = $name.Substring(0, $nul) }
            $parts.Add([pscustomobject]@{
                Number     = [int][BitConverter]::ToUInt32($Buffer, $o + 24)
                Offset     = [BitConverter]::ToInt64($Buffer, $o + 8)
                Length     = [BitConverter]::ToInt64($Buffer, $o + 16)
                TypeGuid   = $type
                Guid       = (New-Object Guid (, $ib))
                Attributes = [BitConverter]::ToUInt64($Buffer, $o + 64)
                Name       = $name
            })
        }
    }
    return [pscustomobject]@{ Style = $styleName; Partitions = $parts.ToArray() }
}

# STORAGE_DEVICE_DESCRIPTOR: RemovableMedia u8 @10, VendorIdOffset u32 @12, ProductIdOffset u32 @16,
# BusType u32 @28. The serial number (SerialNumberOffset @24) is deliberately never read.
function ConvertFrom-GbStorageDescriptor([byte[]]$Buffer) {
    if ($null -eq $Buffer -or $Buffer.Length -lt 32) { throw "storage descriptor buffer too short ($(@($Buffer).Count) bytes)" }
    $bus = [int][BitConverter]::ToUInt32($Buffer, 28)
    $str = {
        param([int]$off)
        if ($off -le 0 -or $off -ge $Buffer.Length) { return '' }
        $end = $off
        while ($end -lt $Buffer.Length -and $Buffer[$end] -ne 0) { $end++ }
        return ([Text.Encoding]::ASCII.GetString($Buffer, $off, $end - $off) -replace '[^\x20-\x7e]', '').Trim()
    }
    $busName = $script:GbBusTypes[$bus]
    if (-not $busName) { $busName = "bus$bus" }
    return [pscustomobject]@{
        BusType     = $bus
        BusTypeName = $busName
        Removable   = ($Buffer[10] -ne 0)
        Vendor      = (& $str ([int][BitConverter]::ToUInt32($Buffer, 12)))
        Product     = (& $str ([int][BitConverter]::ToUInt32($Buffer, 16)))
    }
}

# DISK_GEOMETRY_EX: DISK_GEOMETRY (24 bytes) then DiskSize i64 @24.
function ConvertFrom-GbDriveGeometry([byte[]]$Buffer) {
    if ($null -eq $Buffer -or $Buffer.Length -lt 32) { throw 'geometry buffer too short' }
    return [BitConverter]::ToInt64($Buffer, 24)
}

# VOLUME_DISK_EXTENTS: NumberOfDiskExtents u32 @0, extents @8 (24 bytes each: DiskNumber u32 @0,
# StartingOffset i64 @8, ExtentLength i64 @16).
function ConvertFrom-GbDiskExtents([byte[]]$Buffer) {
    if ($null -eq $Buffer -or $Buffer.Length -lt 8) { throw 'extents buffer too short' }
    $n = [BitConverter]::ToUInt32($Buffer, 0)
    if ($n -gt 256) { throw "implausible extent count $n" }
    $out = @()
    for ($i = 0; $i -lt $n; $i++) {
        $o = 8 + 24 * $i
        if ($o + 24 -gt $Buffer.Length) { throw 'extents buffer truncated' }
        $out += [pscustomobject]@{
            Disk   = [int][BitConverter]::ToUInt32($Buffer, $o)
            Offset = [BitConverter]::ToInt64($Buffer, $o + 8)
            Length = [BitConverter]::ToInt64($Buffer, $o + 16)
        }
    }
    return $out
}

# ---------------------------------------------------------------------------------------------------------------
# Inventory (real hardware; the self-tests replace these functions)
# ---------------------------------------------------------------------------------------------------------------
function Get-GbDiskInventory {
    Assert-GbRealDiskAllowed
    $disks = @()
    for ($n = 0; $n -lt 64; $n++) {
        $path = '\\.\PhysicalDrive' + $n
        $err = [GbLogs.NativeV4]::ProbeDevice($path)
        if ($err -eq 2 -or $err -eq 3) { continue }
        $d = [ordered]@{ Number = $n; BusType = -1; BusTypeName = '?'; Removable = $false; Vendor = ''; Product = '';
                         Size = $null; PartitionStyle = '?'; Partitions = @(); Error = $null }
        if ($err -ne 0) { $d.Error = "cannot open (Win32 error $err)"; $disks += [pscustomobject]$d; continue }
        try {
            $s = ConvertFrom-GbStorageDescriptor ([GbLogs.NativeV4]::GetStorageDescriptorRaw($n))
            $d.BusType = $s.BusType; $d.BusTypeName = $s.BusTypeName; $d.Removable = $s.Removable
            $d.Vendor = $s.Vendor; $d.Product = $s.Product
        } catch { $d.Error = 'bus type unknown: ' + (Get-GbErrText $_) }
        try { $d.Size = ConvertFrom-GbDriveGeometry ([GbLogs.NativeV4]::GetDriveGeometryRaw($n)) } catch { }
        try {
            $l = ConvertFrom-GbDriveLayout ([GbLogs.NativeV4]::GetDriveLayoutRaw($n))
            $d.PartitionStyle = $l.Style; $d.Partitions = $l.Partitions
        } catch {
            $m = 'partition table unreadable: ' + (Get-GbErrText $_)
            if ($d.Error) { $d.Error += '; ' + $m } else { $d.Error = $m }
        }
        $disks += [pscustomobject]$d
    }
    return $disks
}

# Disks that hold the running Windows (refused even if they are USB, e.g. Windows To Go).
function Get-GbSystemDiskNumbers {
    Assert-GbRealDiskAllowed
    $nums = New-Object System.Collections.Generic.List[int]
    $roots = @($env:SystemDrive, [IO.Path]::GetPathRoot([string]$env:windir)) | Where-Object { $_ } |
             ForEach-Object { $_.TrimEnd('\') } | Select-Object -Unique
    foreach ($r in $roots) {
        try {
            foreach ($e in (ConvertFrom-GbDiskExtents ([GbLogs.NativeV4]::GetVolumeExtentsRaw('\\.\' + $r)))) {
                if (-not $nums.Contains($e.Disk)) { $nums.Add($e.Disk) }
            }
        } catch { Write-GbMsg ("  note: cannot map {0} to a disk ({1})" -f $r, (Get-GbErrText $_)) 'DarkYellow' }
    }
    try {   # the Storage module, when it answers for this user: boot and system disks too
        foreach ($d in @(Get-Disk -ErrorAction Stop)) {
            if (($d.IsBoot -or $d.IsSystem) -and -not $nums.Contains([int]$d.Number)) { $nums.Add([int]$d.Number) }
        }
    } catch { }
    return $nums.ToArray()
}

function Get-GbVolumeInventory {
    Assert-GbRealDiskAllowed
    $out = @()
    foreach ($v in [GbLogs.NativeV4]::ListVolumes()) {
        $ext = @(); $err = $null; $mp = @()
        try { $ext = @(ConvertFrom-GbDiskExtents ([GbLogs.NativeV4]::GetVolumeExtentsRaw($v.TrimEnd('\')))) } catch { $err = Get-GbErrText $_ }
        try { $mp = @([GbLogs.NativeV4]::GetMountPoints($v)) } catch { }
        $out += [pscustomobject]@{ VolumeName = $v; Extents = $ext; MountPoints = $mp; Error = $err }
    }
    return $out
}

# Get-Partition's AccessPaths (Storage module), used only when the volume list has no match.
function Get-GbPartitionAccessPaths([int]$DiskNumber, [int]$PartitionNumber) {
    Assert-GbRealDiskAllowed
    try { return @((Get-Partition -DiskNumber $DiskNumber -PartitionNumber $PartitionNumber -ErrorAction Stop).AccessPaths) }
    catch { return @() }
}

function Get-GbLogicalDriveMask { return [GbLogs.NativeV4]::LogicalDrivesMask() }

function Mount-GbTempLetter {
    param([string]$Letter, $Selection, $Volume)
    Assert-GbRealDiskAllowed
    if ($Volume) {
        [GbLogs.NativeV4]::MountVolume($Letter + '\', $Volume.VolumeName)
        return 'mountpoint'
    }
    Add-PartitionAccessPath -DiskNumber $Selection.Disk.Number -PartitionNumber $Selection.Partition.Number -AccessPath ($Letter + '\') -ErrorAction Stop
    return 'partition-access-path'
}

function Dismount-GbTempLetter {
    param([string]$Letter, $Selection, [string]$Kind)
    Assert-GbRealDiskAllowed
    if ($Kind -eq 'mountpoint') { [GbLogs.NativeV4]::UnmountVolume($Letter + '\'); return }
    Remove-PartitionAccessPath -DiskNumber $Selection.Disk.Number -PartitionNumber $Selection.Partition.Number -AccessPath ($Letter + '\') -ErrorAction Stop
}

function Test-GbLetterMapsTo {
    param([string]$Letter, [int]$DiskNumber, [long]$Offset)
    Assert-GbRealDiskAllowed
    try {
        $e = @(ConvertFrom-GbDiskExtents ([GbLogs.NativeV4]::GetVolumeExtentsRaw('\\.\' + $Letter)))
        return ($e.Count -eq 1 -and $e[0].Disk -eq $DiskNumber -and $e[0].Offset -eq $Offset)
    } catch { return $false }
}

# ---------------------------------------------------------------------------------------------------------------
# Selection logic (pure; unit-tested with mock inventories)
# ---------------------------------------------------------------------------------------------------------------
$script:GbEspName = 'EFI-SYSTEM'
$script:GbRequiredNames = @('super', 'boot_a')

function Test-GbBusAllowed($Disk) {
    if ($Disk.BusType -eq 7) { return $true }                                  # USB (sticks, USB SSDs)
    if (($Disk.BusType -eq 12 -or $Disk.BusType -eq 13) -and $Disk.Removable) { return $true }   # SD / MMC cards
    return $false
}

function Select-GbStick {
    param($Disks, [int[]]$SystemDiskNumbers = @(), [int]$DiskNumber = -1)
    $rows = @()
    foreach ($d in @($Disks)) {
        $names = @(@($d.Partitions) | ForEach-Object { [string]$_.Name })
        $esp = @(@($d.Partitions) | Where-Object { [string]::Equals([string]$_.Name, $script:GbEspName, [StringComparison]::Ordinal) })
        $haveReq = @($script:GbRequiredNames | Where-Object { $names -ccontains $_ })
        $looksGb = ($esp.Count -ge 1 -and $haveReq.Count -eq $script:GbRequiredNames.Count)
        $verdict = 'skip'; $reason = ''
        if ($d.Error -and -not $looksGb) { $reason = $d.Error }
        elseif ($d.PartitionStyle -ne 'GPT') { $reason = "not GPT ($($d.PartitionStyle))" }
        elseif (-not $looksGb) {
            $miss = @(@($script:GbEspName) + $script:GbRequiredNames | Where-Object { $names -cnotcontains $_ })
            $reason = 'no Googlebook layout (missing ' + ($miss -join ', ') + ')'
        }
        elseif (@($SystemDiskNumbers) -contains [int]$d.Number) { $verdict = 'refused'; $reason = 'holds the running Windows' }
        elseif (-not (Test-GbBusAllowed $d)) {
            $verdict = 'refused'
            $reason = 'internal or non-USB disk (bus ' + $d.BusTypeName + $(if ($d.Removable) { ', removable media' } else { '' }) + ')'
        }
        elseif ($esp.Count -gt 1) { $verdict = 'refused'; $reason = "$($esp.Count) partitions named $($script:GbEspName) (ambiguous)" }
        else { $verdict = 'candidate'; $reason = 'EFI-SYSTEM + super + boot_a on ' + $d.BusTypeName }
        $rows += [pscustomobject]@{ Disk = $d; Verdict = $verdict; Reason = $reason; Esp = $(if ($esp.Count -eq 1) { $esp[0] } else { $null }) }
    }
    $cands = @($rows | Where-Object { $_.Verdict -eq 'candidate' })
    $sel = $null; $err = $null
    if ($DiskNumber -ge 0) {
        $r = @($rows | Where-Object { [int]$_.Disk.Number -eq $DiskNumber })
        if ($r.Count -eq 0) { $err = "disk $DiskNumber does not exist" }
        elseif ($r[0].Verdict -ne 'candidate') { $err = "disk $DiskNumber is not an eligible Googlebook stick: $($r[0].Reason)" }
        else { $sel = $r[0] }
    }
    elseif ($cands.Count -eq 0) {
        $ref = @($rows | Where-Object { $_.Verdict -eq 'refused' })
        if ($ref.Count) { $err = 'no eligible Googlebook stick; refused: ' + (($ref | ForEach-Object { "disk $($_.Disk.Number) ($($_.Reason))" }) -join '; ') }
        else { $err = 'no Googlebook stick found (a USB disk with GPT partitions EFI-SYSTEM, super and boot_a)' }
    }
    elseif ($cands.Count -gt 1) {
        $err = 'more than one Googlebook stick is plugged in (disks ' + (($cands | ForEach-Object { $_.Disk.Number }) -join ', ') +
               '); unplug the others or pass -DiskNumber <n>'
    }
    else { $sel = $cands[0] }
    $result = [pscustomobject]@{ Rows = $rows; Error = $err; Disk = $null; Partition = $null }
    if ($sel) { $result.Disk = $sel.Disk; $result.Partition = $sel.Esp }
    return $result
}

function Show-GbCandidateTable($Selection) {
    Write-GbMsg 'Disks:'
    foreach ($r in @($Selection.Rows)) {
        $d = $r.Disk
        $model = (($d.Vendor, $d.Product) | Where-Object { $_ }) -join ' '
        if (-not $model) { $model = '-' }
        $rem = if ($d.Removable) { 'removable' } else { 'fixed' }
        $line = '  Disk {0,-2} {1,-8} {2,-9} {3,9}  {4,-28} {5,-9} {6}' -f $d.Number, $d.BusTypeName, $rem, (Format-GbSize $d.Size),
                $(if ($model.Length -gt 28) { $model.Substring(0, 28) } else { $model }), $r.Verdict, $r.Reason
        $color = switch ($r.Verdict) { 'candidate' { 'Green' } 'refused' { 'Yellow' } default { 'Gray' } }
        Write-GbMsg $line $color
    }
}

# The volume whose single extent starts at the partition's offset on that disk.
function Resolve-GbVolumeForPartition {
    param([int]$DiskNumber, [long]$Offset, $Volumes)
    $m = @(@($Volumes) | Where-Object { @($_.Extents).Count -eq 1 -and [int]@($_.Extents)[0].Disk -eq $DiskNumber -and [long]@($_.Extents)[0].Offset -eq $Offset })
    if ($m.Count -gt 1) { throw "$($m.Count) volumes map to disk $DiskNumber at offset $Offset (ambiguous)" }
    if ($m.Count -eq 1) { return $m[0] }
    return $null
}

function Get-GbFreeDriveLetter {
    $mask = [uint32](Get-GbLogicalDriveMask)
    foreach ($i in 25..3) {   # Z: down to D:
        if (($mask -band ([uint32]1 -shl $i)) -eq 0) { return ([string][char](65 + $i)) + ':' }
    }
    return $null
}

function Test-GbLetterInUse([string]$Letter) {
    $i = [int][char]$Letter.ToUpperInvariant()[0] - 65
    return (([uint32](Get-GbLogicalDriveMask)) -band ([uint32]1 -shl $i)) -ne 0
}

# Is <Root>\gblog a readable folder? Code: 0 ok, 2/3 missing, 5 access denied, other Win32 errors.
function Test-GbGblogDir([string]$Root) {
    $dir = Join-GbPath $Root 'gblog'
    $err = 0
    $a = [GbLogs.NativeV4]::GetAttributes($dir, [ref]$err)
    if ($a -lt 0) {
        return [pscustomobject]@{ Ok = $false; Code = $err; Dir = $dir; Message = (New-Object ComponentModel.Win32Exception($err)).Message }
    }
    if (($a -band 0x10) -eq 0) { return [pscustomobject]@{ Ok = $false; Code = 267; Dir = $dir; Message = 'gblog is not a folder' } }
    try {
        [void][GbLogs.NativeV4]::ListDirectory($dir)
        return [pscustomobject]@{ Ok = $true; Code = 0; Dir = $dir; Message = $null }
    } catch {
        return [pscustomobject]@{ Ok = $false; Code = (Get-GbWin32Code $_); Dir = $dir; Message = (Get-GbErrText $_) }
    }
}

# Runs $Action with the root of the EFI-SYSTEM file system: the volume path first (no drive letter); as
# administrator, a temporary drive letter as the fallback, always removed again.
# Status: ok | no-gblog | needs-admin | error.
function Invoke-GbEspAccess {
    param($Selection, $Volume, [scriptblock]$Action, [bool]$Elevated)
    $r = [pscustomobject]@{ Status = 'error'; Method = $null; Result = $null; Message = $null; TempLetter = $null; CleanupFailed = $false }
    if ($Volume) {
        $root = [string]$Volume.VolumeName
        if (-not $root.EndsWith('\')) { $root += '\' }
        $p = Test-GbGblogDir -Root $root
        if ($p.Ok) {
            $r.Method = 'volume-path'
            $r.Result = & $Action $root 'volume-path'
            $r.Status = 'ok'
            return $r
        }
        if ($p.Code -eq 2 -or $p.Code -eq 3) {
            $r.Status = 'no-gblog'
            $r.Message = 'EFI-SYSTEM has no \gblog\ folder yet (no native boot has written logs to this stick)'
            return $r
        }
        if (-not $Elevated) {
            if ($p.Code -eq 5) { $r.Status = 'needs-admin'; $r.Message = 'Windows refused to read the partition without administrator rights (access denied)' }
            else { $r.Status = 'needs-admin'; $r.Message = "reading the volume path failed ($($p.Message)); the drive-letter fallback needs administrator rights" }
            return $r
        }
        Write-GbMsg ("  The volume path did not work ({0}); trying a temporary drive letter." -f $p.Message) 'DarkYellow'
    }
    elseif (-not $Elevated) {
        $r.Status = 'needs-admin'
        $r.Message = 'Windows lists no volume for the EFI-SYSTEM partition; a temporary drive letter needs administrator rights'
        return $r
    }
    $letter = Get-GbFreeDriveLetter
    if (-not $letter) { $r.Message = 'no free drive letter for the temporary mount'; return $r }
    $kind = $null; $attempted = $false
    try {
        $attempted = $true
        $kind = Mount-GbTempLetter -Letter $letter -Selection $Selection -Volume $Volume
        $r.TempLetter = $letter
        Write-GbMsg "  Temporary drive letter $letter (removed again when done)."
        if (-not (Test-GbLetterMapsTo -Letter $letter -DiskNumber ([int]$Selection.Disk.Number) -Offset ([long]$Selection.Partition.Offset))) {
            throw "the temporary drive letter $letter does not point at the selected partition"
        }
        $p = Test-GbGblogDir -Root ($letter + '\')
        if ($p.Ok) {
            $r.Method = 'temporary-drive-letter'
            $r.Result = & $Action ($letter + '\') 'temporary-drive-letter'
            $r.Status = 'ok'
        }
        elseif ($p.Code -eq 2 -or $p.Code -eq 3) {
            $r.Status = 'no-gblog'
            $r.Message = 'EFI-SYSTEM has no \gblog\ folder yet (no native boot has written logs to this stick)'
        }
        else { $r.Message = "cannot read $letter\gblog: $($p.Message)" }
    }
    finally {
        # (review 2026-10-29) Ctrl+C or an error after the letter took effect but before Mount-GbTempLetter returned
        # (Add-PartitionAccessPath is a slow CIM call): $kind was never set, yet the letter exists. It is removed when
        # it points at the selected partition (the letter was free when chosen, so it can only be ours).
        if (-not $kind -and $attempted -and
            (Test-GbLetterMapsTo -Letter $letter -DiskNumber ([int]$Selection.Disk.Number) -Offset ([long]$Selection.Partition.Offset))) {
            $kind = if ($Volume) { 'mountpoint' } else { 'partition-access-path' }
            $r.TempLetter = $letter
        }
        if ($kind) {
            try {
                Dismount-GbTempLetter -Letter $letter -Selection $Selection -Kind $kind
                if (Test-GbLetterInUse $letter) { throw "$letter is still present" }
            } catch {
                $r.CleanupFailed = $true
                Write-Warning ("Could not remove the temporary drive letter {0} ({1}). Remove it from an administrator prompt with:  mountvol {0} /D" -f $letter, (Get-GbErrText $_))
            }
        }
    }
    return $r
}

# ---------------------------------------------------------------------------------------------------------------
# Copy (read-only source, verified destination)
# ---------------------------------------------------------------------------------------------------------------
function Get-GbTreeListing {
    param([string]$Dir, [int]$MaxDepth = 4, [int]$MaxFiles = 4096)
    $out = New-Object System.Collections.Generic.List[object]
    $stack = New-Object System.Collections.Generic.Stack[object]
    $stack.Push(@($Dir, '', 0))
    while ($stack.Count -gt 0) {
        $item = $stack.Pop()
        $d = [string]$item[0]; $rel = [string]$item[1]; $depth = [int]$item[2]
        foreach ($e in [GbLogs.NativeV4]::ListDirectory($d)) {
            if ($e.IsReparsePoint) { continue }
            $r = if ($rel) { $rel + '\' + $e.Name } else { $e.Name }
            if ($e.IsDirectory) {
                if ($depth -lt $MaxDepth) { $stack.Push(@((Join-GbPath $d $e.Name), $r, ($depth + 1))) }
                continue
            }
            $out.Add([pscustomobject]@{
                Rel = $r; Full = (Join-GbPath $d $e.Name); Length = $e.Length
                LastWriteUtc = (New-Object DateTime ([long]$e.LastWriteUtcTicks), ([DateTimeKind]::Utc))
            })
            if ($out.Count -gt $MaxFiles) { throw "more than $MaxFiles files under $Dir; refusing" }
        }
    }
    return ($out | Sort-Object Rel)
}

function Copy-GbLogTree {
    param([string]$SourceDir, [string]$DestDir, [object[]]$Listing)
    if ($null -eq $Listing) { $Listing = @(Get-GbTreeListing -Dir $SourceDir) }
    [void][IO.Directory]::CreateDirectory($DestDir)
    $files = New-Object System.Collections.Generic.List[object]
    $bad = New-Object System.Collections.Generic.List[string]
    $total = [long]0
    $i = 0
    foreach ($f in $Listing) {
        $i++
        if (-not $script:GbQuiet) { Write-Progress -Activity 'Copying gblog (read-only)' -Status $f.Rel -PercentComplete ([int](100 * ($i - 1) / [Math]::Max(1, @($Listing).Count))) }
        $dst = Join-GbPath $DestDir $f.Rel
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dst))
        $n = [long]0
        $h = [GbLogs.NativeV4]::CopyReadOnly($f.Full, $dst, [ref]$n)
        $h2 = [GbLogs.NativeV4]::HashFile($dst)
        if ($h -ne $h2) { $bad.Add("$($f.Rel): the copy differs from what was read") }
        if ($n -ne $f.Length) { Write-GbMsg ("  note: {0} has {1} bytes, the listing said {2}" -f $f.Rel, $n, $f.Length) 'DarkYellow' }
        if ($f.LastWriteUtc.Year -ge 1980) { [IO.File]::SetLastWriteTimeUtc($dst, $f.LastWriteUtc) }
        $total += $n
        $files.Add([pscustomobject]@{ Rel = $f.Rel; Length = $n; Sha256 = $h; LastWriteUtc = $f.LastWriteUtc })
    }
    if (-not $script:GbQuiet) { Write-Progress -Activity 'Copying gblog (read-only)' -Completed }
    return [pscustomobject]@{ Files = $files.ToArray(); Bad = $bad.ToArray(); TotalBytes = $total }
}

function New-GbOutputFolder([string]$Root, [string]$Stamp) {
    [void][IO.Directory]::CreateDirectory($Root)
    for ($i = 1; $i -le 99; $i++) {
        $name = if ($i -eq 1) { $Stamp } else { "$Stamp-$i" }
        $p = Join-Path $Root $name
        if (-not (Test-Path -LiteralPath $p)) { [void][IO.Directory]::CreateDirectory($p); return $p }
    }
    throw "cannot create a new folder under $Root"
}

# -SourcePath: a gblog folder, or a folder / volume root that contains gblog.
function Resolve-GbSourceGblog([string]$Path) {
    $p = $Path
    if ($p -notmatch '^\\\\[?.]\\') { $p = [IO.Path]::GetFullPath($p) }
    $err = 0
    $sub = Join-GbPath $p 'gblog'
    $a = [GbLogs.NativeV4]::GetAttributes($sub, [ref]$err)
    if ($a -ge 0 -and ($a -band 0x10)) { return $sub }
    $a = [GbLogs.NativeV4]::GetAttributes($p, [ref]$err)
    if ($a -lt 0) { throw "cannot read $Path ($((New-Object ComponentModel.Win32Exception($err)).Message))" }
    if (($a -band 0x10) -eq 0) { throw "$Path is not a folder" }
    $leaf = ($p.TrimEnd('\') -split '\\')[-1]
    $names = @([GbLogs.NativeV4]::ListDirectory($p) | ForEach-Object { $_.Name })
    if ($leaf -ieq 'gblog' -or @($names | Where-Object { $_ -match '^(boot\d+-t\d+\.txt|bootcount|audio-b\d+)' }).Count) { return $p.TrimEnd('\') }
    throw "no gblog folder and no gblog files in $Path"
}

function Test-GbPathInside([string]$Child, [string]$Parent) {
    try {
        $c = [IO.Path]::GetFullPath($Child).TrimEnd('\') + '\'
        $p = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
        return $c.StartsWith($p, [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Get-GbFreeBytes([string]$Path) {
    try {
        $root = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))
        if ($root.StartsWith('\\')) { return $null }
        return (New-Object IO.DriveInfo($root)).AvailableFreeSpace
    } catch { return $null }
}

function Set-GbReadOnlyTree([string]$Dir) {
    foreach ($f in [IO.Directory]::GetFiles($Dir, '*', [IO.SearchOption]::AllDirectories)) {
        [IO.File]::SetAttributes($f, ([IO.File]::GetAttributes($f) -bor [IO.FileAttributes]::ReadOnly))
    }
}

# ---------------------------------------------------------------------------------------------------------------
# Redaction (the engine is GbLogs.RedactorV4 above; these are thin wrappers)
# ---------------------------------------------------------------------------------------------------------------
# Text is handled as ISO-8859-1 (one char per byte), so every byte that is not redacted is written back
# unchanged, whatever the encoding of the log was.
$script:GbLatin1 = [Text.Encoding]::GetEncoding(28591)
$script:GbCategoryLabels = [ordered]@{
    serial = 'serial numbers (device, battery, USB, input ids)'; mac = 'MAC / Bluetooth addresses'
    ssid = 'Wi-Fi network names (SSIDs)'; email = 'e-mail addresses (accounts)'; account = 'account names (not e-mail)'
    name = 'user / device names'; imei = 'IMEI / MEID / IMSI / ICCID-like ids'; uuid = 'UUIDs (partitions, file systems, other ids)'
    id = 'Android ids / device descriptors'; secret = 'passwords, keys, tokens'; loc = 'locations (coordinates)'
    ip6 = 'global / EUI-64 IPv6 addresses'; term = 'your extra terms (-RedactTerm)'
}

function New-GbRedactionState([string[]]$Terms) {
    $t = [string[]]@(@($Terms) | Where-Object { $_ })
    return New-Object GbLogs.RedactorV4 (, $t)
}

# Pass A: the context rules (one file).
function Invoke-GbRedactText([string]$Text, $State) { return $State.RedactText($Text) }

# After pass A over all files: build the tables for pass B.
function Complete-GbPropagation($State) { $State.CompletePropagation() }

# Pass B: values found anywhere replaced everywhere, then the -RedactTerm words.
function Invoke-GbPropagateText([string]$Text, $State) {
    $n = 0
    $out = $State.PropagateText($Text, [ref]$n)
    return [pscustomobject]@{ Text = $out; Count = $n }
}

# Pass C: residue records (file, line, check, category; never the value).
function Find-GbResidue([string]$Text, $State, [string]$File) { return $State.FindResidue($Text, $File) }

function Get-GbFileKind([string]$Path, [long]$Length) {
    $ext = [IO.Path]::GetExtension($Path).ToLowerInvariant()
    if (@('.wav', '.pcm', '.raw', '.flac', '.ogg', '.mp3', '.m4a', '.opus') -contains $ext) { return 'audio' }
    if (@('.bin', '.img', '.gz', '.zip', '.xz', '.zst', '.lz4', '.png', '.jpg', '.jpeg', '.dat', '.efi', '.ko') -contains $ext) { return 'binary' }
    $fs = [IO.File]::OpenRead($Path)
    try {
        $buf = New-Object byte[] ([Math]::Min([long]65536, [Math]::Max([long]1, $Length)))
        $n = $fs.Read($buf, 0, $buf.Length)
        for ($i = 0; $i -lt $n; $i++) { if ($buf[$i] -eq 0) { return 'binary' } }
    } finally { $fs.Dispose() }
    return 'text'
}

# The share copy: text files redacted (passes A, B, C), audio only with -IncludeAudio, other binaries left out.
function Invoke-GbShareRedaction {
    param([string]$SourceDir, [string]$ShareDir, [string[]]$Terms, [switch]$IncludeAudio, [long]$MaxTextBytes = 128MB)
    $state = New-GbRedactionState -Terms $Terms
    [void][IO.Directory]::CreateDirectory($ShareDir)
    $plan = foreach ($f in [IO.Directory]::GetFiles($SourceDir, '*', [IO.SearchOption]::AllDirectories)) {
        $rel = $f.Substring($SourceDir.TrimEnd('\').Length + 1)
        $len = (New-Object IO.FileInfo($f)).Length
        $kind = Get-GbFileKind $f $len
        $action = switch ($kind) {
            'text' { if ($len -le $MaxTextBytes) { 'redacted' } else { 'left out (text file too large to check)' } }
            'audio' { if ($IncludeAudio) { 'copied unredacted (-IncludeAudio)' } else { 'left out (audio recording; -IncludeAudio adds it)' } }
            default { 'left out (binary, cannot be checked)' }
        }
        [pscustomobject]@{ Rel = $rel; Source = $f; Dest = (Join-GbPath $ShareDir $rel); Length = $len; Kind = $kind; Action = $action; Found = 0; Spread = 0 }
    }
    $plan = @($plan | Sort-Object Rel)
    # pass A
    $i = 0
    foreach ($p in $plan) {
        $i++
        if (-not $script:GbQuiet) { Write-Progress -Activity 'Redacting the share copy' -Status $p.Rel -PercentComplete ([int](100 * ($i - 1) / [Math]::Max(1, $plan.Count))) }
        if ($p.Action -eq 'redacted') {
            $before = $state.TotalHits
            $text = $script:GbLatin1.GetString([IO.File]::ReadAllBytes($p.Source))
            $text = Invoke-GbRedactText $text $state
            [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($p.Dest))
            [IO.File]::WriteAllBytes($p.Dest, $script:GbLatin1.GetBytes($text))
            $p.Found = $state.TotalHits - $before
        }
        elseif ($p.Action -like 'copied*') {
            [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($p.Dest))
            [IO.File]::Copy($p.Source, $p.Dest, $false)
        }
    }
    Complete-GbPropagation $state
    # pass B
    foreach ($p in @($plan | Where-Object { $_.Action -eq 'redacted' })) {
        $text = $script:GbLatin1.GetString([IO.File]::ReadAllBytes($p.Dest))
        $new = Invoke-GbPropagateText $text $state
        $p.Spread = $new.Count
        if ($p.Spread) { [IO.File]::WriteAllBytes($p.Dest, $script:GbLatin1.GetBytes($new.Text)) }
    }
    # pass C
    $residue = New-Object System.Collections.Generic.List[object]
    foreach ($p in @($plan | Where-Object { $_.Action -eq 'redacted' })) {
        $text = $script:GbLatin1.GetString([IO.File]::ReadAllBytes($p.Dest))
        foreach ($x in (Find-GbResidue $text $state $p.Rel)) { $residue.Add($x) }
    }
    if (-not $script:GbQuiet) { Write-Progress -Activity 'Redacting the share copy' -Completed }
    return [pscustomobject]@{ State = $state; Plan = $plan; Residue = $residue.ToArray() }
}

function Write-GbRedactionReport {
    param($Redaction, [string]$Path, [string]$Stamp)
    $s = $Redaction.State
    $L = New-Object System.Collections.Generic.List[string]
    $L.Add("Googlebook logs - redaction report (Get-GooglebookLogs.ps1 $script:GbVersion, collected $Stamp)")
    $L.Add('')
    $L.Add('This share copy was made from the logs of a Googlebook (Surface Book 3) USB stick. Personal values were')
    $L.Add('replaced by placeholders like <mac-1> or <ssid-2>. The same value always has the same placeholder, in')
    $L.Add('every file, so the logs still make sense. The original values are not in this copy.')
    $L.Add('')
    $L.Add('Replaced (distinct values / occurrences):')
    foreach ($c in $script:GbCategoryLabels.Keys) {
        $n = $s.CountOf($c); $h = $s.HitsOf($c)
        if ($c -eq 'term') { $n = @($s.Terms).Count }
        $L.Add(('  {0,-55} {1,5} / {2,6}' -f $script:GbCategoryLabels[$c], $n, $h))
    }
    $L.Add('')
    $L.Add('Not replaced (please skim before posting publicly): private IPv4 addresses, device and app names, Bluetooth')
    $L.Add('device names outside name= fields, free text typed into apps, and anything spelled in a way the rules do')
    $L.Add('not know. Re-run with -RedactTerm "word" for words you want removed (your name, your network name).')
    $L.Add('')
    $L.Add('Files:')
    foreach ($p in $Redaction.Plan) {
        $L.Add(('  {0,-34} {1,10}  {2}{3}' -f $p.Rel, $p.Length, $p.Action,
                $(if ($p.Action -eq 'redacted') { " ($($p.Found) in context, $($p.Spread) elsewhere)" } else { '' })))
    }
    $L.Add('')
    if (@($Redaction.Residue).Count) {
        $L.Add("CHECK FAILED: $(@($Redaction.Residue).Count) places still look personal; this copy was NOT zipped:")
        foreach ($r in @($Redaction.Residue | Select-Object -First 200)) { $L.Add(('  {0} line {1}: {2} ({3})' -f $r.File, $r.Line, $r.Category, $r.Check)) }
    } else {
        $L.Add('Final check: no known personal value and no match of the rules is left in the redacted files.')
    }
    [IO.File]::WriteAllLines($Path, $L.ToArray(), [Text.Encoding]::ASCII)
}

# Private: placeholder -> original value (bytes as in the logs). Never put it into the share copy.
function Write-GbRedactionMap($State, [string]$Path) {
    $L = New-Object System.Collections.Generic.List[string]
    $L.Add('token,category,value')
    foreach ($e in $State.GetEntries()) {
        $L.Add($e.Token + ',' + $e.Category + ',"' + ($e.Value -replace '"', '""') + '"')
    }
    $terms = @($State.Terms)
    for ($i = 0; $i -lt $terms.Count; $i++) { $L.Add('<term-' + ($i + 1) + '>,term,"' + ($terms[$i] -replace '"', '""') + '"') }
    [IO.File]::WriteAllLines($Path, $L.ToArray(), $script:GbLatin1)
}

function New-GbShareZip {
    param([string]$ShareDir, [string]$ZipPath)
    Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
    $files = @([IO.Directory]::GetFiles($ShareDir, '*', [IO.SearchOption]::AllDirectories) | Sort-Object)
    $fs = New-Object IO.FileStream($ZipPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite)
    try {
        $za = New-Object IO.Compression.ZipArchive($fs, [IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            foreach ($f in $files) {
                $name = $f.Substring($ShareDir.TrimEnd('\').Length + 1).Replace('\', '/')
                $e = $za.CreateEntry($name, [IO.Compression.CompressionLevel]::Optimal)
                $t = [IO.File]::GetLastWriteTime($f)
                if ($t.Year -ge 1980 -and $t.Year -le 2107) { $e.LastWriteTime = $t }
                $es = $e.Open()
                try { $in = [IO.File]::OpenRead($f); try { $in.CopyTo($es) } finally { $in.Dispose() } } finally { $es.Dispose() }
            }
        } finally { $za.Dispose() }
    } finally { $fs.Dispose() }
    # verify: same names, same bytes
    $za = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        if ($za.Entries.Count -ne $files.Count) { throw "zip has $($za.Entries.Count) entries, expected $($files.Count)" }
        $sha = [Security.Cryptography.SHA256]::Create()
        foreach ($e in $za.Entries) {
            $src = Join-GbPath $ShareDir ($e.FullName.Replace('/', '\'))
            $s = $e.Open(); try { $h1 = [BitConverter]::ToString($sha.ComputeHash($s)) } finally { $s.Dispose() }
            $s = [IO.File]::OpenRead($src); try { $h2 = [BitConverter]::ToString($sha.ComputeHash($s)) } finally { $s.Dispose() }
            if ($h1 -ne $h2) { throw "zip entry $($e.FullName) differs from the share file" }
        }
    } finally { $za.Dispose() }
    return $files.Count
}

# ---------------------------------------------------------------------------------------------------------------
# Elevation (only when Windows refuses the partition to a normal user)
# ---------------------------------------------------------------------------------------------------------------
function Get-GbElevatedCommand {
    param([System.Collections.IDictionary]$Params, [string]$ScriptPath)
    $q = { param($s) "'" + ([string]$s).Replace("'", "''") + "'" }
    $parts = @('& ' + (& $q $ScriptPath))
    foreach ($k in $Params.Keys) {
        $v = $Params[$k]
        if ($v -is [System.Management.Automation.SwitchParameter] -or $v -is [bool]) { if ([bool]$v) { $parts += "-$k" } }
        elseif ($v -is [array]) { $parts += "-$k @(" + ((@($v) | ForEach-Object { & $q $_ }) -join ',') + ')' }
        else { $parts += "-$k " + (& $q $v) }
    }
    $cmd = ($parts -join ' ') + "; Write-Host ''; Read-Host 'Press Enter to close this window' | Out-Null"
    return $cmd
}

function Start-GbElevated([string]$Command) {
    $b64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Command))
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $b64) | Out-Null
}

# ---------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------
function Invoke-GbMain {
    [CmdletBinding()]
    param(
        [string]$OutputRoot,
        [string]$SourcePath,
        [int]$DiskNumber = -1,
        [string[]]$RedactTerm,
        [switch]$IncludeAudio,
        [switch]$NoShare,
        [switch]$ListOnly,
        [switch]$NoElevationPrompt,
        [string]$Stamp
    )
    Set-StrictMode -Version 2.0
    $ErrorActionPreference = 'Stop'
    try {
        Write-GbMsg "Googlebook log collector $script:GbVersion" 'Cyan'
        Initialize-GbNative
        $elevated = Test-GbElevated
        if (-not $OutputRoot) {
            $docs = [Environment]::GetFolderPath('MyDocuments')
            if (-not $docs) { throw 'cannot find the Documents folder; pass -OutputRoot <folder>' }
            $OutputRoot = Join-Path $docs 'Googlebook-logs'
            # The private copy and redaction-map.csv must not be uploaded by default: if Documents is synced by
            # OneDrive, default to a folder in the user profile instead (an explicit -OutputRoot is kept as given).
            $docSync = @(@($env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial) | Where-Object { $_ -and (Test-GbPathInside $OutputRoot $_) })
            if ($docSync.Count -and $env:USERPROFILE) {
                $OutputRoot = Join-Path $env:USERPROFILE 'Googlebook-logs'
                Write-GbMsg ("Documents is synced by OneDrive, so the logs go to {0} (kept on this PC). Use -OutputRoot to choose another folder." -f $OutputRoot) 'Yellow'
            }
        }
        $OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
        if (-not $Stamp) { $Stamp = Get-Date -Format 'yyyyMMdd-HHmmss' }

        $ctx = @{ OutputRoot = $OutputRoot; Stamp = $Stamp; OutDir = $null; Copy = $null; Source = $null; Empty = $false }
        $copyAction = {
            param([string]$Root, [string]$Method)
            $src = if ($Root -eq '<gblog>') { $ctx.Source } else { Join-GbPath $Root 'gblog' }
            $ctx.Source = $src
            if (Test-GbPathInside $ctx.OutputRoot $src) { throw "the output folder $($ctx.OutputRoot) is inside the source $src" }
            Write-GbMsg "Listing $src ..."
            $listing = @(Get-GbTreeListing -Dir $src)
            if (@($listing).Count -eq 0) { $ctx.Empty = $true; return 'empty' }
            $sum = [long]0; foreach ($f in $listing) { $sum += $f.Length }
            $free = Get-GbFreeBytes $ctx.OutputRoot
            if ($null -ne $free -and $free -lt ($sum * 3 + 64MB)) {
                throw ("not enough free space for {0} of logs (and the share copy) at {1}: {2} free" -f (Format-GbSize $sum), $ctx.OutputRoot, (Format-GbSize $free))
            }
            # (review 2026-10-29) Documents is often synced by OneDrive: the private copy and redaction-map.csv would be
            # uploaded too. Say so (the default stays Documents, as README-FLASH documents).
            $sync = @(@($env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial) | Where-Object { $_ -and (Test-GbPathInside $ctx.OutputRoot $_) })
            if ($sync.Count) {
                Write-GbMsg ("Note: {0} is inside your OneDrive folder, so the private copy (gblog\, redaction-map.csv) will be uploaded to OneDrive as well. To keep it on this PC only, run again with -OutputRoot <a local folder>, e.g. -OutputRoot C:\Googlebook-logs." -f $ctx.OutputRoot) 'Yellow'
            }
            $ctx.OutDir = New-GbOutputFolder -Root $ctx.OutputRoot -Stamp $ctx.Stamp
            Write-GbMsg ("Copying {0} files ({1}) read-only to {2} ..." -f @($listing).Count, (Format-GbSize $sum), $ctx.OutDir)
            $ctx.Copy = Copy-GbLogTree -SourceDir $src -DestDir (Join-Path $ctx.OutDir 'gblog') -Listing $listing
            return $Method
        }

        $info = New-Object System.Collections.Generic.List[string]
        $info.Add("Get-GooglebookLogs.ps1 $script:GbVersion - PRIVATE copy information (do not share this folder; share only the zip)")
        $info.Add("collected: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))   administrator: $elevated")

        # Windows refused something that needs administrator rights: explain, offer the elevated restart, exit 5.
        $askAdmin = {
            param([string]$Why, [string]$Detail)
            Write-GbMsg "Administrator rights are needed: $Why." 'Yellow'
            if ($Detail) { Write-GbMsg $Detail }
            $relaunch = @{ OutputRoot = $OutputRoot; NoElevationPrompt = $true }
            if ($DiskNumber -ge 0) { $relaunch.DiskNumber = $DiskNumber }
            if ($RedactTerm) { $relaunch.RedactTerm = $RedactTerm }
            if ($IncludeAudio) { $relaunch.IncludeAudio = $true }
            if ($NoShare) { $relaunch.NoShare = $true }
            $cmd = Get-GbElevatedCommand -Params $relaunch -ScriptPath $script:GbScriptPath
            $asked = $false
            if (-not $NoElevationPrompt -and $script:GbScriptPath -and [Environment]::UserInteractive -and $Host.Name -eq 'ConsoleHost') {
                $asked = $true
                $ans = Read-Host 'Restart this script as administrator now (Windows will ask for confirmation)? [y/N]'
                if ($ans -match '^\s*(y|yes|j|ja)\s*$') {
                    Start-GbElevated $cmd
                    Write-GbMsg 'Continuing in the administrator window.' 'Cyan'
                    return 5
                }
            }
            Write-GbMsg 'To do it yourself: Start menu > type PowerShell > right-click > Run as administrator, then run:'
            Write-GbMsg ("  powershell -NoProfile -ExecutionPolicy Bypass -File `"{0}`" -OutputRoot `"{1}`"" -f $script:GbScriptPath, $OutputRoot)
            if (-not $asked) { Write-GbMsg '(Not asked because -NoElevationPrompt was given or this is not an interactive console.)' 'DarkGray' }
            return 5
        }

        if ($SourcePath) {
            $ctx.Source = Resolve-GbSourceGblog $SourcePath
            $info.Add("source: -SourcePath $($ctx.Source)")
            [void](& $copyAction '<gblog>' 'source-path')
            $info.Add('method: source-path')
        }
        else {
            Write-GbMsg 'Looking for the Googlebook stick (GPT partitions EFI-SYSTEM + super + boot_a on a USB disk) ...'
            $disks = @(Get-GbDiskInventory)
            $sys = @(Get-GbSystemDiskNumbers)
            $sel = Select-GbStick -Disks $disks -SystemDiskNumbers $sys -DiskNumber $DiskNumber
            Show-GbCandidateTable $sel
            if ($sel.Error) {
                Write-GbMsg "Stopped: $($sel.Error)." 'Red'
                # a disk whose partition table Windows would not even list for this user may be the stick
                $denied = @($disks | Where-Object { $_.Error -and ([string]$_.Error) -match '\(Win32 error 5\)' })
                if ($denied.Count -gt 0 -and -not $elevated) {
                    $why = 'Windows did not let this user read the partition table of disk ' + (($denied | ForEach-Object { $_.Number }) -join ', ')
                    if ($ListOnly) { Write-GbMsg "$why; run as administrator to see it." 'Yellow' }
                    else { return (& $askAdmin $why 'Nothing was changed.') }
                }
                if (-not $ListOnly) { return 2 }
            }
            if ($ListOnly) { return $(if ($sel.Error) { 2 } else { 0 }) }
            $part = $sel.Partition
            Write-GbMsg ("Using disk {0} ({1}), partition {2} '{3}' ({4})." -f $sel.Disk.Number, $sel.Disk.BusTypeName, $part.Number, $part.Name, (Format-GbSize $part.Length))
            $vol = Resolve-GbVolumeForPartition -DiskNumber ([int]$sel.Disk.Number) -Offset ([long]$part.Offset) -Volumes @(Get-GbVolumeInventory)
            if (-not $vol) {
                $ap = @(Get-GbPartitionAccessPaths -DiskNumber ([int]$sel.Disk.Number) -PartitionNumber ([int]$part.Number) | Where-Object { $_ -match '^\\\\\?\\Volume\{' })
                if ($ap.Count -eq 1) { $vol = [pscustomobject]@{ VolumeName = $ap[0]; Extents = @(); MountPoints = @(); Error = $null } }
            }
            $info.Add(("source: disk {0}, bus {1}, {2}, {3} {4}; partition {5} '{6}', offset {7}, {8} bytes, type {{{9}}}" -f
                $sel.Disk.Number, $sel.Disk.BusTypeName, $(if ($sel.Disk.Removable) { 'removable' } else { 'fixed' }),
                $sel.Disk.Vendor, $sel.Disk.Product, $part.Number, $part.Name, $part.Offset, $part.Length, $part.TypeGuid))
            $info.Add("volume: $(if ($vol) { $vol.VolumeName } else { '(none listed)' })")
            # (review 2026-10-29) a letter left by an earlier run whose window was closed (no finally runs then)
            $oldLetters = @(if ($vol) { @($vol.MountPoints) | Where-Object { "$_" -match '^[A-Za-z]:\\$' } })
            if ($oldLetters.Count) {
                Write-GbMsg ("  Note: EFI-SYSTEM already has the drive letter {0}. If you did not assign it, an earlier run was interrupted; remove it from an administrator prompt with:  mountvol {1} /D" -f ($oldLetters -join ', '), $oldLetters[0].TrimEnd('\')) 'Yellow'
            }
            if ($vol) { Write-GbMsg 'Reading \gblog\ through the volume path (no drive letter) ...' }
            $acc = Invoke-GbEspAccess -Selection $sel -Volume $vol -Action $copyAction -Elevated $elevated
            if ($acc.Status -eq 'no-gblog') { Write-GbMsg "Stopped: $($acc.Message)." 'Red'; return 2 }
            if ($acc.Status -eq 'needs-admin') {
                return (& $askAdmin $acc.Message 'Windows lets only administrators read files on an EFI system partition. Nothing was changed.')
            }
            if ($acc.Status -ne 'ok') { Write-GbMsg "Stopped: $($acc.Message)." 'Red'; return 4 }
            $info.Add("method: $($acc.Method)$(if ($acc.TempLetter) { ' (' + $acc.TempLetter + ', removed: ' + (-not $acc.CleanupFailed) + ')' })")
        }

        if ($ctx.Empty) { Write-GbMsg "Stopped: $($ctx.Source) is empty (no boot has written logs yet). Nothing was copied." 'Red'; return 2 }
        $outDir = $ctx.OutDir
        $copy = $ctx.Copy
        $gbDir = Join-Path $outDir 'gblog'
        $sums = @($copy.Files | ForEach-Object { $_.Sha256 + '  gblog/' + $_.Rel.Replace('\', '/') })
        [IO.File]::WriteAllLines((Join-Path $outDir 'SHA256SUMS.txt'), [string[]]$sums, [Text.Encoding]::ASCII)
        $info.Add("files: $(@($copy.Files).Count), $($copy.TotalBytes) bytes; SHA-256 of every copy matches the bytes read (SHA256SUMS.txt)")
        if (@($copy.Bad).Count) {
            [IO.File]::WriteAllLines((Join-Path $outDir 'COPY-INFO.txt'), [string[]]($info.ToArray() + @('COPY ERRORS:') + $copy.Bad), [Text.Encoding]::ASCII)
            Write-GbMsg "The copy could not be verified: $($copy.Bad -join '; ')" 'Red'
            return 4
        }
        Write-GbMsg ("Copied {0} files ({1}); every copy verified by SHA-256." -f @($copy.Files).Count, (Format-GbSize $copy.TotalBytes)) 'Green'

        $zip = $null; $rc = 0
        if (-not $NoShare) {
            Write-GbMsg 'Making the redacted share copy ...'
            $shareDir = Join-Path $outDir 'share'
            $red = Invoke-GbShareRedaction -SourceDir $gbDir -ShareDir (Join-Path $shareDir 'gblog') -Terms $RedactTerm -IncludeAudio:$IncludeAudio
            Write-GbRedactionReport -Redaction $red -Path (Join-Path $shareDir 'REDACTION-REPORT.txt') -Stamp $Stamp
            Write-GbRedactionMap -State $red.State -Path (Join-Path $outDir 'redaction-map.csv')
            foreach ($c in $script:GbCategoryLabels.Keys) {
                $n = $red.State.CountOf($c); if ($c -eq 'term') { $n = @($red.State.Terms).Count }
                $h = $red.State.HitsOf($c)
                if ($h) { Write-GbMsg ('  {0,-55} {1,4} distinct, {2,6} replaced' -f $script:GbCategoryLabels[$c], $n, $h) }
            }
            $left = @($red.Plan | Where-Object { $_.Action -like 'left out*' })
            foreach ($p in $left) { Write-GbMsg "  not in the share copy: $($p.Rel) - $($p.Action)" 'DarkGray' }
            $info.Add("share: $(@($red.Plan).Count - $left.Count) files in share\gblog, $($left.Count) left out; redaction-map.csv = placeholder -> original value (PRIVATE)")
            if (@($red.Residue).Count) {
                Write-GbMsg ("The final redaction check found {0} places that still look personal (see share\REDACTION-REPORT.txt). The zip was NOT made." -f @($red.Residue).Count) 'Red'
                $rc = 3
            } else {
                $zip = Join-Path $outDir ("Googlebook-logs-$Stamp-share.zip")
                $n = New-GbShareZip -ShareDir $shareDir -ZipPath $zip
                $info.Add("zip: $(Split-Path $zip -Leaf) ($n entries, verified)")
            }
        }
        [IO.File]::WriteAllLines((Join-Path $outDir 'COPY-INFO.txt'), $info.ToArray(), [Text.Encoding]::ASCII)
        Set-GbReadOnlyTree $gbDir

        Write-GbMsg ''
        Write-GbMsg 'Done.' 'Green'
        Write-GbMsg "  Private copy (do not post): $gbDir"
        if ($zip) {
            Write-GbMsg "  Send this file:             $zip" 'Green'
            Write-GbMsg '  It is redacted, but please skim share\REDACTION-REPORT.txt before posting it publicly.'
        }
        return $rc
    }
    catch {
        Write-GbMsg ("Error: {0}" -f (Get-GbErrText $_)) 'Red'
        $code = Get-GbWin32Code $_
        if ($code -eq 5) { Write-GbMsg 'Windows denied access; running as administrator may help.' 'Yellow' }
        return 1
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $gbRc = @(Invoke-GbMain @PSBoundParameters)
    exit ([int]$gbRc[-1])
}
