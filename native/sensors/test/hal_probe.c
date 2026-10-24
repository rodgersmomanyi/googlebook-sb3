// hal_probe: re-implements, step by step, how the lapis sensors sub-HAL
// (/vendor/lib64/android.hardware.sensors@2.1-Desktop-IIO-Subhal.so, reverse-engineered with Ghidra)
// discovers and reads IIO devices, so a kernel IIO device can be checked without Android:
//   load_iio_devices  : name must be exactly one of the cros-ec names; reads "label";
//                       *sampling_frequency_available (tokens " ,", "0.000000" skipped);
//                       scale = last file ending in "scale" but not "calibscale" (0 -> 1.0)
//   scan_elements     : writes 1 to every scan_elements/*_en, reads back, *_index, *_type
//   SensorsSubHal()   : <= 5 channels, every index <= 4
//   buildSensor       : buffer/length = 200; name[_label]; location from label
//   batch/activate    : writes "%f" Hz to every file ending in "sampling_frequency"; buffer/enable
//   pollForEvents     : read() exactly 16 bytes from /dev/iio:deviceN
//   processScanData   : data channel i = (s16) bits [i*storagebytes*8, +16) of the first 8 bytes,
//                       light = (u16) channel 0; timestamp = last 8 bytes of the 16-byte scan
//   OrientationSensor : rotation from the display accelerometer x/y (Android sign convention)
// It also checks that the timestamps are CLOCK_BOOTTIME.
//
//   hal_probe [freq_hz] [samples]      (defaults 10 Hz, 8 samples per sensor)
// Writes to sysfs (scan_elements, buffer, sampling_frequency) like the HAL does; run as root.
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>

#define IIO "/sys/bus/iio/devices/"

static const struct { const char *name; int type; const char *what; } supported[] = {
	{ "cros-ec-accel", 1, "accelerometer" }, { "cros-ec-gyro", 4, "gyroscope" },
	{ "cros-ec-mag", 2, "magnetic_field" }, { "cros-ec-light", 5, "light" },
	{ "cros-ec-lid-angle", 36, "hinge_angle" }, { "cros-ec-activity", 0x10000, "event" },
	{ "cros-ec-mkbp-proximity", 0x10000, "event" }, { "sx9324", 0x10000, "event" },
};

struct chan { char name[64]; unsigned idx, storage_bytes, bits; int sign, be; };

static int rd_line(const char *path, char *buf, size_t n)
{
	FILE *f = fopen(path, "r");
	if (!f) return -errno;
	if (!fgets(buf, n, f)) { fclose(f); return -EINVAL; }
	fclose(f);
	buf[strcspn(buf, "\n")] = 0;
	return 0;
}

static int wr(const char *path, const char *val)
{
	FILE *f = fopen(path, "r+");
	if (!f) return -errno;
	fputs(val, f);
	int r = fclose(f) ? -errno : 0;
	return r;
}

static int ends(const char *s, const char *suf)
{
	size_t a = strlen(s), b = strlen(suf);
	return a >= b && !strcmp(s + a - b, suf);
}

