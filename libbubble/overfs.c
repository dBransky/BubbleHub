#define _GNU_SOURCE

#include "bubblehub/overfs.h"
#include "bubblehub/log.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <unistd.h>

#define OVERFS_LOG_ERRNO(text, fmt, ...) \
    BUBBLEHUB_LOG_ERROR(text, fmt " err=%s", ##__VA_ARGS__, strerror(errno))

int bubblehub_overfs_rootfs_enabled(const bubblehub_sandbox_config *cfg) {
    return cfg->rootfs_dir != NULL && cfg->rootfs_dir[0] != '\0';
}

int bubblehub_overfs_join_mount_path(const char *root, const char *path, char *buffer, size_t buffer_size) {
    if (path == NULL || path[0] != '/') {
        return -EINVAL;
    }
    if (root == NULL || root[0] == '\0') {
        int written = snprintf(buffer, buffer_size, "%s", path);
        return (written < 0 || (size_t)written >= buffer_size) ? -ENAMETOOLONG : 0;
    }
    int written = snprintf(buffer, buffer_size, "%s%s", root, path);
    return (written < 0 || (size_t)written >= buffer_size) ? -ENAMETOOLONG : 0;
}

int bubblehub_overfs_mkdir_p(const char *path, mode_t mode) {
    if (path == NULL || path[0] == '\0') {
        BUBBLEHUB_LOG_ERROR("invalid overfs directory path", "path=%s", path != NULL ? path : "(null)");
        return -EINVAL;
    }
    char buffer[PATH_MAX];
    int written = snprintf(buffer, sizeof(buffer), "%s", path);
    if (written < 0 || (size_t)written >= sizeof(buffer)) {
        return -ENAMETOOLONG;
    }
    size_t len = strlen(buffer);
    if (len == 0) {
        return -EINVAL;
    }
    if (buffer[len - 1] == '/' && len > 1) {
        buffer[len - 1] = '\0';
    }
    for (char *cursor = buffer + 1; *cursor != '\0'; cursor++) {
        if (*cursor != '/') {
            continue;
        }
        *cursor = '\0';
        if (mkdir(buffer, mode) != 0 && errno != EEXIST) {
            OVERFS_LOG_ERRNO("failed to create overfs parent directory", "path=%s mode=%o", buffer, mode);
            return -errno;
        }
        *cursor = '/';
    }
    if (mkdir(buffer, mode) != 0 && errno != EEXIST) {
        OVERFS_LOG_ERRNO("failed to create overfs directory", "path=%s mode=%o", buffer, mode);
        return -errno;
    }
    BUBBLEHUB_LOG_DEBUG("ensured overfs directory", "path=%s mode=%o", buffer, mode);
    return 0;
}

static int ensure_parent_dir(const char *path, mode_t mode) {
    char parent[PATH_MAX];
    int written = snprintf(parent, sizeof(parent), "%s", path);
    if (written < 0 || (size_t)written >= sizeof(parent)) {
        return -ENAMETOOLONG;
    }
    char *slash = strrchr(parent, '/');
    if (slash == NULL) {
        return 0;
    }
    if (slash == parent) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }
    return bubblehub_overfs_mkdir_p(parent, mode);
}

int bubblehub_overfs_ensure_file(const char *path, mode_t mode) {
    struct stat st;
    if (stat(path, &st) == 0) {
        BUBBLEHUB_LOG_DEBUG("overfs file already exists", "path=%s regular=%d", path, S_ISREG(st.st_mode));
        return S_ISREG(st.st_mode) ? 0 : -EINVAL;
    }
    if (errno != ENOENT) {
        OVERFS_LOG_ERRNO("failed to stat overfs file", "path=%s", path);
        return -errno;
    }
    int parent_rc = ensure_parent_dir(path, 0755);
    if (parent_rc != 0) {
        return parent_rc;
    }
    int fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC, mode);
    if (fd < 0) {
        OVERFS_LOG_ERRNO("failed to create overfs file", "path=%s mode=%o", path, mode);
        return -errno;
    }
    close(fd);
    BUBBLEHUB_LOG_DEBUG("created overfs file", "path=%s mode=%o", path, mode);
    return 0;
}

int bubblehub_overfs_bind_file_readonly(const char *source, const char *target) {
    BUBBLEHUB_LOG_DEBUG("binding overfs file readonly", "source=%s target=%s", source, target);
    if (mount(source, target, NULL, MS_BIND, NULL) != 0) {
        OVERFS_LOG_ERRNO("failed to bind overfs file", "source=%s target=%s", source, target);
        return -errno;
    }
    if (mount(NULL, target, NULL, MS_BIND | MS_REMOUNT | MS_RDONLY, NULL) != 0) {
        OVERFS_LOG_ERRNO("failed to remount overfs file readonly", "target=%s", target);
        return -errno;
    }
    return 0;
}

