#include "ageos/log.h"
#include "ageos/sandbox.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static uint64_t parse_bytes(const char *value) {
    char *end = NULL;
    unsigned long long base = strtoull(value, &end, 10);
    if (end == NULL || *end == '\0') {
        return (uint64_t)base;
    }
    if (*end == 'G' || *end == 'g') {
        return (uint64_t)base * 1024ULL * 1024ULL * 1024ULL;
    }
    if (*end == 'M' || *end == 'm') {
        return (uint64_t)base * 1024ULL * 1024ULL;
    }
    return (uint64_t)base;
}

int main(int argc, char **argv) {
    ageos_log_init();
    ageos_sandbox_config cfg = {
        .binary = NULL,
        .argv = NULL,
        .resource_niceness = 0,
        .memory_max = 2ULL * 1024ULL * 1024ULL * 1024ULL,
        .cpu_percent = 0,
        .workdir = ".",
        .root_dir = NULL,
        .rootfs_dir = NULL,
        .overlay_upper_dir = NULL,
        .overlay_work_dir = NULL,
        .isolate_network = 0,
        .inference_host = NULL,
        .inference_port = 0,
        .sandbox_inference_port = 0,
    };

    int i = 1;
    for (; i < argc; i++) {
        if (strcmp(argv[i], "--") == 0) {
            i++;
            break;
        }
        if (strcmp(argv[i], "--memory") == 0 && i + 1 < argc) {
            cfg.memory_max = parse_bytes(argv[++i]);
        } else if (strcmp(argv[i], "--cpu") == 0 && i + 1 < argc) {
            cfg.cpu_percent = (uint32_t)strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--workdir") == 0 && i + 1 < argc) {
            cfg.workdir = argv[++i];
        } else if (strcmp(argv[i], "--root-dir") == 0 && i + 1 < argc) {
            cfg.root_dir = argv[++i];
        } else if (strcmp(argv[i], "--rootfs-dir") == 0 && i + 1 < argc) {
            cfg.rootfs_dir = argv[++i];
        } else if (strcmp(argv[i], "--overlay-upper-dir") == 0 && i + 1 < argc) {
            cfg.overlay_upper_dir = argv[++i];
        } else if (strcmp(argv[i], "--overlay-work-dir") == 0 && i + 1 < argc) {
            cfg.overlay_work_dir = argv[++i];
        } else if (strcmp(argv[i], "--isolate-network") == 0) {
            cfg.isolate_network = 1;
        } else if (strcmp(argv[i], "--log-level") == 0 && i + 1 < argc) {
            ageos_log_set_level(argv[++i]);
        } else {
            AGEOS_LOG_ERROR("unknown ageos-sandbox option", "%s", argv[i]);
            return 2;
        }
    }
    if (i >= argc) {
        AGEOS_LOG_ERROR("missing sandbox command", "");
        AGEOS_LOG_INFO(
            "ageos-sandbox usage",
            "[--memory 2G] [--cpu 50] [--workdir DIR] [--root-dir DIR] [--rootfs-dir DIR] [--overlay-upper-dir DIR] [--overlay-work-dir DIR] [--isolate-network] [--log-level LEVEL] -- COMMAND [ARGS...]"
        );
        return 2;
    }
    cfg.binary = argv[i];
    cfg.argv = &argv[i];
    return ageos_sandbox_run(&cfg);
}
