// fake_ish: a uhid "HID sensor hub" with an accelerometer 3D (report 1) and a gyroscope 3D (report 2),
// shaped like an Intel ISH sensor collection (one field per axis, 32-bit signed, unit exponent, feature
// report with reporting state / power state / report interval / change sensitivity).
// Used only in the QEMU test of the Android-mode hid-sensor-accel-3d / hid-sensor-gyro-3d patches.
//
//   fake_ish [seconds]   (default 30)
// Sends accel = (0, -1000, 0) milli-g  (device upright, Windows/HID sign: gravity on -Y)
//       gyro  = (1000, -500, 250) x 0.01 deg/s
// every 20 ms while the sensor is powered (power state feature != 5 = D4), logs feature SET_REPORTs.
#include <errno.h>
#include <fcntl.h>
#include <linux/uhid.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static const uint8_t rdesc[] = {
	/* ---- Accelerometer 3D, report id 1 ---- */
	0x05, 0x20,			/* Usage Page (Sensor) */
	0x09, 0x73,			/* Usage (Accelerometer 3D) */
	0xA1, 0x01,			/* Collection (Application) */
	0x85, 0x01,			/*   Report ID (1) */
	0x0A, 0x16, 0x03,		/*   Usage (Property: Reporting State) */
	0x15, 0x00, 0x25, 0x05, 0x75, 0x08, 0x95, 0x01,
	0xB1, 0x02,			/*   Feature (Data,Var,Abs) */
	0x0A, 0x19, 0x03,		/*   Usage (Property: Power State) */
	0xB1, 0x02,			/*   Feature */
	0x0A, 0x0E, 0x03,		/*   Usage (Property: Report Interval) */
	0x15, 0x00, 0x27, 0xFF, 0xFF, 0xFF, 0x7F, 0x75, 0x20, 0x95, 0x01,
	0xB1, 0x02,			/*   Feature (ms, unit 0 = default) */
	0x0A, 0x52, 0x14,		/*   Usage (Acceleration | Change Sensitivity Abs) */
	0x15, 0x00, 0x26, 0xFF, 0x7F, 0x75, 0x10, 0x95, 0x01,
	0x55, 0x0E,			/*   Unit Exponent (-2) */
	0xB1, 0x02,			/*   Feature */
	0x17, 0x00, 0x00, 0x00, 0x80, 0x27, 0xFF, 0xFF, 0xFF, 0x7F, 0x75, 0x20, 0x95, 0x01,
	0x55, 0x0D,			/*   Unit Exponent (-3): milli-g */
	0x0A, 0x53, 0x04, 0x81, 0x02,	/*   Usage (Accel X) Input */
	0x0A, 0x54, 0x04, 0x81, 0x02,	/*   Usage (Accel Y) Input */
	0x0A, 0x55, 0x04, 0x81, 0x02,	/*   Usage (Accel Z) Input */
	0x55, 0x00,
	0xC0,				/* End Collection */
	/* ---- Gyrometer 3D, report id 2 ---- */
	0x05, 0x20,
	0x09, 0x76,			/* Usage (Gyrometer 3D) */
	0xA1, 0x01,
	0x85, 0x02,
	0x0A, 0x16, 0x03,
	0x15, 0x00, 0x25, 0x05, 0x75, 0x08, 0x95, 0x01,
	0xB1, 0x02,
	0x0A, 0x19, 0x03,
	0xB1, 0x02,
	0x0A, 0x0E, 0x03,
	0x15, 0x00, 0x27, 0xFF, 0xFF, 0xFF, 0x7F, 0x75, 0x20, 0x95, 0x01,
	0xB1, 0x02,
	0x0A, 0x56, 0x14,		/*   Usage (Angular Velocity | Change Sensitivity Abs) */
	0x15, 0x00, 0x26, 0xFF, 0x7F, 0x75, 0x10, 0x95, 0x01,
	0x55, 0x0E,
	0xB1, 0x02,
	0x17, 0x00, 0x00, 0x00, 0x80, 0x27, 0xFF, 0xFF, 0xFF, 0x7F, 0x75, 0x20, 0x95, 0x01,
	0x55, 0x0E,			/*   Unit Exponent (-2): 0.01 deg/s */
	0x0A, 0x57, 0x04, 0x81, 0x02,	/*   Usage (Angular Velocity X) */
	0x0A, 0x58, 0x04, 0x81, 0x02,
	0x0A, 0x59, 0x04, 0x81, 0x02,
	0x55, 0x00,
	0xC0,
};

/* feature report bytes after the report id: reporting(1) power(1) interval(4) sensitivity(2) */
static uint8_t feat[3][8] = {
	{0}, {0, 0, 100, 0, 0, 0, 10, 0}, {0, 0, 100, 0, 0, 0, 10, 0}
};

static int uwrite(int fd, struct uhid_event *ev)
{
	ssize_t r = write(fd, ev, sizeof(*ev));
	if (r != sizeof(*ev)) { perror("uhid write"); return -1; }
	return 0;
}

static void put32(uint8_t *p, int32_t v) { memcpy(p, &v, 4); }