int bubblehub_overfs_bind_file_readwrite(const char *source, const char *target) {
    BUBBLEHUB_LOG_DEBUG("binding overfs file readwrite", "source=%s target=%s", source, target);
    if (mount(source, target, NULL, MS_BIND, NULL) != 0) {
        OVERFS_LOG_ERRNO("failed to bind overfs file readwrite", "source=%s target=%s", source, target);
        return -errno;
    }
    return 0;
}

int bubblehub_overfs_bind_dir(const char *source, const char *target) {
    BUBBLEHUB_LOG_DEBUG("binding overfs directory", "source=%s target=%s", source, target);
    if (mount(source, target, NULL, MS_BIND, NULL) != 0) {
        OVERFS_LOG_ERRNO("failed to bind overfs directory", "source=%s target=%s", source, target);
        return -errno;
    }
    return 0;
}

static int bind_dir_readonly(const char *source, const char *target) {
    BUBBLEHUB_LOG_DEBUG("binding overfs directory readonly", "source=%s target=%s", source, target);
    if (mount(source, target, NULL, MS_BIND | MS_REC, NULL) != 0) {
        OVERFS_LOG_ERRNO("failed to bind overfs directory readonly", "source=%s target=%s", source, target);
        return -errno;
    }
    if (mount(NULL, target, NULL, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_REC, NULL) != 0) {
        OVERFS_LOG_ERRNO("failed to remount overfs directory readonly", "target=%s", target);
        return -errno;
    }
    return 0;
}

int bubblehub_overfs_bind_optional_dir_readonly(const char *source, const char *target) {
    struct stat st;
    if (stat(source, &st) != 0) {
        if (errno == ENOENT) {
            BUBBLEHUB_LOG_DEBUG("skipping missing optional overfs directory bind", "source=%s target=%s", source, target);
        } else {
            OVERFS_LOG_ERRNO("failed to stat optional overfs directory bind", "source=%s target=%s", source, target);
        }
        return errno == ENOENT ? 0 : -errno;
    }
    if (!S_ISDIR(st.st_mode)) {
        BUBBLEHUB_LOG_DEBUG("skipping non-directory optional overfs bind", "source=%s target=%s", source, target);
        return 0;
    }
    int rc = bubblehub_overfs_mkdir_p(target, 0755);
    if (rc != 0) {
        return rc;
    }
    return bind_dir_readonly(source, target);
}

int bubblehub_overfs_bind_optional_file_readonly(const char *source, const char *target) {
    struct stat st;
    if (stat(source, &st) != 0) {
        if (errno == ENOENT) {
            BUBBLEHUB_LOG_DEBUG("skipping missing optional overfs file bind", "source=%s target=%s", source, target);
        } else {
            OVERFS_LOG_ERRNO("failed to stat optional overfs file bind", "source=%s target=%s", source, target);
        }
        return errno == ENOENT ? 0 : -errno;
    }
    if (!S_ISREG(st.st_mode)) {
        BUBBLEHUB_LOG_DEBUG("skipping non-file optional overfs bind", "source=%s target=%s", source, target);
        return 0;
    }
    int rc = bubblehub_overfs_ensure_file(target, 0644);
    if (rc != 0) {
        return rc;
    }
    return bubblehub_overfs_bind_file_readonly(source, target);
}

static int bind_optional_node_readwrite(const char *source, const char *target) {
    struct stat st;
    if (stat(source, &st) != 0) {
        if (errno == ENOENT) {
            BUBBLEHUB_LOG_DEBUG("skipping missing optional overfs node bind", "source=%s target=%s", source, target);
        } else {
            OVERFS_LOG_ERRNO("failed to stat optional overfs node bind", "source=%s target=%s", source, target);
        }
        return errno == ENOENT ? 0 : -errno;
    }
    if (!S_ISCHR(st.st_mode) && !S_ISBLK(st.st_mode) && !S_ISFIFO(st.st_mode)) {
        BUBBLEHUB_LOG_DEBUG("skipping non-node optional overfs bind", "source=%s target=%s", source, target);
        return 0;
    }
    int rc = bubblehub_overfs_ensure_file(target, 0666);
    if (rc != 0) {
        return rc;
    }
    return bubblehub_overfs_bind_file_readwrite(source, target);
}

