#include "bubblehub/limits.h"

#include <errno.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

static int write_file(const char *path, const char *value) {
    FILE *fp = fopen(path, "w");
    if (fp == NULL) {
        return -errno;
    }
    if (fputs(value, fp) < 0) {
        int err = errno;
        fclose(fp);
        return -err;
    }
    fclose(fp);
    return 0;
}

int bubblehub_apply_cgroup_limits(const bubblehub_sandbox_config *cfg) {
    if (cfg == NULL) {
        return -EINVAL;
    }
    char dir[256];
    snprintf(dir, sizeof(dir), "/sys/fs/cgroup/bubblehub/%ld", (long)getpid());
    if (mkdir("/sys/fs/cgroup/bubblehub", 0755) != 0 && errno != EEXIST) {
        return 0; /* Rootless systems may not delegate cgroups; do not fail launch. */
    }
    if (mkdir(dir, 0755) != 0 && errno != EEXIST) {
        return 0;
    }

    char path[320];
    if (cfg->memory_max > 0) {
        char value[64];
        snprintf(value, sizeof(value), "%llu", (unsigned long long)cfg->memory_max);
        snprintf(path, sizeof(path), "%s/memory.max", dir);
        write_file(path, value);
    }
    if (cfg->cpu_percent > 0 && cfg->cpu_percent <= 100) {
        char value[64];
        snprintf(value, sizeof(value), "%u 100000", cfg->cpu_percent * 1000);
        snprintf(path, sizeof(path), "%s/cpu.max", dir);
        write_file(path, value);
    }
    snprintf(path, sizeof(path), "%s/pids.max", dir);
    write_file(path, "512");
    return 0;
}
