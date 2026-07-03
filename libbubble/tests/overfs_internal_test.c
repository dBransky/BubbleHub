#define _GNU_SOURCE

#include "bubblehub/log.h"
#include "bubblehub/overfs.h"
#include "bubblehub/sandbox.h"

#include "test_common.h"

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int g_mount_calls = 0;
static int g_mount_fail_errno = 0;

static int test_mount(const char *source, const char *target, const char *filesystemtype, unsigned long mountflags, const void *data) {
    (void)source;
    (void)target;
    (void)filesystemtype;
    (void)mountflags;
    (void)data;
    g_mount_calls++;
    if (g_mount_fail_errno != 0) {
        errno = g_mount_fail_errno;
        return -1;
    }
    return 0;
}

void bubblehub_log_init(void) {}
void bubblehub_log_set_level(const char *level) {
    (void)level;
}
void bubblehub_log_set_file(const char *path) {
    (void)path;
}
void bubblehub_log_write(int level, const char *file, int line, const char *text, const char *fmt, ...) {
    (void)level;
    (void)file;
    (void)line;
    (void)text;
    (void)fmt;
}

#define mount test_mount
#include "../overfs.c"
#undef mount

static int test_bind_helpers_with_mocked_mount(void) {
    char *dir = test_mkdtemp_copy("bubblehub-overfs-internal-bind");
    TEST_CHECK(dir != NULL);
    char source_file[512];
    char target_file[512];
    char source_dir[512];
    char target_dir[512];
    snprintf(source_file, sizeof(source_file), "%s/source.txt", dir);
    snprintf(target_file, sizeof(target_file), "%s/target.txt", dir);
    snprintf(source_dir, sizeof(source_dir), "%s/source-dir", dir);
    snprintf(target_dir, sizeof(target_dir), "%s/target-dir", dir);
    FILE *fp = fopen(source_file, "w");
    TEST_CHECK(fp != NULL);
    fputs("data", fp);
    fclose(fp);
    TEST_CHECK(mkdir(source_dir, 0755) == 0);
    TEST_CHECK(bubblehub_overfs_ensure_file(target_file, 0644) == 0);
    TEST_CHECK(bubblehub_overfs_mkdir_p(target_dir, 0755) == 0);

    g_mount_fail_errno = 0;
    g_mount_calls = 0;
    TEST_CHECK(bubblehub_overfs_bind_file_readonly(source_file, target_file) == 0);
    TEST_CHECK_EQ(g_mount_calls, 2);
    TEST_CHECK(bubblehub_overfs_bind_file_readwrite(source_file, target_file) == 0);
    TEST_CHECK(bubblehub_overfs_bind_dir(source_dir, target_dir) == 0);
    TEST_CHECK(bind_dir_readonly(source_dir, target_dir) == 0);

    g_mount_fail_errno = EPERM;
    TEST_CHECK(bubblehub_overfs_bind_file_readonly(source_file, target_file) == -EPERM);
    TEST_CHECK(bubblehub_overfs_bind_file_readwrite(source_file, target_file) == -EPERM);
    TEST_CHECK(bubblehub_overfs_bind_dir(source_dir, target_dir) == -EPERM);
    TEST_CHECK(bind_dir_readonly(source_dir, target_dir) == -EPERM);
    g_mount_fail_errno = 0;
    free(dir);
    return 0;
}

static int test_optional_binds_existing_and_non_matching_nodes(void) {
    char *dir = test_mkdtemp_copy("bubblehub-overfs-internal-optional");
    TEST_CHECK(dir != NULL);
    char source_file[512];
    char source_dir[512];
    char target_file[512];
    char target_dir[512];
    char target_node[512];
    snprintf(source_file, sizeof(source_file), "%s/source.txt", dir);
    snprintf(source_dir, sizeof(source_dir), "%s/source-dir", dir);
    snprintf(target_file, sizeof(target_file), "%s/target/file.txt", dir);
    snprintf(target_dir, sizeof(target_dir), "%s/target/dir", dir);
    snprintf(target_node, sizeof(target_node), "%s/target/node", dir);
    FILE *fp = fopen(source_file, "w");
    TEST_CHECK(fp != NULL);
    fputs("data", fp);
    fclose(fp);
    TEST_CHECK(mkdir(source_dir, 0755) == 0);

    g_mount_fail_errno = 0;
    TEST_CHECK(bubblehub_overfs_bind_optional_file_readonly(source_file, target_file) == 0);
    TEST_CHECK(bubblehub_overfs_bind_optional_dir_readonly(source_dir, target_dir) == 0);
    TEST_CHECK(bubblehub_overfs_bind_optional_file_readonly(source_dir, target_file) == 0);
    TEST_CHECK(bubblehub_overfs_bind_optional_dir_readonly(source_file, target_dir) == 0);
    TEST_CHECK(bind_optional_node_readwrite(source_file, target_node) == 0);
    TEST_CHECK(bind_optional_node_readwrite("/tmp/bubblehub-overfs-missing-node", target_node) == 0);
    free(dir);
    return 0;
}

static int test_tmpfs_and_setup_helpers(void) {
    char *dir = test_mkdtemp_copy("bubblehub-overfs-internal-setup");
    TEST_CHECK(dir != NULL);
    char target[512];
    snprintf(target, sizeof(target), "%s/tmpfs", dir);

    g_mount_fail_errno = 0;
    TEST_CHECK(bubblehub_overfs_mount_tmpfs_at(target, "size=1m") == 0);
    g_mount_fail_errno = EBUSY;
    TEST_CHECK(bubblehub_overfs_mount_tmpfs_at(target, "size=1m") == 0);
    g_mount_fail_errno = EPERM;
    TEST_CHECK(bubblehub_overfs_mount_tmpfs_at(target, "size=1m") == -EPERM);
    g_mount_fail_errno = 0;

    TEST_CHECK(setup_device_mounts(dir) == 0);
    TEST_CHECK(setup_proc_mount(dir) == 0);
    TEST_CHECK(setup_bubblehub_runtime_binds(dir) == 0);

    bubblehub_sandbox_config missing_overlay = {
        .rootfs_dir = "/lower",
        .overlay_upper_dir = "",
        .overlay_work_dir = "",
    };
    TEST_CHECK(setup_overlay_root(&missing_overlay, dir) == -EINVAL);
    free(dir);
    return 0;
}

static int test_setup_mounts_and_path_errors(void) {
    bubblehub_sandbox_config empty = {0};
    g_mount_fail_errno = 0;
    TEST_CHECK(bubblehub_overfs_setup_mounts("/tmp/root", &empty) == 0);

    g_mount_fail_errno = EPERM;
    TEST_CHECK(bubblehub_overfs_setup_mounts("/tmp/root", &empty) == -EPERM);
    g_mount_fail_errno = 0;

    char buffer[4];
    TEST_CHECK(bubblehub_overfs_join_mount_path("/very-long-root", "/path", buffer, sizeof(buffer)) == -ENAMETOOLONG);
    TEST_CHECK(bubblehub_overfs_mkdir_p("", 0755) == -EINVAL);
    char *dir = test_mkdtemp_copy("bubblehub-overfs-internal-errors");
    TEST_CHECK(dir != NULL);
    TEST_CHECK(bubblehub_overfs_ensure_file(dir, 0644) == -EINVAL);
    free(dir);
    return 0;
}

int main(void) {
    int rc = 0;
    rc |= test_bind_helpers_with_mocked_mount();
    rc |= test_optional_binds_existing_and_non_matching_nodes();
    rc |= test_tmpfs_and_setup_helpers();
    rc |= test_setup_mounts_and_path_errors();
    return rc;
}
