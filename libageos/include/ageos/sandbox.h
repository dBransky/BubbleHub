#pragma once

#include <stdint.h>

typedef struct {
    const char *binary;
    char *const *argv;
    int resource_niceness;
    uint64_t memory_max;
    uint32_t cpu_percent;
    const char *workdir;
    const char *root_dir;
    const char *rootfs_dir;
    const char *overlay_upper_dir;
    const char *overlay_work_dir;
    int isolate_network;
    const char *inference_host;
    uint32_t inference_port;
    uint32_t sandbox_inference_port;
} ageos_sandbox_config;

int ageos_sandbox_run(const ageos_sandbox_config *cfg);
