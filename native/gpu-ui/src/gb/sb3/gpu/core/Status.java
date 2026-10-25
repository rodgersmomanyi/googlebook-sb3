package gb.sb3.gpu.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * gb_power status object (INTERFACES 9.5, with the v1.2 graphics fields). Unknown fields are ignored; a missing or
 * mistyped known field takes its documented "empty" value. Only {@code state} is mandatory: without it the reply is not
 * a status. A server without the v1.2 fields (gb_power 1.1) reads as {@code gfx_cap} 0, so the Graphics tile shows
 * Unavailable and the app never pushes a catalog to it ({@code apps_sync} defaults to 1).
 */
public final class Status {
    public final String state;
    public final int n;
    public final List<Holder> holders;
    /** {@code vendor.gb.nouveau.mode} of this boot (not the graphics mode, see {@link #gfxMode}). */
    public final String mode;
    public final String result;
    public final int persist;
    public final int cleanBoots;
    public final int probation;
    public final int used;
    public final int foreign;
    public final int wedged;
    public final String faultReason;
    public final String bdf;
    public final String driver;
    public final String runtimeStatus;
    public final String powerState;
    public final int refcnt;
    public final String latch;
    public final String base;
    public final String dtx;
    public final String compute;
    public final String payload;
    public final int gfxdriver;
    // ---- v1.2 (9.5): in this order after gfxdriver, before t_ms
    /** The effective graphics mode: {@code auto}, {@code intel} or {@code nvidia} ("" from an old server). */
    public final String gfxMode;
    public final String gfxPolicy;
    public final String gfxActive;
    public final String gfxReason;
    /** 1 pass, 0 fail, -2 error, -1 no valid record for this identity. */
    public final int gfxOk;
    public final String gfxTest;
    public final long gfxTestT;
    public final int gfxCap;
    public final int gfxSeq;
    /** "", {@code intel} (Intel requested while apps hold nouveau) or {@code detach} (a pending detach). */
    public final String pending;
    public final int detachRef;
    public final int appsSync;
    /**
     * Not in INTERFACES 9.5 v1.2: the self-test record's {@code reason} if a later gb_power reports it as
     * {@code gfx_test_reason} (spec question UI-1B-Q1); "" today, and the page then leaves the reason out.
     */
    public final String gfxTestReason;
    public final long tMs;

    private Status(Map<String, Object> m, String state) {
        this.state = state;
        n = Json.int32(m, "n", 0);
        List<Holder> hs = new ArrayList<Holder>();
        for (Object o : Json.list(m, "holders")) {
            Holder h = Holder.from(o);
            if (h != null) hs.add(h);
        }
        holders = Collections.unmodifiableList(hs);
        mode = Json.str(m, "mode", "");
        result = Json.str(m, "result", "");
        persist = Json.int32(m, "persist", 0);
        cleanBoots = Json.int32(m, "clean_boots", 0);
        probation = Json.int32(m, "probation", 0);
        used = Json.int32(m, "used", 0);
        foreign = Json.int32(m, "foreign", 0);
        wedged = Json.int32(m, "wedged", 0);
        faultReason = Json.str(m, "fault_reason", "");
        bdf = Json.str(m, "bdf", "");
        driver = Json.str(m, "driver", "");
        runtimeStatus = Json.str(m, "runtime_status", "");
        powerState = Json.str(m, "power_state", "");
        refcnt = Json.int32(m, "refcnt", -1);
        latch = Json.str(m, "latch", "unknown");
        base = Json.str(m, "base", "unknown");
        dtx = Json.str(m, "dtx", "n/a");
        compute = Json.str(m, "compute", "stopped");
        payload = Json.str(m, "payload", "");
        gfxdriver = Json.int32(m, "gfxdriver", 0);
        gfxMode = Json.str(m, "gfx_mode", "");
        gfxPolicy = Json.str(m, "gfx_policy", "games");
        gfxActive = Json.str(m, "gfx_active", "intel");
        gfxReason = Json.str(m, "gfx_reason", "");
        gfxOk = Json.int32(m, "gfx_ok", -1);
        gfxTest = Json.str(m, "gfx_test", "none");
        gfxTestT = Json.num(m, "gfx_test_t", 0);
        gfxCap = Json.int32(m, "gfx_cap", 0);
        gfxSeq = Json.int32(m, "gfx_seq", 0);
        pending = Json.str(m, "pending", "");
        detachRef = Json.int32(m, "detach_ref", 0);
        appsSync = Json.int32(m, "apps_sync", 1);
        gfxTestReason = Json.str(m, "gfx_test_reason", "");
        tMs = Json.num(m, "t_ms", 0);
    }

