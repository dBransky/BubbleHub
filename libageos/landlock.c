#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/landlock.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef LANDLOCK_ACCESS_FS_REFER
#define LANDLOCK_ACCESS_FS_REFER (1ULL << 13)
#endif

#ifndef LANDLOCK_ACCESS_FS_TRUNCATE
#define LANDLOCK_ACCESS_FS_TRUNCATE (1ULL << 14)
#endif

static uint64_t read_rights(void) {
    return LANDLOCK_ACCESS_FS_EXECUTE |
           LANDLOCK_ACCESS_FS_READ_FILE |
           LANDLOCK_ACCESS_FS_READ_DIR;
}

static uint64_t write_rights(int abi) {
    uint64_t rights = LANDLOCK_ACCESS_FS_WRITE_FILE |
                      LANDLOCK_ACCESS_FS_REMOVE_DIR |
                      LANDLOCK_ACCESS_FS_REMOVE_FILE |
                      LANDLOCK_ACCESS_FS_MAKE_CHAR |
                      LANDLOCK_ACCESS_FS_MAKE_DIR |
                      LANDLOCK_ACCESS_FS_MAKE_REG |
                      LANDLOCK_ACCESS_FS_MAKE_SOCK |
                      LANDLOCK_ACCESS_FS_MAKE_FIFO |
                      LANDLOCK_ACCESS_FS_MAKE_BLOCK |
                      LANDLOCK_ACCESS_FS_MAKE_SYM;
    if (abi >= 2) {
        rights |= LANDLOCK_ACCESS_FS_REFER;
    }
    if (abi >= 3) {
        rights |= LANDLOCK_ACCESS_FS_TRUNCATE;
    }
    return rights;
}

static uint64_t handled_rights(int abi) {
    return read_rights() | write_rights(abi);
}

static uint64_t device_rights(int abi) {
    (void)abi;
    return LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_WRITE_FILE;
}

static uint64_t state_file_rights(int abi) {
    uint64_t rights = LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_WRITE_FILE;
    if (abi >= 3) {
        rights |= LANDLOCK_ACCESS_FS_TRUNCATE;
    }
    return rights;
}

static uint64_t readonly_file_rights(void) {
    return LANDLOCK_ACCESS_FS_READ_FILE;
}

static int add_path_rule(int ruleset_fd, const char *path, uint64_t rights) {
    if (path == NULL || path[0] == '\0') {
        return 0;
    }
    struct stat st;
    if (stat(path, &st) != 0) {
        if (errno == ENOENT) {
            return 0;
        }
        return -errno;
    }
    int fd = open(path, O_PATH | O_CLOEXEC);
    if (fd < 0) {
        return -errno;
    }
    struct landlock_path_beneath_attr rule = {
        .allowed_access = rights,
        .parent_fd = fd,
    };
    int rc = syscall(__NR_landlock_add_rule, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH, &rule, 0);
    int err = errno;
    close(fd);
    return rc == 0 ? 0 : -err;
}

static int add_path_rules(int ruleset_fd, const char *const *paths, size_t count, uint64_t rights) {
    for (size_t i = 0; i < count; i++) {
        int rc = add_path_rule(ruleset_fd, paths[i], rights);
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}

static int add_home_child_rule(int ruleset_fd, const char *child, uint64_t rights) {
    const char *home = getenv("HOME");
    if (home == NULL || home[0] == '\0') {
        return 0;
    }
    char path[PATH_MAX];
    int written = snprintf(path, sizeof(path), "%s/%s", home, child);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        return -ENAMETOOLONG;
    }
    return add_path_rule(ruleset_fd, path, rights);
}

static int add_env_or_home_cache_rule(int ruleset_fd) {
    const char *cache = getenv("AGEOS_CACHE");
    if (cache != NULL && cache[0] != '\0') {
        return add_path_rule(ruleset_fd, cache, read_rights());
    }
    return add_home_child_rule(ruleset_fd, ".cache/ageos", read_rights());
}

static int add_env_or_home_pnpm_rule(int ruleset_fd) {
    const char *pnpm_home = getenv("PNPM_HOME");
    if (pnpm_home != NULL && pnpm_home[0] != '\0') {
        return add_path_rule(ruleset_fd, pnpm_home, read_rights());
    }
    return add_home_child_rule(ruleset_fd, ".local/share/pnpm", read_rights());
}

static int add_scheduler_state_rule(int ruleset_fd, int abi) {
    const char *state_path = getenv("AGEOS_SCHEDULER_STATE");
    if (state_path == NULL || state_path[0] == '\0') {
        return 0;
    }
    return add_path_rule(ruleset_fd, state_path, state_file_rights(abi));
}

static int add_models_config_rule(int ruleset_fd) {
    const char *config_path = getenv("AGEOS_MODELS_CONFIG");
    if (config_path == NULL || config_path[0] == '\0') {
        return 0;
    }
    return add_path_rule(ruleset_fd, config_path, readonly_file_rights());
}