int main(int argc, char **argv)
{
	int secs = argc > 1 ? atoi(argv[1]) : 30;
	int fd = open("/dev/uhid", O_RDWR | O_CLOEXEC);
	struct uhid_event ev;
	struct timespec t0, now, last = {0};
	if (fd < 0) { perror("open /dev/uhid"); return 1; }

	memset(&ev, 0, sizeof(ev));
	ev.type = UHID_CREATE2;
	strcpy((char *)ev.u.create2.name, "fake ISH HID Sensor Collection");
	memcpy(ev.u.create2.rd_data, rdesc, sizeof(rdesc));
	ev.u.create2.rd_size = sizeof(rdesc);
	ev.u.create2.bus = 0x18;	/* BUS_I2C-ish, any bus works */
	ev.u.create2.vendor = 0x8087;
	ev.u.create2.product = 0x0ac2;
	if (uwrite(fd, &ev)) return 1;
	fprintf(stderr, "fake_ish: created (%zu byte descriptor)\n", sizeof(rdesc));

	clock_gettime(CLOCK_MONOTONIC, &t0);
	for (;;) {
		struct pollfd pfd = { .fd = fd, .events = POLLIN };
		int n = poll(&pfd, 1, 5);
		clock_gettime(CLOCK_MONOTONIC, &now);
		if (now.tv_sec - t0.tv_sec >= secs) break;
		if (n > 0 && (pfd.revents & POLLIN)) {
			memset(&ev, 0, sizeof(ev));
			if (read(fd, &ev, sizeof(ev)) <= 0) { perror("uhid read"); break; }
			if (ev.type == UHID_GET_REPORT) {
				unsigned id = ev.u.get_report.rnum;
				unsigned rtype = ev.u.get_report.rtype;
				uint32_t idq = ev.u.get_report.id;
				memset(&ev, 0, sizeof(ev));
				ev.type = UHID_GET_REPORT_REPLY;
				ev.u.get_report_reply.id = idq;
				if ((id == 1 || id == 2) && rtype == UHID_INPUT_REPORT) {
					/* synchronous input read (in_*_raw): current sample */
					ev.u.get_report_reply.err = 0;
					ev.u.get_report_reply.size = 13;
					ev.u.get_report_reply.data[0] = id;
					put32(&ev.u.get_report_reply.data[1], id == 1 ? 0 : 1000);
					put32(&ev.u.get_report_reply.data[5], id == 1 ? -1000 : -500);
					put32(&ev.u.get_report_reply.data[9], id == 1 ? 0 : 250);
				} else if (id == 1 || id == 2) {
					ev.u.get_report_reply.err = 0;
					ev.u.get_report_reply.size = 9;
					ev.u.get_report_reply.data[0] = id;
					memcpy(&ev.u.get_report_reply.data[1], feat[id], 8);
				} else {
					ev.u.get_report_reply.err = EIO;
				}
				uwrite(fd, &ev);
			} else if (ev.type == UHID_SET_REPORT) {
				unsigned id = ev.u.set_report.rnum;
				uint32_t idq = ev.u.set_report.id;
				if ((id == 1 || id == 2) && ev.u.set_report.size >= 9) {
					uint32_t iv; uint16_t se;
					memcpy(feat[id], &ev.u.set_report.data[1], 8);
					memcpy(&iv, &feat[id][2], 4); memcpy(&se, &feat[id][6], 2);
					fprintf(stderr, "fake_ish: SET_FEATURE id=%u reporting=%u power=%u interval_ms=%u sensitivity=%u\n",
						id, feat[id][0], feat[id][1], iv, se);
				}
				memset(&ev, 0, sizeof(ev));
				ev.type = UHID_SET_REPORT_REPLY;
				ev.u.set_report_reply.id = idq;
				ev.u.set_report_reply.err = 0;
				uwrite(fd, &ev);
			} else if (ev.type == UHID_START) {
				fprintf(stderr, "fake_ish: START\n");
			} else if (ev.type == UHID_OPEN) {
				fprintf(stderr, "fake_ish: OPEN\n");
			} else if (ev.type == UHID_CLOSE) {
				fprintf(stderr, "fake_ish: CLOSE\n");
			}
		}
		/* input reports every 20 ms */
		long ms = (now.tv_sec - last.tv_sec) * 1000 + (now.tv_nsec - last.tv_nsec) / 1000000;
		if (ms >= 20) {
			last = now;
			memset(&ev, 0, sizeof(ev));
			ev.type = UHID_INPUT2;
			ev.u.input2.size = 13;
			ev.u.input2.data[0] = 1;
			put32(&ev.u.input2.data[1], 0);
			put32(&ev.u.input2.data[5], -1000);
			put32(&ev.u.input2.data[9], 0);
			uwrite(fd, &ev);
			memset(&ev, 0, sizeof(ev));
			ev.type = UHID_INPUT2;
			ev.u.input2.size = 13;
			ev.u.input2.data[0] = 2;
			put32(&ev.u.input2.data[1], 1000);
			put32(&ev.u.input2.data[5], -500);
			put32(&ev.u.input2.data[9], 250);
			uwrite(fd, &ev);
		}
	}
	memset(&ev, 0, sizeof(ev));
	ev.type = UHID_DESTROY;
	uwrite(fd, &ev);
	fprintf(stderr, "fake_ish: done\n");
	return 0;
}
