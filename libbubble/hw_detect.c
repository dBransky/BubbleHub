#include "bubblehub/hw.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

uint64_t bubblehub_hw_total_ram_bytes(void) {
    FILE *fp = fopen("/proc/meminfo", "r");
    if (fp != NULL) {
        char key[64];
        unsigned long long value = 0;
        char unit[32];
        while (fscanf(fp, "%63s %llu %31s", key, &value, unit) == 3) {
            if (strcmp(key, "MemTotal:") == 0) {
                fclose(fp);
                return (uint64_t)value * 1024ULL;
            }
        }
        fclose(fp);
    }
    long pages = sysconf(_SC_PHYS_PAGES);
    long page_size = sysconf(_SC_PAGE_SIZE);
    if (pages <= 0 || page_size <= 0) {
        return 0;
    }
    return (uint64_t)pages * (uint64_t)page_size;
}

uint64_t bubblehub_hw_vram_bytes(void) {
    FILE *fp = popen("nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null", "r");
    if (fp == NULL) {
        return 0;
    }
    unsigned long long best_mib = 0;
    unsigned long long current = 0;
    while (fscanf(fp, "%llu", &current) == 1) {
        if (current > best_mib) {
            best_mib = current;
        }
    }
    pclose(fp);
    return (uint64_t)best_mib * 1024ULL * 1024ULL;
}

uint64_t bubblehub_hw_free_vram_bytes(void) {
    FILE *fp = popen("nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null", "r");
    if (fp == NULL) {
        return 0;
    }
    unsigned long long best_mib = 0;
    unsigned long long current = 0;
    while (fscanf(fp, "%llu", &current) == 1) {
        if (current > best_mib) {
            best_mib = current;
        }
    }
    pclose(fp);
    return (uint64_t)best_mib * 1024ULL * 1024ULL;
}
