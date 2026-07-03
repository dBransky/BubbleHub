#include "bubblehub/hw.h"

#include "test_common.h"

#include <stdio.h>

static int test_total_ram_bytes(void) {
    uint64_t ram = bubblehub_hw_total_ram_bytes();
    TEST_CHECK(ram > 0);
    return 0;
}

static int test_vram_bytes_non_negative(void) {
    uint64_t total = bubblehub_hw_vram_bytes();
    uint64_t free_bytes = bubblehub_hw_free_vram_bytes();
    if (total > 0) {
        TEST_CHECK(free_bytes <= total);
    }
    (void)total;
    (void)free_bytes;
    return 0;
}

int main(void) {
    int rc = 0;
    rc |= test_total_ram_bytes();
    rc |= test_vram_bytes_non_negative();
    return rc;
}