int bubblehub_overfs_mount_tmpfs_at(const char *target, const char *options) {
    BUBBLEHUB_LOG_DEBUG("mounting overfs tmpfs", "target=%s options=%s", target, options != NULL ? options : "");
    int rc = bubblehub_overfs_mkdir_p(target, 0755);
    if (rc != 0) {
        return rc;
    }
    if (mount("tmpfs", target, "tmpfs", MS_NOSUID | MS_NODEV, options) != 0) {
        if (errno == EBUSY) {
            BUBBLEHUB_LOG_DEBUG("overfs tmpfs already mounted", "target=%s", target);
        } else {
            OVERFS_LOG_ERRNO("failed to mount overfs tmpfs", "target=%s options=%s", target, options != NULL ? options : "");
        }
        return errno == EBUSY ? 0 : -errno;
    }
    return 0;
}

static int setup_device_mounts(const char *new_root) {
    BUBBLEHUB_LOG_DEBUG("setting up overfs device mounts", "root=%s", new_root);
    char dev_path[PATH_MAX];
    int rc = bubblehub_overfs_join_mount_path(new_root, "/dev", dev_path, sizeof(dev_path));
    if (rc != 0) {
        return rc;
    }
    rc = bubblehub_overfs_mkdir_p(dev_path, 0755);
    if (rc != 0) {
        return rc;
    }
    if (mount("tmpfs", dev_path, "tmpfs", MS_NOSUID, "mode=755,size=16m") != 0 && errno != EBUSY) {
        OVERFS_LOG_ERRNO("failed to mount overfs dev tmpfs", "target=%s", dev_path);
        return -errno;
    }
    BUBBLEHUB_LOG_DEBUG("mounted overfs dev tmpfs", "target=%s", dev_path);
    const char *devices[] = {
        "/dev/null",
        "/dev/zero",
        "/dev/random",
        "/dev/urandom",
        "/dev/tty",
    };
    for (size_t i = 0; i < sizeof(devices) / sizeof(devices[0]); i++) {
        char target[PATH_MAX];
        rc = bubblehub_overfs_join_mount_path(new_root, devices[i], target, sizeof(target));
        if (rc != 0) {
            return rc;
        }
        rc = bind_optional_node_readwrite(devices[i], target);
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}

static int setup_proc_mount(const char *new_root) {
    BUBBLEHUB_LOG_DEBUG("setting up overfs host proc bind", "root=%s", new_root);
    char proc_path[PATH_MAX];
    int rc = bubblehub_overfs_join_mount_path(new_root, "/proc", proc_path, sizeof(proc_path));
    if (rc != 0) {
        return rc;
    }
    rc = bubblehub_overfs_mkdir_p(proc_path, 0555);
    if (rc != 0) {
        return rc;
    }
    if (mount("/proc", proc_path, NULL, MS_BIND | MS_REC, NULL) != 0) {
        if (errno == EBUSY) {
            BUBBLEHUB_LOG_DEBUG("overfs host proc already mounted", "target=%s", proc_path);
            return 0;
        }
        OVERFS_LOG_ERRNO("failed to bind overfs host proc", "target=%s", proc_path);
        return -errno;
    }
    BUBBLEHUB_LOG_INFO("bound host proc into overfs root", "target=%s", proc_path);
    if (mount(NULL, proc_path, NULL, MS_BIND | MS_REMOUNT | MS_RDONLY | MS_REC, NULL) != 0) {
        BUBBLEHUB_LOG_INFO(
            "host denied readonly remount for overfs host proc",
            "target=%s err=%s",
            proc_path,
            strerror(errno));
    }
    return 0;
}

static int setup_bubblehub_runtime_binds(const char *new_root) {
    BUBBLEHUB_LOG_DEBUG("setting up overfs BubbleHub runtime binds", "root=%s", new_root);
    struct {
        const char *source;
        const char *target;
    } dirs[] = {
        {"/opt/bubblehub", "/opt/bubblehub"},
        {"/usr/local/bin", "/usr/local/bin"},
        {"/usr/local/lib", "/usr/local/lib"},
    };
    for (size_t i = 0; i < sizeof(dirs) / sizeof(dirs[0]); i++) {
        char target[PATH_MAX];
        int rc = bubblehub_overfs_join_mount_path(new_root, dirs[i].target, target, sizeof(target));
        if (rc != 0) {
            return rc;
        }
        rc = bubblehub_overfs_bind_optional_dir_readonly(dirs[i].source, target);
        if (rc != 0) {
            return rc;
        }
    }

    const char *files[] = {
        "/usr/bin/bubble",
        "/usr/bin/bubblehub",
        "/usr/bin/bubblehub-node",
        "/usr/bin/bubblehub-sandbox",
        "/usr/bin/llama-server",
        "/usr/lib/libbubble.so",
        "/usr/lib/x86_64-linux-gnu/libbubble.so",
    };
    for (size_t i = 0; i < sizeof(files) / sizeof(files[0]); i++) {
        char target[PATH_MAX];
        int rc = bubblehub_overfs_join_mount_path(new_root, files[i], target, sizeof(target));
        if (rc != 0) {
            return rc;
        }
        rc = bubblehub_overfs_bind_optional_file_readonly(files[i], target);
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}

static int setup_overlay_root(const bubblehub_sandbox_config *cfg, const char *new_root) {
    BUBBLEHUB_LOG_INFO(
        "setting up BubbleHub overfs root",
        "root=%s lower=%s upper=%s work=%s",
        new_root,
        cfg->rootfs_dir != NULL ? cfg->rootfs_dir : "",
        cfg->overlay_upper_dir != NULL ? cfg->overlay_upper_dir : "",
        cfg->overlay_work_dir != NULL ? cfg->overlay_work_dir : "");
    if (cfg->overlay_upper_dir == NULL || cfg->overlay_upper_dir[0] == '\0' ||
        cfg->overlay_work_dir == NULL || cfg->overlay_work_dir[0] == '\0') {
        BUBBLEHUB_LOG_ERROR(
            "invalid BubbleHub overfs overlay paths",
            "upper=%s work=%s",
            cfg->overlay_upper_dir != NULL ? cfg->overlay_upper_dir : "(null)",
            cfg->overlay_work_dir != NULL ? cfg->overlay_work_dir : "(null)");
        return -EINVAL;
    }
    int rc = bubblehub_overfs_mkdir_p(cfg->overlay_upper_dir, 0700);
    if (rc != 0) {
        return rc;
    }
    rc = bubblehub_overfs_mkdir_p(cfg->overlay_work_dir, 0700);
    if (rc != 0) {
        return rc;
    }
    size_t options_len = strlen(cfg->rootfs_dir) + strlen(cfg->overlay_upper_dir) + strlen(cfg->overlay_work_dir) + 64;
    char *options = malloc(options_len);
    if (options == NULL) {
        return -ENOMEM;
    }
    int written = snprintf(
        options,
        options_len,
        "lowerdir=%s,upperdir=%s,workdir=%s",
        cfg->rootfs_dir,
        cfg->overlay_upper_dir,
        cfg->overlay_work_dir);
    if (written < 0 || (size_t)written >= options_len) {
        free(options);
        return -ENAMETOOLONG;
    }
    if (mount("overlay", new_root, "overlay", MS_NOSUID | MS_NODEV, options) != 0) {
        int err = errno;
        BUBBLEHUB_LOG_ERROR(
            "failed to mount BubbleHub overfs overlay",
            "root=%s lower=%s upper=%s work=%s err=%s",
            new_root,
            cfg->rootfs_dir,
            cfg->overlay_upper_dir,
            cfg->overlay_work_dir,
            strerror(err));
        free(options);
        return -err;
    }
    BUBBLEHUB_LOG_INFO("mounted BubbleHub overfs overlay", "root=%s", new_root);
    free(options);

    char tmp_path[PATH_MAX];
    rc = bubblehub_overfs_join_mount_path(new_root, "/tmp", tmp_path, sizeof(tmp_path));
    if (rc != 0) {
        return rc;
    }
    rc = bubblehub_overfs_mount_tmpfs_at(tmp_path, "mode=1777,size=512m");
    if (rc != 0) {
        return rc;
    }
    rc = setup_proc_mount(new_root);
    if (rc != 0) {
        return rc;
    }
    rc = setup_device_mounts(new_root);
    if (rc != 0) {
        return rc;
    }
    return setup_bubblehub_runtime_binds(new_root);
}

int bubblehub_overfs_setup_mounts(const char *new_root, const bubblehub_sandbox_config *cfg) {
    BUBBLEHUB_LOG_DEBUG("setting up overfs mounts", "root=%s rootfs_enabled=%d", new_root, bubblehub_overfs_rootfs_enabled(cfg));
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) != 0) {
        OVERFS_LOG_ERRNO("failed to make mount namespace private", "root=%s", new_root);
        return -errno;
    }
    if (!bubblehub_overfs_rootfs_enabled(cfg)) {
        BUBBLEHUB_LOG_DEBUG("skipping BubbleHub overfs root because no rootfs is configured", "root=%s", new_root);
        return 0;
    }
    return setup_overlay_root(cfg, new_root);
}