static int64_t boottime_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_BOOTTIME, &ts);
	return (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static int probe_dev(const char *dname, double hz, int samples)
{
	char base[256], p[512], name[128] = "", label[128] = "", line[1024];
	int type = -1, i, nch = 0, err = 0;
	const char *what = "";
	struct chan ch[16];
	double sfa_min = 0, sfa_max = 0; int nsfa = 0;
	float scale = 0;
	DIR *d;
	struct dirent *e;

	snprintf(base, sizeof(base), IIO "%s", dname);
	snprintf(p, sizeof(p), "%s/name", base);
	if (rd_line(p, name, sizeof(name))) return 0;
	for (i = 0; i < (int)(sizeof(supported) / sizeof(supported[0])); i++)
		if (!strcmp(name, supported[i].name)) { type = supported[i].type; what = supported[i].what; }
	if (type < 0) { printf("%-12s name=%-16s -> IGNORED by the HAL (not a cros-ec name)\n", dname, name); return 0; }
	snprintf(p, sizeof(p), "%s/label", base);
	rd_line(p, label, sizeof(label));
	printf("%-12s name=%s label='%s' -> %s\n", dname, name, label, what);

	/* sampling_frequency_available */
	d = opendir(base);
	while (d && (e = readdir(d))) {
		if (!ends(e->d_name, "sampling_frequency_available")) continue;
		snprintf(p, sizeof(p), "%s/%s", base, e->d_name);
		if (rd_line(p, line, sizeof(line))) continue;
		printf("   %s = '%s'\n", e->d_name, line);
		for (char *s, *t = strtok_r(line, " ,", &s); t; t = strtok_r(NULL, " ,", &s)) {
			if (!strcmp(t, "0.000000")) continue;
			double v = atof(t);
			if (!nsfa || v < sfa_min) sfa_min = v;
			if (!nsfa || v > sfa_max) sfa_max = v;
			nsfa++;
		}
	}
	if (d) closedir(d);
	/* scale */
	d = opendir(base);
	while (d && (e = readdir(d))) {
		if (ends(e->d_name, "calibscale") || !ends(e->d_name, "scale")) continue;
		snprintf(p, sizeof(p), "%s/%s", base, e->d_name);
		if (!rd_line(p, line, sizeof(line))) { scale = atof(line); printf("   %s = %s\n", e->d_name, line); }
	}
	if (d) closedir(d);
	if (scale == 0) scale = 1.0f;
	if (nsfa)
		printf("   HAL: minDelay=%d us maxDelay=%d us, scale=%g, maxRange=%g resolution=%g\n",
		       (int)ceil(1e6 / sfa_max), sfa_min == sfa_max ? (int)ceil(1e6 / sfa_max) : (int)(1e6 / sfa_min) - 1,
		       scale, scale * 32768.0, scale);
	else
		printf("   HAL: no sampling_frequency_available -> default min/max delay\n");

	/* scan_elements */
	snprintf(p, sizeof(p), "%s/scan_elements", base);
	d = opendir(p);
	if (!d) { printf("   FAIL: no scan_elements (no buffer) -> HAL cannot read it\n"); return 1; }
	while ((e = readdir(d))) {
		char q[600], t[64];
		if (!ends(e->d_name, "_en")) continue;
		snprintf(q, sizeof(q), "%s/%s", p, e->d_name);
		if ((err = wr(q, "1"))) { printf("   FAIL: write 1 > %s: %d\n", q, err); closedir(d); return 1; }
		if (rd_line(q, t, sizeof(t)) || atoi(t) != 1) { printf("   FAIL: %s did not stick\n", q); closedir(d); return 1; }
		struct chan *c = &ch[nch++];
		snprintf(c->name, sizeof(c->name), "%.*s", (int)(strlen(e->d_name) - 3), e->d_name);
		snprintf(q, sizeof(q), "%s/%s_index", p, c->name);
		rd_line(q, t, sizeof(t)); c->idx = atoi(t);
		snprintf(q, sizeof(q), "%s/%s_type", p, c->name);
		rd_line(q, t, sizeof(t));
		char en, sg; unsigned rb, sb, sh = 0;
		sscanf(t, "%ce:%c%u/%u>>%u", &en, &sg, &rb, &sb, &sh);
		c->storage_bytes = sb / 8; c->bits = rb; c->sign = sg == 's'; c->be = en == 'b';
		printf("   channel %-22s index=%u type=%s\n", c->name, c->idx, t);
	}
	closedir(d);
	if (nch > 5) { printf("   FAIL: %d channels > 5 (HAL: 'Unexpected number of channels')\n", nch); return 1; }
	for (i = 0; i < nch; i++)
		if (ch[i].idx > 4) { printf("   FAIL: channel index %u > 4\n", ch[i].idx); return 1; }

	/* buildSensor: buffer/length=200; batch: sampling_frequency; activate: buffer/enable */
	snprintf(p, sizeof(p), "%s/buffer/length", base);
	if ((err = wr(p, "200"))) printf("   warn: buffer/length: %d\n", err);
	d = opendir(base);
	while (d && (e = readdir(d))) {
		if (!ends(e->d_name, "sampling_frequency")) continue;
		char q[600], v[32];
		snprintf(q, sizeof(q), "%s/%s", base, e->d_name);
		snprintf(v, sizeof(v), "%f", hz);
		err = wr(q, v);
		rd_line(q, line, sizeof(line));
		printf("   %s <- %s (rc %d), reads back %s\n", e->d_name, v, err, line);
	}
	if (d) closedir(d);
	snprintf(p, sizeof(p), "%s/buffer/enable", base);
	if ((err = wr(p, "1"))) { printf("   FAIL: buffer/enable: %d\n", err); return 1; }

	/* /dev/iio:deviceN */
	char devnode[64], devno[32];
	snprintf(devnode, sizeof(devnode), "/dev/%s", dname);
	snprintf(p, sizeof(p), "%s/dev", base);
	if (!rd_line(p, devno, sizeof(devno)) && access(devnode, F_OK)) {
		unsigned ma, mi; sscanf(devno, "%u:%u", &ma, &mi);
		mknod(devnode, S_IFCHR | 0660, makedev(ma, mi));
	}
	int fd = open(devnode, O_RDONLY | O_NONBLOCK);
	if (fd < 0) { printf("   FAIL: open %s: %s\n", devnode, strerror(errno)); return 1; }
	int got = 0; int64_t prev_ts = 0;
	for (int tries = 0; got < samples && tries < samples * 40; tries++) {
		struct pollfd pfd = { .fd = fd, .events = POLLIN };
		if (poll(&pfd, 1, 100) <= 0) continue;
		uint8_t buf[16];
		ssize_t n = read(fd, buf, 16);
		if (n <= 0) { if (errno == EAGAIN) continue; printf("   FAIL: read: %s\n", strerror(errno)); break; }
		if (n != 16) { printf("   FAIL: read %zd bytes, HAL wants 16\n", n); break; }
		uint64_t first8; int64_t ts;
		memcpy(&first8, buf, 8); memcpy(&ts, buf + 8, 8);
		float v[5] = {0};
		for (i = 0; i < nch; i++) {
			if (ch[i].idx == (unsigned)nch - 1) continue;	/* timestamp */
			unsigned sh = (ch[i].idx * ch[i].storage_bytes * 8) & 63;
			uint32_t x = (uint32_t)(first8 >> sh);
			unsigned sb = ch[i].storage_bytes * 8;
			if (sb < 32) x &= (1u << sb) - 1;
			v[ch[i].idx] = type == 5 ? scale * (float)(uint16_t)x : scale * (float)(int16_t)x;
		}
		int64_t now = boottime_ns();
		printf("   sample %d: raw=", got);
		for (i = 0; i < 16; i++) printf("%02x", buf[i]);
		if (type == 5)
			printf("  lux=%.0f", v[0]);
		else
			printf("  x=%.4f y=%.4f z=%.4f", v[0], v[1], v[2]);
		printf("  ts-boottime=%+.1f ms", (ts - now) / 1e6);
		if (prev_ts) printf(" dt=%.1f ms", (ts - prev_ts) / 1e6);
		if (type == 1) {
			float x = v[0], y = v[1];
			int rot = fabsf(x) < fabsf(y) ? (y <= 0) * 2 : (x <= 0) * 2 + 1;
			printf("  -> device_orientation %d", rot);
		}
		printf("\n");
		prev_ts = ts; got++;
	}
	close(fd);
	snprintf(p, sizeof(p), "%s/buffer/enable", base);
	wr(p, "0");
	d = opendir(base);
	while (d && (e = readdir(d))) {
		if (!ends(e->d_name, "sampling_frequency")) continue;
		char q[600]; snprintf(q, sizeof(q), "%s/%s", base, e->d_name);
		wr(q, "0.000000");
	}
	if (d) closedir(d);
	printf("   %s: %d/%d samples\n", got == samples ? "OK" : "FAIL", got, samples);
	return got == samples ? 0 : 1;
}

int main(int argc, char **argv)
{
	double hz = argc > 1 ? atof(argv[1]) : 10;
	int samples = argc > 2 ? atoi(argv[2]) : 8, fails = 0;
	DIR *d = opendir(IIO);
	struct dirent *e;
	char names[32][64]; int n = 0;
	if (!d) { perror(IIO); return 1; }
	while ((e = readdir(d)) && n < 32)
		if (!strncmp(e->d_name, "iio:device", 10)) snprintf(names[n++], 64, "%s", e->d_name);
	closedir(d);
	for (int i = 0; i < n; i++) fails += probe_dev(names[i], hz, samples);
	printf("hal_probe: %d device(s), %d failure(s)\n", n, fails);
	return fails != 0;
}
