package com.ringdown

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.ringdown.ui.theme.RingdownTheme
import com.ringdown.ui.RingdownAppRoot
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            RingdownTheme {
                RingdownAppRoot()
            }
        }
    }
}
