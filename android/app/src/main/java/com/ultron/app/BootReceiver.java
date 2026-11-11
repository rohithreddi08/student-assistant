package com.ultron.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Starts ULTRON's background service when the phone boots or the app is updated. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        UltronService.start(context);
    }
}
