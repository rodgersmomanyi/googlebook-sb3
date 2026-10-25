package gb.sb3.gpu.test;

/**
 * Host test entry point (JDK 17+, no dependencies).
 *
 * <pre>
 *   java -cp classes gb.sb3.gpu.test.AllTests unit
 *   java -cp classes gb.sb3.gpu.test.AllTests spec &lt;INTERFACES.md&gt;
 *   java -cp classes gb.sb3.gpu.test.AllTests nosocket &lt;scratch dir&gt;
 *   java -cp classes gb.sb3.gpu.test.AllTests mock &lt;socket&gt; &lt;ctl socket&gt;
 *   java -cp classes gb.sb3.gpu.test.AllTests real &lt;socket&gt; &lt;props dir&gt;      (wave-1 tree, no DTX)
 *   java -cp classes gb.sb3.gpu.test.AllTests real12 &lt;socket&gt; &lt;props dir&gt;    (INTERFACES 26.3 tree, DTX)
 * </pre>
 * Prints "N checks, M failures"; exit status 1 if any check failed.
 */
public final class AllTests {
    private AllTests() {
    }

    public static void main(String[] a) {
        if (a.length < 1) {
            System.err.println("usage: AllTests unit | spec <md> | nosocket <dir> | mock <sock> <ctl> | real <sock> "
                    + "<propsdir> | real12 <sock> <propsdir>");
            System.exit(2);
        }
        switch (a[0]) {
            case "unit":
                UnitTests.run();
                break;
            case "spec":
                SpecTests.run(a[1]);
                break;
            case "nosocket":
                ProtocolTests.noSocket(a[1]);
                break;
            case "mock":
                ProtocolTests.mock(a[1], a[2]);
                break;
            case "real":
                ProtocolTests.real(a[1], a[2]);
                break;
            case "real12":
                ProtocolTests.real12(a[1], a[2]);
                break;
            default:
                System.err.println("unknown suite " + a[0]);
                System.exit(2);
        }
        System.out.println(a[0] + ": " + T.checks + " checks, " + T.fails + " failures");
        System.exit(T.fails == 0 ? 0 : 1);
    }
}
