#include <stdio.h>
#include "util.h"

int g_verbose = 1;

/* Clamp v into [lo, hi]. */
int clamp(int v, int lo, int hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

void log_message(const char* msg) {
    if (g_verbose) {
        printf("%s\n", msg);
    }
}

int unused_function(int x) {
    goto done;
done:
    return x;
}
