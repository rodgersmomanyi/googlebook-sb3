package gb.sb3.gpu.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal strict JSON reader for gb_power reply lines (INTERFACES 9.1: one RFC 8259 object per line, no floats).
 * Values map to: String, Long, Double (only if a fraction/exponent appears), Boolean, null (JSON null),
 * List&lt;Object&gt; and Map&lt;String,Object&gt; (insertion order kept). Pure Java so it runs in host unit tests.
 */
public final class Json {
    /** Maximum nesting depth accepted (status objects are 3 deep). */
    static final int MAX_DEPTH = 16;

    private final String s;
    private int i;

    private Json(String s) {
        this.s = s;
    }

    /** Thrown for any malformed input. */
    public static final class ParseException extends Exception {
        private static final long serialVersionUID = 1L;

        ParseException(String msg, int at) {
            super(msg + " at " + at);
        }
    }

    /** Parses one complete JSON value; trailing non-whitespace is an error. */
    public static Object parse(String text) throws ParseException {
        if (text == null) throw new ParseException("null input", 0);
        Json p = new Json(text);
        p.ws();
        Object v = p.value(0);
        p.ws();
        if (p.i != p.s.length()) throw new ParseException("trailing data", p.i);
        return v;
    }

    /** Parses a JSON object (the only top-level type gb_power sends). */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) throws ParseException {
        Object v = parse(text);
        if (!(v instanceof Map)) throw new ParseException("not an object", 0);
        return (Map<String, Object>) v;
    }

    private void ws() {
        while (i < s.length()) {
            char c = s.charAt(i);
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') i++;
            else break;
        }
    }

    private Object value(int depth) throws ParseException {
        if (depth > MAX_DEPTH) throw new ParseException("too deep", i);
        if (i >= s.length()) throw new ParseException("unexpected end", i);
        char c = s.charAt(i);
        switch (c) {
            case '{':
                return object(depth);
            case '[':
                return array(depth);
            case '"':
                return string();
            case 't':
                lit("true");
                return Boolean.TRUE;
            case 'f':
                lit("false");
                return Boolean.FALSE;
            case 'n':
                lit("null");
                return null;
            default:
                if (c == '-' || (c >= '0' && c <= '9')) return number();
                throw new ParseException("unexpected character", i);
        }
    }

    private void lit(String w) throws ParseException {
        if (!s.startsWith(w, i)) throw new ParseException("bad literal", i);
        i += w.length();
    }

    private Map<String, Object> object(int depth) throws ParseException {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        i++; // {
        ws();
        if (i < s.length() && s.charAt(i) == '}') {
            i++;
            return m;
        }
        for (;;) {
            ws();
            if (i >= s.length() || s.charAt(i) != '"') throw new ParseException("expected key", i);
            String k = string();
            ws();
            if (i >= s.length() || s.charAt(i) != ':') throw new ParseException("expected ':'", i);
            i++;
            ws();
            Object v = value(depth + 1);
            if (m.containsKey(k)) throw new ParseException("duplicate key " + k, i);
            m.put(k, v);
            ws();
            if (i >= s.length()) throw new ParseException("unterminated object", i);
            char c = s.charAt(i++);
            if (c == '}') return m;
            if (c != ',') throw new ParseException("expected ',' or '}'", i - 1);
        }
    }

    private List<Object> array(int depth) throws ParseException {
        List<Object> l = new ArrayList<Object>();
        i++; // [
        ws();
        if (i < s.length() && s.charAt(i) == ']') {
            i++;
            return Collections.unmodifiableList(l);
        }
        for (;;) {
            ws();
            l.add(value(depth + 1));
            ws();
            if (i >= s.length()) throw new ParseException("unterminated array", i);
            char c = s.charAt(i++);
            if (c == ']') return Collections.unmodifiableList(l);
            if (c != ',') throw new ParseException("expected ',' or ']'", i - 1);
        }
    }

    private String string() throws ParseException {
        StringBuilder b = new StringBuilder();
        i++; // opening quote
        while (i < s.length()) {
            char c = s.charAt(i++);
            if (c == '"') return b.toString();
            if (c < 0x20) throw new ParseException("control character in string", i - 1);
            if (c != '\\') {
                b.append(c);
                continue;
            }
            if (i >= s.length()) break;
            char e = s.charAt(i++);
            switch (e) {
                case '"': b.append('"'); break;
                case '\\': b.append('\\'); break;
                case '/': b.append('/'); break;
                case 'b': b.append('\b'); break;
                case 'f': b.append('\f'); break;
                case 'n': b.append('\n'); break;
                case 'r': b.append('\r'); break;
                case 't': b.append('\t'); break;
                case 'u':
                    if (i + 4 > s.length()) throw new ParseException("short \\u escape", i);
                    int cp = 0;
                    for (int k = 0; k < 4; k++) {
                        int d = Character.digit(s.charAt(i++), 16);
                        if (d < 0) throw new ParseException("bad \\u escape", i - 1);
                        cp = cp * 16 + d;
                    }
                    b.append((char) cp);
                    break;
                default:
                    throw new ParseException("bad escape", i - 1);
            }
        }
        throw new ParseException("unterminated string", i);
    }

    private Object number() throws ParseException {
        int start = i;
        if (s.charAt(i) == '-') i++;
        if (i >= s.length()) throw new ParseException("bad number", start);
        if (s.charAt(i) == '0') {
            i++;
        } else if (s.charAt(i) >= '1' && s.charAt(i) <= '9') {
            while (i < s.length() && Character.isDigit(s.charAt(i))) i++;
        } else {
            throw new ParseException("bad number", start);
        }
        boolean real = false;
        if (i < s.length() && s.charAt(i) == '.') {
            real = true;
            i++;
            int d = i;
            while (i < s.length() && Character.isDigit(s.charAt(i))) i++;
            if (i == d) throw new ParseException("bad fraction", start);
        }
        if (i < s.length() && (s.charAt(i) == 'e' || s.charAt(i) == 'E')) {
            real = true;
            i++;
            if (i < s.length() && (s.charAt(i) == '+' || s.charAt(i) == '-')) i++;
            int d = i;
            while (i < s.length() && Character.isDigit(s.charAt(i))) i++;
            if (i == d) throw new ParseException("bad exponent", start);
        }
        String t = s.substring(start, i);
        try {
            return real ? (Object) Double.valueOf(t) : (Object) Long.valueOf(t);
        } catch (NumberFormatException ex) {
            throw new ParseException("number out of range", start);
        }
    }

    // ---- typed accessors with defaults (unknown or mistyped fields fall back to the default)

    public static String str(Map<String, Object> m, String k, String def) {
        Object v = m == null ? null : m.get(k);
        return v instanceof String ? (String) v : def;
    }

    public static long num(Map<String, Object> m, String k, long def) {
        Object v = m == null ? null : m.get(k);
        return v instanceof Long ? ((Long) v).longValue() : def;
    }

    public static int int32(Map<String, Object> m, String k, int def) {
        long v = num(m, k, def);
        return v < Integer.MIN_VALUE || v > Integer.MAX_VALUE ? def : (int) v;
    }

    public static boolean bool(Map<String, Object> m, String k, boolean def) {
        Object v = m == null ? null : m.get(k);
        return v instanceof Boolean ? ((Boolean) v).booleanValue() : def;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> list(Map<String, Object> m, String k) {
        Object v = m == null ? null : m.get(k);
        return v instanceof List ? (List<Object>) v : Collections.<Object>emptyList();
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> obj(Object v) {
        return v instanceof Map ? (Map<String, Object>) v : null;
    }
}