static int add_identity_file_rules(int ruleset_fd) {
    int rc = add_path_rule(ruleset_fd, "/etc/passwd", readonly_file_rights());
    if (rc != 0) {
        return rc;
    }
    return add_path_rule(ruleset_fd, "/etc/group", readonly_file_rights());
}

static int add_sandbox_home_rule(int ruleset_fd, int abi) {
    const char *home = getenv("HOME");
    if (home == NULL || home[0] == '\0') {
        return 0;
    }
    return add_path_rule(ruleset_fd, home, read_rights() | write_rights(abi));
}

static int path_is_beneath(const char *path, const char *parent) {
    if (strcmp(path, parent) == 0) {
        return 1;
    }
    if (strcmp(parent, "/") == 0) {
        return 0;
    }
    size_t parent_len = strlen(parent);
    return strncmp(path, parent, parent_len) == 0 && path[parent_len] == '/';
}

static int validate_writable_dir(const char *writable_dir) {
    char resolved[PATH_MAX];
    if (realpath(writable_dir, resolved) == NULL) {
        return -errno;
    }
    const char *protected_roots[] = {
        "/",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/opt",
        "/etc",
        "/var",
        "/proc",
        "/sys",
        "/dev",
        "/run",
    };
    for (size_t i = 0; i < sizeof(protected_roots) / sizeof(protected_roots[0]); i++) {
        if (path_is_beneath(resolved, protected_roots[i])) {
            return -EPERM;
        }
    }
    return 0;
}

int ageos_landlock_apply_filesystem(const char *writable_dir, int allow_dns) {
    int validate_rc = validate_writable_dir(writable_dir);
    if (validate_rc != 0) {
        return validate_rc;
    }
    int abi = (int)syscall(__NR_landlock_create_ruleset, NULL, 0, LANDLOCK_CREATE_RULESET_VERSION);
    if (abi <= 0) {
        return -errno;
    }
    struct landlock_ruleset_attr ruleset = {
        .handled_access_fs = handled_rights(abi),
    };
    int ruleset_fd = syscall(__NR_landlock_create_ruleset, &ruleset, sizeof(ruleset), 0);
    if (ruleset_fd < 0) {
        return -errno;
    }
    int rc = 0;

    static const char *const readonly_paths[] = {
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/lib64",
        "/etc/ssl",
        "/opt/ageos",
    };
    static const char *const dns_readonly_paths[] = {
        "/etc/resolv.conf",
        "/etc/nsswitch.conf",
        "/etc/hosts",
    };

    rc = add_path_rules(ruleset_fd, readonly_paths, sizeof(readonly_paths) / sizeof(readonly_paths[0]), read_rights());
    if (rc != 0) {
        close(ruleset_fd);
        return rc;
    }
    if (allow_dns) {
        rc = add_path_rules(
            ruleset_fd,
            dns_readonly_paths,
            sizeof(dns_readonly_paths) / sizeof(dns_readonly_paths[0]),
            readonly_file_rights()
        );
        if (rc != 0) {
            close(ruleset_fd);
            return rc;
        }
    }
    const char *device_paths[] = {
        "/dev/null",
        "/dev/zero",
        "/dev/random",
        "/dev/urandom",
        "/dev/tty",
    };
    for (size_t i = 0; i < sizeof(device_paths) / sizeof(device_paths[0]); i++) {
        rc = add_path_rule(ruleset_fd, device_paths[i], device_rights(abi));
        if (rc != 0) {
            close(ruleset_fd);
            return rc;
        }
    }
    rc = add_home_child_rule(ruleset_fd, ".config/ageos", read_rights());
    if (rc != 0) {
        close(ruleset_fd);
        return rc;
    }
    rc = add_env_or_home_cache_rule(ruleset_fd);
    if (rc != 0) {
        close(ruleset_fd);
        return rc;
    }
    rc = add_env_or_home_pnpm_rule(ruleset_fd);
    if (rc != 0) {
        close(ruleset_fd);
        return rc;
    }
    rc = add_scheduler_state_rule(ruleset_fd, abi);
    if (rc != 0) {
        close(ruleset_fd);
        return rc;
    }
    rc = add_models_config_rule(ruleset_fd);
    if (rc != 0) {
        close(ruleset_fd);
        return rc;
    }
    rc = add_identity_file_rules(ruleset_fd);
    if (rc != 0) {
        close(ruleset_fd);
        return rc;
    }
    rc = add_sandbox_home_rule(ruleset_fd, abi);
    if (rc != 0) {
        close(ruleset_fd);
        return rc;
    }
    rc = add_path_rule(ruleset_fd, writable_dir, read_rights() | write_rights(abi));
    if (rc != 0) {
        close(ruleset_fd);
        return rc;
    }
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        int err = errno;
        close(ruleset_fd);
        return -err;
    }
    rc = syscall(__NR_landlock_restrict_self, ruleset_fd, 0);
    int err = errno;
    close(ruleset_fd);
    return rc == 0 ? 0 : -err;
}
