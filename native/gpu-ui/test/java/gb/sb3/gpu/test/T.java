package gb.sb3.gpu.test;

import java.util.Objects;

/** Minimal assertion helper (no JUnit: the builder has no Maven access, INTERFACES 13.1). */
final class T {
    private T() {
    }

    static int checks;
    static int fails;
    static String group = "";

    static void group(String g) {
        group = g;
        System.out.println("== " + g);
    }

    static boolean check(boolean ok, String what) {
        checks++;
        if (!ok) {
            fails++;
            System.out.println("FAIL [" + group + "] " + what);
        }
        return ok;
    }

    static void info(String what) {
        System.out.println("INFO [" + group + "] " + what);
    }

    static boolean eq(Object want, Object got, String what) {
        return check(Objects.equals(want, got), what + ": want <" + want + "> got <" + got + ">");
    }

    static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /** Polls a condition every 20 ms up to {@code ms}. */
    interface Cond {
        boolean ok();
    }

    static boolean within(long ms, Cond c) {
        long end = System.currentTimeMillis() + ms;
        while (System.currentTimeMillis() < end) {
            if (c.ok()) return true;
            sleep(20);
        }
        return c.ok();
    }
}
