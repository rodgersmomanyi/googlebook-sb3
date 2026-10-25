"""Payload marker (1.2) and manifest (1.4) parsing and validation. Pure: works on bytes/str."""
import json
import re

from . import const
from .util import is_guid, parse_attr, sha256

BUILD_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")
CHUNK_PATH_RE = re.compile(r"^gbsb3/parts/([a-z0-9_-]+)\.([0-9]{3})\.zst$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    pass


def marker_name(build_id):
    return "GBSB3-%s.marker" % build_id


def parse_marker(text):
    """Marker text -> dict(build_id, volume_label, manifest, manifest_sha256). Raises ManifestError."""
    if isinstance(text, bytes):
        try:
            text = text.decode("ascii")
        except UnicodeDecodeError:
            raise ManifestError("marker is not ASCII")
    if "\r" in text:
        raise ManifestError("marker has CR bytes")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if not lines or lines[0] != "GBSB3-PAYLOAD-MARKER 1":
        raise ManifestError("marker header line missing")
    kv = {}
    for ln in lines[1:]:
        if "=" not in ln:
            raise ManifestError("marker line without '='")
        k, v = ln.split("=", 1)
        if k in kv:
            raise ManifestError("duplicate marker key")
        kv[k] = v
    need = ("build_id", "volume_label", "manifest", "manifest_sha256")
    if sorted(kv) != sorted(need):
        raise ManifestError("marker keys %s" % sorted(kv))
    if not BUILD_ID_RE.match(kv["build_id"]):
        raise ManifestError("marker build_id format")
    if kv["volume_label"] != const.VOLUME_LABEL or kv["manifest"] != "gbsb3/manifest.json":
        raise ManifestError("marker label/manifest path")
    if not HEX64.match(kv["manifest_sha256"]):
        raise ManifestError("marker manifest_sha256 format")
    return kv


def check_marker_for_build(marker, manifest_bytes, build_id):
    """1.2 validity: build id equals ours and the manifest hash matches. Returns a problem string or None."""
    if marker["build_id"] != build_id:
        return "marker is for build %s, this installer is %s" % (marker["build_id"], build_id)
    if sha256(manifest_bytes) != marker["manifest_sha256"]:
        return "manifest sha256 does not match the marker"
    return None


def _int(d, k, minimum=0):
    v = d.get(k)
    if not isinstance(v, int) or isinstance(v, bool) or v < minimum:
        raise ManifestError("%s: integer expected" % k)
    return v


def _guid(d, k):
    v = d.get(k)
    if not is_guid(v):
        raise ManifestError("%s: lower-case GUID expected" % k)
    return v


def _hex(d, k):
    v = d.get(k)
    if not isinstance(v, str) or not HEX64.match(v):
        raise ManifestError("%s: sha256 hex expected" % k)
    return v


def parse_manifest(data, build_id=None):
    """manifest.json bytes -> validated dict with derived fields:
    m["db_parts"] = dual-boot partitions for the planner, m["wd"] = whole_disk, m["objects"] as given."""
    try:
        m = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError as e:
        raise ManifestError("manifest is not JSON: %s" % e)
    if not isinstance(m, dict) or m.get("format") != "gbsb3-iso-payload/1":
        raise ManifestError("manifest format")
    if not isinstance(m.get("build_id"), str) or not BUILD_ID_RE.match(m["build_id"]):
        raise ManifestError("manifest build_id")
    if build_id is not None and m["build_id"] != build_id:
        raise ManifestError("manifest build_id %s != %s" % (m["build_id"], build_id))
    for k in ("created_utc", "volume_label", "android_build", "system_slot", "kernel_release"):
        if not isinstance(m.get(k), str):
            raise ManifestError("%s missing" % k)
    if m["volume_label"] != const.VOLUME_LABEL:
        raise ManifestError("volume_label")
    if _int(m, "sector_size") != 512 or _int(m, "align_bytes") != const.ALIGN or \
            _int(m, "chunk_raw_bytes") != const.CHUNK:
        raise ManifestError("sector/align/chunk constants")
    comp = m.get("compression")
    if not isinstance(comp, dict) or comp.get("tool") != "zstd" or comp.get("long") != 27:
        raise ManifestError("compression")
    if not isinstance(m.get("source"), dict) or not isinstance(m.get("dual_boot"), dict):
        raise ManifestError("source/dual_boot missing")
    objs = m.get("objects")
    if not isinstance(objs, dict) or sorted(objs) != sorted(const.OBJECT_IDS):
        raise ManifestError("objects: expected %s" % (const.OBJECT_IDS,))
    for oid, o in objs.items():
        _check_object(oid, o)
    m["wd"] = _check_whole_disk(m.get("whole_disk"), objs)
    m["db_parts"] = _check_dual_boot(m["dual_boot"], objs)
    return m


def _check_object(oid, o):
    if not isinstance(o, dict):
        raise ManifestError("object %s" % oid)
    raw = _int(o, "raw_size", 1)
    _hex(o, "sha256")
    if raw % 512:
        raise ManifestError("object %s raw_size not a sector multiple" % oid)
    chunks = o.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ManifestError("object %s chunks" % oid)
    off = 0
    for k, c in enumerate(chunks):
        if not isinstance(c, dict):
            raise ManifestError("chunk")
        mo = CHUNK_PATH_RE.match(c.get("path") or "")
        if not mo or mo.group(1) != oid or int(mo.group(2)) != k:
            raise ManifestError("chunk path %r" % (c.get("path"),))
        if _int(c, "raw_offset") != off:
            raise ManifestError("object %s chunks not contiguous" % oid)
        n = _int(c, "raw_size", 1)
        if n > const.CHUNK or (k < len(chunks) - 1 and n != const.CHUNK):
            raise ManifestError("object %s chunk size" % oid)
        _int(c, "zst_size", 1)
        _hex(c, "raw_sha256")
        _hex(c, "zst_sha256")
        off += n
    if off != raw:
        raise ManifestError("object %s chunks cover %d of %d bytes" % (oid, off, raw))


def _check_whole_disk(wd, objs):
    if not isinstance(wd, dict):
        raise ManifestError("whole_disk")
    for k in ("image_bytes", "total_lba", "first_usable_lba", "last_usable_lba", "entries_lba",
              "backup_entries_lba", "entry_count", "entry_size"):
        _int(wd, k)
    _guid(wd, "disk_guid")
    _guid(wd, "super_guid")
    T = wd["total_lba"]
    if wd["image_bytes"] != T * 512 or wd["entries_lba"] != 2 or wd["backup_entries_lba"] != T - 33 or \
            wd["entry_count"] != 128 or wd["entry_size"] != 128 or wd["last_usable_lba"] != T - 34 or \
            wd["first_usable_lba"] != 34:
        raise ManifestError("whole_disk geometry")
    for k, size in (("gpt_primary", const.GPT_PRIMARY_BYTES), ("gpt_backup", const.GPT_BACKUP_BYTES)):
        g = wd.get(k)
        if not isinstance(g, dict) or g.get("bytes") != size or not isinstance(g.get("path"), str):
            raise ManifestError("whole_disk %s" % k)
        if g["path"] not in ("gbsb3/wd/gpt-primary.bin", "gbsb3/wd/gpt-backup.bin"):
            raise ManifestError("whole_disk %s path" % k)
        _hex(g, "sha256")
    parts = wd.get("partitions")
    if not isinstance(parts, list) or len(parts) != 18:
        raise ManifestError("whole_disk partitions")
    grow = 0
    prev_end = 0
    for i, p in enumerate(parts):
        if _int(p, "slot") != i or p.get("name") != const.GB_NAMES[i]:
            raise ManifestError("whole_disk partition %d" % i)
        _guid(p, "type")
        _guid(p, "guid")
        p["attrs"] = parse_attr(p.get("attributes"))
        first, last = _int(p, "first_lba"), _int(p, "last_lba")
        if first < 34 or last < first or last > T - 34 or first <= prev_end or \
                _int(p, "size") != (last - first + 1) * 512 or first % 2048:
            raise ManifestError("whole_disk partition %s placement" % p["name"])
        prev_end = last
        if p.get("blank") is True:
            if p.get("object") is not None:
                raise ManifestError("blank partition with an object")
        elif p.get("blank") is False:
            if p.get("object") not in objs or objs[p["object"]]["raw_size"] > p["size"]:
                raise ManifestError("whole_disk partition %s object" % p["name"])
        else:
            raise ManifestError("blank flag")
        if p.get("growable") is True:
            grow += 1
            if p["name"] != "userdata":
                raise ManifestError("only userdata may grow")
        elif p.get("growable") is not False:
            raise ManifestError("growable flag")
    if grow != 1:
        raise ManifestError("one growable partition expected")
    return wd


def _check_dual_boot(db, objs):
    for k in ("export_manifest", "cmdline"):
        if not isinstance(db.get(k), str):
            raise ManifestError("dual_boot %s" % k)
    _hex(db, "export_manifest_sha256")
    _guid(db, "esp_guid")
    _guid(db, "super_guid")
    if _int(db, "reserve_bytes") != const.RESERVE or _int(db, "userdata_min_bytes") != const.USERDATA_MIN:
        raise ManifestError("dual_boot reserve/userdata minimum")
    be = db.get("boot_entry")
    if not isinstance(be, dict) or be.get("label") != const.BOOT_LABEL or be.get("loader") != const.LOADER:
        raise ManifestError("dual_boot boot_entry")
    parts = db.get("partitions")
    if not isinstance(parts, list) or len(parts) != 18:
        raise ManifestError("dual_boot partitions")
    out = []
    seen = set()
    for i, p in enumerate(parts):
        if p.get("name") != const.GB_NAMES[i]:
            raise ManifestError("dual_boot partition order")
        t, g = _guid(p, "type"), _guid(p, "guid")
        if g in seen:
            raise ManifestError("dual_boot duplicate GUID")
        seen.add(g)
        attrs = parse_attr(p.get("attributes"))
        if attrs & ~const.ALLOWED_NEW_ATTRS:
            raise ManifestError("dual_boot attribute bits on %s" % p["name"])
        fill = p.get("fill") is True
        blank = p.get("blank") is True
        if fill:
            if p["name"] != "userdata" or p.get("size") is not None or p.get("min_size") != const.USERDATA_MIN \
                    or not blank:
                raise ManifestError("dual_boot userdata")
            size = None
        else:
            size = _int(p, "size", 1)
        obj = p.get("object")
        if blank:
            if obj is not None:
                raise ManifestError("blank dual_boot partition with an object")
        else:
            if obj not in objs:
                raise ManifestError("dual_boot %s object" % p["name"])
            if p["name"] == "EFI-SYSTEM" and obj != "esp-internal":
                raise ManifestError("dual_boot ESP object")
            if objs[obj]["raw_size"] > size:
                raise ManifestError("dual_boot %s object larger than the partition" % p["name"])
            if p.get("sha256") is not None and not HEX64.match(p["sha256"]):
                raise ManifestError("dual_boot sha256")
        if p["name"] == "EFI-SYSTEM" and (g != db["esp_guid"] or t != const.TYPE_ESP):
            raise ManifestError("dual_boot esp_guid")
        if p["name"] == "super" and g != db["super_guid"]:
            raise ManifestError("dual_boot super_guid")
        out.append(dict(name=p["name"], type=t, guid=g, attrs=attrs, size=size, fill=fill, blank=blank,
                        object=obj, file=p.get("file"), file_size=p.get("file_size"), sha256=p.get("sha256"),
                        min_size=p.get("min_size")))
    if sum(1 for p in out if p["fill"]) != 1:
        raise ManifestError("dual_boot: one fill partition expected")
    return out


def all_gb_guids(m):
    """GUID sets: whole-disk image (disk + 18 partitions) and dual boot (18 partitions)."""
    wd = set([m["wd"]["disk_guid"]] + [p["guid"] for p in m["wd"]["partitions"]])
    db = set(p["guid"] for p in m["db_parts"])
    return wd, db
