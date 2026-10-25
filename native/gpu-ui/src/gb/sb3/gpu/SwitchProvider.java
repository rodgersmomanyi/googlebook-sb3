package gb.sb3.gpu;

import android.Manifest;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.os.Process;
import android.util.Log;

import gb.sb3.gpu.core.StateMapper;
import gb.sb3.gpu.core.Status;
import gb.sb3.gpu.core.Texts;

/**
 * Backs the dynamic summary of the injected Settings entry "Graphics" (INTERFACES 13.2 v1.2, 27.2), following the
 * settingslib {@code TileUtils} call contract for {@code com.android.settings.summary_uri}: with the URI
 * {@code content://gb.sb3.gpu.switches} and the keyhint {@code graphics}, Settings calls method
 * {@code getDynamicSummary} with the key as extra {@code com.android.settings.keyhint} and reads
 * {@code com.android.settings.summary}. The summary is {@code <Mode label>: <tile subtitle>}. v1.2 has no switch
 * ({@code switch_uri} is gone: three modes do not fit a switch), so {@code isChecked}/{@code onCheckedChanged} are no
 * longer served.
 *
 * <p>Exported, but guarded by WRITE_SECURE_SETTINGS (Settings holds it); {@link #call} re-checks the caller.
 */
public final class SwitchProvider extends ContentProvider {
    static final String AUTHORITY = "gb.sb3.gpu.switches";
    static final String KEY = "graphics";

    static final String METHOD_GET_DYNAMIC_SUMMARY = "getDynamicSummary";
    static final String METHOD_GET_DYNAMIC_TITLE = "getDynamicTitle";
    static final String META_KEYHINT = "com.android.settings.keyhint";
    static final String META_TITLE = "com.android.settings.title";
    static final String META_SUMMARY = "com.android.settings.summary";

    static final Uri BASE = Uri.parse("content://" + AUTHORITY);

    @Override
    public boolean onCreate() {
        return true;
    }

    /** Tells Settings (observers of any URI under our authority) to re-read the summary. */
    static void notifyChanged(Context c) {
        try {
            c.getContentResolver().notifyChange(BASE, null);
        } catch (RuntimeException e) {
            Log.i(Gb.TAG, "notifyChange: " + e);
        }
    }

    private void enforceCaller() {
        if (Binder.getCallingUid() == Process.myUid()) return;
        Context c = getContext();
        if (c == null || c.checkCallingPermission(Manifest.permission.WRITE_SECURE_SETTINGS)
                != PackageManager.PERMISSION_GRANTED) {
            throw new SecurityException("WRITE_SECURE_SETTINGS required");
        }
    }

    @Override
    public Bundle call(String method, String arg, Bundle extras) {
        enforceCaller();
        String key = extras == null ? null : extras.getString(META_KEYHINT);
        if (key != null && !KEY.equals(key)) return null;
        Bundle b = new Bundle();
        if (METHOD_GET_DYNAMIC_SUMMARY.equals(method)) {
            b.putString(META_SUMMARY, StateMapper.summary(status()));
            return b;
        }
        if (METHOD_GET_DYNAMIC_TITLE.equals(method)) {
            b.putString(META_TITLE, Texts.SETTINGS_TITLE);
            return b;
        }
        return null;
    }

    private Status status() {
        Status s = Gb.CLIENT.status().status();
        Context c = getContext();
        if (c != null) Gb.publish(c, s);
        return s;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        return null;
    }

    @Override
    public String getType(Uri uri) {
        return null;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }
}