    /**
     * Builds a status from a parsed reply object, or returns null when the object is not a successful status reply
     * ({@code v} 1, {@code ok} true, {@code cmd} "status", string {@code state}).
     */
    public static Status from(Map<String, Object> m) {
        if (m == null) return null;
        if (Json.num(m, "v", -1) != 1 || !Json.bool(m, "ok", false)) return null;
        if (!"status".equals(Json.str(m, "cmd", null))) return null;
        String st = Json.str(m, "state", null);
        if (st == null) return null;
        return new Status(m, st);
    }

    /** True when every field except {@code t_ms} is equal (the `watch` change rule, INTERFACES 9.3). */
    public boolean sameAs(Status o) {
        if (o == null) return false;
        return state.equals(o.state) && n == o.n && holders.equals(o.holders) && mode.equals(o.mode)
                && result.equals(o.result) && persist == o.persist && cleanBoots == o.cleanBoots
                && probation == o.probation && used == o.used && foreign == o.foreign && wedged == o.wedged
                && faultReason.equals(o.faultReason) && bdf.equals(o.bdf) && driver.equals(o.driver)
                && runtimeStatus.equals(o.runtimeStatus) && powerState.equals(o.powerState) && refcnt == o.refcnt
                && latch.equals(o.latch) && base.equals(o.base) && dtx.equals(o.dtx) && compute.equals(o.compute)
                && payload.equals(o.payload) && gfxdriver == o.gfxdriver && gfxMode.equals(o.gfxMode)
                && gfxPolicy.equals(o.gfxPolicy) && gfxActive.equals(o.gfxActive) && gfxReason.equals(o.gfxReason)
                && gfxOk == o.gfxOk && gfxTest.equals(o.gfxTest) && gfxTestT == o.gfxTestT && gfxCap == o.gfxCap
                && gfxSeq == o.gfxSeq && pending.equals(o.pending) && detachRef == o.detachRef
                && appsSync == o.appsSync && gfxTestReason.equals(o.gfxTestReason);
    }

    /** True while the compute service runs (it is stopped with `stop-compute`, never force-stopped). */
    public boolean computeRunning() {
        return !compute.isEmpty() && !"stopped".equals(compute);
    }

    /** INTERFACES 22.2: exactly {@code intel} or {@code nvidia} is that mode; anything else is {@code auto}. */
    public String effectiveMode() {
        return StateMapper.normMode(gfxMode);
    }

    /** INTERFACES 23.1 / 27.1: {@code not-feasible} (clipboard battery low) still means the base is there. */
    public boolean baseAttached() {
        return "attached".equals(base) || "not-feasible".equals(base);
    }

    public boolean nvidiaActive() {
        return "nvidia".equals(gfxActive);
    }

    @Override
    public String toString() {
        return "Status{" + state + " gfx=" + gfxMode + "/" + gfxActive + "/" + gfxReason + " cap=" + gfxCap
                + " test=" + gfxTest + " pending=" + pending + " detach_ref=" + detachRef + " holders=" + holders
                + " base=" + base + " dtx=" + dtx + "}";
    }
}
